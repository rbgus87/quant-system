# factors/value.py
import pandas as pd
import logging
from config.settings import settings

logger = logging.getLogger(__name__)


class ValueFactor:
    """밸류 팩터 계산

    - PBR (Price-to-Book Ratio): 낮을수록 저평가 → 역수 변환 후 순위
    - PER (Price-to-Earnings Ratio): 낮을수록 저평가 → 역수 변환 후 순위 (적자 제외)
    - DIV (Dividend Yield): 높을수록 선호 → 그대로 순위
    """

    def __init__(self) -> None:
        self.w = settings.value_weights  # ValueWeights (pbr=0.5, per=0.3, div=0.2)

    def calculate(self, fundamentals: pd.DataFrame) -> pd.Series:
        """복합 밸류 스코어 계산

        Args:
            fundamentals: DataFrame (index=ticker, columns 중 PBR·PER·DIV 포함)

        Returns:
            Series (index=ticker, values=value_score 0~100, name='value_score')
        """
        score_parts: dict[str, tuple[pd.Series, float]] = {}

        # PBR 스코어 (낮을수록 고득점 → 역수 변환)
        if "PBR" in fundamentals.columns:
            pbr = fundamentals["PBR"].copy()
            pbr = pbr[pbr > 0]
            pbr = pbr.clip(upper=pbr.quantile(0.99))
            score_parts["PBR"] = (self._rank_score(1 / pbr), self.w.pbr)

        # PER 스코어 (낮을수록 고득점, 적자 기업 제외)
        if "PER" in fundamentals.columns:
            per = fundamentals["PER"].copy()
            per = per[per > 0]
            per = per.clip(upper=per.quantile(0.99))
            score_parts["PER"] = (self._rank_score(1 / per), self.w.per)

        # DIV 스코어 (높을수록 고득점)
        if "DIV" in fundamentals.columns:
            div = fundamentals["DIV"].copy()
            div = div[div >= 0]
            score_parts["DIV"] = (self._rank_score(div), self.w.div)

        if not score_parts:
            logger.warning("밸류 팩터: 유효한 지표 없음")
            return pd.Series(dtype=float, name="value_score")

        # 가중 평균 (union + NaN-aware: 지표가 일부 없는 종목도 포함)
        all_scores = [s for s, _ in score_parts.values()]
        union_idx = all_scores[0].index
        for s in all_scores[1:]:
            union_idx = union_idx.union(s.index)

        composite = pd.Series(0.0, index=union_idx)
        weight_sum = pd.Series(0.0, index=union_idx)
        for name, (score, weight) in score_parts.items():
            aligned = score.reindex(union_idx)
            mask = aligned.notna()
            composite[mask] += aligned[mask] * weight
            weight_sum[mask] += weight

        # 유효 가중합으로 정규화
        valid = weight_sum > 0
        composite[valid] /= weight_sum[valid]
        composite = composite[valid]

        composite.name = "value_score"
        logger.info(f"밸류 스코어 계산 완료: {len(composite)}개 종목")
        return composite.sort_values(ascending=False)

    @staticmethod
    def _rank_score(series: pd.Series) -> pd.Series:
        """순위 기반 0~100 정규화 (이상치에 강건)

        Args:
            series: 원시 지표 Series

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        return series.rank(pct=True, na_option="keep") * 100
