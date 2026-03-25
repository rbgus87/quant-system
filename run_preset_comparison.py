"""프리셋별 백테스트 비교 실행 스크립트

4개 전략 프리셋(A, B, C, D)에 대해 동일 기간 백테스트를 실행하고
CAGR, MDD, Sharpe 등 핵심 지표를 비교합니다.

실행:
  python run_preset_comparison.py
  python run_preset_comparison.py --start 2015-01-01 --end 2024-12-31
"""
import argparse
import logging
import os
import sys
import yaml
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.logging_config import setup_logging

logger = logging.getLogger(__name__)

PRESETS = ["A", "B", "C", "D"]


def run_single_preset(
    preset_name: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    """단일 프리셋 백테스트 실행

    config.yaml을 임시로 수정하지 않고,
    Settings를 직접 재생성하여 프리셋을 적용합니다.

    Returns:
        성과 지표 dict 또는 None (실패 시)
    """
    # config.yaml 로드 후 preset만 교체하여 Settings 재생성
    config_path = os.getenv("CONFIG_PATH", "config/config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f)

    yaml_data["preset"] = preset_name

    # 전역 싱글톤 settings의 필드들을 직접 변경 (import 바인딩 유지)
    from config.settings import settings, _apply_yaml, _apply_section_data, validate_settings
    from dataclasses import fields as dc_fields
    import copy

    # 현재 상태 백업 (deepcopy로 원복용)
    backup = {}
    for f in dc_fields(settings):
        backup[f.name] = copy.deepcopy(getattr(settings, f.name))

    # 기본값으로 초기화 후 YAML 적용
    from config.settings import (
        FactorWeights, ValueWeights, MomentumConfig, QualityConfig,
        VolatilityConfig, MarketRegimeConfig, UniverseConfig,
        PortfolioConfig, TradingConfig,
    )
    settings.factor_weights = FactorWeights()
    settings.value_weights = ValueWeights()
    settings.momentum = MomentumConfig()
    settings.quality = QualityConfig()
    settings.volatility = VolatilityConfig()
    settings.market_regime = MarketRegimeConfig()
    settings.universe = UniverseConfig()
    settings.portfolio = PortfolioConfig()
    settings.trading = TradingConfig()

    _apply_yaml(settings, yaml_data)
    validate_settings(settings)

    try:
        from strategy.screener import MultiFactorScreener
        MultiFactorScreener._factor_cache.clear()

        from backtest.engine import MultiFactorBacktest
        from backtest.metrics import PerformanceAnalyzer

        logger.info(f"\n{'='*60}")
        logger.info(f"프리셋 {preset_name} 백테스트: {start_date} ~ {end_date}")
        logger.info(f"  팩터: V={settings.factor_weights.value}, M={settings.factor_weights.momentum}, Q={settings.factor_weights.quality}")
        logger.info(f"  시장: {settings.universe.market}, 종목수: {settings.portfolio.n_stocks}")
        logger.info(f"  MDD서킷: {settings.trading.max_drawdown_pct}, vol_target: {settings.trading.vol_target}")
        logger.info(f"{'='*60}")

        engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
        result = engine.run(start_date=start_date, end_date=end_date)

        if result.empty or "portfolio_value" not in result.columns:
            logger.error(f"프리셋 {preset_name}: 백테스트 결과 없음")
            return None

        analyzer = PerformanceAnalyzer()
        returns = result["returns"].dropna()
        metrics = analyzer.summary(result["portfolio_value"], returns)
        metrics["preset"] = preset_name
        return metrics

    except Exception as e:
        logger.error(f"프리셋 {preset_name} 백테스트 실패: {e}", exc_info=True)
        return None

    finally:
        # settings 원복
        for name, val in backup.items():
            setattr(settings, name, val)
        from strategy.screener import MultiFactorScreener
        MultiFactorScreener._factor_cache.clear()


def print_comparison_table(results: list[dict]) -> None:
    """프리셋별 성과 비교 테이블 출력"""
    # KPI 목표 (PRD_v2.md Section 5)
    kpi = {
        "cagr":   {"target": 0.15, "good": 0.12, "min": 0.08},
        "mdd":    {"target": -0.20, "good": -0.25, "min": -0.30},
        "sharpe": {"target": 1.0,  "good": 0.8,  "min": 0.6},
    }

    def grade(metric: str, value: float) -> str:
        k = kpi[metric]
        if metric == "mdd":
            if value >= k["target"]: return "++ 목표"
            if value >= k["good"]:   return "+  양호"
            if value >= k["min"]:    return "o  최소통과"
            return "x  미달"
        else:
            if value >= k["target"]: return "++ 목표"
            if value >= k["good"]:   return "+  양호"
            if value >= k["min"]:    return "o  최소통과"
            return "x  미달"

    header = f"{'Preset':>8} | {'CAGR':>8} | {'MDD':>8} | {'Sharpe':>7} | {'Sortino':>8} | {'Calmar':>7} | {'CAGR':>12} | {'MDD':>12} | {'Sharpe':>12}"
    sep = "-" * len(header)

    print(f"\n{sep}")
    print(f"{'프리셋별 백테스트 비교':^{len(header)}}")
    print(sep)
    print(f"{'Preset':>8} | {'CAGR':>8} | {'MDD':>8} | {'Sharpe':>7} | {'Sortino':>8} | {'Calmar':>7} | {'CAGR판정':>12} | {'MDD판정':>12} | {'Sharpe판정':>12}")
    print(sep)

    for r in results:
        p = r["preset"]
        cagr = r.get("cagr", 0)
        mdd = r.get("mdd", 0)
        sharpe = r.get("sharpe", 0)
        sortino = r.get("sortino", 0)
        calmar = r.get("calmar", 0)

        print(
            f"{p:>8} | {cagr:>7.1%} | {mdd:>7.1%} | {sharpe:>7.2f} | {sortino:>8.2f} | {calmar:>7.2f} "
            f"| {grade('cagr', cagr):>12} | {grade('mdd', mdd):>12} | {grade('sharpe', sharpe):>12}"
        )

    print(sep)
    print(f"KPI 기준: 목표(CAGR>15%, MDD>-20%, Sharpe>1.0) / 양호(12%/-25%/0.8) / 최소(8%/-30%/0.6)")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="프리셋별 백테스트 비교")
    parser.add_argument("--start", default="2015-01-01", help="시작일")
    parser.add_argument("--end", default="2024-12-31", help="종료일")
    parser.add_argument(
        "--presets", nargs="+", default=PRESETS,
        help="실행할 프리셋 (기본: A B C D)",
    )
    args = parser.parse_args()

    setup_logging()

    results: list[dict] = []
    for preset in args.presets:
        metrics = run_single_preset(preset, args.start, args.end)
        if metrics:
            results.append(metrics)
        else:
            results.append({"preset": preset, "cagr": 0, "mdd": 0, "sharpe": 0, "sortino": 0, "calmar": 0, "error": True})

    if results:
        print_comparison_table(results)


if __name__ == "__main__":
    main()
