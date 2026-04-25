"""backtest_quality_10pct.py — Quality 10% 소량 배합 영향 분석.

배경:
  - V70M30Q0 (현재 Baseline) 가 PRD_v2.md 실험에서 최적 확정
  - Q25% 는 이전 실험에서 성과 악화 확인
  - Q10% 소량 배합은 미검증 → 본 분석으로 채움

시나리오 (4개, 2 기간 × 4 = 8 백테스트):
  A. V70 M30 Q0   (현재 Baseline)
  B. V65 M25 Q10  (균등 감소)
  C. V70 M20 Q10  (모멘텀에서 감소)
  D. V60 M30 Q10  (밸류에서 감소)

기간:
  - 2017-2024 (8년, 코로나·하락장 포함)
  - 2021-2024 (4년, PRD_v2 KPI 측정 구간)

**이 스크립트는 config.yaml / 실전 설정을 영구 변경하지 않습니다.**
런타임 settings.factor_weights 속성만 변경 후 원복.

사용:
    python scripts/backtest_quality_10pct.py
    python scripts/backtest_quality_10pct.py --periods short  # 2021-2024만
    python scripts/backtest_quality_10pct.py --skip-overlap   # 종목 겹침률 분석 스킵
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)


SCENARIOS: dict[str, dict] = {
    "A": {
        "label": "V70 M30 Q0 (Baseline)",
        "weights": {"value": 0.70, "momentum": 0.30, "quality": 0.00},
    },
    "B": {
        "label": "V65 M25 Q10 (균등 감소)",
        "weights": {"value": 0.65, "momentum": 0.25, "quality": 0.10},
    },
    "C": {
        "label": "V70 M20 Q10 (모멘텀↓)",
        "weights": {"value": 0.70, "momentum": 0.20, "quality": 0.10},
    },
    "D": {
        "label": "V60 M30 Q10 (밸류↓)",
        "weights": {"value": 0.60, "momentum": 0.30, "quality": 0.10},
    },
}

PERIODS: dict[str, tuple[str, str]] = {
    "long": ("2017-01-01", "2024-12-31"),    # 8년 (2017-2020 최악 구간 포함)
    "short": ("2021-01-01", "2024-12-31"),   # 4년 (PRD_v2 KPI 구간)
}


@dataclass
class Result:
    name: str
    label: str
    period: str
    start: str
    end: str
    weights: dict[str, float]
    cagr: float = 0.0
    mdd: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    volatility: float = 0.0
    calmar: float = 0.0
    total_return: float = 0.0
    n_days: int = 0
    n_rebalances: int = 0
    avg_selection_size: float = 0.0
    selections_by_date: dict[str, list[str]] = field(default_factory=dict)
    overlap_with_baseline: float = 0.0  # Jaccard 평균
    error: Optional[str] = None


class WeightsGuard:
    """settings.factor_weights 속성을 컨텍스트 종료 시 원복."""

    def __init__(self) -> None:
        fw = settings.factor_weights
        self._backup = {"value": fw.value, "momentum": fw.momentum, "quality": fw.quality}

    def apply(self, weights: dict[str, float]) -> None:
        fw = settings.factor_weights
        fw.value = float(weights["value"])
        fw.momentum = float(weights["momentum"])
        fw.quality = float(weights["quality"])
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception as e:
            logger.warning(f"팩터 캐시 클리어 실패: {e}")

    def restore(self) -> None:
        fw = settings.factor_weights
        fw.value = self._backup["value"]
        fw.momentum = self._backup["momentum"]
        fw.quality = self._backup["quality"]
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception:
            pass

    def __enter__(self) -> "WeightsGuard":
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
            df = screener.screen(ds, market="KOSPI")
            tk = df.index.tolist() if df is not None and not df.empty else []
        except Exception as e:
            logger.warning(f"{ds} screener 실패: {e}")
            tk = []
        out[ds] = tk
        if (i + 1) % 8 == 0:
            logger.info(f"선정 수집 {i + 1}/{len(rebal_dates)}")
    return out


def jaccard_avg(a: dict[str, list[str]], b: dict[str, list[str]]) -> float:
    """두 시나리오 선정 종목의 일자별 Jaccard 유사도 평균."""
    if not a or not b:
        return 0.0
    common_dates = set(a) & set(b)
    vals: list[float] = []
    for d in common_dates:
        sa, sb = set(a[d]), set(b[d])
        if not sa and not sb:
            continue
        uni = len(sa | sb)
        if uni > 0:
            vals.append(len(sa & sb) / uni)
    return sum(vals) / len(vals) if vals else 0.0


def run_one(
    name: str,
    period: str,
    start: str,
    end: str,
    with_overlap: bool,
) -> Result:
    spec = SCENARIOS[name]
    res = Result(
        name=name, label=spec["label"], period=period,
        start=start, end=end, weights=spec["weights"],
    )

    logger.info("=" * 60)
    logger.info(f"[{period}] {name} ({spec['label']}) — {start} ~ {end}")
    logger.info("=" * 60)

    with WeightsGuard() as guard:
        guard.apply(spec["weights"])
        try:
            from backtest.engine import MultiFactorBacktest
            from backtest.metrics import PerformanceAnalyzer

            engine = MultiFactorBacktest()
            df = engine.run(start, end, market="KOSPI")
        except Exception as e:
            logger.error(f"[{period}/{name}] 백테스트 실패: {e}", exc_info=True)
            res.error = f"backtest_failed: {e}"
            return res

        if df is None or df.empty:
            res.error = "empty_backtest_result"
            return res

        pv = df["portfolio_value"]
        rt = df["returns"].dropna()
        analyzer = PerformanceAnalyzer()
        res.cagr = analyzer.calculate_cagr(pv)
        res.mdd = analyzer.calculate_mdd(pv)
        res.sharpe = analyzer.calculate_sharpe(rt)
        res.sortino = analyzer.calculate_sortino(rt)
        res.volatility = analyzer.calculate_volatility(rt)
        res.calmar = analyzer.calculate_calmar(res.cagr, res.mdd)
        res.total_return = float(pv.iloc[-1] / pv.iloc[0] - 1) if len(pv) >= 2 else 0.0
        res.n_days = len(pv)

        rebal_dates = engine._generate_rebalance_dates(start, end, "KOSPI")
        res.n_rebalances = len(rebal_dates)

        if with_overlap:
            logger.info(f"[{period}/{name}] 선정 종목 수집 ({len(rebal_dates)}회)")
            sel = collect_selections(rebal_dates)
            sizes = [len(v) for v in sel.values() if v]
            res.avg_selection_size = sum(sizes) / len(sizes) if sizes else 0.0
            res.selections_by_date = sel

    return res


def df_to_md(df: pd.DataFrame) -> str:
    if df.empty:
        return "_(데이터 없음)_"
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                vals.append("" if pd.isna(v) else f"{v:.4f}")
            elif v is None:
                vals.append("")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def perf_table(results: list[Result]) -> pd.DataFrame:
    rows = []
    for r in results:
        rows.append({
            "시나리오": r.name,
            "라벨": r.label,
            "CAGR(%)": f"{r.cagr * 100:.2f}",
            "MDD(%)": f"{r.mdd * 100:.2f}",
            "Sharpe": f"{r.sharpe:.3f}",
            "Sortino": f"{r.sortino:.3f}",
            "Calmar": f"{r.calmar:.3f}",
            "Vol(%)": f"{r.volatility * 100:.2f}",
            "총수익률(%)": f"{r.total_return * 100:.2f}",
            "에러": r.error or "",
        })
    return pd.DataFrame(rows)


def overlap_table(results: list[Result]) -> pd.DataFrame:
    """A(Baseline) 대비 종목 겹침률."""
    if not results:
        return pd.DataFrame()
    base = next((r for r in results if r.name == "A"), None)
    if base is None or not base.selections_by_date:
        return pd.DataFrame()
    rows = []
    for r in results:
        if r.name == "A":
            jc = 1.0
        else:
            jc = jaccard_avg(base.selections_by_date, r.selections_by_date)
        rows.append({
            "시나리오": r.name,
            "라벨": r.label,
            "평균 선정수": f"{r.avg_selection_size:.1f}",
            "Baseline 일치율(Jaccard)": f"{jc * 100:.1f}%",
        })
    return pd.DataFrame(rows)


def make_recommendation(results_long: list[Result], results_short: list[Result]) -> list[str]:
    lines: list[str] = []

    base_long = next((r for r in results_long if r.name == "A"), None)
    base_short = next((r for r in results_short if r.name == "A"), None)
    if base_long is None or base_short is None:
        lines.append("**판정 실패**: Baseline 결과 누락")
        return lines

    judgments: dict[str, str] = {}
    for sc in ["B", "C", "D"]:
        long_r = next((r for r in results_long if r.name == sc), None)
        short_r = next((r for r in results_short if r.name == sc), None)
        if long_r is None or short_r is None or long_r.error or short_r.error:
            judgments[sc] = "비교 불가 (에러)"
            continue
        # 두 기간 모두에서 CAGR과 Sharpe 동시 개선해야 검토 가치
        long_better = (
            long_r.cagr > base_long.cagr
            and long_r.sharpe > base_long.sharpe
        )
        short_better = (
            short_r.cagr > base_short.cagr
            and short_r.sharpe > base_short.sharpe
        )
        if long_better and short_better:
            judgments[sc] = "🟢 두 기간 모두 개선 — 검토 가치"
        elif long_better or short_better:
            judgments[sc] = "🟡 한 기간만 개선 — 부분 효과"
        else:
            judgments[sc] = "🔴 모두 악화/동등 — 현행 유지"

    lines.append("### 시나리오별 종합 판정")
    lines.append("")
    for sc in ["B", "C", "D"]:
        sp = SCENARIOS[sc]
        lines.append(f"- **{sc}** ({sp['label']}): {judgments[sc]}")
    lines.append("")

    # 최종 권고
    any_green = any("🟢" in v for v in judgments.values())
    if not any_green:
        lines.append("### 최종 권고: 현행 유지 (Q=0)")
        lines.append("")
        lines.append(
            "Q10% 소량 배합 시나리오 B/C/D 중 어느 것도 두 기간(2017-2024 + "
            "2021-2024) 모두에서 CAGR과 Sharpe 동시 개선을 보이지 못함. "
            "PRD_v2.md 의 Q=0 결정 (실험 검증 완료)이 추가 검증으로도 재확인됨. "
            "config.yaml 변경 권고하지 않음."
        )
    else:
        green_scs = [sc for sc, v in judgments.items() if "🟢" in v]
        lines.append(f"### 최종 권고: {', '.join(green_scs)} 추가 검증 검토")
        lines.append("")
        lines.append(
            f"시나리오 {', '.join(green_scs)} 가 두 기간 모두에서 "
            f"Baseline 대비 CAGR + Sharpe 동시 개선. "
            "단, 본 분석은 단일 KOSPI 백테스트 기반이므로 Walk-Forward "
            "검증과 인접 안정성 (Q5%, Q15%) 검토 후 실전 도입 판단 권고. "
            "config.yaml 즉시 변경은 비권고."
        )
    lines.append("")
    return lines


def build_report(
    results_long: list[Result],
    results_short: list[Result],
    with_overlap: bool,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []

    lines.append("# Quality 팩터 10% 소량 배합 분석 보고서")
    lines.append("")
    lines.append(f"> 생성: {now}  ")
    lines.append("> 시장: KOSPI 단독 / 변동성 필터: Vol70 / 분기 리밸런싱  ")
    lines.append("> 스크립트: `scripts/backtest_quality_10pct.py`")
    lines.append("")
    lines.append(
        "**분석 전용** — `config.yaml` / 실전 운용 설정은 변경되지 않습니다. "
        "런타임 `settings.factor_weights` 속성만 시나리오별로 변경 후 원복."
    )
    lines.append("")

    # 0. 배경
    lines.append("## 0. 배경 + 가설")
    lines.append("")
    lines.append(
        "PRD_v2.md (2026-03-26 확정) 는 Q100% 단독 CAGR -2.74% (최악) 와 "
        "프리셋 A(Q=0.25) 대비 Q 제거 시 성과 개선 실험에 근거해 **Q=0** 으로 "
        "복합 스코어에서 Quality 팩터를 제거함. F-Score 는 `min_fscore=4` "
        "필터로만 유지."
    )
    lines.append("")
    lines.append(
        "본 분석은 그 사이 미검증 영역인 **Q10% 소량 배합** 가능성을 점검한다. "
        "Quality 팩터 자체(`factors/quality.py`) 는 OP/A + EY + F-Score 기반 "
        "스코어를 이미 계산하고 있으나, 현재는 가중치 0 으로 합산 미반영 상태."
    )
    lines.append("")

    # 1. 시나리오 정의
    lines.append("## 1. 시나리오 정의")
    lines.append("")
    sc_rows = []
    for k, v in SCENARIOS.items():
        w = v["weights"]
        sc_rows.append({
            "시나리오": k,
            "라벨": v["label"],
            "V": f"{w['value']:.2f}",
            "M": f"{w['momentum']:.2f}",
            "Q": f"{w['quality']:.2f}",
        })
    lines.append(df_to_md(pd.DataFrame(sc_rows)))
    lines.append("")

    # 2. 성과 — 2017-2024 (8년)
    lines.append("## 2. 성과 (2017-2024, 8년 — 코로나/하락장 포함)")
    lines.append("")
    lines.append(df_to_md(perf_table(results_long)))
    lines.append("")

    # 3. 성과 — 2021-2024 (4년)
    lines.append("## 3. 성과 (2021-2024, 4년 — PRD_v2 KPI 구간)")
    lines.append("")
    lines.append(df_to_md(perf_table(results_short)))
    lines.append("")

    # 4. Baseline 차이 분석
    lines.append("## 4. Baseline 대비 차이 (Δ = 시나리오 - A)")
    lines.append("")
    delta_rows = []
    for period_label, results in [("2017-2024", results_long), ("2021-2024", results_short)]:
        base = next((r for r in results if r.name == "A"), None)
        if base is None:
            continue
        for r in results:
            if r.name == "A" or r.error:
                continue
            delta_rows.append({
                "기간": period_label,
                "시나리오": r.name,
                "ΔCAGR(%p)": f"{(r.cagr - base.cagr) * 100:+.2f}",
                "ΔMDD(%p)": f"{(r.mdd - base.mdd) * 100:+.2f}",
                "ΔSharpe": f"{r.sharpe - base.sharpe:+.3f}",
                "ΔSortino": f"{r.sortino - base.sortino:+.3f}",
                "ΔCalmar": f"{r.calmar - base.calmar:+.3f}",
            })
    lines.append(df_to_md(pd.DataFrame(delta_rows)))
    lines.append("")

    # 5. 종목 겹침률 (옵션)
    if with_overlap:
        for idx, (period_label, results) in enumerate(
            [("2017-2024", results_long), ("2021-2024", results_short)], start=1
        ):
            tab = overlap_table(results)
            if not tab.empty:
                lines.append(f"## 5-{idx}. 선정 종목 일치율 — {period_label}")
                lines.append("")
                lines.append(
                    "각 리밸런싱 날짜의 종목 집합 Jaccard 평균. 100% = Baseline 과 완전 동일."
                )
                lines.append("")
                lines.append(df_to_md(tab))
                lines.append("")

    # 6. 권고
    lines.append("## 6. 권고")
    lines.append("")
    lines.extend(make_recommendation(results_long, results_short))

    lines.append("---")
    lines.append("")
    lines.append(
        "> 본 분석은 _분석 전용_입니다. 실전 운용 설정 변경 여부는 운용자가 "
        "별도 판단합니다."
    )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Quality 10% 소량 배합 분석")
    parser.add_argument(
        "--periods", default="all", choices=["all", "long", "short"],
        help="실행 기간 (all=둘 다, long=2017-2024, short=2021-2024)",
    )
    parser.add_argument(
        "--scenarios", default="A,B,C,D",
        help="실행 시나리오 (콤마 구분, 기본 A,B,C,D)",
    )
    parser.add_argument(
        "--skip-overlap", action="store_true",
        help="선정 종목 겹침률 분석 스킵 — 시간 단축",
    )
    parser.add_argument(
        "--report-dir", default="docs/reports",
    )
    args = parser.parse_args()

    setup_logging()
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    invalid = [s for s in scenarios if s not in SCENARIOS]
    if invalid:
        logger.error(f"알 수 없는 시나리오: {invalid}")
        return 1

    period_keys = {"all": ["long", "short"], "long": ["long"], "short": ["short"]}[args.periods]
    with_overlap = not args.skip_overlap

    results_by_period: dict[str, list[Result]] = {}
    for pk in period_keys:
        start, end = PERIODS[pk]
        bucket: list[Result] = []
        for sc in scenarios:
            bucket.append(run_one(sc, pk, start, end, with_overlap=with_overlap))
        results_by_period[pk] = bucket

    # JSON
    report_dir = PROJECT_ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "quality_10pct_results.json"
    payload = {
        "with_overlap": with_overlap,
        "scenarios": SCENARIOS,
        "periods": {k: PERIODS[k] for k in period_keys},
        "results": {
            pk: [asdict(r) for r in bucket]
            for pk, bucket in results_by_period.items()
        },
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON 저장: {json_path}")

    # Markdown 보고서 (long + short 둘 다 있을 때만 권고 출력)
    long_r = results_by_period.get("long", [])
    short_r = results_by_period.get("short", [])
    if long_r and short_r:
        md = build_report(long_r, short_r, with_overlap)
        md_path = report_dir / "quality_10pct_analysis.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        logger.info(f"보고서 저장: {md_path}")
    else:
        logger.warning("두 기간 모두 실행되지 않아 권고 보고서 생성 건너뜀")

    # 콘솔 요약
    print(f"\n=== Quality 10% 분석 요약 ===")
    for pk in period_keys:
        start, end = PERIODS[pk]
        print(f"\n[{pk} {start} ~ {end}]")
        for r in results_by_period[pk]:
            err = f"  ERROR={r.error}" if r.error else ""
            print(
                f"  {r.name} ({r.label:30s}) CAGR={r.cagr * 100:6.2f}% "
                f"MDD={r.mdd * 100:7.2f}% Sharpe={r.sharpe:.3f} Sortino={r.sortino:.3f}{err}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
