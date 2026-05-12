# tests/test_tick_size.py
"""호가단위 유틸리티 테스트."""
import pytest

from trading.tick_size import round_to_tick, tick_size


class TestTickSize:
    """tick_size() 가격대별 호가단위 매핑 검증."""

    def test_under_2000_tick_is_1(self):
        assert tick_size(1_500) == 1
        assert tick_size(1_999) == 1

    def test_under_5000_tick_is_5(self):
        assert tick_size(2_000) == 5
        assert tick_size(4_999) == 5

    def test_under_20000_tick_is_10(self):
        assert tick_size(5_000) == 10
        assert tick_size(15_000) == 10
        assert tick_size(19_999) == 10

    def test_under_50000_tick_is_50(self):
        assert tick_size(20_000) == 50
        assert tick_size(49_999) == 50

    def test_under_200000_tick_is_100(self):
        assert tick_size(50_000) == 100
        assert tick_size(55_000) == 100
        assert tick_size(199_999) == 100

    def test_under_500000_tick_is_500(self):
        assert tick_size(200_000) == 500
        assert tick_size(250_000) == 500
        assert tick_size(499_999) == 500

    def test_500000_or_above_tick_is_1000(self):
        assert tick_size(500_000) == 1_000
        assert tick_size(600_000) == 1_000
        assert tick_size(1_500_000) == 1_000


class TestRoundToTick:
    """round_to_tick() buy=올림, sell=내림 동작 검증."""

    def test_1500_buy_rounds_up(self):
        # 1500원, tick=1, 이미 정렬됨
        assert round_to_tick(1_500.0, "buy") == 1_500.0
        # 1500.5 → 매수 1501
        assert round_to_tick(1_500.5, "buy") == 1_501.0

    def test_1500_sell_rounds_down(self):
        assert round_to_tick(1_500.5, "sell") == 1_500.0

    def test_15000_tick10_buy(self):
        # 15,003 → tick 10 → buy 올림 15010
        assert round_to_tick(15_003.0, "buy") == 15_010.0
        # 15,000 정확 → 그대로
        assert round_to_tick(15_000.0, "buy") == 15_000.0

    def test_15000_tick10_sell(self):
        assert round_to_tick(15_009.0, "sell") == 15_000.0

    def test_55000_tick100_buy(self):
        # 55,030 → tick=100 (가격대 50,000 이상) → buy 55,100
        assert round_to_tick(55_030.0, "buy") == 55_100.0

    def test_55000_tick100_sell(self):
        assert round_to_tick(55_099.0, "sell") == 55_000.0

    def test_250000_tick500_buy(self):
        # 250,030 → tick=500 → buy 250,500
        assert round_to_tick(250_030.0, "buy") == 250_500.0

    def test_250000_tick500_sell(self):
        assert round_to_tick(250_499.0, "sell") == 250_000.0

    def test_600000_tick1000_buy(self):
        # 600,030 → tick=1000 → buy 601,000
        assert round_to_tick(600_030.0, "buy") == 601_000.0

    def test_600000_tick1000_sell(self):
        assert round_to_tick(600_999.0, "sell") == 600_000.0

    def test_zero_or_negative_price_passthrough(self):
        assert round_to_tick(0.0, "buy") == 0.0
        assert round_to_tick(-100.0, "sell") == -100.0

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError):
            round_to_tick(1_000.0, "hold")
