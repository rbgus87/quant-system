"""알파 실험: 팩터별 단독 + 종목 수 변화"""
import os, sys, logging, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import numpy as np
import pandas as pd
from dataclasses import fields as dc_fields

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from strategy.screener import MultiFactorScreener

setup_logging()
logger = logging.getLogger(__name__)

START = "2017-01-01"
END = "2024-12-31"


def backup_settings() -> dict:
    backup = {}
    for f in dc_fields(settings):
        backup[f.name] = copy.deepcopy(getattr(settings, f.name))
    return backup


def restore_settings(backup: dict) -> None:
    for name, val in backup.items():
        setattr(settings, name, val)


def base_config() -> None:
    """공통 기본 설정: CB OFF, 분기, 동일가중"""
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.portfolio.weight_method = "equal"


def run_bt(label: str) -> dict:
    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run(START, END)
    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(result["portfolio_value"], returns)
    metrics["label"] = label
    pv = result["portfolio_value"]
    yearly = {}
    for year in range(2017, 2025):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly
    return metrics


def run_random(label: str) -> dict:
    MultiFactorScreener._factor_cache.clear()
    screener = MultiFactorScreener()
    orig = screener.screen

    def rand_screen(date, market=None, n_stocks=None):
        result = orig(date, market=market, n_stocks=500)
        if result.empty:
            return result
        np.random.seed(hash(date) % 2**31)
        result["composite_score"] = np.random.uniform(0, 100, len(result))
        result = result.sort_values("composite_score", ascending=False)
        return result.head(n_stocks or settings.portfolio.n_stocks)

    screener.screen = rand_screen
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    engine.screener = screener
    result = engine.run(START, END)
    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(result["portfolio_value"], returns)
    metrics["label"] = label
    pv = result["portfolio_value"]
    yearly = {}
    for year in range(2017, 2025):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly
    return metrics


def print_table(results: list[dict], title: str) -> None:
    labels = [r["label"] for r in results]
    print(f"\n{'=' * (14 + 13 * len(labels))}")
    print(f"{title:^{14 + 13 * len(labels)}}")
    print(f"{'=' * (14 + 13 * len(labels))}")

    header = f"{'':>12}"
    for lb in labels:
        header += f" | {lb:>10}"
    print(header)
    print("-" * (14 + 13 * len(labels)))

    for key, name in [("cagr", "CAGR"), ("mdd", "MDD"), ("sharpe", "Sharpe"),
                       ("total_return", "Total")]:
        row = f"{name:>12}"
        for r in results:
            v = r.get(key, 0)
            if key in ("cagr", "mdd", "total_return"):
                row += f" | {v:>9.2%}"
            else:
                row += f" | {v:>9.3f}"
        print(row)

    # 무작위 대비 알파
    rand_cagr = None
    for r in results:
        if "무작위" in r["label"]:
            rand_cagr = r.get("cagr", 0)
            break
    if rand_cagr is not None:
        row = f"{'Alpha':>12}"
        for r in results:
            alpha = r.get("cagr", 0) - rand_cagr
            row += f" | {alpha:>+8.2%}p"
        print(row)

    # 연도별
    print()
    header2 = f"{'연도':>12}"
    for lb in labels:
        header2 += f" | {lb:>10}"
    print(header2)
    print("-" * (14 + 13 * len(labels)))
    for year in range(2017, 2025):
        row = f"{year:>12}"
        for r in results:
            row += f" | {r['yearly'].get(year, 0):>9.1%}"
        print(row)
    print("=" * (14 + 13 * len(labels)))


def experiment1() -> list[dict]:
    """실험 1: 팩터별 단독 전략"""
    print("\n>>> 실험 1: 팩터별 단독 전략")
    backup = backup_settings()
    results = []

    # 무작위
    restore_settings(backup)
    base_config()
    settings.portfolio.n_stocks = 20
    results.append(run_random("무작위"))

    # Momentum 100%
    restore_settings(backup)
    base_config()
    settings.portfolio.n_stocks = 20
    settings.factor_weights.value = 0.0
    settings.factor_weights.momentum = 1.0
    settings.factor_weights.quality = 0.0
    settings.momentum.absolute_momentum_enabled = False
    results.append(run_bt("M100%"))

    # Quality 100%
    restore_settings(backup)
    base_config()
    settings.portfolio.n_stocks = 20
    settings.factor_weights.value = 0.0
    settings.factor_weights.momentum = 0.0
    settings.factor_weights.quality = 1.0
    results.append(run_bt("Q100%"))

    # Value 100%
    restore_settings(backup)
    base_config()
    settings.portfolio.n_stocks = 20
    settings.factor_weights.value = 1.0
    settings.factor_weights.momentum = 0.0
    settings.factor_weights.quality = 0.0
    results.append(run_bt("V100%"))

    # 기존 A (CB OFF)
    restore_settings(backup)
    base_config()
    settings.portfolio.n_stocks = 20
    results.append(run_bt("A(CB OFF)"))

    restore_settings(backup)
    print_table(results, "실험 1: 팩터별 단독 전략 (2017-2024)")
    return results


def experiment2() -> list[dict]:
    """실험 2: 종목 수 변화"""
    print("\n>>> 실험 2: 종목 수 변화 (A 가중치 + CB OFF)")
    backup = backup_settings()
    results = []

    # 무작위 (20종목)
    restore_settings(backup)
    base_config()
    settings.portfolio.n_stocks = 20
    results.append(run_random("무작위20"))

    for n in [10, 20, 30, 40, 60]:
        restore_settings(backup)
        base_config()
        settings.portfolio.n_stocks = n
        results.append(run_bt(f"N={n}"))

    restore_settings(backup)
    print_table(results, "실험 2: 종목 수 변화 (2017-2024)")
    return results


def main():
    r1 = experiment1()
    r2 = experiment2()

    # 최종 요약
    print("\n" + "=" * 60)
    print("최종 요약: 무작위 대비 알파")
    print("=" * 60)
    rand_cagr = r1[0]["cagr"]  # 무작위
    print(f"무작위 벤치마크 CAGR: {rand_cagr:.2%}\n")

    all_strats = r1[1:] + r2[1:]  # 무작위 제외
    sorted_strats = sorted(all_strats, key=lambda x: x["cagr"], reverse=True)

    print(f"{'순위':>4} | {'전략':>12} | {'CAGR':>8} | {'Alpha':>8} | {'MDD':>8} | {'Sharpe':>7}")
    print("-" * 60)
    for i, s in enumerate(sorted_strats):
        alpha = s["cagr"] - rand_cagr
        marker = " <--" if alpha > 0 else ""
        print(f"{i+1:>4} | {s['label']:>12} | {s['cagr']:>7.2%} | {alpha:>+7.2%} | {s['mdd']:>7.1%} | {s.get('sharpe',0):>7.3f}{marker}")
    print("=" * 60)


if __name__ == "__main__":
    main()
