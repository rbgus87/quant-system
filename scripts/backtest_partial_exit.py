"""부분 익절 규칙 백테스트 — 도입 여부 검증용 (실전 적용 X, 분석만)

시나리오 비교 (V70M30+Vol70, 2017-2024):
  A. Baseline (현재, 분기말 100% 리밸런싱만)
  B. +15% 도달 시 50% 익절, 나머지 분기말 보유
  C. +20% 도달 시 50% 익절, 나머지 분기말 보유
  D. +30% 도달 시 50% 익절, 나머지 분기말 보유
  E. +20% 도달 시 100% 전량 익절 (현금 보유)

사용법:
  python -m scripts.backtest_partial_exit
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.metrics import PerformanceAnalyzer
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


@dataclass
class PartialExitRule:
    """익절 규칙 정의"""

    name: str
    profit_threshold: float  # 예: 0.15 = +15%
    exit_ratio: float  # 예: 0.50 = 50% 익절, 1.0 = 전량 익절
    description: str = ""


@dataclass
class ExitEvent:
    """익절 발동 이벤트 기록"""

    date: str  # YYYYMMDD
    ticker: str
    ticker_name: str
    cost_basis: float  # 매수 평균단가
    exit_price: float  # 익절 시 가격
    profit_pct: float  # 수익률 (예: 0.20 = +20%)
    shares_before: int  # 익절 전 보유 주수
    shares_sold: int  # 매도 주수
    shares_after: int  # 익절 후 잔여 주수
    proceed: float  # 매도 수익금 (비용 차감 후)
    # 후속 추적용 (리밸런싱일까지)
    subsequent_price: Optional[float] = None  # 분기말 가격
    missed_return: Optional[float] = None  # 익절 후 놓친 수익률


class PartialExitBacktest:
    """부분 익절 규칙 백테스트 엔진

    기존 MultiFactorBacktest와 동일한 분기 리밸런싱을 수행하되,
    리밸런싱 간 기간에 일별 종가를 모니터링하여 익절 조건을 체크합니다.
    """

    def __init__(
        self,
        rule: Optional[PartialExitRule] = None,
        initial_cash: float = 0,
    ) -> None:
        if initial_cash <= 0:
            initial_cash = settings.portfolio.initial_cash
        self.initial_cash = initial_cash
        self.rule = rule  # None이면 Baseline (익절 없음)
        self.screener = MultiFactorScreener()
        self.krx = self.screener.collector
        self.rebalancer = Rebalancer()
        self.regime_filter = MarketRegimeFilter(self.krx)
        self.analyzer = PerformanceAnalyzer()

        # 익절 이벤트 로그
        self.exit_events: list[ExitEvent] = []
        # 거래 비용 누적
        self.total_extra_commission: float = 0.0
        self.total_extra_tax: float = 0.0
        self.total_extra_trades: int = 0

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
            market: 대상 시장

        Returns:
            DataFrame(index=date, columns=[portfolio_value, cash, n_holdings, returns])
        """
        market = market or settings.universe.market
        run_start = time.monotonic()
        rule_name = self.rule.name if self.rule else "Baseline"
        logger.info(f"[{rule_name}] 백테스트 시작: {start_date} ~ {end_date}")

        # DART 통계 초기화
        if hasattr(self.krx, "dart_client") and self.krx.dart_client:
            self.krx.dart_client.reset_stats()

        rebal_dates = self._generate_rebalance_dates(start_date, end_date, market)
        self._prefetch_fundamentals(rebal_dates, market)

        cash = self.initial_cash
        holdings: dict[str, int] = {}
        cost_basis: dict[str, float] = {}
        history: list[dict] = []

        for i, rebal_dt in enumerate(rebal_dates):
            date_str = rebal_dt.strftime("%Y%m%d")

            try:
                trade_dt = next_krx_business_day(rebal_dt)
                trade_date_str = trade_dt.strftime("%Y%m%d")

                # 팩터 계산 → 목표 포트폴리오
                new_tickers = self._calc_portfolio_with_buffer(
                    date_str, market, holdings
                )
                if not new_tickers:
                    logger.warning(f"{date_str}: 포트폴리오 계산 실패, 스킵")
                    continue

                # T+1일 OHLCV 프리페치
                self.krx.prefetch_daily_trade(trade_date_str, market)

                all_tickers = set(new_tickers) | set(holdings.keys())
                prices = self._get_open_prices_bulk(
                    list(all_tickers), trade_date_str
                )

                # 총 자산 평가
                total_value = cash
                for ticker, shares in holdings.items():
                    price = prices.get(ticker) or cost_basis.get(ticker)
                    if price:
                        total_value += price * shares

                # 변동성 타겟팅 + 시장 레짐
                rebal_value = self._calc_rebalance_value(
                    history, total_value, date_str
                )

                # 매매 실행
                cash = self._execute_trades(
                    holdings, cost_basis, new_tickers, prices,
                    cash, total_value, rebal_value,
                )

                # 리밸런싱 기간 동안 일별 가치 기록 + 익절 모니터링
                period_end = (
                    rebal_dates[i + 1]
                    if i + 1 < len(rebal_dates)
                    else pd.Timestamp(end_date)
                )
                if trade_dt > period_end:
                    period_end = trade_dt + pd.Timedelta(days=20)

                cash = self._record_daily_with_partial_exit(
                    holdings, cost_basis, prices, cash, history,
                    trade_dt, period_end, rebal_dt,
                    rebal_dates[i + 1] if i + 1 < len(rebal_dates) else None,
                )

            except Exception as e:
                logger.error(f"리밸런싱 실패 ({date_str}): {e}", exc_info=True)
                continue

        if not history:
            raise ValueError("백테스트 결과 없음")

        result = pd.DataFrame(history).set_index("date")
        end_ts = pd.Timestamp(end_date)
        result = result[result.index <= end_ts]
        if result.empty:
            raise ValueError("백테스트 결과 없음")
        result["returns"] = result["portfolio_value"].pct_change()

        elapsed = time.monotonic() - run_start
        total_ret = result["portfolio_value"].iloc[-1] / self.initial_cash - 1
        logger.info(
            f"[{rule_name}] 완료 | 수익률: {total_ret * 100:.2f}% | "
            f"{elapsed:.0f}초 | 익절 {len(self.exit_events)}회"
        )

        return result

    def _record_daily_with_partial_exit(
        self,
        holdings: dict[str, int],
        cost_basis: dict[str, float],
        open_prices: dict[str, float],
        cash: float,
        history: list[dict],
        trade_dt: pd.Timestamp,
        period_end: pd.Timestamp,
        rebal_dt: pd.Timestamp,
        next_rebal_dt: Optional[pd.Timestamp],
    ) -> float:
        """일별 가치 기록 + 익절 모니터링

        Args:
            holdings: 보유 종목 (in-place 변경)
            cost_basis: 매수단가 (in-place 변경)
            open_prices: 매매 체결 시가
            cash: 현재 현금
            history: 일별 기록 리스트
            trade_dt: 매매 체결일
            period_end: 기록 종료일
            rebal_dt: 현재 리밸런싱 날짜
            next_rebal_dt: 다음 리밸런싱 날짜

        Returns:
            갱신된 현금
        """
        sd = trade_dt.date() if hasattr(trade_dt, "date") else trade_dt
        ed = period_end.date() if hasattr(period_end, "date") else period_end

        # 벌크 DB 조회
        close_cache: dict[tuple[str, str], float] = {}
        holding_tickers = list(holdings.keys())
        if holding_tickers:
            bulk_df = self.krx.storage.load_daily_prices_bulk(
                holding_tickers, sd, ed
            )
            if not bulk_df.empty:
                valid = bulk_df[
                    bulk_df["close"].notna() & (bulk_df["close"] > 0)
                ]
                for t, d, c in zip(valid["ticker"], valid["date"], valid["close"]):
                    dt_key = (
                        d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d)
                    )
                    close_cache[(t, dt_key)] = float(c)

        last_known: dict[str, float] = {}
        for ticker in holdings:
            if ticker in open_prices:
                last_known[ticker] = open_prices[ticker]

        dates = get_krx_sessions(
            trade_dt.strftime("%Y%m%d"), period_end.strftime("%Y%m%d")
        )

        for dt in dates:
            dt_str = dt.strftime("%Y%m%d")

            # 익절 체크 (Baseline이 아닌 경우)
            if self.rule is not None and holdings:
                cash = self._check_partial_exit(
                    holdings, cost_basis, close_cache, last_known,
                    cash, dt_str, next_rebal_dt,
                )

            # 포트폴리오 가치 계산
            total = cash
            for ticker, shares in holdings.items():
                if shares <= 0:
                    continue
                price = close_cache.get((ticker, dt_str))
                if price is not None:
                    last_known[ticker] = price
                else:
                    price = last_known.get(ticker)
                if price is not None:
                    total += price * shares

            history.append({
                "date": dt,
                "portfolio_value": total,
                "cash": cash,
                "n_holdings": sum(1 for s in holdings.values() if s > 0),
            })

        return cash

    def _check_partial_exit(
        self,
        holdings: dict[str, int],
        cost_basis: dict[str, float],
        close_cache: dict[tuple[str, str], float],
        last_known: dict[str, float],
        cash: float,
        dt_str: str,
        next_rebal_dt: Optional[pd.Timestamp],
    ) -> float:
        """익절 조건 체크 및 실행

        Args:
            holdings: 보유 종목 (in-place 변경)
            cost_basis: 매수단가
            close_cache: 종가 캐시
            last_known: 마지막 알려진 가격
            cash: 현재 현금
            dt_str: 날짜 (YYYYMMDD)
            next_rebal_dt: 다음 리밸런싱 날짜 (후속 추적용)

        Returns:
            갱신된 현금
        """
        if self.rule is None:
            return cash

        for ticker in list(holdings.keys()):
            shares = holdings[ticker]
            if shares <= 0:
                continue

            avg_cost = cost_basis.get(ticker)
            if avg_cost is None or avg_cost <= 0:
                continue

            price = close_cache.get((ticker, dt_str))
            if price is not None:
                last_known[ticker] = price
            else:
                price = last_known.get(ticker)
            if price is None:
                continue

            profit_pct = (price - avg_cost) / avg_cost

            if profit_pct >= self.rule.profit_threshold:
                # 익절 실행
                sell_shares = max(1, int(shares * self.rule.exit_ratio))
                sell_shares = min(sell_shares, shares)

                # 거래 비용 계산
                commission = price * sell_shares * settings.trading.commission_rate
                tax = price * sell_shares * settings.trading.tax_rate
                slippage = price * sell_shares * settings.trading.slippage
                proceed = price * sell_shares - commission - tax - slippage

                cash += proceed
                self.total_extra_commission += commission
                self.total_extra_tax += tax
                self.total_extra_trades += 1

                remaining = shares - sell_shares
                ticker_name = self.krx.get_ticker_name(ticker)

                event = ExitEvent(
                    date=dt_str,
                    ticker=ticker,
                    ticker_name=ticker_name,
                    cost_basis=avg_cost,
                    exit_price=price,
                    profit_pct=profit_pct,
                    shares_before=shares,
                    shares_sold=sell_shares,
                    shares_after=remaining,
                    proceed=proceed,
                )

                # 후속 가격 추적: 다음 리밸런싱일 종가
                if next_rebal_dt is not None:
                    next_dt_str = next_rebal_dt.strftime("%Y%m%d")
                    subseq_price = close_cache.get((ticker, next_dt_str))
                    if subseq_price is not None:
                        event.subsequent_price = subseq_price
                        event.missed_return = (subseq_price - price) / price

                self.exit_events.append(event)

                if remaining > 0:
                    holdings[ticker] = remaining
                else:
                    holdings.pop(ticker, None)
                    cost_basis.pop(ticker, None)

                logger.debug(
                    f"[{dt_str}] 익절: {ticker_name}({ticker}) "
                    f"+{profit_pct:.1%} → {sell_shares}주 매도 "
                    f"(잔여 {remaining}주)"
                )

        return cash

    # ── 기존 엔진 메서드 재활용 ──

    def _generate_rebalance_dates(
        self, start_date: str, end_date: str, market: str,
    ) -> list[pd.Timestamp]:
        """리밸런싱 날짜 생성 (분기 리밸런싱)"""
        all_month_ends = get_krx_month_end_sessions(start_date, end_date)
        freq = settings.portfolio.rebalance_frequency
        if freq == "quarterly":
            rebal_dates = [d for d in all_month_ends if d.month in (3, 6, 9, 12)]
        else:
            rebal_dates = all_month_ends

        # 매매일 OHLCV 프리페치
        for rdt in rebal_dates:
            tdt = next_krx_business_day(rdt)
            self.krx.prefetch_daily_trade(tdt.strftime("%Y%m%d"), market)

        return rebal_dates

    def _prefetch_fundamentals(
        self, rebal_dates: list[pd.Timestamp], market: str,
    ) -> None:
        """펀더멘털 프리페치"""
        from data.collector import _parse_date

        markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
        missing: list[str] = []
        for rdt in rebal_dates:
            date_str = rdt.strftime("%Y%m%d")
            dt = _parse_date(date_str)
            for m in markets:
                cached = self.krx.storage.load_fundamentals(dt, market=m)
                if cached.empty:
                    missing.append(date_str)
                    break

        if not missing:
            return

        logger.info(f"프리페치: {len(missing)}/{len(rebal_dates)}개 날짜")
        for date_str in missing:
            for m in markets:
                self.krx.get_fundamentals_all(date_str, m)

    def _calc_portfolio_with_buffer(
        self, date_str: str, market: str, current_holdings: dict[str, int],
    ) -> list[str]:
        """홀딩 버퍼 적용 포트폴리오 계산"""
        n_stocks = settings.portfolio.n_stocks
        buffer_ratio = settings.portfolio.holding_buffer_ratio
        buffer_n = int(n_stocks * buffer_ratio)

        wide_df = self.screener.screen(date_str, market=market, n_stocks=buffer_n)
        if wide_df.empty:
            return []

        wide_candidates = wide_df.index.tolist()
        if not current_holdings:
            return wide_candidates[:n_stocks]

        buffer_set = set(wide_candidates[:buffer_n])
        held_in_buffer = [t for t in current_holdings if t in buffer_set]
        keep = set(held_in_buffer)
        new_portfolio: list[str] = list(keep)

        for ticker in wide_candidates:
            if len(new_portfolio) >= n_stocks:
                break
            if ticker not in keep:
                new_portfolio.append(ticker)

        return new_portfolio[:n_stocks]

    def _get_open_prices_bulk(
        self, tickers: list[str], date_str: str,
    ) -> dict[str, float]:
        """시가 벌크 조회"""
        from data.collector import _parse_date

        dt = _parse_date(date_str)
        prices: dict[str, float] = {}
        bulk_df = self.krx.storage.load_daily_prices_bulk(tickers, dt, dt)
        if not bulk_df.empty:
            valid = bulk_df[bulk_df["open"].notna() & (bulk_df["open"] > 0)]
            if not valid.empty:
                price_map = valid.set_index("ticker")["open"].to_dict()
                prices.update({str(k): float(v) for k, v in price_map.items()})
        return prices

    def _calc_rebalance_value(
        self, history: list[dict], total_value: float, date_str: str,
    ) -> float:
        """변동성 타겟팅 + 시장 레짐"""
        from strategy.market_regime import calc_vol_target_scale

        values = [h["portfolio_value"] for h in history]
        vol_scale = calc_vol_target_scale(
            values, settings.trading.vol_target, settings.trading.vol_lookback_days,
        )
        invest_ratio = self.regime_filter.get_invest_ratio(date_str)
        raw_ratio = invest_ratio * vol_scale
        combined_ratio = max(raw_ratio, 0.20)
        rebal_value = total_value * combined_ratio

        max_inv = settings.portfolio.max_investment_amount
        if max_inv > 0 and rebal_value > max_inv:
            rebal_value = max_inv

        return rebal_value

    def _execute_trades(
        self,
        holdings: dict[str, int],
        cost_basis: dict[str, float],
        new_tickers: list[str],
        prices: dict[str, float],
        cash: float,
        total_value: float,
        rebal_value: float,
    ) -> float:
        """매도 → 매수 리밸런싱 (엔진과 동일 로직, 턴오버 로그 생략)"""
        orders = self.rebalancer.compute_weight_rebalance(
            holdings, new_tickers, prices, rebal_value
        )

        # 매도
        for ticker, delta in sorted(orders.items()):
            if delta >= 0:
                continue
            sell_shares = min(-delta, holdings.get(ticker, 0))
            if sell_shares <= 0:
                continue
            price = prices.get(ticker)
            if price is None:
                continue
            cost_rate = (
                self.rebalancer.cfg.commission_rate
                + self.rebalancer.cfg.tax_rate
                + self.rebalancer.cfg.slippage
            )
            proceed = price * sell_shares * (1 - cost_rate)
            cash += proceed
            holdings[ticker] = holdings.get(ticker, 0) - sell_shares
            if holdings[ticker] <= 0:
                holdings.pop(ticker, None)
                cost_basis.pop(ticker, None)

        # 매수
        for ticker, delta in sorted(orders.items()):
            if delta <= 0:
                continue
            price = prices.get(ticker)
            if price is None:
                continue
            buy_cost_rate = (
                self.rebalancer.cfg.commission_rate + self.rebalancer.cfg.slippage
            )
            cost = price * delta * (1 + buy_cost_rate)
            if cash >= cost:
                cash -= cost
                prev_shares = holdings.get(ticker, 0)
                prev_cost = cost_basis.get(ticker, 0.0)
                new_total = prev_shares + delta
                if new_total > 0:
                    cost_basis[ticker] = (
                        (prev_cost * prev_shares + price * delta) / new_total
                    )
                holdings[ticker] = new_total

        return cash

    def get_summary(self, result: pd.DataFrame) -> dict:
        """시나리오 성과 요약

        Args:
            result: run() 결과 DataFrame

        Returns:
            성과 지표 딕셔너리
        """
        vals = result["portfolio_value"]
        rets = result["returns"].dropna()

        cagr = self.analyzer.calculate_cagr(vals)
        mdd = self.analyzer.calculate_mdd(vals)
        sharpe = self.analyzer.calculate_sharpe(rets)
        sortino = self.analyzer.calculate_sortino(rets)
        calmar = self.analyzer.calculate_calmar(cagr, mdd)

        # 평균 보유 기간 추정 (일)
        n_days = (vals.index[-1] - vals.index[0]).days
        avg_n_holdings = result["n_holdings"].mean()
        if self.exit_events:
            total_exit_shares = sum(e.shares_sold for e in self.exit_events)
            avg_hold_days = n_days / max(len(self.exit_events), 1)
        else:
            avg_hold_days = n_days / max(
                n_days / (252 / 4), 1  # 분기 리밸런싱 기준
            )

        # 놓친 수익 분석
        events_with_subsequent = [
            e for e in self.exit_events if e.missed_return is not None
        ]
        avg_missed = (
            np.mean([e.missed_return for e in events_with_subsequent])
            if events_with_subsequent else 0.0
        )
        positive_missed = sum(
            1 for e in events_with_subsequent if e.missed_return > 0
        )

        # 익절 시점 분석 (리밸런싱 후 며칠 만에 익절?)
        avg_exit_day = 0.0
        if self.exit_events:
            # 대략적 추정: 분기 내 평균 위치
            avg_exit_day = np.mean([
                int(e.date[6:8]) for e in self.exit_events  # 일자 기반 근사
            ])

        return {
            "name": self.rule.name if self.rule else "A. Baseline",
            "description": (
                self.rule.description if self.rule
                else "분기말 100% 리밸런싱만"
            ),
            "cagr": cagr,
            "mdd": mdd,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "total_return": float(vals.iloc[-1] / vals.iloc[0] - 1),
            "n_exit_events": len(self.exit_events),
            "avg_hold_days": avg_hold_days,
            "extra_trades": self.total_extra_trades,
            "extra_commission": self.total_extra_commission,
            "extra_tax": self.total_extra_tax,
            "extra_cost_total": (
                self.total_extra_commission + self.total_extra_tax
                + self.total_extra_trades
                * settings.trading.slippage * self.initial_cash * 0.01
            ),
            "avg_missed_return": float(avg_missed),
            "missed_positive_pct": (
                positive_missed / len(events_with_subsequent) * 100
                if events_with_subsequent else 0.0
            ),
            "n_events_with_subsequent": len(events_with_subsequent),
        }


def generate_report(
    summaries: list[dict],
    output_path: str,
) -> None:
    """분석 결과를 마크다운 리포트로 생성

    Args:
        summaries: 시나리오별 성과 요약 리스트
        output_path: 리포트 저장 경로
    """
    baseline = summaries[0]
    lines: list[str] = []

    lines.append("# 부분 익절 규칙 백테스트 분석")
    lines.append("")
    lines.append(f"> 생성일: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 전략: V70M30 + Vol70 (프리셋 A)")
    lines.append(f"> 기간: 2017-01-01 ~ 2024-12-31 (8년)")
    lines.append(f"> 리밸런싱: 분기 (3/6/9/12월 말)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 요약 테이블
    lines.append("## 1. 성과 비교 요약")
    lines.append("")
    lines.append(
        "| 시나리오 | CAGR | MDD | Sharpe | Sortino | Calmar | 총수익률 |"
    )
    lines.append(
        "|----------|------|-----|--------|---------|--------|---------|"
    )
    for s in summaries:
        lines.append(
            f"| {s['name']} | {s['cagr']*100:.2f}% | {s['mdd']*100:.2f}% | "
            f"{s['sharpe']:.3f} | {s['sortino']:.3f} | {s['calmar']:.3f} | "
            f"{s['total_return']*100:.1f}% |"
        )
    lines.append("")

    # Baseline 대비 변화
    lines.append("## 2. Baseline 대비 변화")
    lines.append("")
    lines.append(
        "| 시나리오 | CAGR 차이 | MDD 차이 | Sharpe 차이 | 판정 |"
    )
    lines.append(
        "|----------|-----------|----------|-------------|------|"
    )
    for s in summaries[1:]:
        cagr_diff = (s["cagr"] - baseline["cagr"]) * 100
        mdd_diff = (s["mdd"] - baseline["mdd"]) * 100
        sharpe_diff = s["sharpe"] - baseline["sharpe"]

        # 판정 로직
        cagr_better = cagr_diff > 0.1  # +0.1%p 이상
        sharpe_better = sharpe_diff > 0.01
        if cagr_better and sharpe_better:
            verdict = "**도입 검토**"
        elif cagr_better or sharpe_better:
            verdict = "한쪽만 개선 → 도입 안 함"
        else:
            verdict = "둘 다 악화 → 분기 리밸런싱 최적"

        lines.append(
            f"| {s['name']} | {cagr_diff:+.2f}%p | {mdd_diff:+.2f}%p | "
            f"{sharpe_diff:+.3f} | {verdict} |"
        )
    lines.append("")

    # 익절 상세
    lines.append("## 3. 익절 발동 상세")
    lines.append("")
    lines.append(
        "| 시나리오 | 발동 횟수 | 추가 거래 비용 | "
        "놓친 수익(평균) | 놓친 수익 양수 비율 |"
    )
    lines.append(
        "|----------|----------|---------------|-------------|----------------|"
    )
    for s in summaries[1:]:
        extra_cost = s["extra_commission"] + s["extra_tax"]
        lines.append(
            f"| {s['name']} | {s['n_exit_events']}회 | "
            f"{extra_cost:,.0f}원 | "
            f"{s['avg_missed_return']*100:+.2f}% | "
            f"{s['missed_positive_pct']:.1f}% |"
        )
    lines.append("")
    lines.append(
        "> **놓친 수익 양수 비율**: 익절 후 해당 종목이 분기말까지 추가 상승한 비율.\n"
        "> 높을수록 \"너무 일찍 팔았다\"는 의미."
    )
    lines.append("")

    # 결론
    lines.append("## 4. 결론")
    lines.append("")

    # 자동 판정
    any_improvement = False
    for s in summaries[1:]:
        cagr_diff = s["cagr"] - baseline["cagr"]
        sharpe_diff = s["sharpe"] - baseline["sharpe"]
        if cagr_diff > 0.001 and sharpe_diff > 0.01:
            any_improvement = True
            break

    if any_improvement:
        lines.append(
            "일부 시나리오에서 Baseline 대비 개선이 확인되었습니다. "
            "추가 검토가 필요합니다."
        )
    else:
        lines.append(
            "**모든 시나리오에서 Baseline(분기 리밸런싱만) 대비 유의미한 개선이 없습니다.**\n\n"
            "부분 익절은 다음과 같은 이유로 효과가 제한적입니다:\n\n"
            "1. 추가 거래 비용 (수수료 + 세금 + 슬리피지)이 소폭 수익 개선을 상쇄\n"
            "2. 익절 후 해당 종목의 추가 상승분을 놓침 (모멘텀 종목 조기 매도)\n"
            "3. 익절 대금이 현금으로 유휴 → 다음 리밸런싱까지 기회비용 발생\n\n"
            "**권고: 현재의 분기 리밸런싱 전략을 유지합니다.**"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*이 분석은 자동 생성되었습니다. "
        "실전 적용 전 추가 검토가 필요합니다.*"
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"리포트 저장: {output_path}")


def main() -> None:
    """메인 실행"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    start_date = "2017-01-01"
    end_date = "2024-12-31"

    # 시나리오 정의
    scenarios: list[Optional[PartialExitRule]] = [
        None,  # A. Baseline
        PartialExitRule(
            name="B. +15%→50%익절",
            profit_threshold=0.15,
            exit_ratio=0.50,
            description="+15% 도달 시 50% 익절, 나머지 분기말 보유",
        ),
        PartialExitRule(
            name="C. +20%→50%익절",
            profit_threshold=0.20,
            exit_ratio=0.50,
            description="+20% 도달 시 50% 익절, 나머지 분기말 보유",
        ),
        PartialExitRule(
            name="D. +30%→50%익절",
            profit_threshold=0.30,
            exit_ratio=0.50,
            description="+30% 도달 시 50% 익절, 나머지 분기말 보유",
        ),
        PartialExitRule(
            name="E. +20%→100%전량",
            profit_threshold=0.20,
            exit_ratio=1.00,
            description="+20% 도달 시 100% 전량 익절 (현금 보유)",
        ),
    ]

    summaries: list[dict] = []
    total_start = time.monotonic()

    for rule in scenarios:
        bt = PartialExitBacktest(rule=rule)
        result = bt.run(start_date, end_date)
        summary = bt.get_summary(result)
        summaries.append(summary)

        name = summary["name"]
        logger.info(
            f"=== {name} ===\n"
            f"  CAGR: {summary['cagr']*100:.2f}%\n"
            f"  MDD: {summary['mdd']*100:.2f}%\n"
            f"  Sharpe: {summary['sharpe']:.3f}\n"
            f"  Sortino: {summary['sortino']:.3f}\n"
            f"  Calmar: {summary['calmar']:.3f}\n"
            f"  익절 {summary['n_exit_events']}회\n"
            f"  놓친 수익(평균): {summary['avg_missed_return']*100:+.2f}%"
        )

    total_elapsed = time.monotonic() - total_start
    logger.info(f"전체 소요: {total_elapsed:.0f}초 ({total_elapsed/60:.1f}분)")

    # 리포트 생성
    report_path = str(
        Path(__file__).resolve().parent.parent
        / "docs" / "reports" / "partial_exit_analysis.md"
    )
    generate_report(summaries, report_path)

    # 콘솔 요약
    print("\n" + "=" * 70)
    print("부분 익절 규칙 백테스트 결과 요약")
    print("=" * 70)
    baseline = summaries[0]
    print(
        f"\n{'시나리오':<22} {'CAGR':>8} {'MDD':>8} {'Sharpe':>8} "
        f"{'익절횟수':>8} {'CAGR차이':>10}"
    )
    print("-" * 70)
    for s in summaries:
        cagr_diff = (s["cagr"] - baseline["cagr"]) * 100
        diff_str = f"{cagr_diff:+.2f}%p" if s != baseline else "-"
        print(
            f"{s['name']:<22} {s['cagr']*100:>7.2f}% {s['mdd']*100:>7.2f}% "
            f"{s['sharpe']:>8.3f} {s['n_exit_events']:>8} {diff_str:>10}"
        )
    print(f"\n리포트: {report_path}")


if __name__ == "__main__":
    main()
