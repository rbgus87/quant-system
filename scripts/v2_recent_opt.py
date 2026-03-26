"""v2.0 최근 구간 최적화 + 역검증

1. 최근 구간: 2020-2024, 2021-2024
2. 최적 조합 탐색 (2021-2024): V/M 비율 + Vol 변화
3. 역검증: 최적 조합을 2017-2020(최악)에서 확인

V70M30 + Vol60 기준, CB OFF, 분기, 20종목
"""
import os
import sys
import logging
import copy
from dataclasses import fields as dc_fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import numpy as np
import pandas as pd

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from strategy.screener import MultiFactorScreener

setup_logging()
logger = logging.getLogger(__name__)

N_STOCKS = 20


def backup_settings() -> dict:
    backup = {}
    for f in dc_fields(settings):
        backup[f.name] = copy.deepcopy(getattr(settings, f.name))
    return backup


def restore_settings(backup: dict) -> None:
    for name, val in backup.items():
        setattr(settings, name, val)


def base_config(v: float, m: float, vol_pctl: float) -> None:
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.portfolio.weight_method = "equal"
    settings.portfolio.n_stocks = N_STOCKS
    settings.factor_weights.value = v
    settings.factor_weights.momentum = m
    settings.factor_weights.quality = 0.0
    settings.momentum.absolute_momentum_enabled = False
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.volatility.max_percentile = vol_pctl
    settings.market_regime.partial_ratio = 0.6
    settings.market_regime.defensive_ratio = 0.4


def run_bt(start: str, end: str, label: str) -> dict:
    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run(start, end)
    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    pv = result["portfolio_value"]
    metrics = analyzer.summary(pv, returns)
    metrics["label"] = label

    start_year = int(start[:4])
    end_year = int(end[:4])
    yearly = {}
    for year in range(start_year, end_year + 1):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly
    return metrics


def run_random(start: str, end: str, label: str) -> dict:
    MultiFactorScreener._factor_cache.clear()
    screener = MultiFactorScreener()
    orig = screener.screen

    def rand_screen(date, market=None, n_stocks=None, finance_tickers=None):
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
    result = engine.run(start, end)
    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    pv = result["portfolio_value"]
    metrics = analyzer.summary(pv, returns)
    metrics["label"] = label

    start_year = int(start[:4])
    end_year = int(end[:4])
    yearly = {}
    for year in range(start_year, end_year + 1):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly
    return metrics


def print_compare(results: list[dict], rand: dict, title: str, years: list[int]) -> None:
    labels = [r["label"] for r in results]
    w = 14 + 13 * len(labels)
    print(f"\n{'=' * w}")
    print(f"  {title}")
    print(f"{'=' * w}")

    header = f"{'':>12}"
    for lb in labels:
        header += f" | {lb:>10}"
    print(header)
    print("-" * w)

    for key, name in [("cagr", "CAGR"), ("mdd", "MDD"), ("sharpe", "Sharpe"),
                       ("sortino", "Sortino"), ("calmar", "Calmar"),
                       ("total_return", "Total")]:
        row = f"{name:>12}"
        for r in results:
            v = r.get(key, 0)
            if key in ("cagr", "mdd", "total_return"):
                row += f" | {v:>9.2%}"
            else:
                row += f" | {v:>9.3f}"
        print(row)

    rand_cagr = rand.get("cagr", 0)
    row = f"{'Alpha':>12}"
    for r in results:
        alpha = r.get("cagr", 0) - rand_cagr
        row += f" | {alpha:>+8.2%}p"
    print(row)

    print()
    header2 = f"{'year':>12}"
    for lb in labels:
        header2 += f" | {lb:>10}"
    print(header2)
    print("-" * w)
    for year in years:
        row = f"{year:>12}"
        for r in results:
            row += f" | {r['yearly'].get(year, 0):>9.1%}"
        print(row)
    print("=" * w)


# ============================================================
# Part 1: 최근 구간 성과 확인
# ============================================================
def part1(backup: dict) -> dict:
    print("\n" + "=" * 70)
    print("  Part 1: Recent Period Performance")
    print("=" * 70)

    out = {}
    for start, end, tag, years in [
        ("2020-01-01", "2024-12-31", "5Y(20-24)", list(range(2020, 2025))),
        ("2021-01-01", "2024-12-31", "4Y(21-24)", list(range(2021, 2025))),
    ]:
        restore_settings(backup)
        base_config(0.70, 0.30, 60.0)
        print(f"\n[Strat] {tag}")
        strat = run_bt(start, end, f"V70M30_{tag}")

        restore_settings(backup)
        base_config(0.70, 0.30, 60.0)
        print(f"[Rand] {tag}")
        rand = run_random(start, end, f"Rand_{tag}")

        print_compare([strat, rand], rand, f"Part 1: {tag}", years)
        out[tag] = {"strat": strat, "rand": rand}

    return out


# ============================================================
# Part 2: 최적 조합 탐색 (2021-2024)
# ============================================================
def part2(backup: dict) -> tuple[list[dict], dict]:
    print("\n" + "=" * 70)
    print("  Part 2: Optimal Combo Search (2021-2024)")
    print("=" * 70)

    START = "2021-01-01"
    END = "2024-12-31"
    YEARS = list(range(2021, 2025))

    # 먼저 무작위 실행
    restore_settings(backup)
    base_config(0.70, 0.30, 60.0)
    print("\n[Rand] 2021-2024")
    rand = run_random(START, END, "Random")

    combos = [
        (0.70, 0.30, 60.0, "V70M30v60"),
        (0.70, 0.30, 50.0, "V70M30v50"),
        (0.70, 0.30, 70.0, "V70M30v70"),
        (0.80, 0.20, 60.0, "V80M20v60"),
        (0.60, 0.40, 60.0, "V60M40v60"),
        (1.00, 0.00, 60.0, "V100v60"),
    ]

    results = []
    for v, m, vol, label in combos:
        restore_settings(backup)
        base_config(v, m, vol)
        print(f"\n[{label}] V={v}, M={m}, Vol={vol}")
        results.append(run_bt(START, END, label))

    print_compare(results, rand, "Part 2: Combo Search 2021-2024", YEARS)

    # 최고 Alpha 찾기
    rand_cagr = rand.get("cagr", 0)
    best = max(results, key=lambda r: r.get("cagr", 0) - rand_cagr)
    alpha = best.get("cagr", 0) - rand_cagr
    print(f"\n>> Best combo: {best['label']}")
    print(f"   CAGR={best.get('cagr',0):.2%}, Alpha={alpha:+.2%}, "
          f"MDD={best.get('mdd',0):.1%}, Sharpe={best.get('sharpe',0):.3f}")

    return results, rand


# ============================================================
# Part 3: 역검증 (과적합 체크) - 2017-2020
# ============================================================
def part3(backup: dict, best_label: str, best_v: float, best_m: float, best_vol: float) -> None:
    print("\n" + "=" * 70)
    print(f"  Part 3: Reverse Validation ({best_label} on 2017-2020)")
    print("=" * 70)

    START = "2017-01-01"
    END = "2020-12-31"
    YEARS = list(range(2017, 2021))

    restore_settings(backup)
    base_config(best_v, best_m, best_vol)
    print(f"\n[Strat] {best_label} on worst period")
    strat = run_bt(START, END, best_label)

    restore_settings(backup)
    base_config(best_v, best_m, best_vol)
    print("[Rand] 2017-2020")
    rand = run_random(START, END, "Random")

    print_compare([strat, rand], rand, f"Part 3: Reverse Validation 2017-2020", YEARS)

    strat_cagr = strat.get("cagr", 0)
    rand_cagr = rand.get("cagr", 0)
    alpha = strat_cagr - rand_cagr
    print(f"\n>> Reverse Validation Result:")
    print(f"   {best_label} CAGR={strat_cagr:.2%}, Random CAGR={rand_cagr:.2%}, Alpha={alpha:+.2%}")
    if alpha >= 0:
        print(f"   PASS: Strategy >= Random in worst period. Not overfitted.")
    else:
        print(f"   WARNING: Strategy < Random in worst period. Possible overfit!")


def main() -> None:
    backup = backup_settings()

    # Part 1
    part1(backup)

    # Part 2
    p2_results, p2_rand = part2(backup)

    # Part 2에서 최고 Alpha 조합 파라미터 추출
    rand_cagr = p2_rand.get("cagr", 0)
    best = max(p2_results, key=lambda r: r.get("cagr", 0) - rand_cagr)
    best_label = best["label"]

    # 라벨에서 파라미터 역산
    param_map = {
        "V70M30v60": (0.70, 0.30, 60.0),
        "V70M30v50": (0.70, 0.30, 50.0),
        "V70M30v70": (0.70, 0.30, 70.0),
        "V80M20v60": (0.80, 0.20, 60.0),
        "V60M40v60": (0.60, 0.40, 60.0),
        "V100v60": (1.00, 0.00, 60.0),
    }
    best_v, best_m, best_vol = param_map[best_label]

    # Part 3
    part3(backup, best_label, best_v, best_m, best_vol)

    restore_settings(backup)
    print("\n\nDone.")


if __name__ == "__main__":
    main()
