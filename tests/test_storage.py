# tests/test_storage.py
import pandas as pd
import numpy as np
import pytest
import os
import tempfile
from datetime import date

from data.storage import DataStorage, Base, DailyPrice, Fundamental, FactorScore, Portfolio, Trade


@pytest.fixture
def storage(tmp_path) -> DataStorage:
    """임시 SQLite DB로 DataStorage 생성"""
    db_path = str(tmp_path / "test_quant.db")
    return DataStorage(db_path=db_path)


# ───────────────────────────────────────────────
# DailyPrice 테스트
# ───────────────────────────────────────────────

class TestDailyPrice:
    def test_save_and_load(self, storage: DataStorage) -> None:
        df = pd.DataFrame(
            {
                "open": [70000.0, 71000.0],
                "high": [72000.0, 73000.0],
                "low": [69000.0, 70000.0],
                "close": [71000.0, 72000.0],
                "volume": [1000000, 1200000],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )
        df.index.name = "date"

        count = storage.save_daily_prices("005930", df)
        assert count == 2

        loaded = storage.load_daily_prices("005930")
        assert len(loaded) == 2
        assert loaded.iloc[0]["close"] == 71000.0

    def test_upsert(self, storage: DataStorage) -> None:
        """같은 ticker+date로 저장하면 업데이트"""
        df1 = pd.DataFrame(
            {"open": [70000.0], "high": [72000.0], "low": [69000.0], "close": [71000.0], "volume": [1000000]},
            index=pd.to_datetime(["2024-01-02"]),
        )
        df2 = pd.DataFrame(
            {"open": [70000.0], "high": [72000.0], "low": [69000.0], "close": [99999.0], "volume": [1000000]},
            index=pd.to_datetime(["2024-01-02"]),
        )

        storage.save_daily_prices("005930", df1)
        storage.save_daily_prices("005930", df2)

        loaded = storage.load_daily_prices("005930")
        assert len(loaded) == 1
        assert loaded.iloc[0]["close"] == 99999.0

    def test_load_with_date_range(self, storage: DataStorage) -> None:
        df = pd.DataFrame(
            {
                "open": [100.0, 200.0, 300.0],
                "high": [110.0, 210.0, 310.0],
                "low": [90.0, 190.0, 290.0],
                "close": [105.0, 205.0, 305.0],
                "volume": [100, 200, 300],
            },
            index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        )
        storage.save_daily_prices("005930", df)

        loaded = storage.load_daily_prices(
            "005930",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
        )
        assert len(loaded) == 1
        assert loaded.iloc[0]["close"] == 205.0

    def test_load_empty(self, storage: DataStorage) -> None:
        loaded = storage.load_daily_prices("999999")
        assert loaded.empty


# ───────────────────────────────────────────────
# Fundamental 테스트
# ───────────────────────────────────────────────

class TestFundamental:
    def test_save_and_load(self, storage: DataStorage) -> None:
        df = pd.DataFrame(
            {
                "BPS": [50000.0, 80000.0],
                "PER": [12.5, 8.3],
                "PBR": [1.4, 0.9],
                "EPS": [5600.0, 9600.0],
                "DIV": [1.8, 2.5],
            },
            index=["005930", "000660"],
        )
        df.index.name = "ticker"

        count = storage.save_fundamentals(date(2024, 1, 2), df)
        assert count == 2

        loaded = storage.load_fundamentals(date(2024, 1, 2))
        assert len(loaded) == 2
        assert loaded.loc["005930", "PBR"] == 1.4

    def test_upsert(self, storage: DataStorage) -> None:
        df1 = pd.DataFrame(
            {"BPS": [50000.0], "PER": [12.5], "PBR": [1.4], "EPS": [5600.0], "DIV": [1.8]},
            index=["005930"],
        )
        df2 = pd.DataFrame(
            {"BPS": [55000.0], "PER": [13.0], "PBR": [1.5], "EPS": [6000.0], "DIV": [2.0]},
            index=["005930"],
        )

        storage.save_fundamentals(date(2024, 1, 2), df1)
        storage.save_fundamentals(date(2024, 1, 2), df2)

        loaded = storage.load_fundamentals(date(2024, 1, 2))
        assert len(loaded) == 1
        assert loaded.loc["005930", "PBR"] == 1.5


# ───────────────────────────────────────────────
# FactorScore 테스트
# ───────────────────────────────────────────────

class TestFactorScore:
    def test_save(self, storage: DataStorage) -> None:
        df = pd.DataFrame(
            {
                "value_score": [75.0, 60.0],
                "momentum_score": [80.0, 55.0],
                "quality_score": [70.0, 65.0],
                "composite_score": [76.0, 59.0],
            },
            index=["005930", "000660"],
        )
        count = storage.save_factor_scores(date(2024, 1, 2), df)
        assert count == 2


# ───────────────────────────────────────────────
# Portfolio 테스트
# ───────────────────────────────────────────────

class TestPortfolio:
    def test_save(self, storage: DataStorage) -> None:
        df = pd.DataFrame(
            {
                "ticker": ["005930", "000660"],
                "name": ["삼성전자", "SK하이닉스"],
                "weight": [0.5, 0.5],
                "composite_score": [76.0, 59.0],
            }
        )
        count = storage.save_portfolio(date(2024, 1, 31), df)
        assert count == 2


# ───────────────────────────────────────────────
# Trade 테스트
# ───────────────────────────────────────────────

class TestTrade:
    def test_save_and_load(self, storage: DataStorage) -> None:
        storage.save_trade(
            trade_date=date(2024, 1, 2),
            ticker="005930",
            side="BUY",
            quantity=100,
            price=71000.0,
            amount=7100000.0,
            commission=1065.0,
            tax=0.0,
            is_paper=True,
        )

        trades = storage.load_trades()
        assert len(trades) == 1
        assert trades.iloc[0]["ticker"] == "005930"
        assert trades.iloc[0]["side"] == "BUY"
        assert trades.iloc[0]["is_paper"] == True

    def test_load_with_date_range(self, storage: DataStorage) -> None:
        storage.save_trade(date(2024, 1, 1), "A", "BUY", 10, 100.0, 1000.0)
        storage.save_trade(date(2024, 1, 2), "B", "BUY", 20, 200.0, 4000.0)
        storage.save_trade(date(2024, 1, 3), "C", "SELL", 30, 300.0, 9000.0)

        trades = storage.load_trades(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
        )
        assert len(trades) == 1
        assert trades.iloc[0]["ticker"] == "B"

    def test_load_empty(self, storage: DataStorage) -> None:
        trades = storage.load_trades()
        assert trades.empty
