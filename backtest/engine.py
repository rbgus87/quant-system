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
from strategy.rebalancer import Rebalancer
from strategy.screener import MultiFactorScreener

logger = logging.getLogger(__name__)


class MultiFactorBacktest:
    """멀티팩터 전략 월별 리밸런싱 백테스트 엔진

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

                # 전체 목표 종목 + 기존 보유 종목 시가 조회
                all_tickers = set(new_tickers) | set(holdings.keys())
                prices: dict[str, float] = {}
                for ticker in all_tickers:
                    p = self._get_open_price(ticker, trade_date_str)
                    if p is not None:
                        prices[ticker] = p

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

                # MDD 서킷브레이커: 고점 대비 max_drawdown_pct 초과 시 리밸런싱 중단
                peak_value = max(peak_value, total_value)
                current_dd = (total_value - peak_value) / peak_value if peak_value > 0 else 0
                max_dd_threshold = settings.trading.max_drawdown_pct

                if current_dd < -max_dd_threshold:
                    if not circuit_breaker_active:
                        logger.warning(
                            f"[{date_str}] MDD 서킷브레이커 발동: "
                            f"현재 DD={current_dd:.1%} < -{max_dd_threshold:.0%}"
                            f" → 기존 보유 유지, 리밸런싱 중단"
                        )
                        circuit_breaker_active = True
                elif circuit_breaker_active:
                    logger.info(
                        f"[{date_str}] MDD 서킷브레이커 해제: "
                        f"현재 DD={current_dd:.1%}"
                    )
                    circuit_breaker_active = False

                if circuit_breaker_active:
                    # 리밸런싱 스킵, 기존 보유 유지하면서 일별 가치만 기록
                    turnover_log.append({
                        "date": date_str,
                        "sells": 0,
                        "buys": 0,
                        "turnover_rate": 0.0,
                        "n_holdings_before": len(holdings),
                        "n_holdings_after": len(holdings),
                        "sell_details": [],
                        "buy_details": [],
                        "note": f"서킷브레이커 발동 (DD={current_dd:.1%})",
                    })

                    # 일별 가치 기록만 수행 (리밸런싱 없이)
                    period_end = (
                        rebal_dates[i + 1]
                        if i + 1 < len(rebal_dates)
                        else pd.Timestamp(end_date)
                    )
                    if trade_dt > period_end:
                        period_end = trade_dt + pd.Timedelta(days=20)

                    sd = trade_dt.date() if hasattr(trade_dt, "date") else trade_dt
                    ed = period_end.date() if hasattr(period_end, "date") else period_end

                    close_price_cache_cb: dict[tuple[str, str], float] = {}
                    holding_tickers = list(holdings.keys())
                    if holding_tickers:
                        bulk_df = self.krx.storage.load_daily_prices_bulk(
                            holding_tickers, sd, ed
                        )
                        if not bulk_df.empty:
                            valid = bulk_df[bulk_df["close"].notna() & (bulk_df["close"] > 0)]
                            for _, row in valid.iterrows():
                                dt_key = row["date"].strftime("%Y%m%d") if hasattr(row["date"], "strftime") else str(row["date"])
                                close_price_cache_cb[(row["ticker"], dt_key)] = float(row["close"])

                    last_known_price_cb: dict[str, float] = {}
                    for ticker in holdings:
                        if ticker in prices:
                            last_known_price_cb[ticker] = prices[ticker]

                    dates = get_krx_sessions(
                        trade_dt.strftime("%Y%m%d"), period_end.strftime("%Y%m%d")
                    )
                    for dt in dates:
                        dt_str_val = dt.strftime("%Y%m%d")
                        total = cash
                        for ticker, shares in holdings.items():
                            if shares <= 0:
                                continue
                            price = close_price_cache_cb.get((ticker, dt_str_val))
                            if price is not None:
                                last_known_price_cb[ticker] = price
                            else:
                                price = last_known_price_cb.get(ticker)
                            if price is not None:
                                total += price * shares
                        history.append({
                            "date": dt,
                            "portfolio_value": total,
                            "cash": cash,
                            "n_holdings": len(holdings),
                        })

                    continue  # 다음 리밸런싱 날짜로

                # 고정 금액 모드: 리밸런싱 기준 금액 제한
                max_inv = settings.portfolio.max_investment_amount
                rebal_value = total_value
                if max_inv > 0 and total_value > max_inv:
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

                # 매매 상세 내역을 턴오버 로그에 반영
                if turnover_log:
                    turnover_log[-1]["sell_details"] = sell_details
                    turnover_log[-1]["buy_details"] = buy_details

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

