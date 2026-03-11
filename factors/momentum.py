# factors/momentum.py
import pandas as pd
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MomentumFactor:
    """모멘텀 팩터 계산

    표준: 12개월 수익률 (최근 1개월 제외)
    → 단기 반전(Short-term Reversal) 효과 제거
    → 계산: t-1개월 가격 / t-12개월 가격 - 1
    """

    def calculate(
        self,
        returns_12m: pd.Series,
        returns_6m: Optional[pd.Series] = None,
        returns_3m: Optional[pd.Series] = None,
    ) -> pd.Series:
        """복합 모멘텀 스코어 계산

        Args:
            returns_12m: 12개월 수익률 (index=ticker, 최근 1개월 제외된 값)
            returns_6m: 6개월 수익률 (선택)
            returns_3m: 3개월 수익률 (선택)

        Returns:
            Series (index=ticker, values=momentum_score 0~100)
        """
        score_12m = self._single_score(returns_12m)

        if returns_6m is None and returns_3m is None:
            score_12m.name = "momentum_score"
            logger.info(f"모멘텀 스코어 계산 완료: {len(score_12m)}개 종목")
            return score_12m

        # 복합: 12M 60% + 6M 30% + 3M 10% (NaN-aware 가중 합산)
        score_parts: dict[str, tuple[pd.Series, float]] = {"12m": (score_12m, 0.60)}

        if returns_6m is not None:
            score_6m = self._single_score(returns_6m)
            if not score_6m.empty:
                score_parts["6m"] = (score_6m, 0.30)

        if returns_3m is not None:
            score_3m = self._single_score(returns_3m)
            if not score_3m.empty:
                score_parts["3m"] = (score_3m, 0.10)

        # union 인덱스 + 가중치 정규화
        union_idx = score_12m.index
        for _, (s, _) in score_parts.items():
            union_idx = union_idx.union(s.index)

        composite = pd.Series(0.0, index=union_idx)
        weight_sum = pd.Series(0.0, index=union_idx)
        for name, (score, weight) in score_parts.items():
            aligned = score.reindex(union_idx)
            mask = aligned.notna()
            composite[mask] += aligned[mask] * weight
            weight_sum[mask] += weight

        valid = weight_sum > 0
        composite[valid] /= weight_sum[valid]
        result = composite[valid]

        result.name = "momentum_score"
        logger.info(f"모멘텀 스코어 계산 완료: {len(result)}개 종목")
        return result

    @staticmethod
    def _single_score(returns: pd.Series) -> pd.Series:
        """단일 기간 수익률 → 0~100 순위 스코어 (Winsorize 포함)

        Args:
            returns: 수익률 Series (index=ticker)

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        clean = returns.dropna()
        if clean.empty:
            return pd.Series(dtype=float)
        # 상하위 1% Winsorize
        lower = clean.quantile(0.01)
        upper = clean.quantile(0.99)
        clipped = clean.clip(lower, upper)
        return clipped.rank(pct=True) * 100
