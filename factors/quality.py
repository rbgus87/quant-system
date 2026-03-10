# factors/quality.py
import pandas as pd
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class QualityFactor:
    """퀄리티 팩터 계산

    - ROE = EPS / BPS * 100 (pykrx는 ROE 미제공 → 직접 계산)
    - 부채비율 역수 (선택, 외부 데이터 필요)
    """

    def calculate(
        self,
        fundamentals: pd.DataFrame,
        debt_ratio: Optional[pd.Series] = None,
    ) -> pd.Series:
        """복합 퀄리티 스코어 계산

        Args:
            fundamentals: DataFrame (index=ticker, EPS·BPS 컬럼 필요)
            debt_ratio: 부채비율 Series (선택, index=ticker)

        Returns:
            Series (index=ticker, values=quality_score 0~100)
        """
        score_parts: dict[str, tuple[pd.Series, float]] = {}

        # ROE 스코어 (60% 가중)
        roe_score = self._calc_roe_score(fundamentals)
        if not roe_score.empty:
            score_parts["roe"] = (roe_score, 0.60)

        # 부채비율 역수 스코어 (40% 가중, 데이터 있을 때만)
        if debt_ratio is not None and not debt_ratio.empty:
            d = debt_ratio[debt_ratio >= 0]
            d = d.clip(upper=d.quantile(0.99))
            debt_score = (1 / (d + 1)).rank(pct=True) * 100
            score_parts["debt"] = (debt_score, 0.40)

        if not score_parts:
            logger.warning("퀄리티 팩터: 유효한 지표 없음")
            return pd.Series(dtype=float, name="quality_score")

        # 가중 합산 (ROE만 있으면 100% ROE로)
        composite: Optional[pd.Series] = None
        total_w = 0.0
        for name, (score, weight) in score_parts.items():
            if composite is None:
                composite = score * weight
            else:
                composite = composite.add(score * weight, fill_value=0)
            total_w += weight

        result = composite / total_w  # type: ignore[operator]
        result.name = "quality_score"
        logger.info(f"퀄리티 스코어 계산 완료: {len(result)}개 종목")
        return result

    @staticmethod
    def _calc_roe_score(fundamentals: pd.DataFrame) -> pd.Series:
        """ROE = EPS / BPS * 100 계산 후 순위 스코어 변환

        처리 기준:
        - BPS <= 0: 자본잠식 → 제외
        - ROE 범위: -50% ~ +100% (극단값 클리핑)

        Args:
            fundamentals: DataFrame (index=ticker, columns=[EPS, BPS, ...])

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        if "EPS" not in fundamentals.columns or "BPS" not in fundamentals.columns:
            logger.warning("EPS 또는 BPS 컬럼 없음")
            return pd.Series(dtype=float)

        eps = fundamentals["EPS"]
        bps = fundamentals["BPS"]

        valid = bps[bps > 0].index  # 자본잠식 제거
        roe = (eps[valid] / bps[valid]) * 100

        # 극단값 클리핑
        roe = roe.clip(lower=-50, upper=100)

        return roe.rank(pct=True) * 100
