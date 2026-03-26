"""v2.0 구간별/연도별/롤링 CAGR 분석

V70M30 + Vol60, CB OFF, 분기, 20종목

1. 구간별 백테스트: 2016-2024, 2021-2024, 2017-2020
2. 연도별 상세: 전략 vs KOSPI vs 무작위, 순수 알파
3. 롤링 3년 CAGR
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
from data.collector import KRXDataCollector

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


def v70m30_vol60() -> None:
    """V70M30 + Vol60 공통 설정"""
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.portfolio.weight_method = "equal"
    settings.portfolio.n_stocks = N_STOCKS
    settings.factor_weights.value = 0.70
    settings.factor_weights.momentum = 0.30
    settings.factor_weights.quality = 0.0
    settings.momentum.absolute_momentum_enabled = False
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.volatility.max_percentile = 60.0
    settings.market_regime.partial_ratio = 0.6
    settings.market_regime.defensive_ratio = 0.4


def run_bt(start: str, end: str, label: str) -> dict:
    """백테스트 실행"""
    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run(start, end)

    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    pv = result["portfolio_value"]
    metrics = analyzer.summary(pv, returns)
    metrics["label"] = label
    metrics["pv"] = pv

    yearly = {}
    for year in range(int(start[:4]), int(end[:4]) + 1):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly

    return metrics


def run_random(start: str, end: str, label: str) -> dict:
    """무작위 포트폴리오"""
    MultiFactorScreener._factor_cache.clear()
    screener = MultiFactorScreener()
    orig = screener.screen

    def rand_screen(date: str, market: str | None = None,
                    n_stocks: int | None = None,
                    finance_tickers: list[str] | None = None) -> pd.DataFrame:
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
    metrics["pv"] = pv

    yearly = {}
    for year in range(int(start[:4]), int(end[:4]) + 1):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly

    return metrics


def run_kospi(start: str, end: str, label: str) -> dict:
    """KOSPI (KODEX 200)"""
    collector = KRXDataCollector()
    ohlcv = collector.get_ohlcv("069500", start.replace("-", ""), end.replace("-", ""))
    close_col = "close" if "close" in ohlcv.columns else ohlcv.columns[3]
    prices = ohlcv[close_col].dropna()
    if not isinstance(prices.index, pd.DatetimeIndex):
        prices.index = pd.to_datetime(prices.index)

    initial_cash = settings.portfolio.initial_cash
    shares = initial_cash / prices.iloc[0]
    pv = prices * shares
    pv.name = "portfolio_value"
    returns = pv.pct_change().dropna()

    analyzer = PerformanceAnalyzer()
    metrics = analyzer.summary(pv, returns)
    metrics["label"] = label
    metrics["pv"] = pv

    yearly = {}
    for year in range(int(start[:4]), int(end[:4]) + 1):
        yp = pv[pv.index.year == year]
        yearly[year] = yp.iloc[-1] / yp.iloc[0] - 1 if len(yp) >= 2 else 0.0
    metrics["yearly"] = yearly

    return metrics


# ============================================================
# Part 1: 구간별 백테스트
# ============================================================
def part1(backup: dict) -> dict:
    print("\n" + "=" * 70)
    print("  Part 1: Period Comparison")
    print("=" * 70)

    periods = [
        ("2016-01-01", "2024-12-31", "Full(16-24)"),
        ("2021-01-01", "2024-12-31", "Bull(21-24)"),
        ("2017-01-01", "2020-12-31", "Bear(17-20)"),
    ]

    all_strat = []
    all_rand = []
    all_kospi = []

    for start, end, name in periods:
        restore_settings(backup)
        v70m30_vol60()
        print(f"\n[Strategy] {name} ({start} ~ {end})")
        s = run_bt(start, end, f"V70M30_{name}")
        all_strat.append(s)

        restore_settings(backup)
        v70m30_vol60()
        print(f"[Random] {name}")
        r = run_random(start, end, f"Rand_{name}")
        all_rand.append(r)

        print(f"[KOSPI] {name}")
        k = run_kospi(start, end, f"KOSPI_{name}")
        all_kospi.append(k)

    # 테이블 출력
    print(f"\n{'=' * 90}")
    print(f"  Part 1 Results: Period Comparison (V70M30 + Vol60)")
    print(f"{'=' * 90}")
    print(f"{'Period':>12} | {'Type':>8} | {'CAGR':>8} | {'MDD':>8} | {'Sharpe':>7} | {'Total':>8} | {'Alpha':>8}")
    print("-" * 90)

    for i, (start, end, name) in enumerate(periods):
        s, r, k = all_strat[i], all_rand[i], all_kospi[i]
        alpha = s.get("cagr", 0) - r.get("cagr", 0)
        print(f"{name:>12} | {'Strat':>8} | {s.get('cagr',0):>7.2%} | {s.get('mdd',0):>7.1%} | "
              f"{s.get('sharpe',0):>7.3f} | {s.get('total_return',0):>7.1%} | {alpha:>+7.2%}")
        print(f"{'':>12} | {'Random':>8} | {r.get('cagr',0):>7.2%} | {r.get('mdd',0):>7.1%} | "
              f"{r.get('sharpe',0):>7.3f} | {r.get('total_return',0):>7.1%} |")
        print(f"{'':>12} | {'KOSPI':>8} | {k.get('cagr',0):>7.2%} | {k.get('mdd',0):>7.1%} | "
              f"{k.get('sharpe',0):>7.3f} | {k.get('total_return',0):>7.1%} |")
        print("-" * 90)

    return {
        "strat_full": all_strat[0],
        "rand_full": all_rand[0],
        "kospi_full": all_kospi[0],
    }


# ============================================================
# Part 2: 연도별 상세 분석
# ============================================================
def part2(full_data: dict) -> None:
    print(f"\n{'=' * 90}")
    print(f"  Part 2: Yearly Alpha Analysis (2016-2024)")
    print(f"{'=' * 90}")

    strat = full_data["strat_full"]
    rand = full_data["rand_full"]
    kospi = full_data["kospi_full"]

    print(f"{'Year':>6} | {'Strategy':>10} | {'Random':>10} | {'KOSPI':>10} | "
          f"{'Alpha(S-R)':>10} | {'vs KOSPI':>10} | {'Alpha Sign':>10}")
    print("-" * 90)

    alpha_signs = []
    consecutive_neg = 0
    max_consecutive_neg = 0

    for year in range(2016, 2025):
        sy = strat["yearly"].get(year, 0)
        ry = rand["yearly"].get(year, 0)
        ky = kospi["yearly"].get(year, 0)
        alpha = sy - ry
        vs_kospi = sy - ky
        sign = "+" if alpha > 0 else "-"
        alpha_signs.append(alpha > 0)

        if alpha <= 0:
            consecutive_neg += 1
            max_consecutive_neg = max(max_consecutive_neg, consecutive_neg)
        else:
            consecutive_neg = 0

        print(f"{year:>6} | {sy:>9.1%} | {ry:>9.1%} | {ky:>9.1%} | "
              f"{alpha:>+9.1%} | {vs_kospi:>+9.1%} | {sign:>10}")

    print("-" * 90)

    pos_years = sum(1 for a in alpha_signs if a)
    neg_years = sum(1 for a in alpha_signs if not a)
    print(f"\nAlpha + years: {pos_years}/{len(alpha_signs)}")
    print(f"Alpha - years: {neg_years}/{len(alpha_signs)}")
    print(f"Max consecutive Alpha - : {max_consecutive_neg} years")

    if max_consecutive_neg >= 4:
        print(">> WARNING: 4+ consecutive negative alpha years detected!")
    else:
        print(f">> OK: Max consecutive negative alpha = {max_consecutive_neg} years (< 4)")


# ============================================================
# Part 3: 롤링 3년 CAGR
# ============================================================
def part3(backup: dict) -> None:
    print(f"\n{'=' * 90}")
    print(f"  Part 3: Rolling 3-Year CAGR")
    print(f"{'=' * 90}")

    # 3년 윈도우: 2016-2018, 2017-2019, ..., 2022-2024
    windows = []
    for start_year in range(2016, 2023):
        end_year = start_year + 2
        windows.append((start_year, end_year))

    results = []
    for sy, ey in windows:
        start = f"{sy}-01-01"
        end = f"{ey}-12-31"
        label = f"{sy}-{ey}"

        restore_settings(backup)
        v70m30_vol60()
        print(f"\n[Rolling] {label}")
        strat = run_bt(start, end, f"S_{label}")

        restore_settings(backup)
        v70m30_vol60()
        rand = run_random(start, end, f"R_{label}")

        kospi = run_kospi(start, end, f"K_{label}")

        results.append({
            "window": label,
            "strat_cagr": strat.get("cagr", 0),
            "strat_mdd": strat.get("mdd", 0),
            "strat_sharpe": strat.get("sharpe", 0),
            "rand_cagr": rand.get("cagr", 0),
            "kospi_cagr": kospi.get("cagr", 0),
            "alpha": strat.get("cagr", 0) - rand.get("cagr", 0),
        })

    # 테이블
    print(f"\n{'=' * 100}")
    print(f"  Rolling 3-Year CAGR (V70M30 + Vol60)")
    print(f"{'=' * 100}")
    print(f"{'Window':>10} | {'Strat CAGR':>11} | {'MDD':>8} | {'Sharpe':>7} | "
          f"{'Rand CAGR':>10} | {'KOSPI CAGR':>11} | {'Alpha':>8} | {'Status':>8}")
    print("-" * 100)

    neg_count = 0
    for r in results:
        status = "+" if r["strat_cagr"] > 0 else "NEGATIVE"
        if r["strat_cagr"] <= 0:
            neg_count += 1
        print(f"{r['window']:>10} | {r['strat_cagr']:>10.2%} | {r['strat_mdd']:>7.1%} | "
              f"{r['strat_sharpe']:>7.3f} | {r['rand_cagr']:>9.2%} | "
              f"{r['kospi_cagr']:>10.2%} | {r['alpha']:>+7.2%} | {status:>8}")

    print("-" * 100)
    total = len(results)
    pos_count = total - neg_count
    print(f"\nPositive 3Y CAGR: {pos_count}/{total} ({pos_count/total:.0%})")
    print(f"Negative 3Y CAGR: {neg_count}/{total} ({neg_count/total:.0%})")

    # 최악 3년 vs 다음 3년
    print(f"\n{'Worst-to-Next Analysis':^60}")
    print("-" * 60)
    for i in range(len(results) - 1):
        curr = results[i]
        nxt = results[i + 1]
        if curr["strat_cagr"] < 0:
            print(f"  {curr['window']} CAGR={curr['strat_cagr']:+.2%} "
                  f"-> next {nxt['window']} CAGR={nxt['strat_cagr']:+.2%}")


def main() -> None:
    backup = backup_settings()

    # Part 1: 구간별
    full_data = part1(backup)

    # Part 2: 연도별 알파
    part2(full_data)

    # Part 3: 롤링 3년
    part3(backup)

    restore_settings(backup)
    print("\n\nDone.")


if __name__ == "__main__":
    main()
