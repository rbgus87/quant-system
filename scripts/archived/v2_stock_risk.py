"""v2.0 종목 레벨 리스크 관리 실험 - V70M30 기준

실험 1: 트레일링 스톱 재활성화
실험 2: 변동성 필터 강화
실험 3: 유망 조합 (trailing_stop 0.25 + max_percentile 60)

공통: V=0.70, M=0.30, Q=0.00, 분기, 동일가중, 20종목, 2017-2024
      CB OFF (max_drawdown_pct: None), 시장 레짐 기본 (p=0.6, d=0.4)
"""
import os
import sys
import logging
import copy
from dataclasses import fields as dc_fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import pandas as pd

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from strategy.screener import MultiFactorScreener

setup_logging()
logger = logging.getLogger(__name__)

START = "2017-01-01"
END = "2024-12-31"
N_STOCKS = 20
RANDOM_CAGR = 0.0151
BASELINE_CAGR = 0.0292
BASELINE_MDD = -0.5608


def backup_settings() -> dict:
    backup = {}
    for f in dc_fields(settings):
        backup[f.name] = copy.deepcopy(getattr(settings, f.name))
    return backup


def restore_settings(backup: dict) -> None:
    for name, val in backup.items():
        setattr(settings, name, val)


def v70m30_base() -> None:
    """V70M30 공통 기본 설정 - CB OFF"""
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.portfolio.weight_method = "equal"
    settings.portfolio.n_stocks = N_STOCKS
    settings.factor_weights.value = 0.70
    settings.factor_weights.momentum = 0.30
    settings.factor_weights.quality = 0.0
    settings.momentum.absolute_momentum_enabled = False
    settings.trading.max_drawdown_pct = None
    settings.market_regime.partial_ratio = 0.6
    settings.market_regime.defensive_ratio = 0.4


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

    turnover_log = result.attrs.get("turnover_log", [])
    stop_count = 0
    for t in turnover_log:
        note = t.get("note", "")
        if "스톱" in note or "stop" in note.lower():
            stop_count += 1
    metrics["stop_triggers"] = stop_count

    return metrics


def print_table(results: list[dict], title: str) -> None:
    labels = [r["label"] for r in results]
    w = 14 + 14 * len(labels)
    print(f"\n{'=' * w}")
    print(f"  {title}")
    print(f"{'=' * w}")

    header = f"{'':>12}"
    for lb in labels:
        header += f" | {lb:>11}"
    print(header)
    print("-" * w)

    for key, name in [("cagr", "CAGR"), ("mdd", "MDD"), ("sharpe", "Sharpe"),
                       ("sortino", "Sortino"), ("calmar", "Calmar"),
                       ("total_return", "Total")]:
        row = f"{name:>12}"
        for r in results:
            v = r.get(key, 0)
            if key in ("cagr", "mdd", "total_return"):
                row += f" | {v:>10.2%}"
            else:
                row += f" | {v:>10.3f}"
        print(row)

    row = f"{'Alpha':>12}"
    for r in results:
        alpha = r.get("cagr", 0) - RANDOM_CAGR
        row += f" | {alpha:>+9.2%}p"
    print(row)

    row = f"{'MDD diff':>12}"
    for r in results:
        diff = r.get("mdd", 0) - BASELINE_MDD
        row += f" | {diff:>+9.1%}p"
    print(row)

    row = f"{'MDD<-40%':>12}"
    for r in results:
        mdd = r.get("mdd", -1)
        status = "O" if mdd > -0.40 else "X"
        row += f" | {status:>10}"
    print(row)

    print()
    header2 = f"{'year':>12}"
    for lb in labels:
        header2 += f" | {lb:>11}"
    print(header2)
    print("-" * w)
    for year in range(2017, 2025):
        row = f"{year:>12}"
        for r in results:
            row += f" | {r['yearly'].get(year, 0):>10.1%}"
        print(row)
    print("=" * w)


def experiment1(backup: dict) -> list[dict]:
    print("\n" + "=" * 60)
    print(">>> Exp 1: Trailing Stop")
    print("=" * 60)
    results = []

    for pct, label in [(0.20, "TS20%"), (0.25, "TS25%"), (0.30, "TS30%")]:
        restore_settings(backup)
        v70m30_base()
        settings.trading.trailing_stop_pct = pct
        print(f"\n[1] trailing_stop_pct={pct}")
        results.append(run_bt(label))

    print_table(results, "Exp 1: Trailing Stop (2017-2024)")
    return results


def experiment2(backup: dict) -> list[dict]:
    print("\n" + "=" * 60)
    print(">>> Exp 2: Volatility Filter")
    print("=" * 60)
    results = []

    for pctl, label in [(70, "Vol70"), (60, "Vol60"), (50, "Vol50")]:
        restore_settings(backup)
        v70m30_base()
        settings.trading.trailing_stop_pct = 0.0
        settings.volatility.max_percentile = float(pctl)
        print(f"\n[2] max_percentile={pctl}")
        results.append(run_bt(label))

    print_table(results, "Exp 2: Volatility Filter (2017-2024)")
    return results


def experiment3(backup: dict) -> list[dict]:
    print("\n" + "=" * 60)
    print(">>> Exp 3: TS25% + Vol60")
    print("=" * 60)
    results = []

    restore_settings(backup)
    v70m30_base()
    settings.trading.trailing_stop_pct = 0.25
    settings.volatility.max_percentile = 60.0
    print("\n[3] TS25% + Vol60")
    results.append(run_bt("TS25+V60"))

    print_table(results, "Exp 3: TS25% + Vol60 (2017-2024)")
    return results


def final_summary(r1: list[dict], r2: list[dict], r3: list[dict]) -> None:
    all_results = r1 + r2 + r3

    print("\n" + "=" * 100)
    print("  Final: V70M30 Stock-Level Risk (2017-2024)")
    print("  Baseline: CAGR 2.92%, MDD -56.1%, Sharpe 0.074")
    print("=" * 100)

    print(f"\n{'Strategy':>12} | {'CAGR':>8} | {'MDD':>8} | {'Sharpe':>7} | "
          f"{'Sortino':>7} | {'Calmar':>7} | {'Alpha':>8} | {'MDD chg':>8} | {'<-40%':>5}")
    print("-" * 100)

    best_mdd_ok = None
    for r in all_results:
        mdd = r.get("mdd", -1)
        alpha = r.get("cagr", 0) - RANDOM_CAGR
        mdd_chg = mdd - BASELINE_MDD
        mdd_ok = mdd > -0.40
        marker = "O" if mdd_ok else ""
        if mdd_ok and (best_mdd_ok is None or r.get("cagr", 0) > best_mdd_ok.get("cagr", 0)):
            best_mdd_ok = r
        print(f"{r['label']:>12} | {r.get('cagr', 0):>7.2%} | {r.get('mdd', 0):>7.1%} | "
              f"{r.get('sharpe', 0):>7.3f} | {r.get('sortino', 0):>7.3f} | "
              f"{r.get('calmar', 0):>7.3f} | {alpha:>+7.2%} | {mdd_chg:>+7.1%}p | {marker:>5}")

    print("=" * 100)

    if best_mdd_ok:
        print(f"\n>> Best (MDD<-40%): {best_mdd_ok['label']}")
        print(f"   CAGR: {best_mdd_ok.get('cagr', 0):.2%} | MDD: {best_mdd_ok.get('mdd', 0):.1%} | "
              f"Sharpe: {best_mdd_ok.get('sharpe', 0):.3f}")
    else:
        print("\n>> No strategy achieved MDD < -40%")

    # yearly
    print(f"\n{'Year':>6}", end="")
    for r in all_results:
        print(f" | {r['label']:>9}", end="")
    print()
    print("-" * (8 + 12 * len(all_results)))
    for year in range(2017, 2025):
        print(f"{year:>6}", end="")
        for r in all_results:
            print(f" | {r['yearly'].get(year, 0):>8.1%}", end="")
        print()
    print("=" * (8 + 12 * len(all_results)))


def main() -> None:
    backup = backup_settings()
    r1 = experiment1(backup)
    r2 = experiment2(backup)
    r3 = experiment3(backup)
    restore_settings(backup)
    final_summary(r1, r2, r3)


if __name__ == "__main__":
    main()
