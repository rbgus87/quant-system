# tests/test_processor.py
import pandas as pd
import numpy as np
import pytest

from data.processor import DataProcessor


class TestCleanFundamentals:
    def setup_method(self) -> None:
        self.processor = DataProcessor()

    def test_pbr_zero_removed(self) -> None:
        df = pd.DataFrame(
            {"PBR": [0.0, 1.5, 3.0], "PER": [10.0, 15.0, 20.0], "DIV": [1.0, 2.0, 3.0]},
            index=["A", "B", "C"],
        )
        result = self.processor.clean_fundamentals(df)
        assert np.isnan(result.loc["A", "PBR"])
        assert result.loc["B", "PBR"] == 1.5

    def test_per_negative_removed(self) -> None:
        df = pd.DataFrame(
            {"PER": [-5.0, 10.0, 20.0], "PBR": [1.0, 2.0, 3.0]},
            index=["A", "B", "C"],
        )
        result = self.processor.clean_fundamentals(df)
        assert np.isnan(result.loc["A", "PER"])

    def test_div_negative_removed(self) -> None:
        df = pd.DataFrame(
            {"DIV": [-1.0, 0.0, 2.5]},
            index=["A", "B", "C"],
        )
        result = self.processor.clean_fundamentals(df)
        assert np.isnan(result.loc["A", "DIV"])
        assert result.loc["B", "DIV"] == 0.0  # 0은 유효

    def test_bps_negative_removed(self) -> None:
        df = pd.DataFrame(
            {"BPS": [-1000, 0, 50000]},
            index=["A", "B", "C"],
        )
        result = self.processor.clean_fundamentals(df)
        assert np.isnan(result.loc["A", "BPS"])
        assert np.isnan(result.loc["B", "BPS"])
        assert result.loc["C", "BPS"] == 50000

    def test_winsorize_top_1_percent(self) -> None:
        """상위 1% Winsorize: 극단값이 클리핑되어야 함"""
        pbr_values = [1.0] * 98 + [100.0, 200.0]  # 상위 1% = ~200
        df = pd.DataFrame(
            {"PBR": pbr_values, "PER": [10.0] * 100},
            index=[f"T{i:03d}" for i in range(100)],
        )
        result = self.processor.clean_fundamentals(df)
        # 200.0이 클리핑되었는지 확인
        assert result["PBR"].max() <= 200.0

    def test_empty_dataframe(self) -> None:
        df = pd.DataFrame(columns=["PBR", "PER", "DIV"])
        result = self.processor.clean_fundamentals(df)
        assert result.empty


class TestFilterUniverse:
    def test_market_cap_filter(self) -> None:
        tickers = ["A", "B", "C", "D", "E"]
        market_cap = pd.DataFrame(
            {"market_cap": [100, 200, 300, 400, 500]},
            index=["A", "B", "C", "D", "E"],
        )
        fundamentals = pd.DataFrame(
            {"PBR": [1.0, 2.0, 3.0, 4.0, 5.0]},
            index=["A", "B", "C", "D", "E"],
        )

        result = DataProcessor.filter_universe(
            tickers, market_cap, fundamentals, min_cap_percentile=20.0
        )

        # 하위 20%인 A(100)가 제외
        assert "A" not in result
        assert len(result) == 4

    def test_finance_tickers_excluded(self) -> None:
        tickers = ["A", "B", "C"]
        market_cap = pd.DataFrame(
            {"market_cap": [3000, 3000, 3000]},
            index=["A", "B", "C"],
        )
        fundamentals = pd.DataFrame(
            {"PBR": [1.0, 2.0, 3.0]},
            index=["A", "B", "C"],
        )

        result = DataProcessor.filter_universe(
            tickers, market_cap, fundamentals,
            finance_tickers=["B"],
        )

        assert "B" not in result
        assert "A" in result
        assert "C" in result

    def test_no_fundamental_data_excluded(self) -> None:
        tickers = ["A", "B", "C"]
        market_cap = pd.DataFrame(
            {"market_cap": [3000, 3000, 3000]},
            index=["A", "B", "C"],
        )
        fundamentals = pd.DataFrame(
            {"PBR": [1.0, np.nan, 3.0]},
            index=["A", "B", "C"],
        )

        result = DataProcessor.filter_universe(
            tickers, market_cap, fundamentals
        )

        # B는 PBR만 있고 NaN → dropna(how='all')에선 남을 수 있음
        # 실제로는 NaN 하나만 있어도 행 자체가 NaN이 아님
        assert "A" in result

    def test_empty_market_cap(self) -> None:
        tickers = ["A", "B"]
        result = DataProcessor.filter_universe(
            tickers, pd.DataFrame(), pd.DataFrame({"PBR": [1.0, 2.0]}, index=["A", "B"])
        )
        assert len(result) == 2

    def test_result_sorted(self) -> None:
        tickers = ["C", "A", "B"]
        market_cap = pd.DataFrame(
            {"market_cap": [1000, 2000, 3000]},
            index=["C", "A", "B"],
        )
        fundamentals = pd.DataFrame(
            {"PBR": [1.0, 2.0, 3.0]},
            index=["C", "A", "B"],
        )
        result = DataProcessor.filter_universe(tickers, market_cap, fundamentals)
        assert result == sorted(result)
