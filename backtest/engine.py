# backtest/engine.py
from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

import numpy as np
import pandas as pd

from config.calendar import (
    get_krx_month_end_sessions,
    get_krx_sessions,
    next_krx_business_day,
)
from config.settings import settings
from strategy.market_regime import MarketRegimeFilter
from strategy.rebalancer import Rebalancer
from strategy.screener import MultiFactorScreener

logger = logging.getLogger(__name__)


class MultiFactorBacktest:
    """멀티팩터 전략 월별 리밸런싱 백테스트 엔진

    리스크 관리 계층:
      1. 변동성 타겟팅: 실현 변동성 > 목표 시 투자 비중 자동 축소
      2. 시장 레짐 필터: 하락장 시 투자 비중 축소
      3. 종목별 트레일링 스톱: 매수가 대비 -N% 하락 시 강제 매도
      4. MDD 서킷브레이커: 고점 대비 -N% 하락 시 전량 매도 → 현금 대피

    흐름:
      리밸런싱일(T, 월 마지막 영업일) → 팩터 계산 → 포트폴리오 결정
      → T+1 영업일 시가(open)로 매매 체결 (선견 편향 방지)
    """

    def __init__(self, initial_cash: float = 0) -> None:
        if initial_cash <= 0:
            # config.yaml의 initial_cash 사용 (기본 1000만원)
            initial_cash = settings.portfolio.initial_cash
        self.initial_cash = initial_cash
        self.screener = MultiFactorScreener()
        self.krx = self.screener.collector  # OHLCV 조회용 collector 공유
        self.rebalancer = Rebalancer()
        self.regime_filter = MarketRegimeFilter(self.krx)

    def run(
        self,
        start_date: str,
        end_date: str,
        market: str | None = None,
    ) -> pd.DataFrame:
        """백테스트 실행

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
            market: 대상 시장 (None이면 settings.universe.market 사용)

        Returns:
            DataFrame(index=date, columns=[portfolio_value, cash, n_holdings, returns])
        """
        import time as _time

        market = market or settings.universe.market
        run_start = _time.monotonic()
        logger.info(f"백테스트 시작: {start_date} ~ {end_date} ({market})")

        # DART 통계 초기화
        if hasattr(self.krx, "dart_client") and self.krx.dart_client:
            self.krx.dart_client.reset_stats()

        rebal_dates = self._generate_rebalance_dates(start_date, end_date, market)

        # 펀더멘털 프리페치: 리밸런싱 날짜별 DART 데이터를 사전 수집하여 DB 캐시
        self._prefetch_fundamentals(rebal_dates, market)

        cash = self.initial_cash
        holdings: dict[str, int] = {}  # {ticker: shares}
        cost_basis: dict[str, float] = {}  # {ticker: 평균 매수단가}
        history: list[dict] = []
        turnover_log: list[dict] = []  # 리밸런싱별 교체율 기록
        peak_value = self.initial_cash  # MDD 서킷브레이커용 고점 추적
        circuit_breaker_active = False

        for i, rebal_dt in enumerate(rebal_dates):
            date_str = rebal_dt.strftime("%Y%m%d")
            logger.info(f"[{i + 1}/{len(rebal_dates)}] 리밸런싱 신호 계산: {date_str}")

            try:
                # T일 팩터 계산 → 목표 포트폴리오
                new_tickers = self._calc_portfolio(date_str, market)
                if not new_tickers:
                    logger.warning(f"{date_str}: 포트폴리오 계산 실패, 스킵")
                    continue

                # T+1 영업일 시가 체결 (선견 편향 방지, KRX 캘린더)
                trade_dt = next_krx_business_day(rebal_dt)
                trade_date_str = trade_dt.strftime("%Y%m%d")

                # T+1일 전체 OHLCV 프리페치 (개별 조회 대신 배치)
                self.krx.prefetch_daily_trade(trade_date_str, market)

                # 전체 목표 종목 + 기존 보유 종목 시가 벌크 조회
                all_tickers = set(new_tickers) | set(holdings.keys())
                prices = self._get_open_prices_bulk(
                    list(all_tickers), trade_date_str
                )

                # 총 자산 평가 (시가 없으면 매수 평균단가로 대체)
                total_value = cash
                for ticker, shares in holdings.items():
                    price = prices.get(ticker) or cost_basis.get(ticker)
                    if price:
                        total_value += price * shares
                    else:
                        logger.warning(
                            f"[{trade_date_str}] {ticker} 시가·매수가 모두 없음"
                            f" — 보유 {shares}주 자산 평가에서 제외"
                        )

                # ── 종목별 트레일링 스톱 ──
                cash, total_value = self._execute_trailing_stops(
                    holdings, cost_basis, prices, cash, total_value, date_str,
                )

                # ── MDD 서킷브레이커 ──
                circuit_breaker_active, peak_value, cash = (
                    self._apply_circuit_breaker(
                        holdings, cost_basis, prices, cash,
                        total_value, peak_value, circuit_breaker_active,
                        date_str, turnover_log,
                    )
                )

                if circuit_breaker_active:
                    # 체결일 기록
                    if turnover_log:
                        turnover_log[-1]["trade_date"] = trade_date_str
                    # 현금 100% 상태로 일별 가치만 기록 (포지션 없음)
                    period_end = (
                        rebal_dates[i + 1]
                        if i + 1 < len(rebal_dates)
                        else pd.Timestamp(end_date)
                    )
                    if trade_dt > period_end:
                        period_end = trade_dt + pd.Timedelta(days=20)

                    dates = get_krx_sessions(
                        trade_dt.strftime("%Y%m%d"), period_end.strftime("%Y%m%d")
                    )
                    for dt in dates:
                        history.append({
                            "date": dt,
                            "portfolio_value": cash,
                            "cash": cash,
                            "n_holdings": 0,
                        })
                    continue  # 다음 리밸런싱 날짜로

                # ── 변동성 타겟팅 + 시장 레짐 → 투자 금액 결정 ──
                rebal_value = self._calc_rebalance_value(
                    history, total_value, date_str,
                )

                # ── 매매 실행 (매도 → 매수) ──
                cash = self._execute_trades(
                    holdings, cost_basis, new_tickers, prices,
                    cash, total_value, rebal_value, date_str, turnover_log,
                )

                # 체결일 기록
                if turnover_log:
                    turnover_log[-1]["trade_date"] = trade_date_str

                # 배당금 추정 제거 (v2.0):
                # 한국 시장은 연 1회 배당 집중 → 월별 균등 배분은 부정확.
                # 백테스트 수익률에 배당 미포함 (보수적 추정).
                # 실전에서는 키움 API 잔고 조회 시 배당금 자동 반영.

                # ── 일별 포트폴리오 가치 기록 ──
                self._record_daily_values(
                    holdings, prices, cash, history,
                    trade_dt, rebal_dates, i, end_date,
                )

            except Exception as e:
                logger.error(f"리밸런싱 실패 ({date_str}): {e}", exc_info=True)
                continue

        if not history:
            raise ValueError("백테스트 결과 없음. 날짜 범위와 데이터를 확인하세요.")

        result = pd.DataFrame(history).set_index("date")
        # end_date 이후 데이터 제거 (마지막 리밸런싱 T+1 체결이 end_date를 넘는 경우)
        end_ts = pd.Timestamp(end_date)
        result = result[result.index <= end_ts]
        if result.empty:
            raise ValueError("백테스트 결과 없음. 날짜 범위와 데이터를 확인하세요.")
        result["returns"] = result["portfolio_value"].pct_change()

        # 턴오버 로그를 DataFrame 속성으로 첨부
        result.attrs["turnover_log"] = turnover_log
        if turnover_log:
            avg_turnover = sum(t["turnover_rate"] for t in turnover_log) / len(turnover_log)
            logger.info(f"평균 턴오버: {avg_turnover:.1%} ({len(turnover_log)}회 리밸런싱)")

        total_ret = result["portfolio_value"].iloc[-1] / self.initial_cash - 1
        elapsed = _time.monotonic() - run_start
        logger.info(
            f"백테스트 완료 | 총 수익률: {total_ret * 100:.2f}% | "
            f"소요 시간: {elapsed:.0f}초 ({elapsed / 60:.1f}분)"
        )

        # DART API 통계 출력
        if hasattr(self.krx, "dart_client") and self.krx.dart_client:
            self.krx.dart_client.log_stats()

        return result

    # ─────────────────────────────────────────────
    # run() 서브 메서드: 책임별 분리
    # ─────────────────────────────────────────────

    def _prefetch_fundamentals(
        self, rebal_dates: list[pd.Timestamp], market: str,
    ) -> None:
        """백테스트 시작 전 펀더멘털 데이터 일괄 프리페치

        각 리밸런싱 날짜에 필요한 DART 데이터를 미리 수집하여 DB에 저장합니다.
        두 번째 이후 백테스트는 DB 캐시 히트로 DART API 호출 없이 수 분 내에 완료됩니다.

        Args:
            rebal_dates: 리밸런싱 날짜 목록
            market: 대상 시장
        """
        import time as _time
        from data.collector import _parse_date

        markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]

        # 이미 캐시된 날짜 확인 → 미스된 날짜만 프리페치
        missing_dates: list[str] = []
        for rdt in rebal_dates:
            date_str = rdt.strftime("%Y%m%d")
            dt = _parse_date(date_str)
            all_cached = True
            for m in markets:
                cached = self.krx.storage.load_fundamentals(dt, market=m)
                if cached.empty:
                    all_cached = False
                    break
            if not all_cached:
                missing_dates.append(date_str)

        if not missing_dates:
            logger.info(
                f"펀더멘털 프리페치: {len(rebal_dates)}개 날짜 모두 캐시 히트 — 스킵"
            )
            return

        logger.info(
            f"펀더멘털 프리페치: {len(missing_dates)}/{len(rebal_dates)}개 날짜 "
            f"DART 수집 필요"
        )
        prefetch_start = _time.monotonic()

        for i, date_str in enumerate(missing_dates):
            for m in markets:
                # get_fundamentals_all 내부에서 DART 조회 + DB 저장
                self.krx.get_fundamentals_all(date_str, m)
            if (i + 1) % 10 == 0:
                elapsed = _time.monotonic() - prefetch_start
                logger.info(
                    f"  프리페치 진행: {i + 1}/{len(missing_dates)} "
                    f"({elapsed:.0f}초)"
                )

        elapsed = _time.monotonic() - prefetch_start
        logger.info(f"펀더멘털 프리페치 완료: {elapsed:.0f}초 ({elapsed / 60:.1f}분)")

    def _generate_rebalance_dates(
        self, start_date: str, end_date: str, market: str,
    ) -> list[pd.Timestamp]:
        """리밸런싱 날짜 목록 생성 및 T+1 매매일 OHLCV 사전 프리페치

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
            market: 대상 시장

        Returns:
            KRX 월말 영업일 리스트
        """
        rebal_dates = get_krx_month_end_sessions(start_date, end_date)
        logger.info(f"리밸런싱 횟수: {len(rebal_dates)}회")

        trade_dates_for_prefetch: list[str] = []
        for rdt in rebal_dates:
            tdt = next_krx_business_day(rdt)
            trade_dates_for_prefetch.append(tdt.strftime("%Y%m%d"))

        logger.info(
            f"사전 프리페치: {len(trade_dates_for_prefetch)}개 매매일 OHLCV 일괄 수집"
        )
        for td_str in trade_dates_for_prefetch:
            self.krx.prefetch_daily_trade(td_str, market)

        return rebal_dates

    def _execute_trailing_stops(
        self,
        holdings: dict[str, int],
        cost_basis: dict[str, float],
        prices: dict[str, float],
        cash: float,
        total_value: float,
        date_str: str,
    ) -> tuple[float, float]:
        """종목별 트레일링 스톱: 매수가 대비 -N% 하락 종목 강제 매도

        holdings, cost_basis는 in-place로 변경됩니다.

        Args:
            holdings: 보유 종목 {ticker: shares}
            cost_basis: 평균 매수단가 {ticker: price}
            prices: 당일 시가 {ticker: price}
            cash: 현재 현금
            total_value: 현재 총 자산 평가액
            date_str: 기준 날짜 (YYYYMMDD)

        Returns:
            (갱신된 cash, 갱신된 total_value)
        """
        trailing_stop_pct = settings.trading.trailing_stop_pct
        if trailing_stop_pct <= 0 or not holdings:
            return cash, total_value

        stop_sells: list[str] = []
        for ticker, shares in list(holdings.items()):
            if shares <= 0:
                continue
            price = prices.get(ticker)
            avg_cost = cost_basis.get(ticker)
            if price is None or avg_cost is None or avg_cost <= 0:
                continue
            loss_pct = (price - avg_cost) / avg_cost
            if loss_pct < -trailing_stop_pct:
                proceed = self.rebalancer.calc_sell_proceed(price, shares)
                cash += proceed
                stop_sells.append(
                    f"{ticker}({self._get_ticker_name(ticker)}) "
                    f"{loss_pct:.1%}"
                )
                holdings.pop(ticker)
                cost_basis.pop(ticker, None)

        if stop_sells:
            # 전량 매도 후 총 자산 재평가
            total_value = cash
            for t, s in holdings.items():
                if t in prices:
                    total_value += prices[t] * s
            logger.warning(
                f"[{date_str}] 트레일링 스톱 발동: "
                f"{len(stop_sells)}종목 강제 매도 — {', '.join(stop_sells)}"
            )

        return cash, total_value

    def _apply_circuit_breaker(
        self,
        holdings: dict[str, int],
        cost_basis: dict[str, float],
        prices: dict[str, float],
        cash: float,
        total_value: float,
        peak_value: float,
        circuit_breaker_active: bool,
        date_str: str,
        turnover_log: list[dict],
    ) -> tuple[bool, float, float]:
        """MDD 서킷브레이커: 고점 대비 -N% → 전량 매도 → 현금 대피

        holdings, cost_basis, turnover_log는 in-place로 변경됩니다.

        Args:
            holdings: 보유 종목 {ticker: shares}
            cost_basis: 평균 매수단가 {ticker: price}
            prices: 당일 시가 {ticker: price}
            cash: 현재 현금
            total_value: 현재 총 자산 평가액
            peak_value: 역대 최고 자산 평가액
            circuit_breaker_active: 서킷브레이커 활성 여부
            date_str: 기준 날짜 (YYYYMMDD)
            turnover_log: 턴오버 로그 리스트

        Returns:
            (circuit_breaker_active, peak_value, cash)
        """
        peak_value = max(peak_value, total_value)
        max_dd_threshold = settings.trading.max_drawdown_pct

        # max_drawdown_pct가 None이면 서킷브레이커 비활성화
        if max_dd_threshold is None:
            return circuit_breaker_active, peak_value, cash

        current_dd = (
            (total_value - peak_value) / peak_value if peak_value > 0 else 0
        )

        if current_dd < -max_dd_threshold:
            if not circuit_breaker_active:
                logger.warning(
                    f"[{date_str}] MDD 서킷브레이커 발동: "
                    f"현재 DD={current_dd:.1%} < -{max_dd_threshold:.0%}"
                    f" → 전량 매도, 현금 대피"
                )
                # 보유 종목 전량 매도
                liquidation_details: list[dict] = []
                for ticker, shares in list(holdings.items()):
                    if shares <= 0:
                        continue
                    price = prices.get(ticker)
                    if price is None:
                        continue
                    proceed = self.rebalancer.calc_sell_proceed(price, shares)
                    cash += proceed
                    avg_cost = cost_basis.get(ticker)
                    return_pct = (
                        (price - avg_cost) / avg_cost
                        if avg_cost and avg_cost > 0
                        else None
                    )
                    liquidation_details.append({
                        "ticker": ticker,
                        "name": self._get_ticker_name(ticker),
                        "quantity": shares,
                        "price": price,
                        "amount": proceed,
                        "buy_price": avg_cost,
                        "return_pct": return_pct,
                    })
                holdings.clear()
                cost_basis.clear()
                circuit_breaker_active = True

                turnover_log.append({
                    "date": date_str,
                    "sells": len(liquidation_details),
                    "buys": 0,
                    "turnover_rate": 1.0,
                    "n_holdings_before": len(liquidation_details),
                    "n_holdings_after": 0,
                    "sell_details": liquidation_details,
                    "buy_details": [],
                    "note": f"서킷브레이커 전량 매도 (DD={current_dd:.1%})",
                })

        elif circuit_breaker_active:
            # 재진입 조건: DD가 발동 기준의 절반 이내로 회복
            reentry_threshold = -max_dd_threshold * 0.5
            if current_dd >= reentry_threshold:
                logger.info(
                    f"[{date_str}] MDD 서킷브레이커 해제: "
                    f"DD={current_dd:.1%} >= {reentry_threshold:.1%} → 재진입 허용"
                )
                circuit_breaker_active = False
                # 고점을 현재 자산(현금)으로 리셋하여 재발동 방지
                peak_value = total_value
            else:
                logger.info(
                    f"[{date_str}] 서킷브레이커 유지: "
                    f"DD={current_dd:.1%} (해제 기준: {reentry_threshold:.1%})"
                )

        return circuit_breaker_active, peak_value, cash

    def _calc_rebalance_value(
        self,
        history: list[dict],
        total_value: float,
        date_str: str,
    ) -> float:
        """변동성 타겟팅 + 시장 레짐 필터를 적용한 리밸런싱 투자 금액 계산

        Args:
            history: 지금까지의 일별 기록 리스트
            total_value: 현재 총 자산 평가액
            date_str: 기준 날짜 (YYYYMMDD)

        Returns:
            리밸런싱에 사용할 투자 금액
        """
        vol_scale = self._calc_vol_target_scale(history)
        invest_ratio = self.regime_filter.get_invest_ratio(date_str)
        raw_ratio = invest_ratio * vol_scale
        # 곱셈 효과로 과도한 비중 축소 방지: 최소 20% 투자 보장
        combined_ratio = max(raw_ratio, 0.20)
        rebal_value = total_value * combined_ratio

        if combined_ratio < 1.0:
            logger.info(
                f"[{date_str}] 변동성 타겟팅: 투자 비중 "
                f"(레짐 {invest_ratio:.0%} × vol {vol_scale:.0%} = "
                f"raw {raw_ratio:.0%} → 최종 {combined_ratio:.0%})"
            )

        # 고정 금액 모드: 리밸런싱 기준 금액 제한
        max_inv = settings.portfolio.max_investment_amount
        if max_inv > 0 and rebal_value > max_inv:
            rebal_value = max_inv

        return rebal_value

    def _get_avg_daily_volumes(
        self,
        tickers: list[str],
        date_str: str,
        lookback_days: int = 20,
    ) -> dict[str, float]:
        """종목별 20일 평균 거래량 조회 (시장 충격 계산용)

        Args:
            tickers: 종목 코드 리스트
            date_str: 기준 날짜 (YYYYMMDD)
            lookback_days: 평균 산출 기간 (기본 20일)

        Returns:
            {ticker: avg_daily_volume}
        """
        from datetime import datetime, timedelta

        end_dt = datetime.strptime(date_str, "%Y%m%d")
        start_dt = end_dt - timedelta(days=int(lookback_days * 1.5))
        sd = start_dt.date()
        ed = end_dt.date()

        bulk_df = self.krx.storage.load_daily_prices_bulk(tickers, sd, ed)
        if bulk_df.empty:
            return {}

        # 종목별 평균 거래량 (벡터화)
        avg_vols = bulk_df.groupby("ticker")["volume"].mean()
        return {str(k): float(v) for k, v in avg_vols.items() if pd.notna(v) and v > 0}

    def _execute_trades(
        self,
        holdings: dict[str, int],
        cost_basis: dict[str, float],
        new_tickers: list[str],
        prices: dict[str, float],
        cash: float,
        total_value: float,
        rebal_value: float,
        date_str: str,
        turnover_log: list[dict],
    ) -> float:
        """매도 → 매수 순서로 리밸런싱 매매 실행

        holdings, cost_basis, turnover_log는 in-place로 변경됩니다.

        Args:
            holdings: 보유 종목 {ticker: shares}
            cost_basis: 평균 매수단가 {ticker: price}
            new_tickers: 신규 목표 포트폴리오 종목 리스트
            prices: 당일 시가 {ticker: price}
            cash: 현재 현금
            total_value: 매매 전 총 자산 평가액
            rebal_value: 리밸런싱 투자 금액
            date_str: 기준 날짜 (YYYYMMDD)
            turnover_log: 턴오버 로그 리스트

        Returns:
            매매 후 현금 잔액
        """
        # 비중 리밸런싱 주문 계산 (기존 보유종목 포함 재조정)
        old_tickers = set(holdings.keys())
        orders = self.rebalancer.compute_weight_rebalance(
            holdings, new_tickers, prices, rebal_value
        )

        # 턴오버 기록
        new_set = set(new_tickers)
        sells_count = len(old_tickers - new_set)
        buys_count = len(new_set - old_tickers)
        n_prev = len(old_tickers) if old_tickers else 1
        turnover_rate = (sells_count + buys_count) / (
            2 * max(n_prev, len(new_set), 1)
        )
        turnover_log.append({
            "date": date_str,
            "sells": sells_count,
            "buys": buys_count,
            "turnover_rate": turnover_rate,
            "n_holdings_before": len(old_tickers),
            "n_holdings_after": len(new_set),
            "sell_details": [],
            "buy_details": [],
        })

        # 시장 충격 계산용 평균 거래량 조회
        order_tickers = [t for t in orders if orders[t] != 0 and t in prices]
        avg_volumes = self._get_avg_daily_volumes(order_tickers, date_str)

        # 자금 흐름 추적: 매매 전 현금
        cash_before_trade = cash

        # 매도 먼저 실행 (예수금 확보)
        sell_details: list[dict] = []
        for ticker, delta in sorted(orders.items()):
            if delta >= 0:
                continue
            sell_shares = min(-delta, holdings.get(ticker, 0))
            if sell_shares <= 0:
                continue
            price = prices.get(ticker)
            if price is None:
                continue
            # 시장 충격 반영: 주문 수량 대비 거래량 비율로 가격 조정
            # 시장 충격은 슬리피지를 대체하므로, 충격 적용 시 슬리피지 제외
            impact = self.rebalancer.estimate_market_impact(
                sell_shares, avg_volumes.get(ticker, 0)
            )
            adjusted_price = price * (1 - impact)  # 매도 시 불리한 가격
            cost_rate = self.rebalancer.cfg.commission_rate + self.rebalancer.cfg.tax_rate
            proceed = adjusted_price * sell_shares * (1 - cost_rate)
            cash += proceed
            holdings[ticker] = holdings.get(ticker, 0) - sell_shares
            # 종목별 수익률 계산 (매수 평균단가 대비)
            avg_cost = cost_basis.get(ticker)
            return_pct = (
                (price - avg_cost) / avg_cost
                if avg_cost and avg_cost > 0
                else None
            )
            sell_details.append({
                "ticker": ticker,
                "name": self._get_ticker_name(ticker),
                "quantity": sell_shares,
                "price": price,
                "amount": proceed,
                "buy_price": avg_cost,
                "return_pct": return_pct,
            })
            if holdings[ticker] <= 0:
                holdings.pop(ticker, None)
                cost_basis.pop(ticker, None)

        # 자금 흐름 추적: 매도 후 현금
        cash_after_sell = cash

        # 매수 실행
        buy_details: list[dict] = []
        skipped_buys: list[str] = []
        for ticker, delta in sorted(orders.items()):
            if delta <= 0:
                continue
            price = prices.get(ticker)
            if price is None:
                continue
            # 시장 충격 반영: 매수 시 불리한 가격
            # 시장 충격은 슬리피지를 대체하므로, 충격 적용 시 슬리피지 제외
            impact = self.rebalancer.estimate_market_impact(
                delta, avg_volumes.get(ticker, 0)
            )
            adjusted_price = price * (1 + impact)
            buy_cost_rate = self.rebalancer.cfg.commission_rate
            cost = adjusted_price * delta * (1 + buy_cost_rate)
            if cash >= cost:
                cash -= cost
                # 평균 매수단가 업데이트 (가중 평균)
                prev_shares = holdings.get(ticker, 0)
                prev_cost = cost_basis.get(ticker, 0.0)
                new_total_shares = prev_shares + delta
                if new_total_shares > 0:
                    cost_basis[ticker] = (
                        (prev_cost * prev_shares + price * delta)
                        / new_total_shares
                    )
                holdings[ticker] = new_total_shares
                buy_details.append({
                    "ticker": ticker,
                    "name": self._get_ticker_name(ticker),
                    "quantity": delta,
                    "price": price,
                    "amount": cost,
                })
            else:
                skipped_buys.append(ticker)

        # 자금 흐름 추적: 매수 후 현금 및 평가액
        cash_after_buy = cash
        stock_value_after = sum(
            prices.get(t, 0) * s for t, s in holdings.items() if s > 0
        )
        sell_total_amount = sum(s["amount"] for s in sell_details)
        buy_total_amount = sum(b["amount"] for b in buy_details)

        # 매매 상세 내역을 턴오버 로그에 반영
        if turnover_log:
            turnover_log[-1]["sell_details"] = sell_details
            turnover_log[-1]["buy_details"] = buy_details
            turnover_log[-1]["fund_flow"] = {
                "total_value_before": total_value,
                "cash_before": cash_before_trade,
                "sell_amount": sell_total_amount,
                "cash_after_sell": cash_after_sell,
                "buy_amount": buy_total_amount,
                "cash_after_buy": cash_after_buy,
                "stock_value_after": stock_value_after,
                "total_value_after": cash_after_buy + stock_value_after,
                "invest_ratio": (
                    stock_value_after / (cash_after_buy + stock_value_after)
                    if (cash_after_buy + stock_value_after) > 0
                    else 0
                ),
            }

        if skipped_buys:
            logger.warning(
                f"[{date_str}] 현금 부족으로 매수 스킵: "
                f"{len(skipped_buys)}개 종목 {skipped_buys}"
            )

        return cash

    def _record_daily_values(
        self,
        holdings: dict[str, int],
        open_prices: dict[str, float],
        cash: float,
        history: list[dict],
        trade_dt: pd.Timestamp,
        rebal_dates: list[pd.Timestamp],
        rebal_idx: int,
        end_date: str,
    ) -> None:
        """보유 종목의 일별 종가로 포트폴리오 가치를 기록

        history는 in-place로 변경됩니다.

        Args:
            holdings: 보유 종목 {ticker: shares}
            open_prices: 매매 체결 시가 {ticker: price} (초기 가격용)
            cash: 현재 현금
            history: 일별 기록 리스트
            trade_dt: 매매 체결일
            rebal_dates: 전체 리밸런싱 날짜 리스트
            rebal_idx: 현재 리밸런싱 인덱스
            end_date: 백테스트 종료일 (YYYY-MM-DD)
        """
        period_end = (
            rebal_dates[rebal_idx + 1]
            if rebal_idx + 1 < len(rebal_dates)
            else pd.Timestamp(end_date)
        )
        if trade_dt > period_end:
            period_end = trade_dt + pd.Timedelta(days=20)

        sd = trade_dt.date() if hasattr(trade_dt, "date") else trade_dt
        ed = period_end.date() if hasattr(period_end, "date") else period_end

        # 벌크 DB 조회 → {(ticker, date_str): close_price}
        close_price_cache: dict[tuple[str, str], float] = {}
        holding_tickers = list(holdings.keys())
        if holding_tickers:
            bulk_df = self.krx.storage.load_daily_prices_bulk(
                holding_tickers, sd, ed
            )
            if not bulk_df.empty:
                valid = bulk_df[
                    bulk_df["close"].notna() & (bulk_df["close"] > 0)
                ]
                for t, d, c in zip(
                    valid["ticker"], valid["date"], valid["close"]
                ):
                    dt_key = (
                        d.strftime("%Y%m%d")
                        if hasattr(d, "strftime")
                        else str(d)
                    )
                    close_price_cache[(t, dt_key)] = float(c)

        # 마지막 알려진 가격을 보관하여 데이터 갭 시 대체 사용
        last_known_price: dict[str, float] = {}
        # 매매 체결가를 초기값으로 설정
        for ticker in holdings:
            if ticker in open_prices:
                last_known_price[ticker] = open_prices[ticker]

        dates = get_krx_sessions(
            trade_dt.strftime("%Y%m%d"), period_end.strftime("%Y%m%d")
        )
        for dt in dates:
            dt_str_val = dt.strftime("%Y%m%d")
            total = cash
            missing_count = 0
            for ticker, shares in holdings.items():
                if shares <= 0:
                    continue
                price = close_price_cache.get((ticker, dt_str_val))
                if price is not None:
                    last_known_price[ticker] = price
                else:
                    price = last_known_price.get(ticker)
                    if price is not None:
                        missing_count += 1
                if price is not None:
                    total += price * shares

            if missing_count > 0:
                logger.debug(
                    f"[{dt_str_val}] {missing_count}개 종목 가격 데이터 없음"
                    f" — 마지막 알려진 가격으로 대체"
                )

            history.append({
                "date": dt,
                "portfolio_value": total,
                "cash": cash,
                "n_holdings": len(holdings),
            })

    def run_walk_forward(
        self,
        full_start: str,
        full_end: str,
        train_years: int = 4,
        test_years: int = 2,
        step_years: int = 2,
        market: str | None = None,
    ) -> list[dict]:
        """Walk-Forward 백테스트 (슬라이딩 윈도우)

        4~5년 학습 → 2년 검증 윈도우를 step_years씩 슬라이딩.
        각 윈도우의 검증 성과를 기록하여 과적합 여부를 판단합니다.

        Args:
            full_start: 전체 시작일 (YYYY-MM-DD)
            full_end: 전체 종료일 (YYYY-MM-DD)
            train_years: 학습 기간 (년)
            test_years: 검증 기간 (년)
            step_years: 윈도우 이동 간격 (년)
            market: 대상 시장 (기본: settings.universe.market)

        Returns:
            각 윈도우별 결과 리스트
        """
        from datetime import datetime
        from dateutil.relativedelta import relativedelta
        from backtest.metrics import PerformanceAnalyzer

        market = market or settings.universe.market

        logger.info(
            f"Walk-Forward: {full_start} ~ {full_end}, "
            f"학습={train_years}년, 검증={test_years}년, 스텝={step_years}년"
        )

        start_dt = datetime.strptime(full_start, "%Y-%m-%d")
        end_dt = datetime.strptime(full_end, "%Y-%m-%d")

        analyzer = PerformanceAnalyzer()
        results: list[dict] = []
        window_idx = 0

        cursor = start_dt
        while True:
            train_start = cursor
            train_end = cursor + relativedelta(years=train_years) - relativedelta(days=1)
            test_start = train_end + relativedelta(days=1)
            test_end = test_start + relativedelta(years=test_years) - relativedelta(days=1)

            if test_end > end_dt:
                break

            window_idx += 1
            ts = train_start.strftime("%Y-%m-%d")
            te = train_end.strftime("%Y-%m-%d")
            vs = test_start.strftime("%Y-%m-%d")
            ve = test_end.strftime("%Y-%m-%d")

            logger.info(
                f"[윈도우 {window_idx}] Train: {ts}~{te}, Test: {vs}~{ve}"
            )

            result: dict = {
                "window": window_idx,
                "train_start": ts,
                "train_end": te,
                "test_start": vs,
                "test_end": ve,
            }

            # Train 백테스트
            try:
                bt_train = MultiFactorBacktest(self.initial_cash)
                train_df = bt_train.run(ts, te, market)
                train_vals = train_df["portfolio_value"]
                train_rets = train_df["returns"].dropna()
                result["train_cagr"] = analyzer.calculate_cagr(train_vals)
                result["train_sharpe"] = analyzer.calculate_sharpe(train_rets)
                result["train_mdd"] = analyzer.calculate_mdd(train_vals)
            except Exception as e:
                logger.warning(f"윈도우 {window_idx} train 실패: {e}")
                result["train_cagr"] = None
                result["train_sharpe"] = None
                result["train_mdd"] = None

            # Test 백테스트
            try:
                bt_test = MultiFactorBacktest(self.initial_cash)
                test_df = bt_test.run(vs, ve, market)
                test_vals = test_df["portfolio_value"]
                test_rets = test_df["returns"].dropna()
                result["test_cagr"] = analyzer.calculate_cagr(test_vals)
                result["test_sharpe"] = analyzer.calculate_sharpe(test_rets)
                result["test_mdd"] = analyzer.calculate_mdd(test_vals)
            except Exception as e:
                logger.warning(f"윈도우 {window_idx} test 실패: {e}")
                result["test_cagr"] = None
                result["test_sharpe"] = None
                result["test_mdd"] = None

            results.append(result)
            cursor += relativedelta(years=step_years)

        # 요약 로그
        valid = [r for r in results if r.get("test_cagr") is not None]
        if valid:
            avg_train = sum(r["train_cagr"] for r in valid) / len(valid)
            avg_test = sum(r["test_cagr"] for r in valid) / len(valid)
            positive_windows = sum(1 for r in valid if r["test_cagr"] > 0)
            logger.info(
                f"Walk-Forward 요약: {len(valid)}개 윈도우, "
                f"평균 Train CAGR={avg_train:.2%}, "
                f"평균 Test CAGR={avg_test:.2%}, "
                f"과적합 갭={avg_train - avg_test:.2%}, "
                f"양의 수익 비율={positive_windows}/{len(valid)}"
            )

        return results

    # ─────────────────────────────────────────────
    # 내부 메서드
    # ─────────────────────────────────────────────

    def _estimate_dividend_income(
        self,
        holdings: dict[str, int],
        prices: dict[str, float],
        date_str: str,
        market: str,
        cash: float,
    ) -> float:
        """[DEPRECATED v2.0] 월별 배당금 추정 - 한국 시장에 부적합

        v2.0에서 비활성화됨. 한국 시장은 12월 결산 기업이 대부분이라
        배당이 연 1회(3~4월) 집중됨. 월별 균등 배분(연간/12)은 현실과 괴리.
        향후 DART 배당락일 데이터를 활용한 정확한 배당 반영 시 재활용 가능.

        Args:
            holdings: 보유 종목 {ticker: shares}
            prices: 당일 시가 {ticker: price}
            date_str: 기준 날짜 (YYYYMMDD)
            market: 시장
            cash: 현재 현금

        Returns:
            배당금 반영 후 현금
        """
        if not holdings:
            return cash

        try:
            fundamentals = self.krx.get_fundamentals_all(date_str, market)
            if fundamentals is None or fundamentals.empty:
                return cash

            total_div_income = 0.0
            for ticker, shares in holdings.items():
                if shares <= 0:
                    continue
                if ticker not in fundamentals.index:
                    continue
                div_yield = fundamentals.loc[ticker].get("DIV", 0)
                if not div_yield or div_yield <= 0:
                    continue
                price = prices.get(ticker)
                if not price or price <= 0:
                    continue
                # 연간 배당수익률 → 월간 배당금
                monthly_div = price * shares * (div_yield / 100) / 12
                total_div_income += monthly_div

            if total_div_income > 0:
                cash += total_div_income
                logger.debug(
                    f"[{date_str}] 배당금 추정: {total_div_income:,.0f}원 "
                    f"({len(holdings)}종목 보유)"
                )

        except Exception as e:
            logger.debug(f"[{date_str}] 배당금 추정 실패: {e}")

        return cash

    def _calc_portfolio(self, date_str: str, market: str) -> list[str]:
        """T일 기준 팩터 계산 후 상위 N개 종목 반환

        screener.screen()에 위임하여 실전과 동일한 파이프라인 사용.
        (거래정지/금융주 필터 포함)

        Args:
            date_str: 기준 날짜 (YYYYMMDD)
            market: 시장

        Returns:
            선정된 종목 코드 리스트 (빈 리스트 = 실패)
        """
        portfolio_df = self.screener.screen(date_str, market=market)
        if portfolio_df.empty:
            return []
        return portfolio_df.index.tolist()

    def _get_ticker_name(self, ticker: str) -> str:
        """종목코드로 종목명 조회 (collector 캐시 활용)"""
        return self.krx.get_ticker_name(ticker)

    def _get_open_prices_bulk(
        self, tickers: list[str], date_str: str
    ) -> dict[str, float]:
        """여러 종목의 시가를 벌크 DB 조회로 한 번에 가져오기

        Args:
            tickers: 종목코드 리스트
            date_str: 날짜 (YYYYMMDD)

        Returns:
            {ticker: open_price} 딕셔너리
        """
        from data.collector import _parse_date

        dt = _parse_date(date_str)
        prices: dict[str, float] = {}

        # 벌크 DB 조회 (1회 쿼리)
        bulk_df = self.krx.storage.load_daily_prices_bulk(tickers, dt, dt)
        if not bulk_df.empty:
            valid_open = bulk_df[bulk_df["open"].notna() & (bulk_df["open"] > 0)]
            if not valid_open.empty:
                price_map = valid_open.set_index("ticker")["open"].to_dict()
                prices.update({str(k): float(v) for k, v in price_map.items()})

        # DB 미스 종목만 개별 폴백
        missing = [t for t in tickers if t not in prices]
        for ticker in missing:
            p = self._get_open_price(ticker, date_str)
            if p is not None:
                prices[ticker] = p

        return prices

    def _calc_vol_target_scale(self, history: list[dict]) -> float:
        """변동성 타겟팅 — 공통 함수 위임

        Args:
            history: 지금까지의 일별 기록 리스트

        Returns:
            투자 비중 배율 (0.2 ~ 1.0)
        """
        from strategy.market_regime import calc_vol_target_scale

        values = [h["portfolio_value"] for h in history]
        return calc_vol_target_scale(
            values,
            settings.trading.vol_target,
            settings.trading.vol_lookback_days,
        )

    def _get_open_price(self, ticker: str, date_str: str) -> Optional[float]:
        """특정 날짜 시가 조회 (개별 폴백용)

        Args:
            ticker: 종목코드
            date_str: 날짜 (YYYYMMDD)

        Returns:
            시가 또는 None (데이터 없음)
        """
        try:
            df = self.krx.get_ohlcv(ticker, date_str, date_str)
            if df is not None and not df.empty and "open" in df.columns:
                val = df["open"].iloc[0]
                return float(val) if val > 0 else None
        except Exception as e:
            logger.warning(f"시가 조회 실패 ({ticker}, {date_str}): {e}")
        return None

