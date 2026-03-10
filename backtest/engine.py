# backtest/engine.py
import pandas as pd
from typing import Optional
import logging

from config.calendar import (
    get_krx_month_end_sessions,
    get_krx_sessions,
    next_krx_business_day,
)
from config.settings import settings
from data.collector import KRXDataCollector, ReturnCalculator
from data.processor import DataProcessor
from factors.value import ValueFactor
from factors.momentum import MomentumFactor
from factors.quality import QualityFactor
from factors.composite import MultiFactorComposite
from strategy.rebalancer import Rebalancer

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
        self.processor = DataProcessor()
        self.value_f = ValueFactor()
        self.momentum_f = MomentumFactor()
        self.quality_f = QualityFactor()
        self.composite = MultiFactorComposite()
        self.rebalancer = Rebalancer()
        self._prefetched_dates: set[str] = set()  # 프리페치 완료 날짜 추적

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

        cash = self.initial_cash
        holdings: dict[str, int] = {}  # {ticker: shares}
        history: list[dict] = []
        turnover_log: list[dict] = []  # 리밸런싱별 교체율 기록

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

                # 전체 목표 종목 + 기존 보유 종목 시가 조회
                all_tickers = set(new_tickers) | set(holdings.keys())
                prices: dict[str, float] = {}
                for ticker in all_tickers:
                    p = self._get_open_price(ticker, trade_date_str)
                    if p is not None:
                        prices[ticker] = p

                # 총 자산 평가
                total_value = cash
                for ticker, shares in holdings.items():
                    if ticker in prices:
                        total_value += prices[ticker] * shares

                # 비중 리밸런싱 주문 계산 (기존 보유종목 포함 재조정)
                old_tickers = set(holdings.keys())
                orders = self.rebalancer.compute_weight_rebalance(
                    holdings, new_tickers, prices, total_value
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
                })

                # 매도 먼저 실행 (예수금 확보)
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
                    if holdings[ticker] <= 0:
                        holdings.pop(ticker, None)

                # 매수 실행
                for ticker, delta in sorted(orders.items()):
                    if delta <= 0:
                        continue
                    price = prices.get(ticker)
                    if price is None:
                        continue
                    cost = self.rebalancer.calc_buy_cost(price, delta)
                    if cash >= cost:
                        cash -= cost
                        holdings[ticker] = holdings.get(ticker, 0) + delta

                # 보유 종목 OHLCV 기간 일괄 프리페치 (개별 일자 조회 방지)
                period_end = (
                    rebal_dates[i + 1]
                    if i + 1 < len(rebal_dates)
                    else pd.Timestamp(end_date)
                )
                period_end_str = period_end.strftime("%Y%m%d")
                trade_dt_str_for_range = trade_dt.strftime("%Y%m%d")

                # 보유 종목 종가 일괄 로드 → {(ticker, date_str): close_price}
                close_price_cache: dict[tuple[str, str], float] = {}
                for ticker in holdings:
                    try:
                        ohlcv = self.krx.get_ohlcv(
                            ticker, trade_dt_str_for_range, period_end_str
                        )
                        if ohlcv is not None and not ohlcv.empty and "close" in ohlcv.columns:
                            for dt_idx, row in ohlcv.iterrows():
                                dt_key = dt_idx.strftime("%Y%m%d") if hasattr(dt_idx, "strftime") else str(dt_idx)
                                val = row["close"]
                                if val is not None and val > 0:
                                    close_price_cache[(ticker, dt_key)] = float(val)
                    except Exception as e:
                        logger.warning(f"기간 OHLCV 프리페치 실패 ({ticker}): {e}")

                # 일별 포트폴리오 가치 기록 (KRX 거래일만)
                dates = get_krx_sessions(
                    trade_dt.strftime("%Y%m%d"), period_end.strftime("%Y%m%d")
                )
                for dt in dates:
                    dt_str_val = dt.strftime("%Y%m%d")
                    total = cash
                    for ticker, shares in holdings.items():
                        if shares <= 0:
                            continue
                        price = close_price_cache.get((ticker, dt_str_val))
                        if price is not None:
                            total += price * shares

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

        스크리너와 동일한 전처리 파이프라인 적용:
        clean_fundamentals → filter_universe → 팩터 계산 → 상위 N개

        Args:
            date_str: 기준 날짜 (YYYYMMDD)
            market: 시장

        Returns:
            선정된 종목 코드 리스트 (빈 리스트 = 실패)
        """
        # ALL = KOSPI+KOSDAQ 통합
        markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]

        fund_list = []
        cap_list = []
        for m in markets:
            f = self.krx.get_fundamentals_all(date_str, m)
            if not f.empty:
                fund_list.append(f)
            mc = self.krx.get_market_cap(date_str, m)
            if not mc.empty:
                cap_list.append(mc)

        fundamentals = pd.concat(fund_list) if fund_list else pd.DataFrame()
        market_cap_df = pd.concat(cap_list) if cap_list else pd.DataFrame()
        if not market_cap_df.empty:
            market_cap_df = market_cap_df[~market_cap_df.index.duplicated(keep="first")]

        # 펀더멘털 유무에 따라 분기
        has_fundamentals = not fundamentals.empty
        if has_fundamentals:
            fundamentals = fundamentals[~fundamentals.index.duplicated(keep="first")]
            cleaned = self.processor.clean_fundamentals(fundamentals)
        else:
            cleaned = pd.DataFrame()
            logger.warning(f"{date_str}: 펀더멘털 데이터 없음 → 모멘텀 전용 모드")

        # 유니버스 결정 (펀더멘털 없으면 시가총액 기반)
        if not cleaned.empty:
            universe_tickers = cleaned.index.tolist()
        elif not market_cap_df.empty:
            universe_tickers = market_cap_df.index.tolist()
        else:
            return []

        # 유동성 필터 데이터 (현재 날짜 OHLCV 프리페치 보장)
        avg_tv = None
        min_tv = settings.universe.min_avg_trading_value
        if min_tv > 0:
            if date_str not in self._prefetched_dates:
                for m in markets:
                    self.krx.prefetch_daily_trade(date_str, m)
                self._prefetched_dates.add(date_str)
            avg_tv = self.krx.get_avg_trading_value(universe_tickers, date_str)

        # 유니버스 필터 적용
        tickers = self.processor.filter_universe(
            tickers=universe_tickers,
            market_cap=market_cap_df,
            fundamentals=cleaned if not cleaned.empty else None,
            min_cap_percentile=settings.universe.min_market_cap_percentile,
            avg_trading_value=avg_tv,
            min_avg_trading_value=min_tv,
        )

        if not tickers:
            return []

        # 팩터 계산
        value_score = pd.Series(dtype=float, name="value_score")
        quality_score = pd.Series(dtype=float, name="quality_score")

        if not cleaned.empty:
            filtered_fundamentals = cleaned.loc[cleaned.index.isin(tickers)]
            value_score = self.value_f.calculate(filtered_fundamentals)
            quality_score = self.quality_f.calculate(filtered_fundamentals)

        returns_12m = self.ret_calc.get_returns_for_universe(tickers, date_str, 12, 1)
        momentum_score = self.momentum_f.calculate(returns_12m)

        # 합산 → 선정 (펀더멘털 없으면 모멘텀만으로 min_factor_count=1)
        min_factors = 2 if has_fundamentals else 1
        composite_df = self.composite.calculate(
            value_score, momentum_score, quality_score,
            min_factor_count=min_factors,
        )
        if composite_df.empty:
            return []

        selected = self.composite.select_top(composite_df)
        return selected.index.tolist()

    def _get_open_price(self, ticker: str, date_str: str) -> Optional[float]:
        """특정 날짜 시가 조회 (매매 체결용)

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

