# tests/test_collector.py
import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from data.collector import (
    KRXDataCollector,
    ReturnCalculator,
    OHLCV_COLUMNS,
    FUNDAMENTAL_COLUMNS,
    MARKET_CAP_COLUMNS,
    retry_on_failure,
)


# ───────────────────────────────────────────────
# retry_on_failure 데코레이터 테스트
# ───────────────────────────────────────────────

class TestRetryOnFailure:
    def test_success_on_first_try(self) -> None:
        call_count = 0

        @retry_on_failure(max_retries=3, base_delay=0.01)
        def success_func() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        assert success_func() == "ok"
        assert call_count == 1

    def test_success_after_retries(self) -> None:
        call_count = 0

        @retry_on_failure(max_retries=3, base_delay=0.01)
        def flaky_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("임시 오류")
            return "ok"

        assert flaky_func() == "ok"
        assert call_count == 3

    def test_failure_after_all_retries(self) -> None:
        @retry_on_failure(max_retries=2, base_delay=0.01)
        def always_fail() -> None:
            raise ValueError("항상 실패")

        with pytest.raises(ValueError, match="항상 실패"):
            always_fail()


# ───────────────────────────────────────────────
# KRXDataCollector 테스트
# ───────────────────────────────────────────────

class TestKRXDataCollector:
    def setup_method(self) -> None:
        self.collector = KRXDataCollector(request_delay=0.0)

    @patch("data.collector.stock")
    def test_get_universe(self, mock_stock: MagicMock) -> None:
        mock_stock.get_market_ticker_list.return_value = ["005930", "000660"]
        mock_stock.get_market_ticker_name.side_effect = ["삼성전자", "SK하이닉스"]

        df = self.collector.get_universe("20240102", market="KOSPI")

        assert len(df) == 2
        assert list(df.columns) == ["ticker", "name", "market"]
        assert df.iloc[0]["ticker"] == "005930"
        assert df.iloc[0]["name"] == "삼성전자"

    @patch("data.collector.stock")
    def test_get_universe_empty(self, mock_stock: MagicMock) -> None:
        mock_stock.get_market_ticker_list.return_value = []
        df = self.collector.get_universe("20240102")
        assert df.empty

    @patch("data.collector.stock")
    def test_get_ohlcv(self, mock_stock: MagicMock) -> None:
        mock_data = pd.DataFrame(
            {
                "시가": [70000, 71000],
                "고가": [72000, 73000],
                "저가": [69000, 70000],
                "종가": [71000, 72000],
                "거래량": [1000000, 1200000],
                "거래대금": [71000000000, 86400000000],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )
        mock_stock.get_market_ohlcv.return_value = mock_data

        df = self.collector.get_ohlcv("005930", "20240102", "20240103")

        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df.index.name == "date"
        assert df.iloc[0]["close"] == 71000

    @patch("data.collector.stock")
    def test_get_ohlcv_empty(self, mock_stock: MagicMock) -> None:
        mock_stock.get_market_ohlcv.return_value = pd.DataFrame()
        df = self.collector.get_ohlcv("005930", "20240102", "20240103")
        assert df.empty

    @patch("data.collector.stock")
    def test_get_fundamentals_all(self, mock_stock: MagicMock) -> None:
        mock_data = pd.DataFrame(
            {
                "BPS": [50000, 80000],
                "PER": [12.5, 8.3],
                "PBR": [1.4, 0.9],
                "EPS": [5600, 9600],
                "DIV": [1.8, 2.5],
            },
            index=["005930", "000660"],
        )
        mock_stock.get_market_fundamental.return_value = mock_data

        df = self.collector.get_fundamentals_all("20240102", "KOSPI")

        assert list(df.columns) == ["BPS", "PER", "PBR", "EPS", "DIV"]
        assert df.index.name == "ticker"
        assert len(df) == 2

    @patch("data.collector.stock")
    def test_get_market_cap(self, mock_stock: MagicMock) -> None:
        mock_data = pd.DataFrame(
            {
                "시가총액": [400_000_000_000_000, 80_000_000_000_000],
                "상장주식수": [5_969_782_550, 728_002_365],
                "거래량": [10000000, 5000000],
                "거래대금": [710000000000, 640000000000],
            },
            index=["005930", "000660"],
        )
        mock_stock.get_market_cap.return_value = mock_data

        df = self.collector.get_market_cap("20240102", "KOSPI")

        assert "market_cap" in df.columns
        assert "shares" in df.columns
        assert df.index.name == "ticker"


# ───────────────────────────────────────────────
# ReturnCalculator 테스트
# ───────────────────────────────────────────────

class TestReturnCalculator:
    @patch("data.collector.stock")
    def test_get_momentum_return(self, mock_stock: MagicMock) -> None:
        # 12개월 전 종가 50000 → 1개월 전 종가 60000 → 수익률 0.2
        dates = pd.date_range("2023-01-02", periods=220, freq="B")
        prices = np.linspace(50000, 60000, len(dates))
        mock_data = pd.DataFrame(
            {
                "시가": prices,
                "고가": prices + 500,
                "저가": prices - 500,
                "종가": prices,
                "거래량": [1000000] * len(dates),
                "거래대금": [50000000000] * len(dates),
            },
            index=dates,
        )
        mock_stock.get_market_ohlcv.return_value = mock_data

        calc = ReturnCalculator(request_delay=0.0)
        ret = calc.get_momentum_return("005930", "20240102", lookback_months=12, skip_months=1)

        assert ret is not None
        assert abs(ret - 0.2) < 0.01

    @patch("data.collector.stock")
    def test_get_momentum_return_insufficient_data(self, mock_stock: MagicMock) -> None:
        mock_stock.get_market_ohlcv.return_value = pd.DataFrame()

        calc = ReturnCalculator(request_delay=0.0)
        ret = calc.get_momentum_return("005930", "20240102")

        assert ret is None

    @patch("data.collector.stock")
    def test_get_returns_for_universe(self, mock_stock: MagicMock) -> None:
        dates = pd.date_range("2023-01-02", periods=220, freq="B")
        prices = np.linspace(50000, 60000, len(dates))
        mock_data = pd.DataFrame(
            {
                "시가": prices,
                "고가": prices + 500,
                "저가": prices - 500,
                "종가": prices,
                "거래량": [1000000] * len(dates),
                "거래대금": [50000000000] * len(dates),
            },
            index=dates,
        )
        mock_stock.get_market_ohlcv.return_value = mock_data

        calc = ReturnCalculator(request_delay=0.0)
        series = calc.get_returns_for_universe(
            ["005930", "000660"], "20240102"
        )

        assert len(series) == 2
        assert all(v > 0 for v in series.values)
