# backtest/engine.py
import pandas as pd
import numpy as np
from typing import Optional
import logging

from data.collector import KRXDataCollector, ReturnCalculator
from factors.value import ValueFactor
from factors.momentum import MomentumFactor
from factors.quality import QualityFactor
from factors.composite import MultiFactorComposite
from strategy.rebalancer import Rebalancer
from config.settings import settings

logger = logging.getLogger(__name__)


class MultiFactorBacktest:
    """멀티팩터 전략 월별 리밸런싱 백테스트 엔진

    흐름:
      리밸런싱일(T, 월 마지막 영업일) → 팩터 계산 → 포트폴리오 결정
      → T+1 영업일 시가(open)로 매매 체결 (선견 편향 방지)
    """

    def __init__(self, initial_cash: float = 10_000_000) -> None:
        self.initial_cash = initial_cash
        self.krx = KRXDataCollector()
        self.ret_calc = ReturnCalculator()
        self.value_f = ValueFactor()
        self.momentum_f = MomentumFactor()
        self.quality_f = QualityFactor()
        self.composite = MultiFactorComposite()
        self.rebalancer = Rebalancer()

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

        rebal_dates = self._get_rebalance_dates(start_date, end_date)
        logger.info(f"리밸런싱 횟수: {len(rebal_dates)}회")

        cash = self.initial_cash
        holdings: dict[str, int] = {}  # {ticker: shares}
        history: list[dict] = []

        for i, rebal_dt in enumerate(rebal_dates):
            date_str = rebal_dt.strftime("%Y%m%d")
            logger.info(f"[{i + 1}/{len(rebal_dates)}] 리밸런싱 신호 계산: {date_str}")

            try:
                # T일 팩터 계산 → 목표 포트폴리오
                new_tickers = self._calc_portfolio(date_str, market)
                if not new_tickers:
                    logger.warning(f"{date_str}: 포트폴리오 계산 실패, 스킵")
                    continue

                # T+1 영업일 시가 체결 (선견 편향 방지)
                trade_dt = self._next_business_day(rebal_dt)
                trade_date_str = trade_dt.strftime("%Y%m%d")

                # 매도/매수 주문 계산
                sell_tickers, buy_tickers = self.rebalancer.compute_orders(
                    holdings, new_tickers
                )

                # 매도 실행
                for ticker in sell_tickers:
                    shares = holdings.pop(ticker, 0)
                    if shares <= 0:
                        continue
                    price = self._get_open_price(ticker, trade_date_str)
                    if price is None:
                        continue
                    proceed = self.rebalancer.calc_sell_proceed(price, shares)
                    cash += proceed

                # 총 자산 평가
                total_value = cash
                for ticker, shares in holdings.items():
                    price = self._get_open_price(ticker, trade_date_str)
                    if price:
                        total_value += price * shares

                # 매수 실행
                if buy_tickers:
                    target_per_stock = total_value / len(new_tickers)
                    for ticker in buy_tickers:
                        price = self._get_open_price(ticker, trade_date_str)
                        if price is None:
                            continue
                        shares_to_buy = self.rebalancer.calc_buy_shares(
                            target_per_stock, price
                        )
                        if shares_to_buy <= 0:
                            continue
                        cost = self.rebalancer.calc_buy_cost(price, shares_to_buy)
                        if cash >= cost:
                            cash -= cost
                            holdings[ticker] = holdings.get(ticker, 0) + shares_to_buy

                # 일별 포트폴리오 가치 기록
                period_end = (
                    rebal_dates[i + 1] if i + 1 < len(rebal_dates)
                    else pd.Timestamp(end_date)
                )
                dates = pd.bdate_range(trade_dt, period_end)
                for dt in dates:
                    dt_str = dt.strftime("%Y%m%d")
                    total = cash
                    for ticker, shares in holdings.items():
                        if shares <= 0:
                            continue
                        price = self._get_open_price(ticker, dt_str)
                        if price:
                            total += price * shares

                    history.append({
                        "date": dt,
                        "portfolio_value": total,
                        "cash": cash,
                        "n_holdings": len(holdings),
                    })

            except Exception as e:
                logger.error(f"리밸런싱 실패 ({date_str}): {e}", exc_info=True)
                continue

        if not history:
            raise ValueError("백테스트 결과 없음. 날짜 범위와 데이터를 확인하세요.")

        result = pd.DataFrame(history).set_index("date")
        result["returns"] = result["portfolio_value"].pct_change()

        total_ret = result["portfolio_value"].iloc[-1] / self.initial_cash - 1
        logger.info(f"백테스트 완료 | 총 수익률: {total_ret * 100:.2f}%")
        return result

    # ─────────────────────────────────────────────
    # 내부 메서드
    # ─────────────────────────────────────────────

    def _calc_portfolio(self, date_str: str, market: str) -> list[str]:
        """T일 기준 팩터 계산 후 상위 N개 종목 반환

        Args:
            date_str: 기준 날짜 (YYYYMMDD)
            market: 시장

        Returns:
            선정된 종목 코드 리스트 (빈 리스트 = 실패)
        """
        fundamentals = self.krx.get_fundamentals_all(date_str, market)
        if fundamentals.empty:
            return []

        market_cap_df = self.krx.get_market_cap(date_str, market)
        market_cap = (
            market_cap_df["market_cap"]
            if "market_cap" in market_cap_df.columns
            else pd.Series(dtype=float)
        )

        tickers = fundamentals.index.tolist()

        # 팩터 계산
        value_score = self.value_f.calculate(fundamentals)
        returns_12m = self.ret_calc.get_returns_for_universe(tickers, date_str, 12, 1)
        momentum_score = self.momentum_f.calculate(returns_12m)
        quality_score = self.quality_f.calculate(fundamentals)

        # 합산 → 필터 → 선정
        composite_df = self.composite.calculate(value_score, momentum_score, quality_score)
        filtered_df = self.composite.apply_universe_filter(composite_df, market_cap)
        selected = self.composite.select_top(filtered_df)

        return selected.index.tolist()

    def _get_open_price(self, ticker: str, date_str: str) -> Optional[float]:
        """특정 날짜 시가 조회

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

    def _get_rebalance_dates(self, start_date: str, end_date: str) -> list[pd.Timestamp]:
        """매월 마지막 영업일 목록 생성

        pd.offsets.BMonthEnd() 사용 (deprecated freq 문자열 미사용)

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)

        Returns:
            월말 영업일 Timestamp 리스트
        """
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        dates = pd.date_range(start, end, freq=pd.offsets.BMonthEnd())
        return list(dates)

    def _next_business_day(self, dt: pd.Timestamp) -> pd.Timestamp:
        """다음 영업일 반환

        Args:
            dt: 기준 Timestamp

        Returns:
            다음 영업일 Timestamp
        """
        return dt + pd.offsets.BDay(1)
