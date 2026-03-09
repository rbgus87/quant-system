# tests/test_backtest.py
import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch, MagicMock
from datetime import date

from backtest.engine import MultiFactorBacktest
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
            assert next_bday.month != dt.month, f"{dt} is not the last business day of the month"

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

        # Mock OHLCV (시가 = 50000 고정)
        def mock_ohlcv(start, end, ticker_or_market=None):
            if ticker_or_market is None:
                return pd.DataFrame()
            dates = pd.bdate_range(start, end)
            if len(dates) == 0:
                dates = pd.bdate_range(start, start)
            n = len(dates)
            return pd.DataFrame(
                {
                    "시가": [50000.0] * n,
                    "고가": [51000.0] * n,
                    "저가": [49000.0] * n,
                    "종가": [50500.0] * n,
                    "거래량": [1000000] * n,
                    "거래대금": [50000000000] * n,
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
