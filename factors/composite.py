# factors/composite.py
import pandas as pd
import logging
from typing import Optional
from config.settings import settings

logger = logging.getLogger(__name__)

# 금융주 섹터명 (WICS 기준)
FINANCE_SECTORS = {"은행", "증권", "보험", "기타금융", "다각화된금융"}


class MultiFactorComposite:
    """멀티팩터 스코어 합산 및 최종 종목 선정

    밸류 40% + 모멘텀 40% + 퀄리티 20%
    """

    def __init__(self) -> None:
        self.w = settings.factor_weights
        logger.info(
            f"팩터 가중치 — 밸류:{self.w.value}, 모멘텀:{self.w.momentum}, 퀄리티:{self.w.quality}"
        )

    def calculate(
        self,
        value_score: pd.Series,
        momentum_score: pd.Series,
        quality_score: pd.Series,
    ) -> pd.DataFrame:
        """3개 팩터 가중 합산

        Args:
            value_score: 밸류 스코어 (0~100)
            momentum_score: 모멘텀 스코어 (0~100)
            quality_score: 퀄리티 스코어 (0~100)

        Returns:
            DataFrame(index=ticker, columns=[value_score, momentum_score, quality_score, composite_score])
            composite_score 내림차순 정렬
        """
        # 세 팩터 공통 종목만 사용 (교집합)
        common = (
            set(value_score.index)
            & set(momentum_score.index)
            & set(quality_score.index)
        )
        logger.info(f"팩터 공통 종목: {len(common)}개")

        if not common:
            logger.warning("공통 종목 없음 — 빈 결과 반환")
            return pd.DataFrame(
                columns=[
                    "value_score",
                    "momentum_score",
                    "quality_score",
                    "composite_score",
                ]
            )

        common_list = sorted(common)
        df = pd.DataFrame(
            {
                "value_score": value_score.reindex(common_list),
                "momentum_score": momentum_score.reindex(common_list),
                "quality_score": quality_score.reindex(common_list),
            }
        )
        df["composite_score"] = (
            df["value_score"] * self.w.value
            + df["momentum_score"] * self.w.momentum
            + df["quality_score"] * self.w.quality
        )
        return df.sort_values("composite_score", ascending=False)

    def apply_universe_filter(
        self,
        composite_df: pd.DataFrame,
        market_cap: pd.Series,
        finance_tickers: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """유니버스 필터 적용

        Args:
            composite_df: 복합 스코어 DataFrame
            market_cap: 시가총액 Series (index=ticker)
            finance_tickers: 금융주 종목 코드 리스트

        Returns:
            필터 적용된 DataFrame
        """
        result = composite_df.copy()

        # 시가총액 하위 N% 제외
        if not market_cap.empty:
            threshold = market_cap.quantile(
                settings.universe.min_market_cap_percentile / 100
            )
            valid = market_cap[market_cap >= threshold].index
            before = len(result)
            result = result[result.index.isin(valid)]
            logger.info(f"시가총액 필터: {before} → {len(result)}개")

        # 금융주 제외
        if finance_tickers:
            before = len(result)
            result = result[~result.index.isin(finance_tickers)]
            logger.info(f"금융주 제외: {before} → {len(result)}개")

        return result

    def select_top(
        self,
        composite_df: pd.DataFrame,
        n: Optional[int] = None,
    ) -> pd.DataFrame:
        """상위 N개 종목 선정 (동일 비중)

        Args:
            composite_df: 복합 스코어 DataFrame (composite_score 내림차순)
            n: 선정 종목 수 (기본: settings.portfolio.n_stocks)

        Returns:
            DataFrame with weight 컬럼 추가
        """
        n = n or settings.portfolio.n_stocks
        selected = composite_df.head(n).copy()

        if len(selected) > 0:
            selected["weight"] = 1.0 / len(selected)

        logger.info(
            f"포트폴리오 구성 완료: {len(selected)}개 종목 | "
            f"평균 복합스코어: {selected['composite_score'].mean():.1f}"
        )
        return selected
