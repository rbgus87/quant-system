"""v2.0 MDD 관리 실험 - V70M30 기준

V70M30 (V=0.70, M=0.30, Q=0.00)의 MDD를 -30% 이내로 줄이기 위한 실험.

실험 1: 시장 레짐 필터 강화
실험 2: 서킷브레이커 느슨하게 재활성화
실험 3: 레짐 강화 + 서킷브레이커 조합

공통 조건:
  - 기간: 2017-01-01 ~ 2024-12-31
  - 분기 리밸런싱, 동일가중, 20종목
  - V=0.70, M=0.30, Q=0.00
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

START = "2017-01-01"
END = "2024-12-31"
N_STOCKS = 20

# 이전 실험에서 확인된 무작위 CAGR
RANDOM_CAGR = 0.0151  # 1.51%


def backup_settings() -> dict:
    backup = {}
    for f in dc_fields(settings):
        backup[f.name] = copy.deepcopy(getattr(settings, f.name))
    return backup


def restore_settings(backup: dict) -> None:
    for name, val in backup.items():
        setattr(settings, name, val)


def v70m30_base() -> None:
    """V70M30 공통 기본 설정"""
    settings.portfolio.rebalance_frequency = "quarterly"
    settings.portfolio.weight_method = "equal"
    settings.portfolio.n_stocks = N_STOCKS
    settings.factor_weights.value = 0.70
    settings.factor_weights.momentum = 0.30
    settings.factor_weights.quality = 0.0
    settings.momentum.absolute_momentum_enabled = False


def run_bt(label: str) -> dict:
    """백테스트 실행 + 성과 분석"""
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

    # 서킷브레이커 발동 횟수
    turnover_log = result.attrs.get("turnover_log", [])
    cb_count = sum(1 for t in turnover_log if "서킷브레이커" in t.get("note", ""))
    metrics["cb_triggers"] = cb_count

    return metrics


def print_table(results: list[dict], title: str) -> None:
    """결과 테이블 출력"""
    labels = [r["label"] for r in results]
    w = 14 + 14 * len(labels)
    print(f"\n{'=' * w}")
    print(f"{title:^{w}}")
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

    # 무작위 대비 알파
    row = f"{'Alpha':>12}"
    for r in results:
        alpha = r.get("cagr", 0) - RANDOM_CAGR
        row += f" | {alpha:>+9.2%}p"
    print(row)

    # CB 발동 횟수
    row = f"{'CB발동':>12}"
    for r in results:
        row += f" | {r.get('cb_triggers', 0):>10}회"
    print(row)

    # MDD 목표 달성 여부
    row = f"{'MDD<-30%':>12}"
    for r in results:
        mdd = r.get("mdd", -1)
        status = "O 달성" if mdd > -0.30 else "X 미달"
        row += f" | {status:>10}"
    print(row)

    # 연도별
    print()
    header2 = f"{'연도':>12}"
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
    """실험 1: 시장 레짐 필터 강화"""
    print("\n" + "=" * 60)
    print(">>> 실험 1: 시장 레짐 필터 강화 (CB OFF)")
    print("=" * 60)
    results = []

    # 1a. 기준선 - 현재 설정 (p=0.6, d=0.4), CB OFF
    restore_settings(backup)
    v70m30_base()
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.market_regime.partial_ratio = 0.6
    settings.market_regime.defensive_ratio = 0.4
    print("\n[1a] 기준선 (p=0.6, d=0.4, CB OFF)")
    results.append(run_bt("기준선"))

    # 1b. 더 보수적 (p=0.4, d=0.2)
    restore_settings(backup)
    v70m30_base()
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.market_regime.partial_ratio = 0.4
    settings.market_regime.defensive_ratio = 0.2
    print("\n[1b] 보수적 (p=0.4, d=0.2)")
    results.append(run_bt("레짐p4d2"))

    # 1c. 매우 보수적 (p=0.3, d=0.1)
    restore_settings(backup)
    v70m30_base()
    settings.trading.max_drawdown_pct = None
    settings.trading.trailing_stop_pct = 0.0
    settings.market_regime.partial_ratio = 0.3
    settings.market_regime.defensive_ratio = 0.1
    print("\n[1c] 매우 보수적 (p=0.3, d=0.1)")
    results.append(run_bt("레짐p3d1"))

    print_table(results, "실험 1: 시장 레짐 필터 강화 (2017-2024)")
    return results


def experiment2(backup: dict) -> list[dict]:
    """실험 2: 서킷브레이커 느슨하게 재활성화 (레짐은 기본값)"""
    print("\n" + "=" * 60)
    print(">>> 실험 2: 서킷브레이커 재활성화 (레짐 기본)")
    print("=" * 60)
    results = []

    # 2a. CB -35%
    restore_settings(backup)
    v70m30_base()
    settings.trading.max_drawdown_pct = 0.35
    settings.trading.trailing_stop_pct = 0.0
    settings.market_regime.partial_ratio = 0.6
    settings.market_regime.defensive_ratio = 0.4
    print("\n[2a] CB -35% (p=0.6, d=0.4)")
    results.append(run_bt("CB35%"))

    # 2b. CB -40%
    restore_settings(backup)
    v70m30_base()
    settings.trading.max_drawdown_pct = 0.40
    settings.trading.trailing_stop_pct = 0.0
    settings.market_regime.partial_ratio = 0.6
    settings.market_regime.defensive_ratio = 0.4
    print("\n[2b] CB -40% (p=0.6, d=0.4)")
    results.append(run_bt("CB40%"))

    # 2c. CB -45%
    restore_settings(backup)
    v70m30_base()
    settings.trading.max_drawdown_pct = 0.45
    settings.trading.trailing_stop_pct = 0.0
    settings.market_regime.partial_ratio = 0.6
    settings.market_regime.defensive_ratio = 0.4
    print("\n[2c] CB -45% (p=0.6, d=0.4)")
    results.append(run_bt("CB45%"))

    print_table(results, "실험 2: 서킷브레이커 재활성화 (2017-2024)")
    return results


def experiment3(backup: dict) -> list[dict]:
    """실험 3: 레짐 강화 + 서킷브레이커 조합"""
    print("\n" + "=" * 60)
    print(">>> 실험 3: 레짐 강화 + 서킷브레이커 조합")
    print("=" * 60)
    results = []

    # 3a. 레짐 (p=0.4, d=0.2) + CB -40%
    restore_settings(backup)
    v70m30_base()
    settings.trading.max_drawdown_pct = 0.40
    settings.trading.trailing_stop_pct = 0.0
    settings.market_regime.partial_ratio = 0.4
    settings.market_regime.defensive_ratio = 0.2
    print("\n[3a] 레짐(p=0.4,d=0.2) + CB40%")
    results.append(run_bt("레짐+CB40"))

    print_table(results, "실험 3: 레짐 + 서킷브레이커 조합 (2017-2024)")
    return results


def final_summary(
    r1: list[dict], r2: list[dict], r3: list[dict],
) -> None:
    """전체 결과 통합 비교"""
    all_results = r1 + r2 + r3

    print("\n" + "=" * 100)
    print("최종 통합 비교 - V70M30 MDD 관리 실험 (2017-2024)")
    print("=" * 100)

    print(f"\n{'전략':>12} | {'CAGR':>8} | {'MDD':>8} | {'Sharpe':>7} | "
          f"{'Sortino':>7} | {'Calmar':>7} | {'Alpha':>8} | {'CB발동':>5} | {'목표':>8}")
    print("-" * 100)

    # MDD -30% 이내 달성하면서 CAGR 최고인 조합 찾기
    best = None
    for r in all_results:
        mdd = r.get("mdd", -1)
        alpha = r.get("cagr", 0) - RANDOM_CAGR
        mdd_ok = mdd > -0.30
        marker = ""
        if mdd_ok:
            marker = "MDD OK"
            if best is None or r.get("cagr", 0) > best.get("cagr", 0):
                best = r
        print(f"{r['label']:>12} | {r.get('cagr', 0):>7.2%} | {r.get('mdd', 0):>7.1%} | "
              f"{r.get('sharpe', 0):>7.3f} | {r.get('sortino', 0):>7.3f} | "
              f"{r.get('calmar', 0):>7.3f} | {alpha:>+7.2%} | "
              f"{r.get('cb_triggers', 0):>4} | {marker:>8}")

    print("=" * 100)

    if best:
        print(f"\n** 최적 조합: {best['label']}")
        print(f"  CAGR: {best.get('cagr', 0):.2%} | MDD: {best.get('mdd', 0):.1%} | "
              f"Sharpe: {best.get('sharpe', 0):.3f} | "
              f"Alpha: {best.get('cagr', 0) - RANDOM_CAGR:+.2%}")
    else:
        print("\n** MDD -30% 이내 달성한 조합 없음 - 추가 실험 필요")

    # 연도별 전체 비교
    print(f"\n{'연도별 수익률':^100}")
    print("-" * 100)
    header = f"{'연도':>6}"
    for r in all_results:
        header += f" | {r['label']:>10}"
    print(header)
    print("-" * 100)
    for year in range(2017, 2025):
        row = f"{year:>6}"
        for r in all_results:
            row += f" | {r['yearly'].get(year, 0):>9.1%}"
        print(row)
    print("=" * 100)


def main() -> None:
    backup = backup_settings()

    r1 = experiment1(backup)
    r2 = experiment2(backup)
    r3 = experiment3(backup)

    restore_settings(backup)
    final_summary(r1, r2, r3)


if __name__ == "__main__":
    main()
