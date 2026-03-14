# factors/utils.py
import pandas as pd
import logging

logger = logging.getLogger(__name__)


def weighted_average_nan_safe(
    parts: dict[str, tuple[pd.Series, float]],
) -> pd.Series:
    """NaN-aware 가중 합산 (union index 기반)

    각 지표가 일부 종목에만 존재해도 가용 가중치를 재분배하여
    모든 종목의 복합 스코어를 계산합니다.

    Args:
        parts: {지표명: (스코어 Series, 가중치)} dict
            스코어 Series의 index는 ticker, values는 0~100 스코어

    Returns:
        가중 평균 스코어 Series (유효 가중치 > 0인 종목만 포함)
    """
    if not parts:
        return pd.Series(dtype=float)

    all_scores = [s for s, _ in parts.values()]
    union_idx = all_scores[0].index
    for s in all_scores[1:]:
        union_idx = union_idx.union(s.index)

    composite = pd.Series(0.0, index=union_idx)
    weight_sum = pd.Series(0.0, index=union_idx)

    for name, (score, weight) in parts.items():
        aligned = score.reindex(union_idx)
        mask = aligned.notna()
        composite[mask] += aligned[mask] * weight
        weight_sum[mask] += weight

    valid = weight_sum > 0
    composite[valid] /= weight_sum[valid]
    return composite[valid]
