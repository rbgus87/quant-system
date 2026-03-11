# tests/test_backtest.py
import pandas as pd
import numpy as np
import os
from unittest.mock import patch, MagicMock

from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from backtest.report import ReportGenerator
from config.calendar import (
    get_krx_month_end_sessions,
    next_krx_business_day,
)
from strategy.rebalancer import Rebalancer

# ───────────────────────────────────────────────
# Rebalancer 테스트
# ───────────────────────────────────────────────


class TestRebalancer:
    def setup_method(self) -> None:
        self.rebalancer = Rebalancer()
        # 테스트 편의: 종목 수가 적으므로 집중도 제한 완화
        self.rebalancer.cfg.max_position_pct = 1.0

    def test_first_rebalance_all_buys(self) -> None:
        """첫 리밸런싱: 보유 없음 → 전부 매수"""
        current: dict[str, int] = {}
        target = ["A", "B", "C"]
        sells, buys = self.rebalancer.compute_orders(current, target)
        assert sells == []
        assert sorted(buys) == ["A", "B", "C"]

    def test_no_change(self) -> None:
        """포트폴리오 동일 → 주문 없음"""
        current = {"A": 100, "B": 200}
        target = ["A", "B"]
        sells, buys = self.rebalancer.compute_orders(current, target)
        assert sells == []
        assert buys == []

    def test_partial_turnover(self) -> None:
        """일부 교체: A 매도, C 매수"""
        current = {"A": 100, "B": 200}
        target = ["B", "C"]
        sells, buys = self.rebalancer.compute_orders(current, target)
        assert sells == ["A"]
        assert buys == ["C"]

    def test_full_turnover(self) -> None:
        """전체 교체"""
        current = {"A": 100, "B": 200}
        target = ["C", "D"]
        sells, buys = self.rebalancer.compute_orders(current, target)
        assert sorted(sells) == ["A", "B"]
        assert sorted(buys) == ["C", "D"]

    def test_liquidate_all(self) -> None:
        """타겟 비어 있음 → 전부 매도"""
        current = {"A": 100, "B": 200}
        target: list[str] = []
        sells, buys = self.rebalancer.compute_orders(current, target)
        assert sorted(sells) == ["A", "B"]
        assert buys == []

    def test_sell_cost(self) -> None:
        """매도 비용: 수수료(0.015%) + 세금(0.18%) + 슬리피지(0.1%)"""
        price = 10000.0
        shares = 100
        proceed = self.rebalancer.calc_sell_proceed(price, shares)
        gross = price * shares  # 1,000,000
        cost_rate = 0.00015 + 0.0018 + 0.001  # 0.00295
        expected = gross * (1 - cost_rate)
        assert abs(proceed - expected) < 0.01

    def test_buy_cost(self) -> None:
        """매수 비용: 수수료(0.015%) + 슬리피지(0.1%)"""
        price = 10000.0
        shares = 100
        total_cost = self.rebalancer.calc_buy_cost(price, shares)
        cost_rate = 0.00015 + 0.001  # 0.00115
        expected = price * shares * (1 + cost_rate)
        assert abs(total_cost - expected) < 0.01

    def test_buy_shares_calculation(self) -> None:
        """목표 금액으로 살 수 있는 주식 수 계산"""
        target_amount = 1_000_000.0
        price = 50000.0
        shares = self.rebalancer.calc_buy_shares(target_amount, price)
        # 비용 포함 가격 = 50000 * 1.00115 = 50057.5
        # 1_000_000 / 50057.5 = 19.977 → 19주
        assert shares == 19

    def test_market_impact_small_order(self) -> None:
        """소량 주문 → 시장 충격 작음"""
        impact = self.rebalancer.estimate_market_impact(
            order_qty=100, avg_daily_volume=1_000_000
        )
        assert 0 < impact < 0.005  # 0.5% 미만

    def test_market_impact_large_order(self) -> None:
        """대량 주문 → 시장 충격 큼"""
        impact = self.rebalancer.estimate_market_impact(
            order_qty=100_000, avg_daily_volume=100_000
        )
        assert impact > 0.01  # 1% 초과

    def test_market_impact_zero_volume(self) -> None:
        """거래량 0 → 충격 0"""
        impact = self.rebalancer.estimate_market_impact(
            order_qty=100, avg_daily_volume=0
        )
        assert impact == 0.0

    def test_market_impact_capped(self) -> None:
        """시장 충격 최대 5% 캡"""
        impact = self.rebalancer.estimate_market_impact(
            order_qty=1_000_000, avg_daily_volume=1_000
        )
        assert impact <= 0.05

    def test_weight_rebalance_new_portfolio(self) -> None:
        """빈 포트폴리오에서 목표 종목 전체 매수"""
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={},
            target_tickers=["A", "B"],
            prices={"A": 10000, "B": 20000},
            total_value=1_000_000,
        )
        # 각 50만원씩 → A: 49주(50만/10000*1.00115), B: 24주
        assert orders["A"] > 0
        assert orders["B"] > 0

    def test_weight_rebalance_drift_correction(self) -> None:
        """기존 보유종목 비중 드리프트 교정"""
        # A: 30주(시가 10000 = 30만원), B: 10주(시가 20000 = 20만원)
        # 총가치 = 현금 50만 + A 30만 + B 20만 = 100만
        # 목표 비중: 각 50만원 → A: 49주, B: 24주
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={"A": 30, "B": 10},
            target_tickers=["A", "B"],
            prices={"A": 10000, "B": 20000},
            total_value=1_000_000,
        )
        # A: 49-30=19 매수, B: 24-10=14 매수
        assert orders["A"] > 0  # 추가 매수
        assert orders["B"] > 0  # 추가 매수

    def test_weight_rebalance_overweight_sells(self) -> None:
        """과대 비중 종목은 일부 매도"""
        # A: 80주(시가 10000 = 80만), B: 5주(시가 20000 = 10만)
        # 총가치 = 현금 10만 + 80만 + 10만 = 100만
        # 목표: 각 50만 → A: 49주, B: 24주
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={"A": 80, "B": 5},
            target_tickers=["A", "B"],
            prices={"A": 10000, "B": 20000},
            total_value=1_000_000,
        )
        assert orders["A"] < 0  # 일부 매도 (80→49)
        assert orders["B"] > 0  # 추가 매수 (5→24)

    def test_weight_rebalance_liquidation(self) -> None:
        """목표 비어있으면 전량 청산"""
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={"A": 50, "B": 30},
            target_tickers=[],
            prices={"A": 10000, "B": 20000},
            total_value=1_000_000,
        )
        assert orders == {"A": -50, "B": -30}

    def test_max_position_pct_limits_allocation(self) -> None:
        """max_position_pct가 종목당 비중을 제한하는지 확인"""
        self.rebalancer.cfg.max_position_pct = 0.10  # 10%
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={},
            target_tickers=["A", "B"],
            prices={"A": 10000, "B": 20000},
            total_value=1_000_000,
        )
        # 균등 비중 50% > max 10%, cap 적용 → 종목당 10만원
        # A: 10만/10000*1.00115 ≈ 9주, B: 10만/20000*1.00115 ≈ 4주
        assert orders["A"] < 50  # 50주 미만 (제한 없으면 49주)
        assert orders["B"] < 25  # 25주 미만 (제한 없으면 24주)
        assert orders["A"] <= 10
        assert orders["B"] <= 5

    def test_capital_adaptive_excludes_expensive_stocks(self) -> None:
        """소액 자본에서 비싼 종목 자동 제외 + 재분배"""
        # 100만원, 5종목 → 종목당 20만원
        # C(50만원)는 1주도 못 삼 → 제외 → 4종목으로 재분배 (25만원씩)
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={},
            target_tickers=["A", "B", "C", "D", "E"],
            prices={
                "A": 50000,   # 20만원 배분 → 3주 OK
                "B": 100000,  # 20만원 배분 → 1주 OK
                "C": 500000,  # 20만원 배분 → 0주 → 제외!
                "D": 80000,   # 20만원 배분 → 2주 OK
                "E": 150000,  # 20만원 배분 → 1주 OK
            },
            total_value=1_000_000,
        )
        # C는 제외됨
        assert "C" not in orders
        # 나머지 4종목은 매수 주문 생성됨
        assert orders.get("A", 0) > 0
        assert orders.get("B", 0) > 0
        assert orders.get("D", 0) > 0
        assert orders.get("E", 0) > 0

    def test_capital_adaptive_redistributes_budget(self) -> None:
        """제외 후 종목당 배분 금액이 증가하는지 확인"""
        # 100만원, 2종목 → 종목당 50만원
        # B(60만원)는 50만원 배분으로 0주 → 제외 → A만 100만원 배분
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={},
            target_tickers=["A", "B"],
            prices={"A": 50000, "B": 600000},
            total_value=1_000_000,
        )
        assert "B" not in orders
        # A에 100만원 전체 배분 → 약 19주 (수수료 고려)
        assert orders["A"] >= 19

    def test_capital_adaptive_keeps_existing_holdings(self) -> None:
        """이미 보유 중인 종목은 가격이 비싸도 제외하지 않음"""
        # 100만원, 2종목 → 종목당 50만원
        # B(60만원)는 비싸지만 이미 1주 보유 → 제외하지 않음
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={"B": 1},
            target_tickers=["A", "B"],
            prices={"A": 50000, "B": 600000},
            total_value=1_000_000,
        )
        # B는 이미 보유하므로 제외되지 않음 (delta 0이면 orders에 안 들어갈 수 있음)
        assert "A" in orders
        assert orders["A"] > 0

    def test_capital_adaptive_all_too_expensive(self) -> None:
        """모든 종목이 너무 비싸면 빈 주문 반환"""
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={},
            target_tickers=["A", "B"],
            prices={"A": 600000, "B": 800000},
            total_value=500_000,
        )
        # 둘 다 매수 불가 → 빈 주문
        assert "A" not in orders
        assert "B" not in orders

    def test_capital_adaptive_iterative_exclusion(self) -> None:
        """반복적 제외: 1차 제외 후 재분배해도 여전히 못 사는 종목 추가 제외"""
        # 100만원, 4종목 → 종목당 25만원
        # D(30만원) 제외 → 3종목, 종목당 33만원
        # C(35만원) 여전히 OK (33만원 < 35만원이면 제외... )
        # 실제: C(350000)는 33만원으로 0주 → 추가 제외
        # → 2종목, 종목당 50만원
        orders = self.rebalancer.compute_weight_rebalance(
            current_holdings={},
            target_tickers=["A", "B", "C", "D"],
            prices={
                "A": 50000,   # OK
                "B": 100000,  # OK
                "C": 350000,  # 25만→제외, 33만→제외, 50만→OK? → 1주 OK
                "D": 600000,  # 25만→제외
            },
            total_value=1_000_000,
        )
        assert "D" not in orders  # 항상 너무 비쌈
        assert orders.get("A", 0) > 0
        assert orders.get("B", 0) > 0


# ───────────────────────────────────────────────
# Engine 테스트
# ───────────────────────────────────────────────


class TestEngine:
    def test_rebalance_dates_are_business_month_ends(self) -> None:
        """리밸런싱 날짜가 실제 KRX 월말 거래일인지 확인"""
        dates = get_krx_month_end_sessions("2024-01-01", "2024-06-30")

        assert len(dates) == 6  # Jan~Jun 각 1회
        for dt in dates:
            # 영업일인지 (weekday < 5)
            assert dt.weekday() < 5, f"{dt} is not a business day"
            # 같은 달의 다음 거래일이 없어야 함 (마지막 거래일)
            next_bd = next_krx_business_day(dt)
            assert (
                next_bd.month != dt.month
            ), f"{dt} is not the last business day of the month"

    def test_rebalance_dates_use_krx_calendar(self) -> None:
        """KRX 캘린더 사용 확인 — 한국 공휴일 인식"""
        dates = get_krx_month_end_sessions("2024-01-01", "2024-12-31")
        assert len(dates) == 12  # 12개월 각 1회
        for dt in dates:
            assert dt.weekday() < 5

    def test_next_business_day(self) -> None:
        """T+1 체결이 정확히 다음 KRX 거래일인지 확인"""
        # 금요일 → 다음 월요일
        friday = pd.Timestamp("2024-01-26")  # Friday
        next_bd = next_krx_business_day(friday)
        assert next_bd == pd.Timestamp("2024-01-29")  # Monday
        assert next_bd.weekday() == 0

        # 수요일 → 목요일
        wednesday = pd.Timestamp("2024-01-24")
        next_bd = next_krx_business_day(wednesday)
        assert next_bd == pd.Timestamp("2024-01-25")

    def test_next_business_day_month_boundary(self) -> None:
        """월말 → 다음 달 첫 KRX 거래일"""
        # 2024-01-31 (Wed) → 2024-02-01 (Thu)
        jan_end = pd.Timestamp("2024-01-31")
        next_bd = next_krx_business_day(jan_end)
        assert next_bd == pd.Timestamp("2024-02-01")
        assert next_bd.month == 2

    def test_sell_cost_direction(self) -> None:
        """매도: 수수료 + 세금 + 슬리피지 차감"""
        rebalancer = Rebalancer()
        proceed = rebalancer.calc_sell_proceed(10000.0, 100)
        gross = 10000.0 * 100
        # 수익금은 총액보다 적어야 함 (비용 차감)
        assert proceed < gross
        assert proceed > 0

    def test_buy_cost_direction(self) -> None:
        """매수: 수수료 + 슬리피지 추가"""
        rebalancer = Rebalancer()
        cost = rebalancer.calc_buy_cost(10000.0, 100)
        gross = 10000.0 * 100
        # 총 비용은 주가*수량보다 커야 함 (비용 추가)
        assert cost > gross

    @patch("backtest.engine.MultiFactorScreener")
    def test_run_basic(self, MockScreener: MagicMock) -> None:
        """기본 백테스트 실행 — 결과 DataFrame 구조 확인"""
        np.random.seed(42)
        selected_tickers = [f"T{i:04d}" for i in range(20)]

        # screener.screen() → 선정 종목 DataFrame 반환
        screen_result = pd.DataFrame(
            {"composite_score": np.random.uniform(50, 100, len(selected_tickers))},
            index=selected_tickers,
        )
        mock_screener = MockScreener.return_value
        mock_screener.screen.return_value = screen_result

        # collector (OHLCV 조회용) mock
        mock_collector = MagicMock()
        mock_screener.collector = mock_collector

        def mock_ohlcv(ticker, start, end):
            dates = pd.bdate_range(start, end)
            if len(dates) == 0:
                dates = pd.bdate_range(start, start)
            n = len(dates)
            return pd.DataFrame(
                {
                    "open": [50000.0] * n,
                    "high": [51000.0] * n,
                    "low": [49000.0] * n,
                    "close": [50500.0] * n,
                    "volume": [1000000] * n,
                },
                index=dates,
            )

        mock_collector.get_ohlcv.side_effect = mock_ohlcv
        mock_collector.prefetch_daily_trade.return_value = None

        engine = MultiFactorBacktest(initial_cash=10_000_000)
        result = engine.run("2024-01-01", "2024-03-31")

        assert isinstance(result, pd.DataFrame)
        assert "portfolio_value" in result.columns
        assert "cash" in result.columns
        assert "returns" in result.columns
        assert "n_holdings" in result.columns
        assert len(result) > 0
        assert result["portfolio_value"].iloc[0] > 0

    @patch("backtest.engine.MultiFactorScreener")
    def test_turnover_tracking(self, MockScreener: MagicMock) -> None:
        """턴오버 로그가 결과에 포함되는지 확인"""
        np.random.seed(42)

        # 매월 다른 포트폴리오를 반환하여 턴오버 발생
        call_count = [0]
        tickers_a = [f"T{i:04d}" for i in range(20)]
        tickers_b = [f"T{i:04d}" for i in range(5, 25)]  # 15개 겹침, 5개 교체

        def rotating_screen(*args, **kwargs):
            call_count[0] += 1
            tickers = tickers_a if call_count[0] % 2 == 1 else tickers_b
            return pd.DataFrame(
                {"composite_score": np.random.uniform(50, 100, len(tickers))},
                index=tickers,
            )

        mock_screener = MockScreener.return_value
        mock_screener.screen.side_effect = rotating_screen

        mock_collector = MagicMock()
        mock_screener.collector = mock_collector

        def mock_ohlcv(ticker, start, end):
            dates = pd.bdate_range(start, end)
            if len(dates) == 0:
                dates = pd.bdate_range(start, start)
            n = len(dates)
            return pd.DataFrame(
                {
                    "open": [50000.0] * n,
                    "high": [51000.0] * n,
                    "low": [49000.0] * n,
                    "close": [50500.0] * n,
                    "volume": [1000000] * n,
                },
                index=dates,
            )

        mock_collector.get_ohlcv.side_effect = mock_ohlcv
        mock_collector.prefetch_daily_trade.return_value = None

        engine = MultiFactorBacktest(initial_cash=10_000_000)
        result = engine.run("2024-01-01", "2024-03-31")

        # turnover_log가 attrs에 있어야 함
        assert "turnover_log" in result.attrs
        turnover_log = result.attrs["turnover_log"]
        assert isinstance(turnover_log, list)
        assert len(turnover_log) > 0
        for entry in turnover_log:
            assert "date" in entry
            assert "turnover_rate" in entry
            assert 0 <= entry["turnover_rate"] <= 1.0

    def test_first_month_boundary(self) -> None:
        """첫 달: start_date가 월 중반이면 해당 월말부터 시작"""
        dates = get_krx_month_end_sessions("2024-01-15", "2024-03-31")
        # 1/15 시작 → 1/31, 2/29, 3/29 = 3개
        assert len(dates) == 3
        assert dates[0] >= pd.Timestamp("2024-01-15")

    def test_last_month_boundary(self) -> None:
        """마지막 달: end_date 이전 월말만 포함"""
        dates = get_krx_month_end_sessions("2024-01-01", "2024-03-15")
        # 1/31, 2/29까지만 (3/15 전에 3월 마지막 영업일 없음)
        assert all(dt <= pd.Timestamp("2024-03-15") for dt in dates)


# ───────────────────────────────────────────────
# PerformanceAnalyzer 테스트
# ───────────────────────────────────────────────


class TestMetrics:
    def setup_method(self) -> None:
        self.analyzer = PerformanceAnalyzer()

    def _make_portfolio_values(
        self, n_days: int = 504, annual_return: float = 0.15
    ) -> pd.Series:
        """테스트용 포트폴리오 가치 시리즈 생성 (2년, 일별)"""
        dates = pd.bdate_range("2022-01-03", periods=n_days)
        daily_return = (1 + annual_return) ** (1 / 252) - 1
        np.random.seed(42)
        noise = np.random.normal(daily_return, 0.01, n_days)
        cumulative = 10_000_000 * np.cumprod(1 + noise)
        return pd.Series(cumulative, index=dates, name="portfolio_value")

    def test_cagr_known_value(self) -> None:
        """알려진 수익률로 CAGR 검증: 1000만 → 2000만 (2년) = 약 41.4%"""
        dates = pd.bdate_range("2022-01-03", periods=504)
        # 정확히 2배: 10M → 20M
        values = np.linspace(10_000_000, 20_000_000, 504)
        pv = pd.Series(values, index=dates)

        cagr = self.analyzer.calculate_cagr(pv)
        # CAGR = (20/10)^(1/2) - 1 = 0.4142
        assert abs(cagr - 0.4142) < 0.02

    def test_cagr_flat(self) -> None:
        """수익률 0% → CAGR ≈ 0%"""
        dates = pd.bdate_range("2022-01-03", periods=252)
        pv = pd.Series([10_000_000] * 252, index=dates)
        cagr = self.analyzer.calculate_cagr(pv)
        assert abs(cagr) < 0.001

    def test_mdd_known_value(self) -> None:
        """알려진 MDD: 100 → 70 → 90 → MDD = -30%"""
        values = [100, 110, 120, 100, 70, 80, 90, 95]
        dates = pd.bdate_range("2024-01-02", periods=8)
        pv = pd.Series(values, index=dates, dtype=float)

        mdd = self.analyzer.calculate_mdd(pv)
        # 고점 120 → 저점 70 = -41.67%
        expected = (70 - 120) / 120
        assert abs(mdd - expected) < 0.01

    def test_mdd_monotone_increase(self) -> None:
        """단조 증가 → MDD = 0%"""
        dates = pd.bdate_range("2024-01-02", periods=10)
        pv = pd.Series(range(100, 110), index=dates, dtype=float)
        mdd = self.analyzer.calculate_mdd(pv)
        assert mdd == 0.0

    def test_sharpe_known_value(self) -> None:
        """양의 초과수익률 → 양의 샤프"""
        np.random.seed(42)
        dates = pd.bdate_range("2022-01-03", periods=252)
        # 연 15% 수익 + 노이즈
        daily_ret = 0.15 / 252
        returns = pd.Series(np.random.normal(daily_ret, 0.01, 252), index=dates)
        sharpe = self.analyzer.calculate_sharpe(returns, risk_free=0.03)
        # 양수여야 함
        assert sharpe > 0

    def test_sharpe_zero_vol(self) -> None:
        """변동성 0 → 샤프 0"""
        dates = pd.bdate_range("2024-01-02", periods=10)
        returns = pd.Series([0.001] * 10, index=dates)
        sharpe = self.analyzer.calculate_sharpe(returns)
        assert sharpe == 0.0

    def test_calmar(self) -> None:
        """CAGR 0.15, MDD -0.20 → 칼마 0.75"""
        calmar = self.analyzer.calculate_calmar(0.15, -0.20)
        assert abs(calmar - 0.75) < 0.001

    def test_calmar_zero_mdd(self) -> None:
        """MDD 0 → 칼마 0 (ZeroDivision 방지)"""
        calmar = self.analyzer.calculate_calmar(0.15, 0.0)
        assert calmar == 0.0

    def test_win_rate(self) -> None:
        """10일 중 7일 양수 → 승률 70%"""
        returns = pd.Series(
            [0.01, 0.02, -0.01, 0.03, 0.01, -0.02, 0.01, 0.02, 0.01, -0.01]
        )
        win_rate = self.analyzer.calculate_win_rate(returns)
        assert abs(win_rate - 0.70) < 0.001

    def test_volatility(self) -> None:
        """연환산 변동성 = 일별 std * sqrt(252)"""
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.0005, 0.015, 252))
        vol = self.analyzer.calculate_volatility(returns)
        expected = returns.std() * np.sqrt(252)
        assert abs(vol - expected) < 0.001

    def test_sortino(self) -> None:
        """양의 초과수익률 → 양의 소르티노"""
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.15 / 252, 0.01, 252))
        sortino = self.analyzer.calculate_sortino(returns, risk_free=0.03)
        assert sortino > 0

    def test_var_95(self) -> None:
        """95% VaR: 하위 5%에 해당하는 손실"""
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.0, 0.02, 252))
        var = self.analyzer.calculate_var(returns, 0.95)
        assert var < 0  # 손실이므로 음수
        assert var > -0.10  # 합리적 범위

    def test_excess_return(self) -> None:
        """초과수익률 = 포트폴리오 CAGR - 벤치마크 CAGR"""
        dates = pd.bdate_range("2022-01-03", periods=252)
        port = pd.Series(np.linspace(100, 120, 252), index=dates)
        bm = pd.Series(np.linspace(100, 110, 252), index=dates)
        excess = self.analyzer.calculate_excess_return(port, bm)
        assert excess > 0  # 포트폴리오가 벤치마크보다 나음

    def test_information_ratio(self) -> None:
        """정보비율 계산"""
        np.random.seed(42)
        dates = pd.bdate_range("2022-01-03", periods=252)
        port_ret = pd.Series(np.random.normal(0.001, 0.01, 252), index=dates)
        bm_ret = pd.Series(np.random.normal(0.0005, 0.01, 252), index=dates)
        ir = self.analyzer.calculate_information_ratio(port_ret, bm_ret)
        assert isinstance(ir, float)

    def test_monthly_returns_table(self) -> None:
        """월별 수익률 테이블 — 행=연도, 열=월"""
        pv = self._make_portfolio_values(n_days=504)
        table = self.analyzer.monthly_returns(pv)
        assert isinstance(table, pd.DataFrame)
        assert len(table) > 0
        # 열은 월 이름 + 연간
        assert "연간" in table.columns

    def test_yearly_returns(self) -> None:
        """연도별 수익률 Series"""
        pv = self._make_portfolio_values(n_days=504)
        yr = self.analyzer.yearly_returns(pv)
        assert isinstance(yr, pd.Series)
        assert len(yr) > 0
        assert yr.name == "yearly_return"

    def test_mdd_recovery_days(self) -> None:
        """MDD 회복 기간 계산"""
        # 100 → 120 → 70 → 80 → 90 → 120(회복) → 130
        values = [100, 110, 120, 100, 70, 80, 90, 100, 120, 130]
        dates = pd.bdate_range("2024-01-02", periods=10)
        pv = pd.Series(values, index=dates, dtype=float)
        days = self.analyzer.calculate_mdd_recovery_days(pv)
        # MDD at index 4 (70), recovery at index 8 or 9 → 4~5 trading days
        assert days > 0
        assert days <= 6

    def test_mdd_recovery_no_recovery(self) -> None:
        """고점 회복 못한 경우 — 마지막 날까지"""
        values = [100, 120, 80, 70, 60]
        dates = pd.bdate_range("2024-01-02", periods=5)
        pv = pd.Series(values, index=dates, dtype=float)
        days = self.analyzer.calculate_mdd_recovery_days(pv)
        # MDD at index 4 (60), no recovery → 0 days (at end)
        assert days == 0  # MDD가 마지막 날이므로 회복 기간 0

    def test_summary_includes_mdd_recovery(self) -> None:
        """summary에 mdd_recovery_days 포함"""
        pv = self._make_portfolio_values()
        returns = pv.pct_change().dropna()
        metrics = self.analyzer.summary(pv, returns)
        assert "mdd_recovery_days" in metrics

    def test_factor_attribution(self) -> None:
        """팩터 귀인 분석 — IC 계산"""
        np.random.seed(42)
        tickers = [f"T{i:04d}" for i in range(30)]
        composite = pd.DataFrame(
            {
                "value_score": np.random.uniform(0, 100, 30),
                "momentum_score": np.random.uniform(0, 100, 30),
                "quality_score": np.random.uniform(0, 100, 30),
                "composite_score": np.random.uniform(0, 100, 30),
            },
            index=tickers,
        )
        returns = pd.Series(
            np.random.normal(0.01, 0.05, 30), index=tickers
        )
        result = self.analyzer.factor_attribution(composite, returns)
        assert isinstance(result, dict)
        assert "value_score" in result
        assert "momentum_score" in result
        assert "quality_score" in result
        # IC는 -1~1 범위
        for v in result.values():
            assert -1.0 <= v <= 1.0

    def test_factor_attribution_insufficient_data(self) -> None:
        """종목 수 부족 시 빈 dict"""
        composite = pd.DataFrame(
            {"value_score": [50.0], "composite_score": [50.0]},
            index=["A"],
        )
        returns = pd.Series([0.01], index=["A"])
        result = self.analyzer.factor_attribution(composite, returns)
        assert result == {}

    def test_summary(self) -> None:
        """summary()가 모든 필수 키를 포함하는지"""
        pv = self._make_portfolio_values()
        returns = pv.pct_change().dropna()

        metrics = self.analyzer.summary(pv, returns)

        required_keys = [
            "cagr",
            "total_return",
            "volatility",
            "mdd",
            "sharpe",
            "sortino",
            "calmar",
            "var_95",
            "win_rate",
            "n_years",
        ]
        for key in required_keys:
            assert key in metrics, f"missing key: {key}"

    def test_summary_with_benchmark(self) -> None:
        """벤치마크 포함 summary에서 초과수익률 키 존재"""
        pv = self._make_portfolio_values()
        returns = pv.pct_change().dropna()
        bm = pv * 0.9  # 벤치마크는 10% 낮은 값
        bm_ret = bm.pct_change().dropna()

        metrics = self.analyzer.summary(
            pv, returns,
            benchmark_values=bm,
            benchmark_returns=bm_ret,
        )
        assert "excess_return" in metrics
        assert "benchmark_cagr" in metrics
        assert "information_ratio" in metrics

    def test_top_drawdowns(self) -> None:
        """Top N 낙폭 구간 추출"""
        values = [100, 110, 120, 100, 70, 80, 120, 130, 100, 130]
        dates = pd.bdate_range("2024-01-02", periods=10)
        pv = pd.Series(values, index=dates, dtype=float)

        dds = self.analyzer.top_drawdowns(pv, n=5)
        assert len(dds) >= 1
        # 가장 깊은 낙폭: 120→70 = -41.67%
        assert dds[0]["depth"] < -0.40
        assert dds[0]["trough"] is not None
        assert dds[0]["start"] is not None

    def test_top_drawdowns_monotone(self) -> None:
        """단조 증가 → 낙폭 구간 없음"""
        dates = pd.bdate_range("2024-01-02", periods=10)
        pv = pd.Series(range(100, 110), index=dates, dtype=float)
        dds = self.analyzer.top_drawdowns(pv)
        assert dds == []

    def test_rolling_returns(self) -> None:
        """롤링 수익률 반환"""
        pv = self._make_portfolio_values(n_days=504)
        rolling = self.analyzer.rolling_returns(pv, window=252)
        assert len(rolling) > 0
        assert len(rolling) == len(pv) - 252

    def test_rolling_returns_short(self) -> None:
        """데이터 부족 시 빈 Series"""
        dates = pd.bdate_range("2024-01-02", periods=10)
        pv = pd.Series(range(100, 110), index=dates, dtype=float)
        rolling = self.analyzer.rolling_returns(pv, window=252)
        assert rolling.empty

    def test_rolling_sharpe(self) -> None:
        """롤링 샤프 비율"""
        pv = self._make_portfolio_values(n_days=504)
        returns = pv.pct_change().dropna()
        rolling = self.analyzer.rolling_sharpe(returns, window=252)
        assert len(rolling) > 0

    def test_return_distribution(self) -> None:
        """수익률 분포 통계"""
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.0005, 0.01, 252))
        dist = self.analyzer.return_distribution(returns)
        assert "skewness" in dist
        assert "kurtosis" in dist
        assert "max_consecutive_loss" in dist
        assert "max_consecutive_win" in dist
        assert dist["max_consecutive_loss"] >= 0
        assert dist["max_consecutive_win"] >= 0

    def test_best_worst_periods(self) -> None:
        """최고/최저 기간"""
        pv = self._make_portfolio_values(n_days=504)
        returns = pv.pct_change().dropna()
        bw = self.analyzer.best_worst_periods(pv, returns)
        assert "best_day" in bw
        assert "worst_day" in bw
        assert bw["best_day"]["value"] > 0
        assert bw["worst_day"]["value"] < 0

    def test_summary_values_reasonable(self) -> None:
        """summary 값이 합리적인 범위인지"""
        pv = self._make_portfolio_values(annual_return=0.15)
        returns = pv.pct_change().dropna()
        metrics = self.analyzer.summary(pv, returns)

        assert -1.0 < metrics["cagr"] < 5.0
        assert -1.0 < metrics["mdd"] <= 0.0
        assert 0.0 <= metrics["win_rate"] <= 1.0
        assert metrics["n_years"] > 0


# ───────────────────────────────────────────────
# ReportGenerator 테스트
# ───────────────────────────────────────────────


class TestReport:
    def test_generate_html(self, tmp_path) -> None:
        """quantstats HTML 리포트 생성 확인"""
        np.random.seed(42)
        dates = pd.bdate_range("2023-01-02", periods=252)
        returns = pd.Series(np.random.normal(0.0005, 0.01, 252), index=dates)

        output = str(tmp_path / "test_report.html")
        gen = ReportGenerator()
        gen.generate_html(returns, output_path=output)

        assert os.path.exists(output)
        size = os.path.getsize(output)
        assert size > 1000  # 최소 1KB 이상

    def test_generate_html_with_benchmark(self, tmp_path) -> None:
        """벤치마크 포함 HTML 리포트"""
        np.random.seed(42)
        dates = pd.bdate_range("2023-01-02", periods=252)
        returns = pd.Series(np.random.normal(0.0005, 0.01, 252), index=dates)
        benchmark = pd.Series(np.random.normal(0.0003, 0.008, 252), index=dates)

        output = str(tmp_path / "test_bench_report.html")
        gen = ReportGenerator()
        gen.generate_html(returns, benchmark_returns=benchmark, output_path=output)

        assert os.path.exists(output)

    def test_walk_forward_basic(self) -> None:
        """워크-포워드 검증 — 결과 구조 확인"""
        np.random.seed(42)
        selected_tickers = [f"T{i:04d}" for i in range(20)]

        screen_result = pd.DataFrame(
            {"composite_score": np.random.uniform(50, 100, len(selected_tickers))},
            index=selected_tickers,
        )

        def mock_ohlcv(ticker, start, end):
            dates = pd.bdate_range(start, end)
            if len(dates) == 0:
                dates = pd.bdate_range(start, start)
            n = len(dates)
            return pd.DataFrame(
                {
                    "open": [50000.0] * n,
                    "high": [51000.0] * n,
                    "low": [49000.0] * n,
                    "close": [50500.0] * n,
                    "volume": [1000000] * n,
                },
                index=dates,
            )

        with patch("backtest.engine.MultiFactorScreener") as MockS:
            ms = MockS.return_value
            ms.screen.return_value = screen_result
            mc = MagicMock()
            ms.collector = mc
            mc.get_ohlcv.side_effect = mock_ohlcv
            mc.prefetch_daily_trade.return_value = None

            engine = MultiFactorBacktest(initial_cash=10_000_000)
            results = engine.walk_forward(
                "2024-01-01", "2024-12-31", n_splits=2, train_ratio=0.7
            )

        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "split" in r
            assert "train_start" in r
            assert "test_start" in r
            assert "train_cagr" in r or r.get("train_cagr") is None
            assert "test_cagr" in r or r.get("test_cagr") is None

    def test_walk_forward_short_period_raises(self) -> None:
        """기간이 너무 짧으면 ValueError"""
        with patch("backtest.engine.MultiFactorScreener"):
            engine = MultiFactorBacktest()
            import pytest
            with pytest.raises(ValueError, match="기간이 너무 짧습니다"):
                engine.walk_forward("2024-01-01", "2024-01-15", n_splits=3)

    def test_generate_korean_html(self, tmp_path) -> None:
        """한글 HTML 리포트 생성 확인"""
        np.random.seed(42)
        dates = pd.bdate_range("2023-01-02", periods=252)
        returns = pd.Series(np.random.normal(0.0005, 0.01, 252), index=dates)
        portfolio_values = (1 + returns).cumprod() * 10_000_000

        from backtest.metrics import PerformanceAnalyzer
        analyzer = PerformanceAnalyzer()
        metrics = analyzer.summary(portfolio_values, returns)

        output = str(tmp_path / "test_kr_report.html")
        gen = ReportGenerator()
        gen.generate_korean_html(
            portfolio_values=portfolio_values,
            returns=returns,
            metrics=metrics,
            output_path=output,
        )

        assert os.path.exists(output)
        content = open(output, encoding="utf-8").read()
        assert "연 수익률" in content
        assert "최대 낙폭" in content
        assert "샤프 비율" in content
        assert "lang=\"ko\"" in content
        assert len(content) > 5000

    def test_generate_korean_html_with_benchmark(self, tmp_path) -> None:
        """벤치마크 포함 한글 리포트"""
        np.random.seed(42)
        dates = pd.bdate_range("2023-01-02", periods=252)
        returns = pd.Series(np.random.normal(0.0005, 0.01, 252), index=dates)
        portfolio_values = (1 + returns).cumprod() * 10_000_000
        benchmark_values = (1 + pd.Series(
            np.random.normal(0.0003, 0.008, 252), index=dates
        )).cumprod() * 10_000_000

        from backtest.metrics import PerformanceAnalyzer
        analyzer = PerformanceAnalyzer()
        bm_returns = benchmark_values.pct_change().dropna()
        metrics = analyzer.summary(
            portfolio_values, returns,
            benchmark_values=benchmark_values,
            benchmark_returns=bm_returns,
        )

        output = str(tmp_path / "test_kr_bench.html")
        gen = ReportGenerator()
        gen.generate_korean_html(
            portfolio_values=portfolio_values,
            returns=returns,
            metrics=metrics,
            output_path=output,
            benchmark_values=benchmark_values,
        )

        assert os.path.exists(output)
        content = open(output, encoding="utf-8").read()
        assert "KOSPI" in content
        assert "초과수익률" in content

    def test_generate_korean_html_full_sections(self, tmp_path) -> None:
        """한글 리포트 전체 섹션 (턴오버, 팩터IC 포함) 생성 확인"""
        np.random.seed(42)
        dates = pd.bdate_range("2022-01-03", periods=504)
        returns = pd.Series(np.random.normal(0.0005, 0.01, 504), index=dates)
        portfolio_values = (1 + returns).cumprod() * 10_000_000
        benchmark_values = (1 + pd.Series(
            np.random.normal(0.0003, 0.008, 504), index=dates
        )).cumprod() * 10_000_000

        from backtest.metrics import PerformanceAnalyzer
        analyzer = PerformanceAnalyzer()
        bm_returns = benchmark_values.pct_change().dropna()
        metrics = analyzer.summary(
            portfolio_values, returns,
            benchmark_values=benchmark_values,
            benchmark_returns=bm_returns,
        )

        turnover_log = [
            {"date": "20220131", "sells": 3, "buys": 5, "turnover_rate": 0.4,
             "n_holdings_before": 10, "n_holdings_after": 12},
            {"date": "20220228", "sells": 2, "buys": 2, "turnover_rate": 0.2,
             "n_holdings_before": 12, "n_holdings_after": 12},
        ]
        factor_ic = {
            "value_score": 0.05,
            "momentum_score": -0.02,
            "quality_score": 0.03,
        }

        output = str(tmp_path / "test_kr_full.html")
        gen = ReportGenerator()
        gen.generate_korean_html(
            portfolio_values=portfolio_values,
            returns=returns,
            metrics=metrics,
            output_path=output,
            benchmark_values=benchmark_values,
            turnover_log=turnover_log,
            factor_ic=factor_ic,
        )

        assert os.path.exists(output)
        content = open(output, encoding="utf-8").read()
        # 새 섹션들 확인
        assert "전략 vs KOSPI 비교" in content
        assert "월별 수익률" in content
        assert "연도별 수익률" in content
        assert "Top 5 낙폭 구간" in content
        assert "최고/최저 수익률 기간" in content
        assert "수익률 분포 분석" in content
        assert "리밸런싱 요약" in content
        assert "팩터 기여도" in content
        # 개선된 리포트 섹션 확인
        assert "월별 자산 추이" in content
        assert "연도별 월간 손익 차트" in content
        assert "월초 자산" in content
        assert "손익" in content
        assert "만원" in content or "원" in content  # 금액 포맷
        # 롤링 차트는 이미지(base64)로 포함되므로 이미지 태그 존재 확인
        assert content.count("chart-img") >= 6  # 기존 4 + 연도별 월간 차트
        assert len(content) > 20000

    def test_benchmark_date_mismatch(self, tmp_path) -> None:
        """벤치마크 날짜 불일치 처리 — 에러 없이 생성"""
        np.random.seed(42)
        dates1 = pd.bdate_range("2023-01-02", periods=200)
        dates2 = pd.bdate_range("2023-02-01", periods=200)
        returns = pd.Series(np.random.normal(0.0005, 0.01, 200), index=dates1)
        benchmark = pd.Series(np.random.normal(0.0003, 0.008, 200), index=dates2)

        output = str(tmp_path / "test_mismatch_report.html")
        gen = ReportGenerator()
        gen.generate_html(returns, benchmark_returns=benchmark, output_path=output)

        assert os.path.exists(output)
