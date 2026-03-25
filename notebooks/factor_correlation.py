"""팩터 상관관계 검증 스크립트 (Phase 1-4)

v2.0 팩터 교체 후 Value-Quality 독립성이 개선됐는지 수치로 확인.
실제 데이터 없이도 synthetic 데이터로 테스트 가능.

실행:
  python notebooks/factor_correlation.py
  python notebooks/factor_correlation.py --date 20240628
  python notebooks/factor_correlation.py --synthetic
"""

import os
import sys
import logging
import argparse

import numpy as np
import pandas as pd

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


def check_factor_correlation(
    value_score: pd.Series,
    momentum_score: pd.Series,
    quality_score: pd.Series,
) -> pd.DataFrame:
    """3개 팩터 스코어 간 피어슨 상관계수 매트릭스 계산

    판단 기준:
    - |상관계수| < 0.3: 양호 (독립적)
    - |상관계수| 0.3~0.5: 주의 (약한 상관)
    - |상관계수| > 0.5: 경고 (이중 가중 가능성)

    Args:
        value_score: 밸류 팩터 스코어 (index=ticker)
        momentum_score: 모멘텀 팩터 스코어 (index=ticker)
        quality_score: 퀄리티 팩터 스코어 (index=ticker)

    Returns:
        3x3 상관계수 매트릭스 DataFrame
    """
    df = pd.DataFrame({
        "value": value_score,
        "momentum": momentum_score,
        "quality": quality_score,
    }).dropna()

    if len(df) < 10:
        logger.warning(f"유효 종목 수 부족: {len(df)}개 (최소 10개 필요)")
        return pd.DataFrame()

    corr = df.corr()

    print("\n" + "=" * 50)
    print("팩터 상관계수 매트릭스")
    print("=" * 50)
    print(corr.round(3).to_string())
    print()

    # 판단 출력
    pairs = [("value", "momentum"), ("value", "quality"), ("momentum", "quality")]
    for a, b in pairs:
        c = abs(corr.loc[a, b])
        if c > 0.5:
            label = "!! 경고 (이중 가중 가능성)"
        elif c > 0.3:
            label = "~~ 주의 (약한 상관)"
        else:
            label = "OK 양호 (독립적)"
        print(f"  {a:10s} - {b:10s}: {corr.loc[a, b]:+.3f}  {label}")

    # Value-Quality 핵심 체크
    vq_corr = abs(corr.loc["value", "quality"])
    print()
    if vq_corr > 0.5:
        print(f"  * Value-Quality 상관 {vq_corr:.3f} - 이중 가중 잔존 가능")
    else:
        print(f"  * Value-Quality 상관 {vq_corr:.3f} - 독립성 양호 (v2.0 목표 달성)")

    print("=" * 50)
    return corr


def run_with_real_data(date_str: str) -> pd.DataFrame:
    """실제 데이터로 상관관계 검증

    Args:
        date_str: 기준 날짜 (YYYYMMDD)

    Returns:
        상관계수 매트릭스
    """
    from strategy.screener import MultiFactorScreener

    screener = MultiFactorScreener()
    composite_df = screener.screen(date_str)

    if composite_df.empty:
        logger.error("스크리닝 결과 없음")
        return pd.DataFrame()

    return check_factor_correlation(
        composite_df["value_score"],
        composite_df["momentum_score"],
        composite_df["quality_score"],
    )


def run_with_synthetic_data() -> pd.DataFrame:
    """Synthetic 데이터로 상관관계 검증 (API 없이 테스트)

    v2.0 팩터 구성을 시뮬레이션:
    - Value: PBR(역수) + PCR(역수) + DIV → 낮은 PBR/PCR, 높은 배당
    - Momentum: 12M 수익률 기반 → 가격 모멘텀
    - Quality: GP/A + EY(1/PER) + F-Score → 수익성 + 이익수익률

    Returns:
        상관계수 매트릭스
    """
    np.random.seed(42)
    n = 200

    from factors.value import ValueFactor
    from factors.momentum import MomentumFactor
    from factors.quality import QualityFactor

    tickers = [f"T{i:04d}" for i in range(n)]

    # 팩터 입력 데이터 생성 (상호 독립)
    fundamentals = pd.DataFrame({
        "PBR": np.random.lognormal(0, 0.5, n),
        "PCR": np.random.lognormal(1.5, 0.6, n),
        "DIV": np.random.exponential(1.5, n),
        "EPS": np.random.normal(5000, 3000, n),
        "BPS": np.abs(np.random.normal(30000, 10000, n)) + 1000,
        "PER": np.random.lognormal(2.3, 0.5, n),
        "PBR_raw": np.random.lognormal(0, 0.5, n),
    }, index=tickers)

    returns_12m = pd.Series(
        np.random.normal(0.1, 0.3, n), index=tickers
    )

    value_scores = ValueFactor().calculate(fundamentals)
    momentum_scores = MomentumFactor().calculate(returns_12m)
    quality_scores = QualityFactor().calculate(fundamentals)

    print(f"\nSynthetic 데이터: {n}개 종목")
    print(f"  Value: {len(value_scores)}개, Momentum: {len(momentum_scores)}개, "
          f"Quality: {len(quality_scores)}개")

    return check_factor_correlation(value_scores, momentum_scores, quality_scores)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="팩터 상관관계 검증")
    parser.add_argument("--date", default=None, help="기준 날짜 (YYYYMMDD)")
    parser.add_argument(
        "--synthetic", action="store_true",
        help="Synthetic 데이터로 검증 (API 불필요)"
    )
    args = parser.parse_args()

    if args.synthetic or args.date is None:
        run_with_synthetic_data()
    else:
        run_with_real_data(args.date)
