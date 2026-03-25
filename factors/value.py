# factors/value.py
import pandas as pd
import logging
from config.settings import settings
from factors.utils import weighted_average_nan_safe

logger = logging.getLogger(__name__)


class ValueFactor:
    """밸류 팩터 계산

    - PBR (Price-to-Book Ratio): 낮을수록 저평가 → 역수 변환 후 순위
    - PCR (Price-to-Cashflow Ratio): 낮을수록 저평가 → 역수 변환 후 순위 (영업CF 마이너스 제외)
    - DIV (Dividend Yield): 높을수록 선호 → 그대로 순위
    """

    def __init__(self) -> None:
        self.w = settings.value_weights  # ValueWeights (pbr=0.5, pcr=0.3, div=0.2)

    def calculate(self, fundamentals: pd.DataFrame) -> pd.Series:
        """복합 밸류 스코어 계산

        Args:
            fundamentals: DataFrame (index=ticker, columns 중 PBR·PCR·DIV 포함)

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

        # PCR 스코어 (낮을수록 고득점, 영업현금흐름 마이너스 제외)
        # PCR 데이터가 없으면 PSR(주가매출비율)로 폴백
        pcr_col = None
        if "PCR" in fundamentals.columns and fundamentals["PCR"].notna().any():
            pcr_col = "PCR"
        elif "PSR" in fundamentals.columns and fundamentals["PSR"].notna().any():
            pcr_col = "PSR"
            logger.debug("PCR 데이터 없음 → PSR 폴백")

        if pcr_col is not None:
            pcr = fundamentals[pcr_col].copy()
            pcr = pcr[pcr > 0]
            if not pcr.empty:
                pcr = pcr.clip(upper=pcr.quantile(0.99))
                score_parts["PCR"] = (self._rank_score(1 / pcr), self.w.pcr)

        # DIV 스코어 (높을수록 고득점)
        if "DIV" in fundamentals.columns:
            div = fundamentals["DIV"].copy()
            div = div[div >= 0]
            score_parts["DIV"] = (self._rank_score(div), self.w.div)

        if not score_parts:
            logger.warning("밸류 팩터: 유효한 지표 없음")
            return pd.Series(dtype=float, name="value_score")

        composite = weighted_average_nan_safe(score_parts)
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
