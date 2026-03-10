# tests/test_backtest.py
import pandas as pd
import numpy as np
import os
from unittest.mock import patch, MagicMock

from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from backtest.report import ReportGenerator
from strategy.rebalancer import Rebalancer

# ───────────────────────────────────────────────
# Rebalancer 테스트
# ───────────────────────────────────────────────


class TestRebalancer:
    def setup_method(self) -> None:
        self.rebalancer = Rebalancer()

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


# ───────────────────────────────────────────────
# Engine 테스트
# ───────────────────────────────────────────────


class TestEngine:
    def test_rebalance_dates_are_business_month_ends(self) -> None:
        """리밸런싱 날짜가 실제 월말 영업일인지 확인"""
        engine = MultiFactorBacktest.__new__(MultiFactorBacktest)
        dates = engine._get_rebalance_dates("2024-01-01", "2024-06-30")

        assert len(dates) == 6  # Jan~Jun 각 1회
        for dt in dates:
            # 영업일인지 (weekday < 5)
            assert dt.weekday() < 5, f"{dt} is not a business day"
            # 해당 월의 마지막 영업일인지
            next_bday = dt + pd.offsets.BDay(1)
            assert (
                next_bday.month != dt.month
            ), f"{dt} is not the last business day of the month"

    def test_rebalance_dates_no_bme_freq(self) -> None:
        """freq='BME' 미사용 확인 (deprecated) — pd.offsets.BMonthEnd() 사용"""
        import inspect

        source = inspect.getsource(MultiFactorBacktest._get_rebalance_dates)
        assert "BME" not in source, "freq='BME' 사용 금지 — pd.offsets.BMonthEnd() 사용"
        assert "BMonthEnd" in source

    def test_next_business_day(self) -> None:
        """T+1 체결이 정확히 다음 영업일인지 확인"""
        engine = MultiFactorBacktest.__new__(MultiFactorBacktest)

        # 금요일 → 다음 월요일
        friday = pd.Timestamp("2024-01-26")  # Friday
        next_bd = engine._next_business_day(friday)
        assert next_bd == pd.Timestamp("2024-01-29")  # Monday
        assert next_bd.weekday() == 0

        # 수요일 → 목요일
        wednesday = pd.Timestamp("2024-01-24")
        next_bd = engine._next_business_day(wednesday)
        assert next_bd == pd.Timestamp("2024-01-25")

    def test_next_business_day_month_boundary(self) -> None:
        """월말 → 다음 달 첫 영업일"""
        engine = MultiFactorBacktest.__new__(MultiFactorBacktest)
        # 2024-01-31 (Wed) → 2024-02-01 (Thu)
        jan_end = pd.Timestamp("2024-01-31")
        next_bd = engine._next_business_day(jan_end)
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

    @patch("backtest.engine.KRXDataCollector")
    @patch("backtest.engine.ReturnCalculator")
    def test_run_basic(
        self, MockReturnCalc: MagicMock, MockCollector: MagicMock
    ) -> None:
        """기본 백테스트 실행 — 결과 DataFrame 구조 확인"""
        np.random.seed(42)
        n_tickers = 50
        tickers = [f"T{i:04d}" for i in range(n_tickers)]

        # Mock 기본 지표
        fundamentals = pd.DataFrame(
            {
                "BPS": np.random.uniform(10000, 100000, n_tickers),
                "PER": np.random.uniform(3, 30, n_tickers),
                "PBR": np.random.uniform(0.3, 5.0, n_tickers),
                "EPS": np.random.uniform(1000, 20000, n_tickers),
                "DIV": np.random.uniform(0, 5, n_tickers),
            },
            index=tickers,
        )

        market_cap_df = pd.DataFrame(
            {
                "market_cap": np.random.uniform(1e10, 1e14, n_tickers),
                "shares": np.random.randint(1000000, 100000000, n_tickers),
            },
            index=tickers,
        )

        # Mock OHLCV (시가 = 50000, 종가 = 50500 고정)
        def mock_ohlcv(start, end, ticker_or_market=None):
            if ticker_or_market is None:
                return pd.DataFrame()
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

        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = fundamentals
        mock_collector.get_market_cap.return_value = market_cap_df
        mock_collector.get_ohlcv.side_effect = lambda t, s, e: mock_ohlcv(s, e, t)

        # Mock 수익률
        returns = pd.Series(
            np.random.uniform(-0.2, 0.5, n_tickers),
            index=tickers,
            name="return_12m",
        )
        mock_return_calc = MockReturnCalc.return_value
        mock_return_calc.get_returns_for_universe.return_value = returns

        engine = MultiFactorBacktest(initial_cash=10_000_000)
        result = engine.run("2024-01-01", "2024-03-31")

        assert isinstance(result, pd.DataFrame)
        assert "portfolio_value" in result.columns
        assert "cash" in result.columns
        assert "returns" in result.columns
        assert "n_holdings" in result.columns
        assert len(result) > 0
        # 첫 포트폴리오 가치는 초기 자금 근처
        assert result["portfolio_value"].iloc[0] > 0

    def test_first_month_boundary(self) -> None:
        """첫 달: start_date가 월 중반이면 해당 월말부터 시작"""
        engine = MultiFactorBacktest.__new__(MultiFactorBacktest)
        dates = engine._get_rebalance_dates("2024-01-15", "2024-03-31")
        # 1/15 시작 → 1/31(수), 2/29(목), 3/29(금) = 3개
        assert len(dates) == 3
        assert dates[0] >= pd.Timestamp("2024-01-15")

    def test_last_month_boundary(self) -> None:
        """마지막 달: end_date 이전 월말만 포함"""
        engine = MultiFactorBacktest.__new__(MultiFactorBacktest)
        dates = engine._get_rebalance_dates("2024-01-01", "2024-03-15")
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
            "calmar",
            "win_rate",
            "n_years",
        ]
        for key in required_keys:
            assert key in metrics, f"missing key: {key}"

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
