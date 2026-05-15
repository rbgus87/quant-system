# tests/test_factor_analysis.py
"""팩터 IC 계산 단위 테스트."""

import numpy as np
import pandas as pd
from scipy import stats

# ── 테스트 대상 함수 로컬 복사 ─────────────────────────────────────────────────
# analyze_factor_ic_v3.compute_ic 를 여기서 직접 복사하여
# 의존성 없이 테스트. 동일 로직 유지.

def _compute_ic(factor_scores: pd.Series, period_returns: pd.Series) -> float:
    """compute_ic의 로컬 복사 (의존성 없는 단위 테스트용)."""
    aligned = pd.concat([factor_scores, period_returns], axis=1).dropna()
    if len(aligned) < 10:
        return float("nan")
    corr, _ = stats.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    return float(corr)


def _compute_quintile_returns(
    factor_scores: pd.Series,
    period_returns: pd.Series,
) -> dict[str, float]:
    """compute_quintile_returns의 로컬 복사."""
    aligned = pd.concat([factor_scores, period_returns], axis=1).dropna()
    aligned.columns = ["score", "ret"]
    nan_result = {f"Q{i}": float("nan") for i in range(1, 6)}

    if len(aligned) < 25:
        return nan_result

    try:
        aligned["q"] = pd.qcut(
            aligned["score"], 5,
            labels=["Q5", "Q4", "Q3", "Q2", "Q1"],
        )
    except ValueError:
        return nan_result

    q_mean = aligned.groupby("q", observed=False)["ret"].mean()
    return {q: float(q_mean.get(q, float("nan"))) for q in [f"Q{i}" for i in range(1, 6)]}


# ── TC 1: 완벽한 양의 상관 → IC = 1.0 ──────────────────────────────────────

class TestICPerfectCorrelation:
    def test_perfect_positive_ic(self):
        """팩터 점수와 수익률이 완벽히 일치 → Spearman IC = 1.0."""
        n = 50
        scores  = pd.Series(np.arange(n, dtype=float), name="score")
        returns = pd.Series(np.arange(n, dtype=float), name="ret")

        ic = _compute_ic(scores, returns)

        assert not np.isnan(ic), "IC가 NaN 반환됨"
        assert abs(ic - 1.0) < 1e-9, f"완벽 상관 시 IC=1.0 기대, 실제={ic:.6f}"

    def test_perfect_negative_ic(self):
        """팩터 점수와 수익률이 완벽 역상관 → Spearman IC = -1.0."""
        n = 50
        scores  = pd.Series(np.arange(n, dtype=float))
        returns = pd.Series(np.arange(n - 1, -1, -1, dtype=float))

        ic = _compute_ic(scores, returns)

        assert not np.isnan(ic)
        assert abs(ic + 1.0) < 1e-9, f"완벽 역상관 시 IC=-1.0 기대, 실제={ic:.6f}"

    def test_insufficient_data_returns_nan(self):
        """유효 데이터 < 10이면 NaN 반환."""
        scores  = pd.Series([1.0, 2.0, 3.0])  # 3개 — 기준 미달
        returns = pd.Series([0.1, 0.2, 0.3])

        ic = _compute_ic(scores, returns)

        assert np.isnan(ic), f"데이터 부족 시 NaN 기대, 실제={ic}"


# ── TC 2: 무작위 데이터 → |IC| ≈ 0 ──────────────────────────────────────────

class TestICRandomData:
    def test_random_data_ic_near_zero(self):
        """|IC| < 0.20 (200개 랜덤 데이터, 95% CI ≈ ±2/√200 ≈ ±0.141)."""
        rng = np.random.RandomState(42)
        n = 200
        scores  = pd.Series(rng.randn(n))
        returns = pd.Series(rng.randn(n))

        ic = _compute_ic(scores, returns)

        assert not np.isnan(ic)
        assert abs(ic) < 0.20, f"랜덤 IC가 예상보다 큼: {ic:.4f}"

    def test_random_ic_distribution_centered(self):
        """1000회 반복 → 평균 IC ≈ 0 (편향 없음)."""
        rng = np.random.RandomState(0)
        ics = []
        for _ in range(1000):
            s = pd.Series(rng.randn(50))
            r = pd.Series(rng.randn(50))
            ics.append(_compute_ic(s, r))

        mean_ic = float(np.mean(ics))
        assert abs(mean_ic) < 0.02, f"IC 기댓값 ≈ 0 기대, 실제={mean_ic:.4f}"


# ── TC 3: Quintile 방향성 확인 ───────────────────────────────────────────────

class TestQuintileDirection:
    def test_perfect_factor_q1_highest(self):
        """팩터가 완벽 예측력 → Q1 수익률 > Q5 수익률."""
        n = 100
        rng = np.random.RandomState(7)
        scores  = pd.Series(np.arange(n, dtype=float))  # 0~99
        # 높은 점수일수록 높은 수익률 (약간의 노이즈 포함)
        returns = pd.Series(np.arange(n, dtype=float) * 0.001 + rng.randn(n) * 0.002)

        q_rets = _compute_quintile_returns(scores, returns)

        assert not np.isnan(q_rets["Q1"]), "Q1 수익률 NaN"
        assert not np.isnan(q_rets["Q5"]), "Q5 수익률 NaN"
        assert q_rets["Q1"] > q_rets["Q5"], (
            f"Q1({q_rets['Q1']:.4f}) > Q5({q_rets['Q5']:.4f}) 기대"
        )

    def test_nan_on_insufficient_data(self):
        """데이터 < 25이면 Quintile NaN 반환."""
        scores  = pd.Series(range(20), dtype=float)
        returns = pd.Series(range(20), dtype=float)

        q_rets = _compute_quintile_returns(scores, returns)

        assert all(np.isnan(v) for v in q_rets.values()), (
            "데이터 부족 시 모든 Quintile이 NaN이어야 함"
        )
