# tests/test_collector.py
import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from data.collector import (
    KRXDataCollector,
    ReturnCalculator,
    retry_on_failure,
)
from data.storage import DataStorage

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
        # 테스트마다 독립적인 in-memory DB 사용
        self.collector.storage = DataStorage(db_path=":memory:")
        # KRX/DART API 비활성화 (pykrx mock만 사용)
        self.collector._krx_api = None
        self.collector._krx_api_checked = True
        self.collector._dart_client = None
        self.collector._dart_client_checked = True

    def test_get_universe(self) -> None:
        """KRX API mock으로 유니버스 조회 테스트"""
        mock_api = MagicMock()
        mock_api.get_stock_daily_trade.return_value = {
            "OutBlock_1": [
                {"ISU_CD": "005930", "ISU_NM": "삼성전자"},
                {"ISU_CD": "000660", "ISU_NM": "SK하이닉스"},
            ]
        }
        self.collector._krx_api = mock_api
        self.collector._krx_api_checked = True

        df = self.collector.get_universe("20240102", market="KOSPI")

        assert len(df) == 2
        assert list(df.columns) == ["ticker", "name", "market"]
        assert df.iloc[0]["ticker"] == "005930"
        assert df.iloc[0]["name"] == "삼성전자"

    def test_get_universe_empty(self) -> None:
        """KRX API 없으면 빈 결과 반환"""
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
    def test_get_ohlcv_cache_hit(self, mock_stock: MagicMock) -> None:
        """캐시에 데이터가 있으면 pykrx 호출 없이 캐시에서 반환"""
        mock_data = pd.DataFrame(
            {
                "시가": [70000],
                "고가": [72000],
                "저가": [69000],
                "종가": [71000],
                "거래량": [1000000],
                "거래대금": [71000000000],
            },
            index=pd.to_datetime(["2024-01-02"]),
        )
        mock_stock.get_market_ohlcv.return_value = mock_data

        # 첫 번째 호출: API → 캐시 저장
        self.collector.get_ohlcv("005930", "20240102", "20240102")
        # 두 번째 호출: 캐시 히트
        df = self.collector.get_ohlcv("005930", "20240102", "20240102")

        assert not df.empty
        # pykrx는 첫 호출에서만 호출됨
        assert mock_stock.get_market_ohlcv.call_count == 1

    def test_get_fundamentals_all_cache(self) -> None:
        """DB 캐시에 데이터를 넣고 캐시 히트 확인"""
        from datetime import date

        fund_df = pd.DataFrame(
            {
                "BPS": [50000, 80000],
                "PER": [12.5, 8.3],
                "PBR": [1.4, 0.9],
                "EPS": [5600, 9600],
                "DIV": [1.8, 2.5],
            },
            index=["005930", "000660"],
        )
        fund_df.index.name = "ticker"
        self.collector.storage.save_fundamentals(date(2024, 1, 2), fund_df)

        df = self.collector.get_fundamentals_all("20240102", "KOSPI")

        # v2.0 확장 필드 포함 (PSR, REVENUE, OPERATING_INCOME, TOTAL_ASSETS, OPA, DATA_SOURCE)
        required_cols = {"BPS", "PER", "PBR", "PCR", "EPS", "DIV"}
        assert required_cols.issubset(set(df.columns))
        assert df.index.name == "ticker"
        assert len(df) == 2

    def test_get_fundamentals_all_empty(self) -> None:
        """캐시도 API도 없으면 빈 결과"""
        df = self.collector.get_fundamentals_all("20240102", "KOSPI")
        assert df.empty

    def test_get_market_cap_cache(self) -> None:
        """DB 캐시에서 시가총액 조회"""
        from datetime import date

        cap_df = pd.DataFrame(
            {
                "market_cap": [400_000_000_000_000, 80_000_000_000_000],
                "shares": [5_969_782_550, 728_002_365],
            },
            index=["005930", "000660"],
        )
        cap_df.index.name = "ticker"
        self.collector.storage.save_market_caps(date(2024, 1, 2), cap_df)

        df = self.collector.get_market_cap("20240102", "KOSPI")

        assert "market_cap" in df.columns
        assert "shares" in df.columns
        assert df.index.name == "ticker"
        assert len(df) == 2

    def test_get_market_cap_empty(self) -> None:
        """캐시도 API도 없으면 빈 결과"""
        df = self.collector.get_market_cap("20240102", "KOSPI")
        assert df.empty


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
        calc.collector.storage = DataStorage(db_path=":memory:")
        ret = calc.get_momentum_return(
            "005930", "20240102", lookback_months=12, skip_months=1
        )

        assert ret is not None
        assert abs(ret - 0.2) < 0.01

    @patch("data.collector.stock")
    def test_get_momentum_return_insufficient_data(self, mock_stock: MagicMock) -> None:
        mock_stock.get_market_ohlcv.return_value = pd.DataFrame()

        calc = ReturnCalculator(request_delay=0.0)
        calc.collector.storage = DataStorage(db_path=":memory:")
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
        calc.collector.storage = DataStorage(db_path=":memory:")
        series = calc.get_returns_for_universe(["005930", "000660"], "20240102")

        assert len(series) == 2
        assert all(v > 0 for v in series.values)
