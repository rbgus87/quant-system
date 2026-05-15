# tests/test_composite_lowvol.py
"""composite.py에 low_vol_score 추가 후 하위 호환성 + 통합 테스트."""

import numpy as np
import pandas as pd

from config.settings import settings


def _make_scores(n: int = 20, seed: int = 0) -> tuple[pd.Series, pd.Series, pd.Series]:
    rng = np.random.RandomState(seed)
    idx = [f"T{i:03d}" for i in range(n)]
    v = pd.Series(rng.uniform(0, 100, n), index=idx, name="value_score")
    m = pd.Series(rng.uniform(0, 100, n), index=idx, name="momentum_score")
    q = pd.Series(dtype=float, name="quality_score")  # empty (weight=0 시나리오)
    return v, m, q


class TestCompositeLowVolBackwardCompat:
    """low_vol_score=None 또는 weight=0 이면 기존과 동일 결과여야 함."""

    def test_no_low_vol_score_identical(self):
        """low_vol_score=None(기본) → 기존 calculate() 와 동일 결과."""
        from factors.composite import MultiFactorComposite

        orig_lv = settings.factor_weights.low_vol
        settings.factor_weights.low_vol = 0.00
        try:
            composite = MultiFactorComposite()
            v, m, q = _make_scores(20, seed=1)

            result_old = composite.calculate(v, m, q)
            result_new = composite.calculate(v, m, q, low_vol_score=None)

            pd.testing.assert_frame_equal(
                result_old.reset_index(drop=True),
                result_new.reset_index(drop=True),
                check_like=True,
            )
        finally:
            settings.factor_weights.low_vol = orig_lv

    def test_zero_weight_low_vol_score_ignored(self):
        """weight=0일 때 low_vol_score 제공해도 composite_score 변화 없음."""
        from factors.composite import MultiFactorComposite

        orig_lv = settings.factor_weights.low_vol
        settings.factor_weights.low_vol = 0.00
        try:
            composite = MultiFactorComposite()
            v, m, q = _make_scores(20, seed=2)
            lv = pd.Series(np.random.rand(20) * 100, index=v.index, name="low_vol_score")

            result_base = composite.calculate(v, m, q, low_vol_score=None)
            result_with = composite.calculate(v, m, q, low_vol_score=lv)

            np.testing.assert_allclose(
                result_base["composite_score"].values,
                result_with.reindex(result_base.index)["composite_score"].values,
                rtol=1e-6,
            )
        finally:
            settings.factor_weights.low_vol = orig_lv


class TestCompositeLowVolIntegration:
    """low_vol_score 제공 + weight > 0 → composite_score가 변해야 함."""

    def test_low_vol_changes_composite(self):
        """low_vol 가중치 활성화 → composite_score가 기존과 달라야 한다."""
        from factors.composite import MultiFactorComposite

        orig_v  = settings.factor_weights.value
        orig_m  = settings.factor_weights.momentum
        orig_lv = settings.factor_weights.low_vol
        settings.factor_weights.value    = 0.70
        settings.factor_weights.momentum = 0.00
        settings.factor_weights.low_vol  = 0.30
        try:
            composite = MultiFactorComposite()
            v, m, q = _make_scores(20, seed=3)
            lv_asc  = pd.Series(np.arange(20, dtype=float), index=v.index)
            lv_desc = pd.Series(np.arange(19, -1, -1, dtype=float), index=v.index)

            result_asc  = composite.calculate(v, m, q, low_vol_score=lv_asc)
            result_desc = composite.calculate(v, m, q, low_vol_score=lv_desc)

            common = result_asc.index.intersection(result_desc.index)
            assert len(common) > 0
            diff = (
                result_asc.loc[common, "composite_score"]
                - result_desc.loc[common, "composite_score"]
            ).abs().sum()
            assert diff > 1.0, f"low_vol 변화 시 composite_score 변화 없음 (diff={diff:.4f})"
        finally:
            settings.factor_weights.value    = orig_v
            settings.factor_weights.momentum = orig_m
            settings.factor_weights.low_vol  = orig_lv

    def test_low_vol_score_column_present(self):
        """반환 DataFrame에 low_vol_score 컬럼이 존재해야 한다."""
        from factors.composite import MultiFactorComposite

        orig_lv = settings.factor_weights.low_vol
        settings.factor_weights.low_vol = 0.30
        try:
            composite = MultiFactorComposite()
            v, m, q = _make_scores(20, seed=4)
            lv = pd.Series(np.random.rand(20) * 100, index=v.index)
            result = composite.calculate(v, m, q, low_vol_score=lv)
            assert "low_vol_score" in result.columns
        finally:
            settings.factor_weights.low_vol = orig_lv
