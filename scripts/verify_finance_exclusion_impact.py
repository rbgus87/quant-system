"""verify_finance_exclusion_impact.py — 금융주 제외 전/후 baseline 비교.

S4-A에서 발견된 PRD 위반(엔진에서 금융주 제외 미작동) 수정의 실제 영향 측정.
Step 1 + Step 3 변형(2)가 활성 상태에서 finance_tickers 자동 감지 ON vs OFF 비교.

A 모드: 금융주 포함 (기존 결과 재현)
B 모드: 금융주 제외 (S4-A 자동 감지)

판정:
- 금융주가 32분기 합산 Top20 에 거의 안 들어가면 → baseline 변화 미미 (Step 1/3 채택 유효)
- 큰 차이가 있으면 → Step 1/3 채택 결과 재검토 필요

**분석 전용** — config.yaml 변경 없음.

사용:
    python scripts/verify_finance_exclusion_impact.py
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)


RANDOM_SEED: int = 42
BACKTEST_START: str = "2017-01-01"
BACKTEST_END: str = "2024-12-31"
MARKET: str = "KOSPI"


@dataclass
class ModeResult:
    name: str
    label: str
    exclude_finance: bool
    cagr: float = 0.0
    mdd: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    volatility: float = 0.0
    total_return: float = 0.0
    selections_by_date: dict[str, list[str]] = field(default_factory=dict)
    n_rebalances: int = 0
    error: Optional[str] = None


class Guard:
    """settings.universe.exclude_finance 토글."""

    def __init__(self) -> None:
        self._backup = settings.universe.exclude_finance

    def apply(self, exclude: bool) -> None:
        settings.universe.exclude_finance = exclude
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception as e:
            logger.warning(f"팩터 캐시 클리어 실패: {e}")

    def restore(self) -> None:
        settings.universe.exclude_finance = self._backup
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception:
            pass

    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, *_args) -> None:
        self.restore()


def collect_selections(rebal_dates: list[pd.Timestamp]) -> dict[str, list[str]]:
    from strategy.screener import MultiFactorScreener

    screener = MultiFactorScreener()
    out: dict[str, list[str]] = {}
    for i, rdt in enumerate(rebal_dates):
        ds = rdt.strftime("%Y%m%d")
        try:
            df = screener.screen(ds, market=MARKET)
            tk = df.index.tolist() if df is not None and not df.empty else []
        except Exception as e:
            logger.warning(f"{ds} screener 실패: {e}")
            tk = []
        out[ds] = tk
        if (i + 1) % 8 == 0:
            logger.info(f"  선정 수집 {i + 1}/{len(rebal_dates)}")
    return out


def count_finance_selections(
    selections_by_date: dict[str, list[str]], storage,
) -> tuple[int, list[tuple[str, str]]]:
    """선정 종목 중 금융주 카운트.

    Returns:
        (총 금융주 선정 건수, [(date, ticker), ...] 상위 20)
    """
    samples: list[tuple[str, str]] = []
    total = 0
    for ds, tickers in selections_by_date.items():
        if not tickers:
            continue
        fin = set(storage.get_finance_tickers(ds))
        for tk in tickers:
            if tk in fin:
                total += 1
                if len(samples) < 20:
                    samples.append((ds, tk))
    return total, samples


def run_mode(name: str, exclude_finance: bool) -> ModeResult:
    label = (
        "Finance INCLUDED (legacy)" if not exclude_finance
        else "Finance EXCLUDED (S4-A)"
    )
    res = ModeResult(
        name=name, label=label, exclude_finance=exclude_finance,
    )

    logger.info("=" * 70)
    logger.info(f"모드 {name}: {label}  ({BACKTEST_START} ~ {BACKTEST_END})")
    logger.info("=" * 70)

    with Guard() as guard:
        guard.apply(exclude=exclude_finance)
        try:
            from backtest.engine import MultiFactorBacktest
            from backtest.metrics import PerformanceAnalyzer

            engine = MultiFactorBacktest()
            df = engine.run(BACKTEST_START, BACKTEST_END, market=MARKET)
        except Exception as e:
            logger.error(f"[모드 {name}] 백테스트 실패: {e}", exc_info=True)
            res.error = f"backtest_failed: {e}"
            return res

        if df is None or df.empty:
            res.error = "empty_backtest_result"
            return res

        pv = df["portfolio_value"]
        rt = df["returns"].dropna() if "returns" in df.columns else pd.Series(dtype=float)
        analyzer = PerformanceAnalyzer()
        res.cagr = analyzer.calculate_cagr(pv)
        res.mdd = analyzer.calculate_mdd(pv)
        res.sharpe = analyzer.calculate_sharpe(rt)
        res.sortino = analyzer.calculate_sortino(rt)
        res.volatility = analyzer.calculate_volatility(rt)
        res.calmar = analyzer.calculate_calmar(res.cagr, res.mdd)
        res.total_return = (
            float(pv.iloc[-1] / pv.iloc[0] - 1) if len(pv) >= 2 else 0.0
        )

        rebal_dates = engine._generate_rebalance_dates(
            BACKTEST_START, BACKTEST_END, MARKET,
        )
        res.n_rebalances = len(rebal_dates)
        logger.info(f"[모드 {name}] 선정 종목 수집 ({len(rebal_dates)}회)")
        res.selections_by_date = collect_selections(rebal_dates)

    return res


def print_summary(
    res_a: ModeResult, res_b: ModeResult,
    a_fin: int, b_fin: int, a_samples: list,
) -> None:
    print()
    print("=" * 76)
    print("Finance exclusion baseline impact -- Summary")
    print("=" * 76)
    print(
        f"{'Metric':<32} {'A (Finance IN)':>15} {'B (Finance OUT)':>15} {'Delta':>8}"
    )
    print("-" * 76)
    print(
        f"{'CAGR':<32} {res_a.cagr * 100:>14.2f}% {res_b.cagr * 100:>14.2f}% "
        f"{(res_b.cagr - res_a.cagr) * 100:>+7.2f}"
    )
    print(
        f"{'MDD':<32} {res_a.mdd * 100:>14.2f}% {res_b.mdd * 100:>14.2f}% "
        f"{(res_b.mdd - res_a.mdd) * 100:>+7.2f}"
    )
    print(
        f"{'Sharpe':<32} {res_a.sharpe:>15.3f} {res_b.sharpe:>15.3f} "
        f"{res_b.sharpe - res_a.sharpe:>+8.3f}"
    )
    print(
        f"{'Sortino':<32} {res_a.sortino:>15.3f} {res_b.sortino:>15.3f} "
        f"{res_b.sortino - res_a.sortino:>+8.3f}"
    )
    print(
        f"{'Total return':<32} {res_a.total_return * 100:>14.2f}% "
        f"{res_b.total_return * 100:>14.2f}% "
        f"{(res_b.total_return - res_a.total_return) * 100:>+7.2f}"
    )
    print(
        f"{'Finance picks (sum over 32Q)':<32} {a_fin:>15d} {b_fin:>15d}"
    )
    print("=" * 76)
    if a_samples:
        print()
        print("A 모드 금융주 선정 샘플 (date, ticker):")
        for ds, tk in a_samples[:10]:
            print(f"  {ds}  {tk}")


def main() -> int:
    parser = argparse.ArgumentParser(description="금융주 제외 baseline 영향 측정")
    parser.add_argument("--report-dir", default="docs/reports")
    args = parser.parse_args()

    setup_logging()
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    res_a = run_mode("A", exclude_finance=False)
    res_b = run_mode("B", exclude_finance=True)

    # 금융주 선정 건수
    from data.storage import DataStorage
    storage = DataStorage()
    a_fin, a_samples = count_finance_selections(res_a.selections_by_date, storage)
    b_fin, _ = count_finance_selections(res_b.selections_by_date, storage)

    # 보고서 저장
    report_dir = PROJECT_ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "finance_exclusion_impact_results.json"
    payload = {
        "seed": RANDOM_SEED,
        "period": [BACKTEST_START, BACKTEST_END],
        "market": MARKET,
        "results": {"A": asdict(res_a), "B": asdict(res_b)},
        "finance_picks": {
            "A_total": a_fin,
            "B_total": b_fin,
            "A_samples": a_samples,
        },
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON 저장: {json_path}")

    # 보고서 (간단 MD)
    md_path = report_dir / "finance_exclusion_impact_analysis.md"
    lines = []
    lines.append("# 금융주 제외 baseline 영향 분석 (S4-A)")
    lines.append("")
    lines.append(f"기간: {BACKTEST_START} ~ {BACKTEST_END} / 시장: {MARKET}")
    lines.append("")
    lines.append("| 지표 | A (Finance IN) | B (Finance OUT) | Δ |")
    lines.append("| --- | ---: | ---: | ---: |")
    lines.append(
        f"| CAGR | {res_a.cagr * 100:.2f}% | {res_b.cagr * 100:.2f}% | "
        f"{(res_b.cagr - res_a.cagr) * 100:+.2f}%p |"
    )
    lines.append(
        f"| MDD | {res_a.mdd * 100:.2f}% | {res_b.mdd * 100:.2f}% | "
        f"{(res_b.mdd - res_a.mdd) * 100:+.2f}%p |"
    )
    lines.append(
        f"| Sharpe | {res_a.sharpe:.3f} | {res_b.sharpe:.3f} | "
        f"{res_b.sharpe - res_a.sharpe:+.3f} |"
    )
    lines.append(
        f"| Sortino | {res_a.sortino:.3f} | {res_b.sortino:.3f} | "
        f"{res_b.sortino - res_a.sortino:+.3f} |"
    )
    lines.append(
        f"| 총수익률 | {res_a.total_return * 100:.2f}% | "
        f"{res_b.total_return * 100:.2f}% | "
        f"{(res_b.total_return - res_a.total_return) * 100:+.2f}%p |"
    )
    lines.append(f"| 금융주 선정 합계 (32분기) | {a_fin} | {b_fin} | |")
    lines.append("")
    if a_samples:
        lines.append("## A 모드에서 금융주가 선정된 분기 (상위 20)")
        lines.append("")
        lines.append("| 리밸런싱 | 티커 |")
        lines.append("| --- | --- |")
        for ds, tk in a_samples:
            lines.append(f"| {ds} | {tk} |")
        lines.append("")
    lines.append("---")
    lines.append("")
    if a_fin == 0:
        lines.append(
            "> **결론**: 금융주는 32분기 합산 Top20 에 0건 선정됨. "
            "Step 1 + Step 3v2 활성 상태에서 금융주 제외는 baseline에 영향 없음. "
            "기존 채택 결과(Step 1, Step 3v2)는 그대로 유효."
        )
    else:
        lines.append(
            f"> **결론**: 금융주가 32분기 합산 {a_fin}건 선정됨. "
            f"금융주 제외 효과: ΔCAGR={(res_b.cagr - res_a.cagr) * 100:+.2f}%p, "
            f"ΔSharpe={res_b.sharpe - res_a.sharpe:+.3f}."
        )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"보고서 저장: {md_path}")

    print_summary(res_a, res_b, a_fin, b_fin, a_samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
