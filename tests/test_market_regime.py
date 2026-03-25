# tests/test_market_regime.py
"""MarketRegimeFilter 단위 테스트"""
import pandas as pd
import numpy as np
import pytest
from unittest.mock import MagicMock

from strategy.market_regime import MarketRegimeFilter, calc_vol_target_scale


def _make_trend_ohlcv(direction: str, ma_days: int = 200) -> pd.DataFrame:
    """추세 방향에 따른 가짜 OHLCV DataFrame 생성

    Args:
        direction: "up" (현재가 > MA) 또는 "down" (현재가 < MA)
        ma_days: 이동평균 기간

    Returns:
        close 컬럼을 가진 DataFrame
    """
    n_days = int(ma_days * 1.5)
    dates = pd.bdate_range("2022-01-01", periods=n_days)

    if direction == "up":
        # 상승 추세: 꾸준히 올라가서 현재가 > MA
        prices = np.linspace(10000, 15000, n_days)
    else:
        # 하락 추세: 꾸준히 내려가서 현재가 < MA
        prices = np.linspace(15000, 10000, n_days)

    return pd.DataFrame({"close": prices}, index=dates)


def _make_short_ohlcv(n_days: int = 10) -> pd.DataFrame:
    """데이터 부족 상황용 짧은 OHLCV 생성

    Args:
        n_days: 데이터 일수 (MA 기간보다 짧아야 함)

    Returns:
        close 컬럼을 가진 DataFrame
    """
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    prices = np.linspace(10000, 10500, n_days)
    return pd.DataFrame({"close": prices}, index=dates)


class TestMarketRegimeFilter:
    """MarketRegimeFilter 테스트"""

    def _make_filter(self, ohlcv_df: pd.DataFrame) -> MarketRegimeFilter:
        """mock collector를 가진 MarketRegimeFilter 생성

        Args:
            ohlcv_df: get_ohlcv가 반환할 DataFrame

        Returns:
            MarketRegimeFilter 인스턴스
        """
        collector = MagicMock()
        collector.get_ohlcv.return_value = ohlcv_df
        return MarketRegimeFilter(collector)

    def test_disabled_returns_full_ratio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cfg.enabled = False일 때 get_invest_ratio()가 1.0 반환"""
        from config.settings import settings

        monkeypatch.setattr(settings.market_regime, "enabled", False)

        filt = self._make_filter(_make_trend_ohlcv("down"))
        ratio = filt.get_invest_ratio("20240101")

        assert ratio == 1.0
        # enabled=False이면 collector를 호출하지 않아야 함
        filt.collector.get_ohlcv.assert_not_called()

    def test_bullish_regime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """추세 + 모멘텀 모두 강세일 때 1.0 반환"""
        from config.settings import settings

        monkeypatch.setattr(settings.market_regime, "enabled", True)
        monkeypatch.setattr(settings.market_regime, "ma_days", 200)
        monkeypatch.setattr(settings.market_regime, "partial_ratio", 0.5)
        monkeypatch.setattr(settings.market_regime, "defensive_ratio", 0.3)

        # 상승 추세 DataFrame → 현재가 > MA, 수익률 양수
        up_df = _make_trend_ohlcv("up", ma_days=200)
        filt = self._make_filter(up_df)
        ratio = filt.get_invest_ratio("20240101")

        assert ratio == 1.0

    def test_bearish_regime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """추세 + 모멘텀 모두 약세일 때 최소 비중(defensive_ratio) 반환"""
        from config.settings import settings

        monkeypatch.setattr(settings.market_regime, "enabled", True)
        monkeypatch.setattr(settings.market_regime, "ma_days", 200)
        monkeypatch.setattr(settings.market_regime, "partial_ratio", 0.5)
        monkeypatch.setattr(settings.market_regime, "defensive_ratio", 0.3)

        # 하락 추세 DataFrame → 현재가 < MA, 수익률 음수
        down_df = _make_trend_ohlcv("down", ma_days=200)
        filt = self._make_filter(down_df)
        ratio = filt.get_invest_ratio("20240101")

        assert ratio == 0.3

    def test_mixed_regime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """추세만 강세이고 모멘텀 약세일 때 중간 비중(partial_ratio) 반환"""
        from config.settings import settings

        monkeypatch.setattr(settings.market_regime, "enabled", True)
        monkeypatch.setattr(settings.market_regime, "ma_days", 200)
        monkeypatch.setattr(settings.market_regime, "partial_ratio", 0.5)
        monkeypatch.setattr(settings.market_regime, "defensive_ratio", 0.3)

        collector = MagicMock()
        filt = MarketRegimeFilter(collector)

        # 추세: 강세 (True), 모멘텀: 약세 (False)
        monkeypatch.setattr(filt, "_check_trend_signal", lambda date, ma_days=200: True)
        monkeypatch.setattr(filt, "_check_momentum_signal", lambda date: False)

        ratio = filt.get_invest_ratio("20240101")
        assert ratio == 0.5

    def test_insufficient_data_defaults_bullish(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """데이터 부족 시 기본 강세 판단 (ratio=1.0)"""
        from config.settings import settings

        monkeypatch.setattr(settings.market_regime, "enabled", True)
        monkeypatch.setattr(settings.market_regime, "ma_days", 200)

        # MA 계산에 필요한 200일보다 훨씬 적은 10일 데이터
        short_df = _make_short_ohlcv(n_days=10)
        filt = self._make_filter(short_df)
        ratio = filt.get_invest_ratio("20240101")

        # 데이터 부족 시 상승 추세 가정 → 둘 다 True → 1.0
        assert ratio == 1.0


class TestCalcVolTargetScale:
    """calc_vol_target_scale 공통 함수 테스트"""

    def test_none_target_returns_one(self) -> None:
        """vol_target=None이면 1.0 반환"""
        assert calc_vol_target_scale([100, 101, 102], None, 60) == 1.0

    def test_zero_target_returns_one(self) -> None:
        """vol_target=0이면 1.0 반환"""
        assert calc_vol_target_scale([100, 101, 102], 0.0, 60) == 1.0

    def test_insufficient_data_returns_one(self) -> None:
        """데이터 부족 시 1.0 반환"""
        assert calc_vol_target_scale([100, 101], 0.15, 60) == 1.0

    def test_low_volatility_returns_one(self) -> None:
        """실현 변동성이 목표 미만이면 1.0 (비중 유지)"""
        # 거의 변동 없는 데이터 (일 변동 0.01%)
        values = [10000 + i * 0.1 for i in range(100)]
        result = calc_vol_target_scale(values, 0.50, 60)
        assert result == 1.0

    def test_high_volatility_reduces_scale(self) -> None:
        """실현 변동성이 목표 초과하면 비중 축소"""
        np.random.seed(42)
        # 일 변동성 ~2% → 연환산 ~32%
        base = 10000.0
        values = [base]
        for _ in range(99):
            base *= 1 + np.random.normal(0, 0.02)
            values.append(base)

        result = calc_vol_target_scale(values, 0.15, 60)
        assert 0.2 <= result < 1.0

    def test_scale_bounded(self) -> None:
        """결과가 0.2~1.0 범위 내"""
        np.random.seed(99)
        base = 10000.0
        values = [base]
        for _ in range(99):
            base *= 1 + np.random.normal(0, 0.05)
            values.append(base)

        result = calc_vol_target_scale(values, 0.10, 60)
        assert 0.2 <= result <= 1.0
