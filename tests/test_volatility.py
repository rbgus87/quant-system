"""VolatilityFactor 단위 테스트."""

import datetime
import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock


def _make_storage_mock(tickers_closes: dict[str, list[float]]) -> MagicMock:
    """종목별 종가 리스트로 mock DataStorage 생성."""
    storage = MagicMock()
    rows = []
    for ticker, closes in tickers_closes.items():
        for i, c in enumerate(closes):
            rows.append({
                "ticker": ticker,
                "date": datetime.date(2024, 1, 1) + datetime.timedelta(days=i),
                "close": float(c),
            })
    df = pd.DataFrame(rows)
    storage.load_daily_prices_bulk.return_value = df
    return storage


class TestVolatilityFactor:

    # TC-1: 완벽한 저변동성이 최고 점수를 받는지
    def test_zero_variance_max_score(self):
        """일정 가격(변동성=0) 종목이 가장 높은 점수를 받아야 한다."""
        storage = _make_storage_mock({
            "ZERO": [100.0] * 80,
            "NONZ": [100.0 + i * 0.5 for i in range(80)],
        })
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score("20240301", ["ZERO", "NONZ"], storage, lookback_days=60)

        assert "ZERO" in scores.index
        assert "NONZ" in scores.index
        assert scores["ZERO"] >= scores["NONZ"], (
            f"zero-var should score >= non-zero-var: ZERO={scores['ZERO']:.1f}, NONZ={scores['NONZ']:.1f}"
        )

    # TC-2: 고변동성이 저변동성보다 낮은 점수를 받는지
    def test_high_vol_lower_score(self):
        """고변동성 종목이 저변동성 종목보다 낮은 점수를 받아야 한다."""
        rng = np.random.RandomState(42)
        low_closes = [100.0 + rng.randn() * 0.1 for _ in range(80)]
        high_closes = [100.0 + rng.randn() * 10.0 for _ in range(80)]
        high_closes = [max(c, 1.0) for c in high_closes]

        storage = _make_storage_mock({"LOW": low_closes, "HIGH": high_closes})
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score("20240301", ["LOW", "HIGH"], storage, lookback_days=60)

        assert "LOW" in scores.index and "HIGH" in scores.index
        assert scores["LOW"] > scores["HIGH"], (
            f"low-vol should score > high-vol: LOW={scores['LOW']:.1f}, HIGH={scores['HIGH']:.1f}"
        )

    # TC-3: 데이터 부족 시 NaN 반환
    def test_insufficient_data_returns_nan(self):
        """유효 데이터 < lookback * min_data_ratio 이면 NaN 반환."""
        storage = _make_storage_mock({
            "ENOUGH": [100.0 + i * 0.1 for i in range(80)],
            "SHORT":  [100.0 + i * 0.1 for i in range(10)],
        })
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score("20240301", ["ENOUGH", "SHORT"], storage, lookback_days=60)

        assert not np.isnan(scores["ENOUGH"]), "ENOUGH should have valid score"
        assert np.isnan(scores["SHORT"]), "SHORT should be NaN (insufficient data)"

    # TC-4: 빈 tickers 입력 → 빈 Series
    def test_empty_tickers_returns_empty(self):
        storage = MagicMock()
        storage.load_daily_prices_bulk.return_value = pd.DataFrame()
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score("20240301", [], storage, lookback_days=60)
        assert scores.empty

    # TC-5: 점수 범위 0~100 확인
    def test_scores_in_range(self):
        """모든 유효 점수는 0 이상 100 이하여야 한다."""
        rng = np.random.RandomState(7)
        tickers_closes = {
            f"T{i:03d}": [max(100.0 + rng.randn() * (i + 1), 1.0) for _ in range(80)]
            for i in range(10)
        }
        storage = _make_storage_mock(tickers_closes)
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score(
            "20240301", list(tickers_closes.keys()), storage, lookback_days=60
        )
        valid = scores.dropna()
        assert (valid >= 0).all() and (valid <= 100).all(), (
            f"Scores out of range: {valid.describe()}"
        )
