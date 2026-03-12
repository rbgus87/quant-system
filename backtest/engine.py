# backtest/engine.py
import numpy as np
import pandas as pd
from typing import Optional
import logging

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
        market: str = "KOSPI",
    ) -> pd.DataFrame:
        """백테스트 실행

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)
            market: 대상 시장

        Returns:
            DataFrame(index=date, columns=[portfolio_value, cash, n_holdings, returns])
        """
        logger.info(f"백테스트 시작: {start_date} ~ {end_date}")

        rebal_dates = get_krx_month_end_sessions(start_date, end_date)
        logger.info(f"리밸런싱 횟수: {len(rebal_dates)}회")

        # ── 사전 데이터 수집: 모든 T+1 매매일 OHLCV 일괄 프리페치 ──
        trade_dates_for_prefetch = []
        for rdt in rebal_dates:
            tdt = next_krx_business_day(rdt)
            trade_dates_for_prefetch.append(tdt.strftime("%Y%m%d"))

        logger.info(f"사전 프리페치: {len(trade_dates_for_prefetch)}개 매매일 OHLCV 일괄 수집")
        for td_str in trade_dates_for_prefetch:
            self.krx.prefetch_daily_trade(td_str, market)

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

                # 총 자산 평가 (시가 없으면 이전 history에서 마지막 종가 사용)
                total_value = cash
                for ticker, shares in holdings.items():
                    if ticker in prices:
                        total_value += prices[ticker] * shares
                    else:
                        logger.warning(
                            f"[{trade_date_str}] {ticker} 시가 없음"
                            f" — 보유 {shares}주 자산 평가에서 제외"
                        )

                # ── 종목별 트레일링 스톱: 매수가 대비 -N% 하락 종목 강제 매도 ──
                trailing_stop_pct = settings.trading.trailing_stop_pct
                if trailing_stop_pct > 0 and holdings:
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

                # ── MDD 서킷브레이커: 고점 대비 -N% → 전량 매도 → 현금 대피 ──
                peak_value = max(peak_value, total_value)
                current_dd = (total_value - peak_value) / peak_value if peak_value > 0 else 0
                max_dd_threshold = settings.trading.max_drawdown_pct

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
                    # (예: -20% 발동 → -10% 이내 회복 시 해제)
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

                if circuit_breaker_active:
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

                # ── 변동성 타겟팅: 실현 변동성 > 목표 시 투자 비중 축소 ──
                vol_scale = self._calc_vol_target_scale(history)

                # 시장 레짐 필터: 하락장 시 투자 비중 축소
                invest_ratio = self.regime_filter.get_invest_ratio(date_str)
                rebal_value = total_value * invest_ratio * vol_scale

                if vol_scale < 1.0:
                    logger.info(
                        f"[{date_str}] 변동성 타겟팅: 투자 비중 ×{vol_scale:.0%} "
                        f"(레짐 {invest_ratio:.0%} → 최종 {invest_ratio * vol_scale:.0%})"
                    )

                # 고정 금액 모드: 리밸런싱 기준 금액 제한
                max_inv = settings.portfolio.max_investment_amount
                if max_inv > 0 and rebal_value > max_inv:
                    rebal_value = max_inv

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
                turnover_rate = (sells_count + buys_count) / (2 * max(n_prev, len(new_set), 1))
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
                    proceed = self.rebalancer.calc_sell_proceed(price, sell_shares)
                    cash += proceed
                    holdings[ticker] = holdings.get(ticker, 0) - sell_shares
                    # 종목별 수익률 계산 (매수 평균단가 대비)
                    avg_cost = cost_basis.get(ticker)
                    return_pct = (
                        (price - avg_cost) / avg_cost if avg_cost and avg_cost > 0 else None
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
                    cost = self.rebalancer.calc_buy_cost(price, delta)
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
                        "invest_ratio": stock_value_after / (cash_after_buy + stock_value_after) if (cash_after_buy + stock_value_after) > 0 else 0,
                    }

                if skipped_buys:
                    logger.warning(
                        f"[{date_str}] 현금 부족으로 매수 스킵: "
                        f"{len(skipped_buys)}개 종목 {skipped_buys}"
                    )

                # 보유 종목 OHLCV 기간 일괄 프리페치 (벌크 DB 조회)
                period_end = (
                    rebal_dates[i + 1]
                    if i + 1 < len(rebal_dates)
                    else pd.Timestamp(end_date)
                )
                if trade_dt > period_end:
                    period_end = trade_dt + pd.Timedelta(days=20)

                from datetime import date as date_type

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
                        valid = bulk_df[bulk_df["close"].notna() & (bulk_df["close"] > 0)]
                        for _, row in valid.iterrows():
                            dt_key = row["date"].strftime("%Y%m%d") if hasattr(row["date"], "strftime") else str(row["date"])
                            close_price_cache[(row["ticker"], dt_key)] = float(row["close"])

                # 일별 포트폴리오 가치 기록 (KRX 거래일만)
                # 마지막 알려진 가격을 보관하여 데이터 갭 시 대체 사용
                last_known_price: dict[str, float] = {}
                # 매매 체결가를 초기값으로 설정
                for ticker in holdings:
                    if ticker in prices:
                        last_known_price[ticker] = prices[ticker]

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

                    history.append(
                        {
                            "date": dt,
                            "portfolio_value": total,
                            "cash": cash,
                            "n_holdings": len(holdings),
                        }
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
        logger.info(f"백테스트 완료 | 총 수익률: {total_ret * 100:.2f}%")
        return result

    def walk_forward(
        self,
        start_date: str,
        end_date: str,
        n_splits: int = 3,
        train_ratio: float = 0.7,
        market: str = "KOSPI",
    ) -> list[dict]:
        """워크-포워드 검증

        전체 기간을 n_splits 구간으로 나누고, 각 구간 내
        앞 train_ratio를 in-sample, 나머지를 out-of-sample로 분할하여
        백테스트를 실행합니다.

        Args:
            start_date: 전체 시작일 (YYYY-MM-DD)
            end_date: 전체 종료일 (YYYY-MM-DD)
            n_splits: 분할 구간 수
            train_ratio: in-sample 비율 (0~1)
            market: 대상 시장

        Returns:
            각 구간별 결과 리스트 [{split, train_start, train_end,
            test_start, test_end, train_cagr, test_cagr, ...}]
        """
        from backtest.metrics import PerformanceAnalyzer

        logger.info(
            f"워크-포워드 검증: {start_date} ~ {end_date}, "
            f"{n_splits}분할, train={train_ratio:.0%}"
        )

        all_dates = get_krx_sessions(
            start_date.replace("-", ""), end_date.replace("-", "")
        )
        if len(all_dates) < n_splits * 20:
            raise ValueError(
                f"기간이 너무 짧습니다: {len(all_dates)}거래일 "
                f"(최소 {n_splits * 20}거래일 필요)"
            )

        split_size = len(all_dates) // n_splits
        analyzer = PerformanceAnalyzer()
        results: list[dict] = []

        for i in range(n_splits):
            seg_start = i * split_size
            seg_end = (i + 1) * split_size if i < n_splits - 1 else len(all_dates)
            segment = all_dates[seg_start:seg_end]

            train_len = int(len(segment) * train_ratio)
            if train_len < 20 or (len(segment) - train_len) < 10:
                logger.warning(f"구간 {i + 1} 데이터 부족, 스킵")
                continue

            train_start = segment[0].strftime("%Y-%m-%d")
            train_end = segment[train_len - 1].strftime("%Y-%m-%d")
            test_start = segment[train_len].strftime("%Y-%m-%d")
            test_end = segment[-1].strftime("%Y-%m-%d")

            logger.info(
                f"[구간 {i + 1}/{n_splits}] "
                f"Train: {train_start}~{train_end}, "
                f"Test: {test_start}~{test_end}"
            )

            split_result: dict = {
                "split": i + 1,
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
            }

            # In-sample 백테스트
            try:
                bt_train = MultiFactorBacktest(self.initial_cash)
                train_df = bt_train.run(train_start, train_end, market)
                train_vals = train_df["portfolio_value"]
                train_rets = train_df["returns"].dropna()
                split_result["train_cagr"] = analyzer.calculate_cagr(train_vals)
                split_result["train_sharpe"] = analyzer.calculate_sharpe(train_rets)
                split_result["train_mdd"] = analyzer.calculate_mdd(train_vals)
            except Exception as e:
                logger.warning(f"구간 {i + 1} train 실패: {e}")
                split_result["train_cagr"] = None
                split_result["train_sharpe"] = None
                split_result["train_mdd"] = None

            # Out-of-sample 백테스트
            try:
                bt_test = MultiFactorBacktest(self.initial_cash)
                test_df = bt_test.run(test_start, test_end, market)
                test_vals = test_df["portfolio_value"]
                test_rets = test_df["returns"].dropna()
                split_result["test_cagr"] = analyzer.calculate_cagr(test_vals)
                split_result["test_sharpe"] = analyzer.calculate_sharpe(test_rets)
                split_result["test_mdd"] = analyzer.calculate_mdd(test_vals)
            except Exception as e:
                logger.warning(f"구간 {i + 1} test 실패: {e}")
                split_result["test_cagr"] = None
                split_result["test_sharpe"] = None
                split_result["test_mdd"] = None

            results.append(split_result)

        # 요약 로그
        valid = [r for r in results if r.get("test_cagr") is not None]
        if valid:
            avg_train = sum(r["train_cagr"] for r in valid) / len(valid)
            avg_test = sum(r["test_cagr"] for r in valid) / len(valid)
            logger.info(
                f"워크-포워드 요약: 평균 Train CAGR={avg_train:.2%}, "
                f"평균 Test CAGR={avg_test:.2%}, "
                f"과적합 갭={avg_train - avg_test:.2%}"
            )

        return results

    # ─────────────────────────────────────────────
    # 내부 메서드
    # ─────────────────────────────────────────────

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
            for _, row in bulk_df.iterrows():
                ticker = str(row["ticker"])
                val = row.get("open")
                if val is not None and val > 0:
                    prices[ticker] = float(val)

        # DB 미스 종목만 개별 폴백
        missing = [t for t in tickers if t not in prices]
        for ticker in missing:
            p = self._get_open_price(ticker, date_str)
            if p is not None:
                prices[ticker] = p

        return prices

    def _calc_vol_target_scale(self, history: list[dict]) -> float:
        """변동성 타겟팅 — 실현 변동성 대비 목표 비율 계산

        최근 N거래일 포트폴리오 수익률의 연환산 변동성을 계산하고,
        목표 변동성 대비 비율로 투자 비중을 조절합니다.

        Args:
            history: 지금까지의 일별 기록 리스트

        Returns:
            투자 비중 배율 (0.0 ~ 1.0, 1.0 = 변동성 타겟 이하)
        """
        vol_target = settings.trading.vol_target
        lookback = settings.trading.vol_lookback_days

        if vol_target <= 0 or len(history) < max(lookback, 20):
            return 1.0

        recent = history[-lookback:]
        values = [h["portfolio_value"] for h in recent]
        returns = []
        for j in range(1, len(values)):
            if values[j - 1] > 0:
                returns.append(values[j] / values[j - 1] - 1)

        if len(returns) < 10:
            return 1.0

        realized_vol = float(np.std(returns)) * np.sqrt(252)
        if realized_vol <= 0:
            return 1.0

        scale = vol_target / realized_vol
        return min(1.0, max(0.2, scale))  # 최소 20%, 최대 100%

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

