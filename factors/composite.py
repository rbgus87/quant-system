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
        min_factor_count: int = 2,
    ) -> pd.DataFrame:
        """3개 팩터 가중 합산

        2/3 이상 팩터가 있는 종목은 가용 가중치 정규화로 포함.
        결측 팩터가 있는 종목은 나머지 팩터 가중치를 재분배합니다.

        Args:
            value_score: 밸류 스코어 (0~100)
            momentum_score: 모멘텀 스코어 (0~100)
            quality_score: 퀄리티 스코어 (0~100)
            min_factor_count: 최소 필요 팩터 수 (기본 2)

        Returns:
            DataFrame(index=ticker, columns=[value_score, momentum_score, quality_score, composite_score])
            composite_score 내림차순 정렬
        """
        # 유니온 기반 — 1개 이상 팩터가 있는 모든 종목
        all_tickers = sorted(
            set(value_score.index)
            | set(momentum_score.index)
            | set(quality_score.index)
        )

        if not all_tickers:
            logger.warning("유효 종목 없음 — 빈 결과 반환")
            return pd.DataFrame(
                columns=[
                    "value_score",
                    "momentum_score",
                    "quality_score",
                    "composite_score",
                ]
            )

        df = pd.DataFrame(
            {
                "value_score": value_score.reindex(all_tickers),
                "momentum_score": momentum_score.reindex(all_tickers),
                "quality_score": quality_score.reindex(all_tickers),
            }
        )

        # 최소 팩터 수 필터
        factor_cols = ["value_score", "momentum_score", "quality_score"]
        factor_count = df[factor_cols].notna().sum(axis=1)
        df = df[factor_count >= min_factor_count].copy()

        if df.empty:
            logger.warning(f"최소 {min_factor_count}개 팩터 충족 종목 없음")
            return pd.DataFrame(
                columns=factor_cols + ["composite_score"]
            )

        n_full = (factor_count.reindex(df.index) == 3).sum()
        n_partial = len(df) - n_full
        logger.info(
            f"팩터 종목: {len(df)}개 (완전 {n_full}개, 부분 {n_partial}개)"
        )

        # 가중 합산 (NaN 팩터 가중치 재분배)
        weights = {
            "value_score": self.w.value,
            "momentum_score": self.w.momentum,
            "quality_score": self.w.quality,
        }
        weighted_sum = pd.Series(0.0, index=df.index)
        weight_sum = pd.Series(0.0, index=df.index)

        for col, w in weights.items():
            mask = df[col].notna()
            weighted_sum[mask] += df.loc[mask, col] * w
            weight_sum[mask] += w

        df["composite_score"] = weighted_sum / weight_sum
        df = df.dropna(subset=["composite_score"])
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
