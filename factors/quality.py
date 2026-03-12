# factors/quality.py
import pandas as pd
import numpy as np
import logging
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)


class QualityFactor:
    """퀄리티 팩터 계산

    구성 지표:
    - ROE = EPS / BPS * 100 (수익성)
    - Earnings Yield = 1 / PER (이익수익률, 수익 안정성)
    - 배당 지급 여부 (기업 질 신호)
    - 부채비율 역수 (선택, 외부 데이터 필요)
    - F-Score 필터 (간소화 5점 피오트로스키)
    """

    def calculate(
        self,
        fundamentals: pd.DataFrame,
        debt_ratio: Optional[pd.Series] = None,
    ) -> pd.Series:
        """복합 퀄리티 스코어 계산

        Args:
            fundamentals: DataFrame (index=ticker, EPS·BPS·PER·DIV 컬럼 필요)
            debt_ratio: 부채비율 Series (선택, index=ticker)

        Returns:
            Series (index=ticker, values=quality_score 0~100)
        """
        score_parts: dict[str, tuple[pd.Series, float]] = {}

        # ROE 스코어 (40% 가중)
        roe_score = self._calc_roe_score(fundamentals)
        if not roe_score.empty:
            score_parts["roe"] = (roe_score, 0.40)

        # Earnings Yield 스코어 (30% 가중)
        ey_score = self._calc_earnings_yield_score(fundamentals)
        if not ey_score.empty:
            score_parts["earnings_yield"] = (ey_score, 0.30)

        # 배당 지급 스코어 (30% 가중)
        div_score = self._calc_dividend_score(fundamentals)
        if not div_score.empty:
            score_parts["dividend"] = (div_score, 0.30)

        # 부채비율 역수 스코어 (데이터 있을 때만, 가중치 재분배)
        if debt_ratio is not None and not debt_ratio.empty:
            d = debt_ratio[debt_ratio >= 0]
            d = d.clip(upper=d.quantile(0.99))
            debt_score = (1 / (d + 1)).rank(pct=True) * 100
            score_parts["debt"] = (debt_score, 0.20)

        if not score_parts:
            logger.warning("퀄리티 팩터: 유효한 지표 없음")
            return pd.Series(dtype=float, name="quality_score")

        # 가중 합산 (NaN-aware: 종목별 가용 가중치 정규화)
        all_tickers = sorted(
            set().union(*(s.index for s, _ in score_parts.values()))
        )
        df = pd.DataFrame(index=all_tickers)
        weights: dict[str, float] = {}
        for name, (score, weight) in score_parts.items():
            df[name] = score.reindex(all_tickers)
            weights[name] = weight

        weighted_sum = pd.Series(0.0, index=all_tickers)
        weight_sum = pd.Series(0.0, index=all_tickers)
        for col, w in weights.items():
            mask = df[col].notna()
            weighted_sum[mask] += df.loc[mask, col] * w
            weight_sum[mask] += w

        # 최소 1개 지표 필요
        valid_mask = weight_sum > 0
        result = pd.Series(dtype=float, index=all_tickers)
        result[valid_mask] = weighted_sum[valid_mask] / weight_sum[valid_mask]
        result = result.dropna()
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

    @staticmethod
    def _calc_earnings_yield_score(fundamentals: pd.DataFrame) -> pd.Series:
        """Earnings Yield (1/PER) 순위 스코어

        PER가 양수인 종목만 대상 (적자 기업 제외)
        높은 이익수익률 = 높은 스코어

        Args:
            fundamentals: DataFrame (PER 컬럼 필요)

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        if "PER" not in fundamentals.columns:
            return pd.Series(dtype=float)

        per = fundamentals["PER"]
        valid = per[per > 0]
        if valid.empty:
            return pd.Series(dtype=float)

        ey = 1 / valid  # Earnings Yield
        ey = ey.clip(upper=ey.quantile(0.99))
        return ey.rank(pct=True) * 100

    @staticmethod
    def _calc_dividend_score(fundamentals: pd.DataFrame) -> pd.Series:
        """배당수익률 기반 퀄리티 스코어

        배당을 지급하는 기업일수록 높은 스코어.
        배당 미지급(DIV=0 or NaN) 종목도 포함하되 낮은 스코어 부여.

        Args:
            fundamentals: DataFrame (DIV 컬럼 필요)

        Returns:
            0~100 범위의 순위 스코어 Series
        """
        if "DIV" not in fundamentals.columns:
            return pd.Series(dtype=float)

        div = fundamentals["DIV"].fillna(0)
        valid = div[div >= 0]
        if valid.empty:
            return pd.Series(dtype=float)

        valid = valid.clip(upper=valid.quantile(0.99))
        return valid.rank(pct=True) * 100

    @staticmethod
    def calc_fscore(fundamentals: pd.DataFrame) -> pd.Series:
        """간소화 F-Score 계산 (5점 만점, 피오트로스키 기반)

        현재 보유한 펀더멘털 데이터만으로 계산 가능한 5개 항목:
          1. 수익성: ROE > 0 (+1)
          2. 흑자: PER > 0 (EPS 양수) (+1)
          3. 배당: DIV > 0 (배당 지급) (+1)
          4. 가치: PBR < 유니버스 중앙값 (+1)
          5. 수익 효율: ROE > 유니버스 중앙값 (+1)

        Args:
            fundamentals: DataFrame (index=ticker, 필요 컬럼: EPS, BPS, PER, PBR, DIV)

        Returns:
            Series (index=ticker, values=0~5 정수 F-Score)
        """
        if fundamentals.empty:
            return pd.Series(dtype=int, name="fscore")

        fscore = pd.Series(0, index=fundamentals.index, name="fscore")

        # 1. 수익성: ROE > 0
        if "EPS" in fundamentals.columns and "BPS" in fundamentals.columns:
            eps = fundamentals["EPS"]
            bps = fundamentals["BPS"]
            valid_bps = bps > 0
            roe = pd.Series(np.nan, index=fundamentals.index)
            roe[valid_bps] = eps[valid_bps] / bps[valid_bps]
            fscore += (roe > 0).astype(int)

            # 5. 수익 효율: ROE > 유니버스 중앙값
            roe_median = roe[roe.notna()].median()
            if not np.isnan(roe_median):
                fscore += (roe > roe_median).astype(int)

        # 2. 흑자: PER > 0 (= EPS 양수)
        if "PER" in fundamentals.columns:
            fscore += (fundamentals["PER"] > 0).astype(int)

        # 3. 배당 지급: DIV > 0
        if "DIV" in fundamentals.columns:
            div = fundamentals["DIV"].fillna(0)
            fscore += (div > 0).astype(int)

        # 4. 가치: PBR < 유니버스 중앙값
        if "PBR" in fundamentals.columns:
            pbr = fundamentals["PBR"]
            pbr_valid = pbr[pbr > 0]
            if not pbr_valid.empty:
                pbr_median = pbr_valid.median()
                fscore += ((pbr > 0) & (pbr < pbr_median)).astype(int)

        logger.info(
            f"F-Score 계산 완료: {len(fscore)}개 종목, "
            f"평균={fscore.mean():.1f}, 분포={dict(fscore.value_counts().sort_index())}"
        )
        return fscore

    @staticmethod
    def apply_fscore_filter(
        fundamentals: pd.DataFrame,
        fscore: pd.Series,
        min_fscore: Optional[int] = None,
    ) -> pd.DataFrame:
        """F-Score 기준 미달 종목 제거

        Args:
            fundamentals: 펀더멘털 DataFrame
            fscore: F-Score Series
            min_fscore: 최소 F-Score (기본: settings.quality.min_fscore)

        Returns:
            필터링된 fundamentals DataFrame
        """
        if min_fscore is None:
            min_fscore = settings.quality.min_fscore

        if fscore.empty:
            return fundamentals

        passing = fscore[fscore >= min_fscore].index
        before = len(fundamentals)
        filtered = fundamentals[fundamentals.index.isin(passing)]
        removed = before - len(filtered)

        if removed > 0:
            logger.info(
                f"F-Score 필터: {before} → {len(filtered)}개 종목 "
                f"({removed}개 제거, 기준={min_fscore}점 이상)"
            )

        return filtered
