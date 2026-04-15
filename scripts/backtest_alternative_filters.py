"""대안 방어 장치 비교 — 4가지 시나리오 백테스트.

strict_reporting_lag=False로 원복한 뒤 005620 유형 방어를 위한 대안 장치
3종(EPS 부호 반전 필터, 거래정지 이력 필터, min_fscore 상향)의 비용 대비
효과를 측정한다.

시나리오:
  Baseline: 현재 코드 (모든 방어 off, min_fscore=4)
  +방어1:   eps_flip_filter_enabled=True
  +방어2:   halt_history_filter_enabled=True
  +방어3:   min_fscore=5
  +1+2+3:   세 장치 모두 적용

측정:
  - CAGR / MDD / Sharpe (simulate_portfolio 재사용)
  - 005620 회피 여부 (2017-06-30에서 선정 제외 확인)
  - 폐지 failure 노출 건수
  - 의도치 않은 종목 누락 수 (Baseline 대비)

사용:
    python scripts/backtest_alternative_filters.py
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import setup_logging
from config.settings import settings
from strategy.screener import MultiFactorScreener
from scripts.backtest_lag_impact import (
    generate_quarterly_rebalance_dates,
    simulate_portfolio,
    metrics_from_returns,
    _is_failure_delisted,
)

logger = logging.getLogger(__name__)


SCENARIOS = [
    ("Baseline", {
        "strict_reporting_lag": False,
        "eps_flip_filter_enabled": False,
        "halt_history_filter_enabled": False,
        "min_fscore": 4,
    }),
    ("+방어1_EPS반전", {
        "strict_reporting_lag": False,
        "eps_flip_filter_enabled": True,
        "halt_history_filter_enabled": False,
        "min_fscore": 4,
    }),
    ("+방어2_거래정지이력", {
        "strict_reporting_lag": False,
        "eps_flip_filter_enabled": False,
        "halt_history_filter_enabled": True,
        "min_fscore": 4,
    }),
    ("+방어3_min_fscore_5", {
        "strict_reporting_lag": False,
        "eps_flip_filter_enabled": False,
        "halt_history_filter_enabled": False,
        "min_fscore": 5,
    }),
    ("+1+2+3_통합", {
        "strict_reporting_lag": False,
        "eps_flip_filter_enabled": True,
        "halt_history_filter_enabled": True,
        "min_fscore": 5,
    }),
]


def apply_scenario(cfg: dict) -> None:
    settings.quality.strict_reporting_lag = cfg["strict_reporting_lag"]
    settings.quality.eps_flip_filter_enabled = cfg["eps_flip_filter_enabled"]
    settings.quality.halt_history_filter_enabled = cfg["halt_history_filter_enabled"]
    settings.quality.min_fscore = cfg["min_fscore"]
    MultiFactorScreener._factor_cache.clear()


def run_selections(
    label: str, cfg: dict, dates: list[date]
) -> dict[str, list[str]]:
    apply_scenario(cfg)
    screener = MultiFactorScreener()
    selections: dict[str, list[str]] = {}
    for i, d in enumerate(dates):
        ds = d.strftime("%Y%m%d")
        logger.info(f"[{label}] [{i + 1}/{len(dates)}] {ds}")
        try:
            df = screener.screen(ds)
            tickers = df.index.tolist() if df is not None and not df.empty else []
        except Exception as e:
            logger.warning(f"[{label}] {ds} 실패: {e}")
            tickers = []
        selections[d.isoformat()] = tickers
    return selections


def analyze_005620(
    selections: dict[str, list[str]]
) -> tuple[bool, bool]:
    """005620이 2017-06-30 리밸런싱에서 선정됐는지 확인.

    Returns:
        (selected_in_2017_q2, selected_in_any_quarter_before_delist_2017_08_25)
    """
    q2_2017 = selections.get("2017-06-30", [])
    in_q2 = "005620" in q2_2017
    q1_2017 = selections.get("2017-03-31", [])
    in_q1 = "005620" in q1_2017
    return in_q2, in_q2 or in_q1


def count_failure_exposures(
    selections: dict[str, list[str]],
    rebal_dates: list[date],
    db_path: str | None = None,
) -> int:
    db_path = db_path or settings.db_path
    count = 0
    with sqlite3.connect(db_path) as conn:
        for i, d in enumerate(rebal_dates[:-1]):
            next_d = rebal_dates[i + 1]
            tickers = selections.get(d.isoformat(), [])
            for t in tickers:
                if _is_failure_delisted(conn, t, d, next_d):
                    count += 1
    return count


def run_analysis(
    start: date, end: date, cache_path: Path | None
) -> dict:
    rebal_dates = generate_quarterly_rebalance_dates(start, end)
    logger.info(f"리밸런싱 날짜: {len(rebal_dates)}개")

    all_selections: dict[str, dict[str, list[str]]] = {}

    if cache_path and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if set(cached.keys()) == {s[0] for s in SCENARIOS}:
            logger.info(f"캐시 로드: {cache_path}")
            all_selections = cached

    for label, cfg in SCENARIOS:
        if label in all_selections:
            logger.info(f"=== {label} (cache hit) ===")
            continue
        logger.info(f"=== {label} ===")
        logger.info(f"  cfg: {cfg}")
        all_selections[label] = run_selections(label, cfg, rebal_dates)

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(all_selections, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"캐시 저장: {cache_path}")

    # 각 시나리오 성과
    results: list[dict] = []
    baseline_sel = all_selections["Baseline"]
    for label, cfg in SCENARIOS:
        sel = all_selections[label]
        sim = simulate_portfolio(sel, rebal_dates)
        m = metrics_from_returns(sim["quarterly_return"])
        in_q2, in_any = analyze_005620(sel)
        fail_exposures = count_failure_exposures(sel, rebal_dates)

        # Baseline 대비 종목 누락
        overlaps = []
        missing_count = 0
        for d in rebal_dates:
            iso = d.isoformat()
            b_set = set(baseline_sel.get(iso, []))
            s_set = set(sel.get(iso, []))
            if b_set and s_set:
                overlaps.append(len(b_set & s_set) / max(len(b_set), 1))
                missing_count += len(b_set - s_set)
        avg_overlap = float(np.mean(overlaps)) if overlaps else 1.0

        results.append({
            "label": label,
            "cfg": cfg,
            "metrics": m,
            "avg_turnover": float(sim["turnover_proxy"].mean()),
            "avg_n_stocks": float(sim["n_stocks"].mean()),
            "005620_in_q2": in_q2,
            "005620_in_any_pre_delist": in_any,
            "failure_exposures": fail_exposures,
            "overlap_vs_baseline": avg_overlap,
            "missing_vs_baseline": missing_count,
        })

    return {
        "rebal_dates": rebal_dates,
        "results": results,
    }


def write_report(analysis: dict, output_path: Path) -> None:
    lines: list[str] = []
    lines.append("# 대안 방어 장치 비교 리포트\n")
    lines.append(f"**작성일**: {date.today()}  ")
    lines.append(
        "**배경**: strict_reporting_lag=True 실험에서 CAGR -12.18%p 부작용 확인 → 원복. "
        "005620 유형 재발 방지를 위한 3가지 대안 방어 장치 효과 측정.\n"
    )

    results = analysis["results"]
    baseline = results[0]
    b_cagr = baseline["metrics"]["CAGR"]

    lines.append("## 종합 비교\n")
    lines.append(
        "| 시나리오 | CAGR | ΔCAGR | MDD | Sharpe | "
        "005620 회피 | 폐지 노출 | Baseline 대비 겹침 |"
    )
    lines.append(
        "|----------|------|-------|-----|--------|-------------|-----------|---------------------|"
    )
    for r in results:
        m = r["metrics"]
        delta = (m["CAGR"] - b_cagr) * 100
        cagr_pct = m["CAGR"] * 100
        mdd_pct = m["MDD"] * 100
        avoid_mark = "✅" if not r["005620_in_any_pre_delist"] else "❌"
        overlap_pct = r["overlap_vs_baseline"] * 100
        lines.append(
            f"| {r['label']} | {cagr_pct:+.2f}% | {delta:+.2f}%p | "
            f"{mdd_pct:.2f}% | {m['Sharpe']:.3f} | {avoid_mark} | "
            f"{r['failure_exposures']}건 | {overlap_pct:.1f}% |"
        )
    lines.append("")

    lines.append("## 상세 지표\n")
    for r in results:
        lines.append(f"### {r['label']}\n")
        lines.append(f"- 설정: `{r['cfg']}`")
        m = r["metrics"]
        lines.append(f"- CAGR: **{m['CAGR']*100:+.2f}%**")
        lines.append(f"- MDD: **{m['MDD']*100:.2f}%**")
        lines.append(f"- Sharpe: **{m['Sharpe']:.3f}**")
        lines.append(f"- Sortino: **{m['Sortino']:.3f}**")
        lines.append(f"- Calmar: **{m['Calmar']:.3f}**")
        lines.append(
            f"- 평균 분기 회전율: {r['avg_turnover']*100:.1f}%, "
            f"평균 선정 종목: {r['avg_n_stocks']:.1f}개"
        )
        lines.append(
            f"- 005620 (2017-06-30) 선정: "
            f"**{'회피 ✅' if not r['005620_in_q2'] else '선정됨 ❌'}**"
        )
        lines.append(
            f"- 전체 기간 failure 폐지 노출: **{r['failure_exposures']}건**"
        )
        lines.append(
            f"- Baseline 대비 평균 종목 겹침: "
            f"{r['overlap_vs_baseline']*100:.1f}% "
            f"(총 누락 {r['missing_vs_baseline']}건)"
        )
        lines.append("")

    # 권고
    lines.append("## 결론 및 권고\n")
    lines.append(
        "기준: CAGR 손실 -1%p 이내 + 005620 회피 + 폐지 노출 감소"
    )
    lines.append("")
    candidates = []
    for r in results[1:]:
        delta_pct = (r["metrics"]["CAGR"] - b_cagr) * 100
        avoids = not r["005620_in_any_pre_delist"]
        if delta_pct >= -1.0 and avoids:
            candidates.append((r["label"], delta_pct, r["failure_exposures"]))

    if candidates:
        best = max(candidates, key=lambda x: (-abs(x[1]), -x[2]))
        lines.append(
            f"✅ **권고 시나리오: {best[0]}**"
        )
        lines.append(
            f"- CAGR 손실 {best[1]:+.2f}%p (목표 -1%p 이내 달성)"
        )
        lines.append(
            f"- 005620 회피 성공, 폐지 노출 {best[2]}건"
        )
    else:
        best_dcagr = max(results[1:], key=lambda r: r["metrics"]["CAGR"])
        delta = (best_dcagr["metrics"]["CAGR"] - b_cagr) * 100
        lines.append(
            "⚠️ **CAGR -1%p 이내 조건을 만족하는 방어 장치 없음**"
        )
        lines.append(
            f"- 최선은 `{best_dcagr['label']}` (ΔCAGR {delta:+.2f}%p, "
            f"폐지 노출 {best_dcagr['failure_exposures']}건)"
        )
        lines.append(
            "- 적용 기준을 -2%p로 완화하거나, 방어 장치 없이 `risk_guard` "
            "일일 알림으로만 005620 유형 대응 고려"
        )
    lines.append("")

    lines.append("## 한계점\n")
    lines.append(
        "- 간이 백테스트 (시장 레짐/변동성 타겟팅 미반영)\n"
        "- 거래비용 고정 0.5%/교체율\n"
        "- 폐지 failure 종목은 -100% 가정"
    )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"리포트 저장: {output_path}")


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="대안 방어 장치 비교")
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument(
        "--cache",
        default="data/alt_filter_selections.json",
    )
    parser.add_argument(
        "--output",
        default="docs/reports/alternative_defense_comparison.md",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    analysis = run_analysis(start, end, Path(args.cache))
    write_report(analysis, Path(args.output))

    # 시나리오 기본값으로 복원
    apply_scenario(SCENARIOS[0][1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
