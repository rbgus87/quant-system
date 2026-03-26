"""v1.1 vs v2.0 팩터 대조 실험

v1.1: PBR(50%) + PER(30%) + DIV(20%), ROE(40%) + EY(30%) + 배당(30%)
v2.0: PBR(50%) + PSR(30%) + DIV(20%), OP/A(40%) + EY(30%) + F-Score(30%)

코드를 영구 변경하지 않고, 팩터를 임시로 오버라이드하여 비교합니다.
"""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import pandas as pd
import numpy as np
from config.logging_config import setup_logging
from config.settings import settings
from factors.utils import weighted_average_nan_safe

setup_logging()
logger = logging.getLogger(__name__)


class ValueFactorV11:
    """v1.1 밸류: PBR(50%) + PER(30%) + DIV(20%)"""

    def calculate(self, fundamentals: pd.DataFrame) -> pd.Series:
        score_parts: dict[str, tuple[pd.Series, float]] = {}

        if "PBR" in fundamentals.columns:
            pbr = fundamentals["PBR"].copy()
            pbr = pbr[pbr > 0].clip(upper=pbr[pbr > 0].quantile(0.99))
            score_parts["PBR"] = ((1 / pbr).rank(pct=True) * 100, 0.50)

        if "PER" in fundamentals.columns:
            per = fundamentals["PER"].copy()
            per = per[per > 0].clip(upper=per[per > 0].quantile(0.99))
            if not per.empty:
                score_parts["PER"] = ((1 / per).rank(pct=True) * 100, 0.30)

        if "DIV" in fundamentals.columns:
            div = fundamentals["DIV"].copy()
            div = div[div >= 0]
            score_parts["DIV"] = (div.rank(pct=True) * 100, 0.20)

        if not score_parts:
            return pd.Series(dtype=float, name="value_score")
        result = weighted_average_nan_safe(score_parts)
        result.name = "value_score"
        return result.sort_values(ascending=False)


class QualityFactorV11:
    """v1.1 퀄리티: ROE(40%) + EY(30%) + 배당(30%)"""

    @staticmethod
    def calc_fscore(fundamentals: pd.DataFrame) -> pd.Series:
        from factors.quality import QualityFactor
        return QualityFactor.calc_fscore(fundamentals)

    @staticmethod
    def apply_fscore_filter(fundamentals, fscore, min_fscore=None):
        from factors.quality import QualityFactor
        return QualityFactor.apply_fscore_filter(fundamentals, fscore, min_fscore)

    def calculate(self, fundamentals: pd.DataFrame) -> pd.Series:
        score_parts: dict[str, tuple[pd.Series, float]] = {}

        # ROE
        if "EPS" in fundamentals.columns and "BPS" in fundamentals.columns:
            eps = fundamentals["EPS"]
            bps = fundamentals["BPS"]
            valid = bps[bps > 0].index
            roe = (eps[valid] / bps[valid] * 100).clip(lower=-50, upper=100)
            score_parts["roe"] = (roe.rank(pct=True) * 100, 0.40)

        # EY
        if "PER" in fundamentals.columns:
            per = fundamentals["PER"]
            valid_per = per[per > 0]
            if not valid_per.empty:
                ey = (1 / valid_per).clip(upper=(1 / valid_per).quantile(0.99))
                score_parts["ey"] = (ey.rank(pct=True) * 100, 0.30)

        # 배당
        if "DIV" in fundamentals.columns:
            div = fundamentals["DIV"].fillna(0)
            valid_div = div[div >= 0].clip(upper=div[div >= 0].quantile(0.99))
            score_parts["div"] = (valid_div.rank(pct=True) * 100, 0.30)

        if not score_parts:
            return pd.Series(dtype=float, name="quality_score")
        result = weighted_average_nan_safe(score_parts)
        result.name = "quality_score"
        return result


def run_comparison() -> None:
    from backtest.engine import MultiFactorBacktest
    from backtest.metrics import PerformanceAnalyzer
    from strategy.screener import MultiFactorScreener

    # 2015~2016은 DART 데이터 부재(2013~2014 없음)로 모멘텀 전용
    # 3팩터 비교를 위해 2017년부터 시작
    start = "2017-01-01"
    end = "2024-12-31"

    # --- v2.0 (현재 코드) ---
    logger.info("=" * 60)
    logger.info("v2.0 팩터 (PSR + OP/A) 백테스트")
    logger.info("=" * 60)
    MultiFactorScreener._factor_cache.clear()
    engine_v2 = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result_v2 = engine_v2.run(start, end)
    analyzer = PerformanceAnalyzer()
    returns_v2 = result_v2["returns"].dropna()
    metrics_v2 = analyzer.summary(result_v2["portfolio_value"], returns_v2)

    # --- v1.1 (PER + ROE) ---
    # screener의 팩터 엔진을 임시로 교체
    logger.info("=" * 60)
    logger.info("v1.1 팩터 (PER + ROE) 백테스트")
    logger.info("=" * 60)
    MultiFactorScreener._factor_cache.clear()
    engine_v1 = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)

    # 팩터 엔진 교체 (screener 내부)
    engine_v1.screener.value_factor = ValueFactorV11()
    engine_v1.screener.quality_factor = QualityFactorV11()

    result_v1 = engine_v1.run(start, end)
    returns_v1 = result_v1["returns"].dropna()
    metrics_v1 = analyzer.summary(result_v1["portfolio_value"], returns_v1)

    # --- 비교 테이블 ---
    print("\n" + "=" * 70)
    print(f"{'v1.1 vs v2.0 팩터 대조 실험 (2015-2024)':^70}")
    print("=" * 70)
    print(f"{'지표':>18} | {'v1.1 (PER+ROE)':>15} | {'v2.0 (PSR+OP/A)':>15} | {'차이':>10}")
    print("-" * 70)
    for key, label in [
        ("cagr", "CAGR"),
        ("mdd", "MDD"),
        ("sharpe", "Sharpe"),
        ("sortino", "Sortino"),
        ("calmar", "Calmar"),
        ("volatility", "Volatility"),
        ("total_return", "Total Return"),
    ]:
        v1 = metrics_v1.get(key, 0)
        v2 = metrics_v2.get(key, 0)
        diff = v2 - v1
        if key in ("cagr", "mdd", "volatility", "total_return"):
            print(f"{label:>18} | {v1:>14.2%} | {v2:>14.2%} | {diff:>+9.2%}")
        else:
            print(f"{label:>18} | {v1:>14.3f} | {v2:>14.3f} | {diff:>+9.3f}")
    print("=" * 70)


if __name__ == "__main__":
    run_comparison()
