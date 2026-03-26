"""구조적 수정 실험: 서킷브레이커 OFF / 시총 가중 / 40종목"""
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
    """무작위 포트폴리오"""
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


def get_kospi() -> dict:
    collector = KRXDataCollector()
    df = collector.get_ohlcv(KODEX200, START.replace("-", ""), END.replace("-", ""))
    if df is None or df.empty:
        return {"cagr": 0, "mdd": 0, "total_return": 0, "yearly": {}}
    close = df["close"]
    close.index = pd.to_datetime(close.index)
    total = close.iloc[-1] / close.iloc[0] - 1
    cagr = (1 + total) ** (1 / 8) - 1
    peak = close.cummax()
    mdd = ((close - peak) / peak).min()
    yearly = {}
    for year in range(2017, 2025):
        yc = close[close.index.year == year]
        yearly[year] = yc.iloc[-1] / yc.iloc[0] - 1 if len(yc) >= 2 else 0.0
    return {"cagr": cagr, "mdd": mdd, "total_return": total, "sharpe": 0, "yearly": yearly}


def main():
    backup = backup_settings()
    all_results = []

    # 0. 기존 프리셋 A
    print("0. 기존 프리셋 A...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    all_results.append(run_bt("기존 A"))

    # 1. 서킷브레이커 OFF
    print("1. 서킷브레이커 OFF...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    all_results.append(run_bt("CB OFF"))

    # 2. 서킷브레이커 OFF + 시총 가중
    print("2. 시총 가중...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.portfolio.weight_method = "value_weighted"
    all_results.append(run_bt("시총가중"))

    # 3. 서킷브레이커 OFF + 시총 가중 + 40종목
    print("3. 시총가중 + 40종목...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.portfolio.weight_method = "value_weighted"
    settings.portfolio.n_stocks = 40
    all_results.append(run_bt("시총40"))

    # 4. 무작위
    print("4. 무작위 포트폴리오...")
    restore_settings(backup)
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    all_results.append(run_random("무작위"))

    restore_settings(backup)

    # KOSPI
    kospi = get_kospi()

    # 비교 테이블
    labels = ["KOSPI"] + [r["label"] for r in all_results]
    print(f"\n{'=' * 100}")
    print(f"{'구조적 수정 실험 비교 (2017-2024)':^100}")
    print(f"{'=' * 100}")

    header = f"{'지표':>12}"
    for lb in labels:
        header += f" | {lb:>10}"
    print(header)
    print("-" * 100)

    for key, name in [("cagr", "CAGR"), ("mdd", "MDD"), ("sharpe", "Sharpe"),
                       ("total_return", "Total")]:
        row = f"{name:>12}"
        # KOSPI
        kv = kospi.get(key, 0)
        if key in ("cagr", "mdd", "total_return"):
            row += f" | {kv:>9.2%}"
        else:
            row += f" | {'--':>10}"
        # 나머지
        for r in all_results:
            v = r.get(key, 0)
            if key in ("cagr", "mdd", "total_return"):
                row += f" | {v:>9.2%}"
            else:
                row += f" | {v:>9.3f}"
        print(row)

    # 연도별
    print()
    header2 = f"{'연도':>12}"
    for lb in labels:
        header2 += f" | {lb:>10}"
    print(header2)
    print("-" * 100)
    for year in range(2017, 2025):
        row = f"{year:>12}"
        row += f" | {kospi['yearly'].get(year, 0):>9.1%}"
        for r in all_results:
            row += f" | {r['yearly'].get(year, 0):>9.1%}"
        print(row)

    print("=" * 100)


if __name__ == "__main__":
    main()
