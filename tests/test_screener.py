# tests/test_screener.py
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from strategy.screener import MultiFactorScreener


class TestReportingLag:
    """Reporting Lag 처리 테스트"""

    def test_jan_uses_two_years_ago(self) -> None:
        """1~3월 리밸런싱 → 전전년도 12월 마지막 거래일"""
        result = MultiFactorScreener._get_effective_fundamental_date("20240229")
        assert result.startswith("2022")
        assert result >= "20221226"  # 12월 마지막 거래일은 26~30일 사이

    def test_apr_uses_prev_year(self) -> None:
        """4월 리밸런싱 → 전년도 12월 마지막 거래일"""
        result = MultiFactorScreener._get_effective_fundamental_date("20240430")
        assert result.startswith("2023")

    def test_dec_uses_prev_year(self) -> None:
        """12월 리밸런싱 → 전년도 12월 마지막 거래일"""
        result = MultiFactorScreener._get_effective_fundamental_date("20241231")
        assert result.startswith("2023")

    def test_jun_uses_prev_year(self) -> None:
        """6월 리밸런싱 → 전년도 12월 마지막 거래일"""
        result = MultiFactorScreener._get_effective_fundamental_date("20240628")
        assert result.startswith("2023")


class TestMultiFactorScreener:
    """MultiFactorScreener 통합 테스트 (mock 기반)"""

    def setup_method(self) -> None:
        """테스트 간 팩터 캐시 오염 방지"""
        MultiFactorScreener._factor_cache.clear()

    def _make_fundamentals(self, n: int = 50) -> pd.DataFrame:
        """테스트용 기본 지표 DataFrame 생성"""
        np.random.seed(42)
        tickers = [f"T{i:04d}" for i in range(n)]
        return pd.DataFrame(
            {
                "BPS": np.random.uniform(10000, 100000, n),
                "PER": np.random.uniform(3, 30, n),
                "PBR": np.random.uniform(0.3, 5.0, n),
                "EPS": np.random.uniform(1000, 20000, n),
                "DIV": np.random.uniform(0, 5, n),
            },
            index=tickers,
        )

    def _make_market_cap(self, tickers: list[str]) -> pd.DataFrame:
        """테스트용 시가총액 DataFrame 생성"""
        np.random.seed(42)
        n = len(tickers)
        return pd.DataFrame(
            {
                "market_cap": np.random.uniform(1e10, 1e14, n),
                "shares": np.random.randint(1000000, 100000000, n),
            },
            index=tickers,
        )

    def _make_returns(self, tickers: list[str]) -> pd.Series:
        """테스트용 수익률 Series 생성"""
        np.random.seed(42)
        return pd.Series(
            np.random.uniform(-0.3, 0.5, len(tickers)),
            index=tickers,
            name="return_12m",
        )

    def _make_avg_trading_value(self, tickers: list[str]) -> pd.Series:
        """테스트용 평균 거래대금 Series (모두 유동성 통과)"""
        np.random.seed(42)
        return pd.Series(
            np.random.uniform(1e8, 1e10, len(tickers)),
            index=tickers,
        )

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_all_market(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        """ALL 시장 — KOSPI+KOSDAQ 각각 호출 확인"""
        fundamentals = self._make_fundamentals(50)
        tickers = fundamentals.index.tolist()
        market_cap = self._make_market_cap(tickers)

        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = fundamentals
        mock_collector.get_market_cap.return_value = market_cap
        mock_collector.get_avg_trading_value.return_value = (
            self._make_avg_trading_value(tickers)
        )
        mock_collector.get_suspended_tickers.return_value = set()

        mock_return_calc = MockReturnCalc.return_value
        mock_return_calc.get_returns_for_universe.return_value = self._make_returns(
            tickers
        )

        screener = MultiFactorScreener()
        result = screener.screen("20240102", market="ALL", n_stocks=10)

        # ALL 모드에서 KOSPI, KOSDAQ 2번 호출
        assert mock_collector.get_fundamentals_all.call_count == 2
        assert mock_collector.get_market_cap.call_count == 2
        assert len(result) <= 10

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_basic(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        fundamentals = self._make_fundamentals(50)
        tickers = fundamentals.index.tolist()
        market_cap = self._make_market_cap(tickers)

        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = fundamentals
        mock_collector.get_market_cap.return_value = market_cap
        mock_collector.get_avg_trading_value.return_value = (
            self._make_avg_trading_value(tickers)
        )
        mock_collector.get_suspended_tickers.return_value = set()

        mock_return_calc = MockReturnCalc.return_value
        mock_return_calc.get_returns_for_universe.return_value = self._make_returns(
            tickers
        )

        screener = MultiFactorScreener()
        result = screener.screen("20240102", n_stocks=10)

        assert len(result) <= 10
        assert "composite_score" in result.columns
        assert "weight" in result.columns
        if len(result) > 0:
            assert abs(result["weight"].sum() - 1.0) < 0.01

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_empty_fundamentals(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = pd.DataFrame()

        screener = MultiFactorScreener()
        result = screener.screen("20240102")

        assert result.empty

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_with_finance_filter(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        fundamentals = self._make_fundamentals(50)
        tickers = fundamentals.index.tolist()
        market_cap = self._make_market_cap(tickers)

        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.return_value = fundamentals
        mock_collector.get_market_cap.return_value = market_cap
        mock_collector.get_avg_trading_value.return_value = (
            self._make_avg_trading_value(tickers)
        )
        mock_collector.get_suspended_tickers.return_value = set()

        mock_return_calc = MockReturnCalc.return_value
        mock_return_calc.get_returns_for_universe.return_value = self._make_returns(
            tickers
        )

        screener = MultiFactorScreener()
        finance = tickers[:5]
        result = screener.screen("20240102", finance_tickers=finance, n_stocks=30)

        # 금융주로 지정된 종목은 결과에 포함되지 않아야 함
        for t in finance:
            assert t not in result.index

    @patch("strategy.screener.ReturnCalculator")
    @patch("strategy.screener.KRXDataCollector")
    def test_screen_exception_handling(
        self, MockCollector: MagicMock, MockReturnCalc: MagicMock
    ) -> None:
        """예외 발생 시 빈 DataFrame 반환"""
        mock_collector = MockCollector.return_value
        mock_collector.get_fundamentals_all.side_effect = Exception("네트워크 오류")

        screener = MultiFactorScreener()
        result = screener.screen("20240102")

        assert result.empty
