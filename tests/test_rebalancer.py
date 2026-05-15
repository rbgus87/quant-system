"""Rebalancer 단위 테스트.

compute_inverse_vol_rebalance 4 케이스:
TC-1: 동일 변동성 → equal-weight와 동일 결과
TC-2: 변동성 2배 차이 → 저변동성 종목 비중 2배
TC-3: max_position_pct 초과 → cap + 재분배
TC-4: σ=NaN 종목 → median σ 대체

estimate_market_impact 2 케이스 (E2):
TC-5: daily_volatility=None → 기본 sigma=0.01 사용 (하위 호환)
TC-6: daily_volatility=0.03 → sigma 3배, impact 3배
"""

import math
import pytest


def _make_rebalancer():
    from strategy.rebalancer import Rebalancer
    return Rebalancer()


class TestInverseVolRebalance:

    def test_equal_volatility_gives_equal_weight(self):
        """TC-1: 동일 변동성(σ=0.3) → 모든 종목 동일 비중 (equal-weight와 동일)."""
        rb = _make_rebalancer()
        tickers = ["A", "B", "C"]
        vols = {"A": 0.3, "B": 0.3, "C": 0.3}
        prices = {"A": 10_000.0, "B": 20_000.0, "C": 5_000.0}
        total_value = 3_000_000.0

        result = rb.compute_inverse_vol_rebalance(tickers, vols, total_value, prices)

        assert len(result) == 3
        weights = {item["ticker"]: item["target_weight"] for item in result}
        expected = 1.0 / 3
        for t in tickers:
            assert abs(weights[t] - expected) < 0.001, (
                f"{t} weight {weights[t]:.4f} != expected {expected:.4f}"
            )
        assert abs(sum(weights.values()) - 1.0) < 0.001

    def test_double_volatility_halves_weight(self):
        """TC-2: A의 σ가 B의 2배 → B 비중이 A 비중의 2배."""
        rb = _make_rebalancer()
        tickers = ["A", "B"]
        vols = {"A": 0.6, "B": 0.3}
        prices = {"A": 10_000.0, "B": 10_000.0}
        total_value = 2_000_000.0

        result = rb.compute_inverse_vol_rebalance(
            tickers, vols, total_value, prices,
            max_position_pct=0.80,  # cap 미발동 (B raw=0.667 < 0.80)
            min_position_pct=0.0,
        )

        weights = {item["ticker"]: item["target_weight"] for item in result}
        ratio = weights["B"] / weights["A"]
        assert abs(ratio - 2.0) < 0.01, (
            f"B/A weight ratio={ratio:.3f}, expected 2.0 "
            f"(B={weights['B']:.4f}, A={weights['A']:.4f})"
        )

    def test_max_position_pct_cap_and_redistribution(self):
        """TC-3: LOW(σ=0.1)가 max_position_pct=0.40 초과 → cap 후 초과분 A/B/C에 재분배."""
        rb = _make_rebalancer()
        tickers = ["LOW", "A", "B", "C"]
        # raw weights: LOW=1/0.1=10, others=1/0.5=2 each, total=16
        # LOW initial weight = 10/16 = 0.625 → capped at 0.40
        vols = {"LOW": 0.1, "A": 0.5, "B": 0.5, "C": 0.5}
        prices = {t: 10_000.0 for t in tickers}
        total_value = 4_000_000.0

        result = rb.compute_inverse_vol_rebalance(
            tickers, vols, total_value, prices, max_position_pct=0.40, min_position_pct=0.0
        )

        weights = {item["ticker"]: item["target_weight"] for item in result}
        # LOW cap 적용 검증
        assert weights.get("LOW", 0) <= 0.40 + 0.001, (
            f"LOW weight {weights.get('LOW', 0):.4f} exceeds max_position_pct 0.40"
        )
        # 비중 합 ≈ 1.0
        assert abs(sum(weights.values()) - 1.0) < 0.01, (
            f"weights sum={sum(weights.values()):.4f} ≠ 1.0"
        )
        # LOW의 실제 비중이 raw 비중(0.625)보다 작아야 함 (cap 적용 확인)
        assert weights.get("LOW", 1.0) < 0.60

    def test_nan_volatility_replaced_by_median(self):
        """TC-4: σ=NaN 종목은 나머지 median σ(0.4)로 대체되어 결과에 포함된다."""
        rb = _make_rebalancer()
        tickers = ["A", "B", "NAN"]
        # median(0.3, 0.5) = 0.4 → NAN의 σ가 0.4로 대체됨
        vols = {"A": 0.3, "B": 0.5, "NAN": float("nan")}
        prices = {t: 10_000.0 for t in tickers}
        total_value = 3_000_000.0

        result = rb.compute_inverse_vol_rebalance(
            tickers, vols, total_value, prices,
            max_position_pct=0.60,  # cap 미발동 (A raw=0.426 < 0.60)
            min_position_pct=0.0,
        )

        result_tickers = [item["ticker"] for item in result]
        # NAN 종목도 결과에 포함되어야 함
        assert "NAN" in result_tickers, "NAN ticker should appear in result"

        weights = {item["ticker"]: item["target_weight"] for item in result}
        # σ_NAN=0.4 < σ_B=0.5 이므로 NAN 비중 > B 비중
        assert weights["NAN"] > weights["B"], (
            f"NAN (σ_replaced=0.4) should weight > B (σ=0.5): "
            f"NAN={weights['NAN']:.4f}, B={weights['B']:.4f}"
        )
        # 모든 비중 양수
        for t in result_tickers:
            assert weights[t] > 0, f"{t} weight should be positive"


class TestEstimateMarketImpact:
    """E2: estimate_market_impact 종목별 σ 파라미터 테스트."""

    def test_backward_compat_none_uses_default_sigma(self):
        """TC-5: daily_volatility=None → sigma=0.01, 기존 동작과 동일."""
        rb = _make_rebalancer()
        adv = 100_000   # 거래량
        shares = 10_000
        price = 50_000.0
        # volume_fraction = 10000 / (100000 * 0.1) = 1.0 → impact = 0.01 * 1.0 = 0.01
        impact = rb.estimate_market_impact(price, shares, adv, daily_volatility=None)
        assert abs(impact - 0.01) < 1e-9, f"expected 0.01, got {impact}"

    def test_custom_sigma_scales_impact_proportionally(self):
        """TC-6: daily_volatility=0.03 → sigma 3배 → impact 3배."""
        rb = _make_rebalancer()
        adv = 100_000
        shares = 10_000
        price = 50_000.0
        impact_default = rb.estimate_market_impact(price, shares, adv, daily_volatility=None)
        impact_custom  = rb.estimate_market_impact(price, shares, adv, daily_volatility=0.03)
        assert impact_custom > impact_default, "high sigma should produce higher impact"
        ratio = impact_custom / impact_default
        assert abs(ratio - 3.0) < 1e-6, f"expected 3× ratio, got {ratio:.6f}"
