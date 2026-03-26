"""팩터 실험: Value 제거 / F-Score 완화 / 종목 수 확대"""
import os, sys, logging, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

import pandas as pd
from dataclasses import fields as dc_fields

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from strategy.screener import MultiFactorScreener
from data.collector import KRXDataCollector

setup_logging()
logger = logging.getLogger(__name__)

START = "2017-01-01"
END = "2024-12-31"
KODEX200 = "069500"


def backup_settings() -> dict:
    backup = {}
    for f in dc_fields(settings):
        backup[f.name] = copy.deepcopy(getattr(settings, f.name))
    return backup


def restore_settings(backup: dict) -> None:
    for name, val in backup.items():
        setattr(settings, name, val)


def run_backtest(label: str) -> dict:
    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run(START, END)
    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(result["portfolio_value"], returns)
    metrics["label"] = label

    # 연도별 수익률
    port_val = result["portfolio_value"]
    yearly = {}
    for year in range(2017, 2025):
        pv = port_val[port_val.index.year == year]
        if len(pv) >= 2:
            yearly[year] = pv.iloc[-1] / pv.iloc[0] - 1
        else:
            yearly[year] = 0.0
    metrics["yearly"] = yearly
    return metrics


def get_kospi_yearly() -> dict:
    collector = KRXDataCollector()
    df = collector.get_ohlcv(KODEX200, START.replace("-", ""), END.replace("-", ""))
    if df is None or df.empty:
        return {}
    close = df["close"]
    close.index = pd.to_datetime(close.index)
    yearly = {}
    for year in range(2017, 2025):
        yc = close[close.index.year == year]
        if len(yc) >= 2:
            yearly[year] = yc.iloc[-1] / yc.iloc[0] - 1
    total = close.iloc[-1] / close.iloc[0] - 1
    yearly["total"] = total
    return yearly


def main() -> None:
    backup = backup_settings()
    results = []

    # 기준: 프리셋 A 기본
    print("실험 0: 기준 (프리셋 A 기본)...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    results.append(run_backtest("기준 (A 기본)"))

    # 실험 1: Value 제거
    print("실험 1: Value 제거 (Q+M only)...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.factor_weights.value = 0.00
    settings.factor_weights.momentum = 0.55
    settings.factor_weights.quality = 0.45
    results.append(run_backtest("Value 제거"))

    # 실험 2: F-Score 완화
    print("실험 2: F-Score 완화 (4→2)...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.quality.min_fscore = 2
    results.append(run_backtest("F-Score 완화"))

    # 실험 3: 종목 수 확대
    print("실험 3: 종목 수 확대 (20→40)...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.portfolio.n_stocks = 40
    results.append(run_backtest("종목 40개"))

    restore_settings(backup)

    # KOSPI 벤치마크
    kospi = get_kospi_yearly()

    # 비교 테이블
    print("\n" + "=" * 90)
    print(f"{'팩터 실험 비교 (2017-2024, 분기 리밸런싱)':^90}")
    print("=" * 90)
    print(f"{'지표':>14} | {'KOSPI':>10} | {'기준(A)':>10} | {'V제거':>10} | {'F완화':>10} | {'40종목':>10}")
    print("-" * 90)

    for key, label in [("cagr", "CAGR"), ("mdd", "MDD"), ("sharpe", "Sharpe"),
                        ("sortino", "Sortino"), ("total_return", "Total")]:
        vals = [r.get(key, 0) for r in results]
        if key == "cagr":
            kospi_cagr = (1 + kospi.get("total", 0)) ** (1/8) - 1
            kv = f"{kospi_cagr:>9.2%}"
        elif key == "total_return":
            kv = f"{kospi.get('total', 0):>9.2%}"
        elif key == "mdd":
            kv = "    -"
        else:
            kv = "    -"

        if key in ("cagr", "mdd", "total_return"):
            row = f"{label:>14} | {kv} | " + " | ".join(f"{v:>9.2%}" for v in vals)
        else:
            row = f"{label:>14} | {kv} | " + " | ".join(f"{v:>9.3f}" for v in vals)
        print(row)

    # 연도별
    print()
    print(f"{'연도':>14} | {'KOSPI':>10} | {'기준(A)':>10} | {'V제거':>10} | {'F완화':>10} | {'40종목':>10}")
    print("-" * 90)
    for year in range(2017, 2025):
        kv = kospi.get(year, 0)
        vals = [r["yearly"].get(year, 0) for r in results]
        row = f"{year:>14} | {kv:>9.1%} | " + " | ".join(f"{v:>9.1%}" for v in vals)
        print(row)

    print("=" * 90)


if __name__ == "__main__":
    main()
