# tests/test_storage.py
import pandas as pd
import pytest
from datetime import date

from data.storage import (
    DataStorage,
)


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
            {
                "open": [70000.0],
                "high": [72000.0],
                "low": [69000.0],
                "close": [71000.0],
                "volume": [1000000],
            },
            index=pd.to_datetime(["2024-01-02"]),
        )
        df2 = pd.DataFrame(
            {
                "open": [70000.0],
                "high": [72000.0],
                "low": [69000.0],
                "close": [99999.0],
                "volume": [1000000],
            },
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
            {
                "BPS": [50000.0],
                "PER": [12.5],
                "PBR": [1.4],
                "EPS": [5600.0],
                "DIV": [1.8],
            },
            index=["005930"],
        )
        df2 = pd.DataFrame(
            {
                "BPS": [55000.0],
                "PER": [13.0],
                "PBR": [1.5],
                "EPS": [6000.0],
                "DIV": [2.0],
            },
            index=["005930"],
        )

        storage.save_fundamentals(date(2024, 1, 2), df1)
        storage.save_fundamentals(date(2024, 1, 2), df2)

        loaded = storage.load_fundamentals(date(2024, 1, 2))
        assert len(loaded) == 1
        assert loaded.loc["005930", "PBR"] == 1.5

    def test_save_debt_ratio_columns(self, storage: DataStorage) -> None:
        """S2: total_equity / total_liabilities / debt_ratio 저장·조회"""
        df = pd.DataFrame(
            {
                "BPS": [50000.0],
                "PER": [12.5],
                "PBR": [1.4],
                "EPS": [5600.0],
                "DIV": [1.8],
                "TOTAL_EQUITY": [3.61e14],
                "TOTAL_LIABILITIES": [9.28e13],
                "DEBT_RATIO": [25.7],
            },
            index=["005930"],
        )
        storage.save_fundamentals(date(2024, 1, 2), df)
        loaded = storage.load_fundamentals(date(2024, 1, 2))
        assert "TOTAL_EQUITY" in loaded.columns
        assert "TOTAL_LIABILITIES" in loaded.columns
        assert "DEBT_RATIO" in loaded.columns
        assert loaded.loc["005930", "TOTAL_EQUITY"] == 3.61e14
        assert loaded.loc["005930", "TOTAL_LIABILITIES"] == 9.28e13
        assert loaded.loc["005930", "DEBT_RATIO"] == 25.7


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
        assert trades.iloc[0]["is_paper"]

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


# ───────────────────────────────────────────────
# load_close_matrix 테스트
# ───────────────────────────────────────────────


class TestLoadCloseMatrix:
    def _save_prices(self, storage: DataStorage) -> None:
        for ticker, closes in [("A", [100.0, 110.0, 120.0]), ("B", [200.0, 210.0, None])]:
            rows = []
            for i, (d, c) in enumerate(
                zip(["2024-01-02", "2024-01-03", "2024-01-04"], closes)
            ):
                if c is None:
                    continue
                rows.append({"open": c, "high": c, "low": c, "close": c, "volume": 1000})
            df = pd.DataFrame(
                rows,
                index=pd.to_datetime(
                    ["2024-01-02", "2024-01-03"]
                    if ticker == "B"
                    else ["2024-01-02", "2024-01-03", "2024-01-04"]
                ),
            )
            df.index.name = "date"
            storage.save_daily_prices(ticker, df)

    def test_returns_pivot_matrix(self, storage: DataStorage) -> None:
        self._save_prices(storage)
        matrix = storage.load_close_matrix(["A", "B"])
        assert set(matrix.columns) == {"A", "B"}
        assert matrix.loc[matrix.index[0], "A"] == 100.0

    def test_missing_ticker_is_nan(self, storage: DataStorage) -> None:
        self._save_prices(storage)
        matrix = storage.load_close_matrix(["A", "B"])
        # B has no data for 2024-01-04 → NaN
        last_row = matrix.iloc[-1]
        assert pd.isna(last_row["B"])

    def test_empty_when_no_data(self, storage: DataStorage) -> None:
        matrix = storage.load_close_matrix(["999999"])
        assert matrix.empty


# ───────────────────────────────────────────────
# load_fundamentals_quarterly_bulk + preload 테스트
# ───────────────────────────────────────────────


class TestFundamentalsQuarterlyBulkAndPreload:
    def _insert_quarterly(self, storage: DataStorage) -> None:
        rows = [
            {"ticker": "005930", "bsns_year": "2023", "reprt_code": "11011",
             "eps": 4000.0, "operating_income": 1e12, "revenue": 2e13, "fs_div": "CFS"},
            {"ticker": "005930", "bsns_year": "2023", "reprt_code": "11014",
             "eps": 900.0, "operating_income": 2e11, "revenue": 5e12, "fs_div": "CFS"},
            {"ticker": "000660", "bsns_year": "2023", "reprt_code": "11011",
             "eps": 8000.0, "operating_income": 3e12, "revenue": 1e13, "fs_div": "CFS"},
        ]
        storage.upsert_fundamentals_quarterly(rows)

    def test_bulk_returns_all_tickers(self, storage: DataStorage) -> None:
        self._insert_quarterly(storage)
        result = storage.load_fundamentals_quarterly_bulk(date(2024, 6, 30))
        assert "005930" in result
        assert "000660" in result

    def test_bulk_matches_single(self, storage: DataStorage) -> None:
        self._insert_quarterly(storage)
        as_of = date(2024, 6, 30)
        bulk = storage.load_fundamentals_quarterly_bulk(as_of)
        single = storage.load_fundamentals_quarterly("005930", as_of)
        if not single.empty and "005930" in bulk:
            assert len(bulk["005930"]) == len(single)

    def test_preload_cache_hit(self, storage: DataStorage) -> None:
        self._insert_quarterly(storage)
        as_of = date(2024, 6, 30)

        n = storage.preload_fundamentals_quarterly(as_of)
        assert n > 0
        assert storage._fq_preload_date == as_of

        # 캐시에서 반환 (DB 쿼리 없이)
        cached = storage.load_fundamentals_quarterly("005930", as_of)
        assert not cached.empty

    def test_preload_clear(self, storage: DataStorage) -> None:
        self._insert_quarterly(storage)
        storage.preload_fundamentals_quarterly(date(2024, 6, 30))
        storage.clear_fq_preload()
        assert storage._fq_preload == {}
        assert storage._fq_preload_date is None

    def test_preload_miss_returns_empty(self, storage: DataStorage) -> None:
        """프리로드에 없는 ticker → 빈 DataFrame 반환."""
        self._insert_quarterly(storage)
        as_of = date(2024, 6, 30)
        storage.preload_fundamentals_quarterly(as_of)
        result = storage.load_fundamentals_quarterly("999999", as_of)
        assert result.empty


# ───────────────────────────────────────────────
# FundamentalQuarterly 테스트 (Step 3 분기 시계열)
# ───────────────────────────────────────────────


class TestFundamentalQuarterly:
    """분기별 재무 시계열 upsert + PIT 조회"""

    def test_upsert_insert_and_update(self, storage: DataStorage) -> None:
        """동일 키 두 번 upsert: 첫번째는 insert, 두번째는 update"""
        rows1 = [
            {"ticker": "005930", "bsns_year": "2024", "reprt_code": "11013",
             "eps": 1000.0, "operating_income": 5e12, "revenue": 50e12,
             "fs_div": "CFS"},
        ]
        ins1, upd1 = storage.upsert_fundamentals_quarterly(rows1)
        assert ins1 == 1
        assert upd1 == 0

        rows2 = [
            {"ticker": "005930", "bsns_year": "2024", "reprt_code": "11013",
             "eps": 1500.0, "operating_income": 6e12, "revenue": 55e12,
             "fs_div": "CFS"},
        ]
        ins2, upd2 = storage.upsert_fundamentals_quarterly(rows2)
        assert ins2 == 0
        assert upd2 == 1

        # 갱신된 값이 반영되었는지 확인
        # 2025-04-15 PIT → _pit_end_period = (2024, Annual), 4분기 역행 = 2024 전 분기 커버
        df = storage.load_fundamentals_quarterly(
            "005930", as_of_date=date(2025, 4, 15), n_quarters=4,
        )
        q1_rows = df[
            (df["bsns_year"] == "2024") & (df["reprt_code"] == "11013")
        ]
        assert len(q1_rows) == 1
        assert q1_rows.iloc[0]["eps"] == 1500.0

    def test_pit_excludes_future_quarter(self, storage: DataStorage) -> None:
        """as_of_date에서 아직 공시 안 된 분기는 반환 안 됨.

        as_of_date=2017-06-30 → _pit_end_period가 2017-Q1을 한계로 결정.
        2017-Q2(11012=Half)는 8월 중순 공시되므로 미포함이어야 함.
        """
        rows = [
            # 2017 Q1 (11013) — 6월 PIT에 포함되어야 함
            {"ticker": "005620", "bsns_year": "2017", "reprt_code": "11013",
             "eps": -62729, "operating_income": -5e9, "revenue": 1e10,
             "fs_div": "CFS"},
            # 2017 Half (11012) — 6월 PIT에 미포함 (공시 전)
            {"ticker": "005620", "bsns_year": "2017", "reprt_code": "11012",
             "eps": 111049, "operating_income": 5e9, "revenue": 1.5e10,
             "fs_div": "CFS"},
            # 2016 Annual — 항상 포함
            {"ticker": "005620", "bsns_year": "2016", "reprt_code": "11011",
             "eps": -50000, "operating_income": -3e9, "revenue": 8e9,
             "fs_div": "CFS"},
        ]
        storage.upsert_fundamentals_quarterly(rows)

        df = storage.load_fundamentals_quarterly(
            "005620", as_of_date=date(2017, 6, 30), n_quarters=4,
        )
        # 2017 Half는 미포함
        assert not (
            (df["bsns_year"] == "2017") & (df["reprt_code"] == "11012")
        ).any()
        # 2017 Q1은 포함
        assert (
            (df["bsns_year"] == "2017") & (df["reprt_code"] == "11013")
        ).any()
        # 2016 Annual도 포함
        assert (
            (df["bsns_year"] == "2016") & (df["reprt_code"] == "11011")
        ).any()

    def test_load_returns_descending(self, storage: DataStorage) -> None:
        """period_end 내림차순 (최신이 맨 위)"""
        rows = [
            {"ticker": "A", "bsns_year": "2024", "reprt_code": "11011",
             "eps": 4.0, "operating_income": None, "revenue": None,
             "fs_div": "CFS"},
            {"ticker": "A", "bsns_year": "2024", "reprt_code": "11013",
             "eps": 1.0, "operating_income": None, "revenue": None,
             "fs_div": "CFS"},
            {"ticker": "A", "bsns_year": "2024", "reprt_code": "11012",
             "eps": 2.0, "operating_income": None, "revenue": None,
             "fs_div": "CFS"},
            {"ticker": "A", "bsns_year": "2024", "reprt_code": "11014",
             "eps": 3.0, "operating_income": None, "revenue": None,
             "fs_div": "CFS"},
        ]
        storage.upsert_fundamentals_quarterly(rows)
        # 2025-04-01 PIT → 2024 Annual (Q4)까지 모두 접근
        df = storage.load_fundamentals_quarterly(
            "A", as_of_date=date(2025, 4, 1), n_quarters=4,
        )
        eps_seq = df["eps"].tolist()
        # 최신(Annual=4.0)이 맨 위, Q3=3.0, Half=2.0, Q1=1.0 순
        assert eps_seq == [4.0, 3.0, 2.0, 1.0]

    def test_pit_end_period_logic(self) -> None:
        """_pit_end_period: dart_client lag 규칙과 동일성 검증"""
        # 1월 → 전전년 Annual
        assert DataStorage._pit_end_period(date(2025, 1, 15)) == ("2023", "11011")
        # 3월 → 전전년 Annual
        assert DataStorage._pit_end_period(date(2025, 3, 31)) == ("2023", "11011")
        # 4월 → 전년 Annual
        assert DataStorage._pit_end_period(date(2025, 4, 1)) == ("2024", "11011")
        # 6월 → 그해 Q1
        assert DataStorage._pit_end_period(date(2025, 6, 30)) == ("2025", "11013")
        # 9월 → 그해 Half
        assert DataStorage._pit_end_period(date(2025, 9, 30)) == ("2025", "11012")
        # 12월 → 그해 Q3
        assert DataStorage._pit_end_period(date(2025, 12, 15)) == ("2025", "11014")

    def test_walk_back_crosses_year_boundary(self) -> None:
        """분기 역행이 연도 경계를 올바르게 처리"""
        # 2024 Q1부터 4분기 역행 → Q1, (2023) Annual, Q3, Half
        keys = DataStorage._walk_back_quarters("2024", "11013", 4)
        assert keys == [
            ("2024", "11013"),
            ("2023", "11011"),
            ("2023", "11014"),
            ("2023", "11012"),
        ]


# ───────────────────────────────────────────────
# StockSector 테스트 (S4-A 섹터 인프라)
# ───────────────────────────────────────────────


class TestStockSector:
    """upsert / load_stock_sectors (PIT 폴백) / get_finance_tickers"""

    def test_upsert_and_load(self, storage: DataStorage) -> None:
        rows = [
            {"ticker": "005930", "date": "20240630",
             "sector_name": "전기전자", "is_financial": False,
             "data_source": "name_heuristic"},
            {"ticker": "055550", "date": "20240630",
             "sector_name": "금융업", "is_financial": True,
             "data_source": "name_heuristic"},
        ]
        ins, upd = storage.upsert_stock_sectors(rows)
        assert ins == 2
        assert upd == 0

        df = storage.load_stock_sectors("20240630")
        assert len(df) == 2
        assert bool(df.loc["055550", "is_financial"]) is True
        assert bool(df.loc["005930", "is_financial"]) is False

    def test_get_finance_tickers(self, storage: DataStorage) -> None:
        rows = [
            {"ticker": "005930", "date": "20240630",
             "sector_name": "전기전자", "is_financial": False,
             "data_source": "name_heuristic"},
            {"ticker": "055550", "date": "20240630",
             "sector_name": "금융업", "is_financial": True,
             "data_source": "name_heuristic"},
            {"ticker": "323410", "date": "20240630",
             "sector_name": "은행", "is_financial": True,
             "data_source": "name_heuristic"},
        ]
        storage.upsert_stock_sectors(rows)

        fin = storage.get_finance_tickers("20240630")
        assert set(fin) == {"055550", "323410"}

    def test_pit_fallback_to_earlier_date(self, storage: DataStorage) -> None:
        """정확한 date에 데이터 없으면 가장 가까운 이전 날짜 사용"""
        rows = [
            {"ticker": "055550", "date": "20240331",
             "sector_name": "금융업", "is_financial": True,
             "data_source": "name_heuristic"},
        ]
        storage.upsert_stock_sectors(rows)

        # 20240630에 직접 데이터 없음 → 20240331 폴백
        df = storage.load_stock_sectors("20240630")
        assert len(df) == 1
        assert bool(df.loc["055550", "is_financial"]) is True

    def test_pit_too_old_excluded(self, storage: DataStorage) -> None:
        """180일 초과 오래된 데이터는 제외"""
        rows = [
            {"ticker": "055550", "date": "20230101",
             "sector_name": "금융업", "is_financial": True,
             "data_source": "name_heuristic"},
        ]
        storage.upsert_stock_sectors(rows)

        # 20240630은 20230101 기준 545일 후 → 180일 초과로 미반환
        df = storage.load_stock_sectors("20240630")
        assert df.empty

    def test_upsert_updates_existing(self, storage: DataStorage) -> None:
        """동일 (ticker, date) 두 번째 upsert는 갱신"""
        rows1 = [{
            "ticker": "055550", "date": "20240630",
            "sector_name": "금융업", "is_financial": True,
            "data_source": "name_heuristic",
        }]
        rows2 = [{
            "ticker": "055550", "date": "20240630",
            "sector_name": "은행", "is_financial": True,
            "data_source": "krx_api",
        }]
        storage.upsert_stock_sectors(rows1)
        ins, upd = storage.upsert_stock_sectors(rows2)
        assert ins == 0
        assert upd == 1
        df = storage.load_stock_sectors("20240630")
        assert df.loc["055550", "sector_name"] == "은행"
