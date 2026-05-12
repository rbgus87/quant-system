"""backtest_quality_filter_step3_variant2.py — Step 3 변형 (2): 평균 영업이익 > 0.

본 실험 (require_all_positive=True)는 미채택 — 위양성 평균 +18.15%, 겹침 70.7%.
변형 (2)는 4분기 영업이익의 **평균 > 0** 만 요구하여 일시적 1분기 적자를 허용.
위양성 감소 + 005620 회피 효과 동시 달성 가능성 검증.

비교 대상:
  A 모드: Step 1만 ON (baseline)
  B 모드: Step 1 + Step 3 (require_all_positive=False)

본 실험 결과 (참고용):
  - CAGR 5.60%, Sharpe 0.205, 겹침률 70.7%
  - 위양성 평균 +18.15% (n=118)
  - 2020-2022 ΔCAGR=-3.63%p
  - 005620 회피 ✅, POLICY 미채택 ❌

**분석 전용** — config.yaml 변경 없음.

사용:
    python scripts/backtest_quality_filter_step3_variant2.py
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
SUB_PERIODS: list[tuple[str, str, str]] = [
    ("2017-2020 소형밸류 최악", "2017-01-01", "2020-12-31"),
    ("2020-2022 코로나+회복", "2020-01-01", "2022-12-31"),
    ("2022-2024 정상", "2022-01-01", "2024-12-31"),
]
TARGET_DELIST_TICKER: str = "005620"
TARGET_REBALANCE_DATE: str = "20170630"
MARKET: str = "KOSPI"

THRESHOLD_CAGR_LOSS_PCT: float = -1.0
THRESHOLD_SHARPE_DROP: float = 0.10
THRESHOLD_OVERLAP_PCT: float = 90.0
THRESHOLD_SUB_CAGR_LOSS_PCT: float = -2.0

# 본 실험 (require_all_positive=True) 결과 — 참고용 비교 기준
MAIN_EXPERIMENT_REF: dict = {
    "cagr": 0.0560,
    "sharpe": 0.205,
    "overlap_pct": 70.7,
    "fp_mean_pct": 18.15,
    "fp_count": 118,
    "sub_2020_2022_delta_pp": -3.63,
    "005620_avoided": True,
    "policy_passed": False,
}


@dataclass
class ModeResult:
    name: str
    label: str
    step1_enabled: bool
    step3_enabled: bool
    cagr: float = 0.0
    mdd: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    volatility: float = 0.0
    total_return: float = 0.0
    sub_cagr: dict[str, float] = field(default_factory=dict)
    avg_turnover: float = 0.0
    avg_selection_size: float = 0.0
    n_rebalances: int = 0
    selections_by_date: dict[str, list[str]] = field(default_factory=dict)
    delist_exposures: list[dict] = field(default_factory=list)
    target_005620_in_selection: bool = False
    portfolio_value_records: list[dict] = field(default_factory=list)
    error: Optional[str] = None


class FilterGuard:
    """Step 1 / Step 3 + require_all_positive 동시 토글, 원복."""

    def __init__(self) -> None:
        q = settings.quality
        self._backup = {
            "operating_quality_filter_enabled": q.operating_quality_filter_enabled,
            "consecutive_profit_filter_enabled": q.consecutive_profit_filter_enabled,
            "consecutive_profit_n_quarters": q.consecutive_profit_n_quarters,
            "consecutive_profit_metric": q.consecutive_profit_metric,
            "consecutive_profit_require_all": q.consecutive_profit_require_all,
            "consecutive_profit_min_data": q.consecutive_profit_min_data,
        }

    def apply(
        self, step1: bool, step3: bool, require_all_positive: bool = False,
    ) -> None:
        settings.quality.operating_quality_filter_enabled = step1
        settings.quality.consecutive_profit_filter_enabled = step3
        # 변형 (2) 핵심: require_all_positive=False (평균만 양수)
        settings.quality.consecutive_profit_require_all = require_all_positive
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception as e:
            logger.warning(f"팩터 캐시 클리어 실패: {e}")

    def restore(self) -> None:
        for k, v in self._backup.items():
            setattr(settings.quality, k, v)
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception:
            pass

    def __enter__(self) -> "FilterGuard":
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


def compute_sub_period_cagr(pv: pd.Series, start: str, end: str) -> float:
    if pv.empty:
        return 0.0
    sd = pd.Timestamp(start)
    ed = pd.Timestamp(end)
    idx = pd.to_datetime(pv.index)
    mask = (idx >= sd) & (idx <= ed)
    sliced = pv[mask]
    if len(sliced) < 2:
        return 0.0
    start_val = float(sliced.iloc[0])
    end_val = float(sliced.iloc[-1])
    if start_val <= 0:
        return 0.0
    days = (pd.Timestamp(sliced.index[-1]) - pd.Timestamp(sliced.index[0])).days
    years = max(days / 365.25, 1e-9)
    return (end_val / start_val) ** (1.0 / years) - 1.0


def detect_delist_exposures(
    selections_by_date: dict[str, list[str]], storage,
) -> list[dict]:
    from datetime import timedelta

    dates_sorted = sorted(selections_by_date.keys())
    if not dates_sorted:
        return []
    start_dt = datetime.strptime(dates_sorted[0], "%Y%m%d").date()
    end_dt = datetime.strptime(dates_sorted[-1], "%Y%m%d").date() + timedelta(days=365)

    try:
        delisted = storage.load_delisted_stocks(
            start_date=start_dt, end_date=end_dt, category="failure",
        )
    except Exception as e:
        logger.warning(f"delisted_stock 조회 실패: {e}")
        return []

    if delisted.empty:
        return []

    delist_map: dict[str, tuple] = {
        str(r["ticker"]): (r["delist_date"], r["name"])
        for _, r in delisted.iterrows()
    }

    exposures: list[dict] = []
    for i, ds in enumerate(dates_sorted):
        sel = set(selections_by_date.get(ds, []))
        if not sel:
            continue
        rebal_d = datetime.strptime(ds, "%Y%m%d").date()
        next_d = (
            datetime.strptime(dates_sorted[i + 1], "%Y%m%d").date()
            if i + 1 < len(dates_sorted) else end_dt
        )
        for tk in sel:
            if tk not in delist_map:
                continue
            ddate, dname = delist_map[tk]
            if rebal_d < ddate <= next_d:
                exposures.append({
                    "rebalance_date": ds,
                    "ticker": tk,
                    "name": dname,
                    "delist_date": str(ddate),
                })
    return exposures


def jaccard_avg(a: dict[str, list[str]], b: dict[str, list[str]]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    vals: list[float] = []
    for d in common:
        sa, sb = set(a[d]), set(b[d])
        if not sa and not sb:
            continue
        uni = len(sa | sb)
        if uni > 0:
            vals.append(len(sa & sb) / uni)
    return sum(vals) / len(vals) if vals else 0.0


def compute_false_positive_returns(
    a_selections: dict[str, list[str]],
    b_selections: dict[str, list[str]],
) -> dict:
    from data.collector import KRXDataCollector

    coll = KRXDataCollector(request_delay=0.5)

    sorted_dates = sorted(a_selections.keys())
    fp_returns: list[float] = []
    sample_count = 0

    for i, ds in enumerate(sorted_dates):
        if i + 1 >= len(sorted_dates):
            break
        sa = set(a_selections.get(ds, []))
        sb = set(b_selections.get(ds, []))
        removed = sa - sb
        if not removed:
            continue
        next_ds = sorted_dates[i + 1]
        try:
            from datetime import datetime as _dt

            start_dt = _dt.strptime(ds, "%Y%m%d").date()
            end_dt = _dt.strptime(next_ds, "%Y%m%d").date()
            for tk in removed:
                try:
                    df = coll.get_ohlcv(
                        tk,
                        start_dt.strftime("%Y%m%d"),
                        end_dt.strftime("%Y%m%d"),
                    )
                    if df is None or df.empty or "close" not in df.columns:
                        continue
                    closes = df["close"].dropna()
                    if len(closes) < 2:
                        continue
                    ret = float(closes.iloc[-1] / closes.iloc[0] - 1)
                    fp_returns.append(ret)
                    sample_count += 1
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"위양성 수익률 계산 실패 {ds}: {e}")
            continue

    if not fp_returns:
        return {"count": 0, "mean": 0.0, "median": 0.0}
    return {
        "count": sample_count,
        "mean": float(np.mean(fp_returns)),
        "median": float(np.median(fp_returns)),
    }


def run_mode(
    name: str, step1: bool, step3: bool, require_all: bool = False,
) -> ModeResult:
    label = (
        f"Step1={'ON' if step1 else 'OFF'}, "
        f"Step3={'ON' if step3 else 'OFF'}"
    )
    if step3:
        label += f", require_all_positive={require_all}"
    res = ModeResult(
        name=name, label=label, step1_enabled=step1, step3_enabled=step3,
    )

    logger.info("=" * 70)
    logger.info(f"모드 {name}: {label}  ({BACKTEST_START} ~ {BACKTEST_END})")
    logger.info("=" * 70)

    with FilterGuard() as guard:
        guard.apply(step1=step1, step3=step3, require_all_positive=require_all)
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
        res.total_return = float(pv.iloc[-1] / pv.iloc[0] - 1) if len(pv) >= 2 else 0.0

        for label_sub, sub_start, sub_end in SUB_PERIODS:
            res.sub_cagr[label_sub] = compute_sub_period_cagr(pv, sub_start, sub_end)

        rebal_dates = engine._generate_rebalance_dates(
            BACKTEST_START, BACKTEST_END, MARKET,
        )
        res.n_rebalances = len(rebal_dates)
        logger.info(f"[모드 {name}] 선정 종목 수집 ({len(rebal_dates)}회)")
        sel = collect_selections(rebal_dates)
        sizes = [len(v) for v in sel.values() if v]
        res.avg_selection_size = sum(sizes) / len(sizes) if sizes else 0.0
        res.selections_by_date = sel

        target_sel = sel.get(TARGET_REBALANCE_DATE, [])
        res.target_005620_in_selection = TARGET_DELIST_TICKER in target_sel
        logger.info(
            f"[모드 {name}] 2017-06-30 선정 종목 수={len(target_sel)}, "
            f"005620 포함={res.target_005620_in_selection}"
        )

        res.delist_exposures = detect_delist_exposures(sel, engine.krx.storage)

        turnovers: list[float] = []
        date_keys = sorted(sel.keys())
        for i in range(1, len(date_keys)):
            prev_s = set(sel[date_keys[i - 1]])
            curr_s = set(sel[date_keys[i]])
            if not prev_s and not curr_s:
                continue
            n = max(len(prev_s), len(curr_s))
            if n == 0:
                continue
            turnovers.append(len(curr_s - prev_s) / n)
        res.avg_turnover = sum(turnovers) / len(turnovers) if turnovers else 0.0

        pv_records = []
        for dt, val in pv.items():
            pv_records.append({
                "date": str(dt),
                "portfolio_value": float(val) if pd.notna(val) else None,
            })
        res.portfolio_value_records = pv_records

    return res


def fmt_pct_signless(x: float) -> str:
    return f"{x * 100:.2f}%"


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


def evaluate_adoption(
    res_a: ModeResult, res_b: ModeResult, overlap_pct: float,
) -> tuple[bool, list[tuple[str, bool, str]]]:
    delta_cagr_pp = (res_b.cagr - res_a.cagr) * 100.0
    cond1 = delta_cagr_pp >= THRESHOLD_CAGR_LOSS_PCT
    detail1 = f"ΔCAGR={delta_cagr_pp:+.2f}%p (기준: ≥ {THRESHOLD_CAGR_LOSS_PCT}%p)"

    cond_2a = not res_b.target_005620_in_selection
    cond_2b = res_b.cagr > res_a.cagr and res_b.sharpe > res_a.sharpe
    cond2 = cond_2a or cond_2b
    paths = []
    paths.append("(a) 005620 회피 ✅" if cond_2a else "(a) 005620 회피 ❌")
    if cond_2b:
        paths.append("(b) Alpha 동시 개선 ✅")
    else:
        paths.append(
            f"(b) ΔCAGR={delta_cagr_pp:+.2f}%p, "
            f"ΔSharpe={res_b.sharpe - res_a.sharpe:+.3f} ❌"
        )
    detail2 = " | ".join(paths)

    sharpe_drop = res_a.sharpe - res_b.sharpe
    cond3 = sharpe_drop < THRESHOLD_SHARPE_DROP
    detail3 = f"ΔSharpe={-sharpe_drop:+.3f} (기준: 하락 < {THRESHOLD_SHARPE_DROP})"

    cond4 = overlap_pct >= THRESHOLD_OVERLAP_PCT
    detail4 = f"겹침률={overlap_pct:.1f}% (기준: ≥ {THRESHOLD_OVERLAP_PCT}%)"

    sub_failures = []
    for label_sub, _, _ in SUB_PERIODS:
        a = res_a.sub_cagr.get(label_sub, 0.0)
        b = res_b.sub_cagr.get(label_sub, 0.0)
        d_pp = (b - a) * 100.0
        if d_pp < THRESHOLD_SUB_CAGR_LOSS_PCT:
            sub_failures.append(f"{label_sub}: ΔCAGR={d_pp:+.2f}%p")
    cond5 = len(sub_failures) == 0
    detail5 = (
        f"모든 구간 ΔCAGR ≥ {THRESHOLD_SUB_CAGR_LOSS_PCT}%p ✅"
        if cond5 else f"미달: {'; '.join(sub_failures)}"
    )

    conditions = [
        ("1. CAGR 손실 ≤ -1%p (8년)", cond1, detail1),
        ("2. (a) 005620 회피 OR (b) Alpha 동시 개선", cond2, detail2),
        ("3. Sharpe 하락 < 0.10", cond3, detail3),
        ("4. 종목 겹침률 ≥ 90%", cond4, detail4),
        ("5. 하위 구간 안정성", cond5, detail5),
    ]
    return all(c[1] for c in conditions), conditions


def build_report(
    res_a: ModeResult, res_b: ModeResult, fp_stats: dict,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overlap_jacc = jaccard_avg(res_a.selections_by_date, res_b.selections_by_date)
    overlap_pct = overlap_jacc * 100.0
    adopted, conditions = evaluate_adoption(res_a, res_b, overlap_pct)

    sub_2020 = (
        res_b.sub_cagr.get("2020-2022 코로나+회복", 0.0)
        - res_a.sub_cagr.get("2020-2022 코로나+회복", 0.0)
    ) * 100.0

    lines: list[str] = []
    lines.append(
        "# Step 3 변형 (2): require_all_positive=False — 평균 영업이익 > 0"
    )
    lines.append("")
    lines.append(f"> 생성: {now}  ")
    lines.append(
        f"> 기간: {BACKTEST_START} ~ {BACKTEST_END} (8년) / "
        f"시장: {MARKET} / 프리셋: A / 시드={RANDOM_SEED}"
    )
    lines.append(
        "> 스크립트: `scripts/backtest_quality_filter_step3_variant2.py`  \n"
        "> A 모드: Step 1 ON, Step 3 OFF (현재 운용 baseline)  \n"
        "> B 모드: Step 1 ON, Step 3 ON, **require_all_positive=False**"
    )
    lines.append("")
    lines.append(
        "**분석 전용** — `config.yaml`은 변경되지 않습니다."
    )
    lines.append("")

    # 0. 결론
    verdict = "✅ 채택 권고" if adopted else "❌ 미채택"
    lines.append("## 0. 결론")
    lines.append("")
    lines.append(f"**판정**: {verdict}")
    lines.append("")
    lines.append("| 조건 | 통과 | 상세 |")
    lines.append("| --- | --- | --- |")
    for name, ok, detail in conditions:
        flag = "✅" if ok else "❌"
        lines.append(f"| {name} | {flag} | {detail} |")
    lines.append("")

    # 1. 본 실험 대비 변화
    lines.append("## 1. 본 실험 (require_all_positive=True) 대비")
    lines.append("")
    cmp_rows = [
        {
            "지표": "CAGR",
            "본 실험 (require_all=True)": f"{MAIN_EXPERIMENT_REF['cagr'] * 100:.2f}%",
            "변형 (2) (require_all=False)": f"{res_b.cagr * 100:.2f}%",
            "Δ (변형 - 본실험)":
                f"{(res_b.cagr - MAIN_EXPERIMENT_REF['cagr']) * 100:+.2f}%p",
        },
        {
            "지표": "Sharpe",
            "본 실험 (require_all=True)": f"{MAIN_EXPERIMENT_REF['sharpe']:.3f}",
            "변형 (2) (require_all=False)": f"{res_b.sharpe:.3f}",
            "Δ (변형 - 본실험)": f"{res_b.sharpe - MAIN_EXPERIMENT_REF['sharpe']:+.3f}",
        },
        {
            "지표": "종목 겹침률 vs A",
            "본 실험 (require_all=True)": f"{MAIN_EXPERIMENT_REF['overlap_pct']:.1f}%",
            "변형 (2) (require_all=False)": f"{overlap_pct:.1f}%",
            "Δ (변형 - 본실험)":
                f"{overlap_pct - MAIN_EXPERIMENT_REF['overlap_pct']:+.1f}%p",
        },
        {
            "지표": "위양성 평균 수익률",
            "본 실험 (require_all=True)":
                f"+{MAIN_EXPERIMENT_REF['fp_mean_pct']:.2f}% "
                f"(n={MAIN_EXPERIMENT_REF['fp_count']})",
            "변형 (2) (require_all=False)":
                f"{fp_stats.get('mean', 0) * 100:+.2f}% "
                f"(n={fp_stats.get('count', 0)})",
            "Δ (변형 - 본실험)":
                f"{fp_stats.get('mean', 0) * 100 - MAIN_EXPERIMENT_REF['fp_mean_pct']:+.2f}%p",
        },
        {
            "지표": "2020-2022 ΔCAGR vs A",
            "본 실험 (require_all=True)":
                f"{MAIN_EXPERIMENT_REF['sub_2020_2022_delta_pp']:+.2f}%p",
            "변형 (2) (require_all=False)": f"{sub_2020:+.2f}%p",
            "Δ (변형 - 본실험)":
                f"{sub_2020 - MAIN_EXPERIMENT_REF['sub_2020_2022_delta_pp']:+.2f}%p",
        },
        {
            "지표": "005620 회피",
            "본 실험 (require_all=True)": (
                "✅" if MAIN_EXPERIMENT_REF["005620_avoided"] else "❌"
            ),
            "변형 (2) (require_all=False)": (
                "✅" if not res_b.target_005620_in_selection else "❌"
            ),
            "Δ (변형 - 본실험)": "",
        },
        {
            "지표": "POLICY 통과",
            "본 실험 (require_all=True)": (
                "✅" if MAIN_EXPERIMENT_REF["policy_passed"] else "❌"
            ),
            "변형 (2) (require_all=False)": "✅" if adopted else "❌",
            "Δ (변형 - 본실험)": "",
        },
    ]
    lines.append(df_to_md(pd.DataFrame(cmp_rows)))
    lines.append("")

    # 2. 핵심 비교 (A vs B)
    lines.append("## 2. A (Step1만) vs B (Step1+Step3 변형2)")
    lines.append("")
    perf_rows = []
    for r in [res_a, res_b]:
        perf_rows.append({
            "모드": r.name,
            "라벨": r.label,
            "CAGR": fmt_pct_signless(r.cagr),
            "MDD": fmt_pct_signless(r.mdd),
            "Sharpe": f"{r.sharpe:.3f}",
            "Sortino": f"{r.sortino:.3f}",
            "Calmar": f"{r.calmar:.3f}",
            "Vol": fmt_pct_signless(r.volatility),
            "총수익률": fmt_pct_signless(r.total_return),
        })
    lines.append(df_to_md(pd.DataFrame(perf_rows)))
    lines.append("")

    # 3. 회전·겹침
    lines.append("## 3. 회전율·선정 종목·겹침률")
    lines.append("")
    op_rows = []
    for r in [res_a, res_b]:
        op_rows.append({
            "모드": r.name,
            "평균 분기 회전율": fmt_pct_signless(r.avg_turnover),
            "평균 선정 종목 수": f"{r.avg_selection_size:.1f}",
            "리밸런싱 횟수": str(r.n_rebalances),
        })
    lines.append(df_to_md(pd.DataFrame(op_rows)))
    lines.append("")
    lines.append(f"- 종목 겹침률 (Jaccard 평균): **{overlap_pct:.1f}%**")
    lines.append("")

    # 4. 폐지 회피
    lines.append("## 4. 폐지(failure) 종목 회피")
    lines.append("")
    expose_rows = []
    for r in [res_a, res_b]:
        expose_rows.append({
            "모드": r.name,
            "노출 건수": str(len(r.delist_exposures)),
            "포함 티커": ", ".join(sorted({e["ticker"] for e in r.delist_exposures})) or "(없음)",
        })
    lines.append(df_to_md(pd.DataFrame(expose_rows)))
    lines.append("")

    # 5. 005620
    lines.append("## 5. 005620 사례 회피")
    lines.append("")
    t_rows = []
    for r in [res_a, res_b]:
        sel = r.selections_by_date.get(TARGET_REBALANCE_DATE, [])
        t_rows.append({
            "모드": r.name,
            "선정 종목 수": str(len(sel)),
            "005620 포함": "❌ 포함됨" if r.target_005620_in_selection else "✅ 미선정",
        })
    lines.append(df_to_md(pd.DataFrame(t_rows)))
    lines.append("")

    # 6. 위양성
    lines.append("## 6. 위양성 분석")
    lines.append("")
    lines.append(
        f"- 표본 수: {fp_stats.get('count', 0)}  \n"
        f"- 평균 수익률: {fp_stats.get('mean', 0) * 100:+.2f}%  \n"
        f"- 중간값 수익률: {fp_stats.get('median', 0) * 100:+.2f}%"
    )
    lines.append("")

    # 7. 하위 구간
    lines.append("## 7. 하위 구간 안정성")
    lines.append("")
    sub_rows = []
    for label_sub, sub_start, sub_end in SUB_PERIODS:
        a = res_a.sub_cagr.get(label_sub, 0.0)
        b = res_b.sub_cagr.get(label_sub, 0.0)
        sub_rows.append({
            "구간": label_sub,
            "기간": f"{sub_start} ~ {sub_end}",
            "CAGR A": fmt_pct_signless(a),
            "CAGR B": fmt_pct_signless(b),
            "ΔCAGR(%p)": f"{(b - a) * 100:+.2f}",
        })
    lines.append(df_to_md(pd.DataFrame(sub_rows)))
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> 변형 (2) 분석 전용. POLICY.md 5조건 통과 시에도 `config.yaml` 변경은 "
        "사용자 최종 확인 후 별도 커밋."
    )
    return "\n".join(lines)


def print_summary(res_a: ModeResult, res_b: ModeResult, fp_stats: dict) -> None:
    overlap = jaccard_avg(res_a.selections_by_date, res_b.selections_by_date) * 100.0
    a_005620 = "INCLUDED" if res_a.target_005620_in_selection else "AVOIDED"
    b_005620 = "INCLUDED" if res_b.target_005620_in_selection else "AVOIDED"
    sub_2020_b = res_b.sub_cagr.get("2020-2022 코로나+회복", 0.0)
    sub_2020_a = res_a.sub_cagr.get("2020-2022 코로나+회복", 0.0)
    delta_2020 = (sub_2020_b - sub_2020_a) * 100.0

    print()
    print("=" * 88)
    print("Step 3 Variant (2): require_all_positive=False -- Summary")
    print("=" * 88)
    print(
        f"{'Metric':<22} {'A (Step1)':>14} {'B (variant2)':>14} "
        f"{'Main exp':>12} {'D vs main':>12}"
    )
    print("-" * 88)
    print(
        f"{'CAGR':<22} {res_a.cagr * 100:>13.2f}% {res_b.cagr * 100:>13.2f}% "
        f"{MAIN_EXPERIMENT_REF['cagr'] * 100:>11.2f}% "
        f"{(res_b.cagr - MAIN_EXPERIMENT_REF['cagr']) * 100:>+11.2f}"
    )
    print(
        f"{'Sharpe':<22} {res_a.sharpe:>14.3f} {res_b.sharpe:>14.3f} "
        f"{MAIN_EXPERIMENT_REF['sharpe']:>12.3f} "
        f"{res_b.sharpe - MAIN_EXPERIMENT_REF['sharpe']:>+12.3f}"
    )
    print(
        f"{'Overlap (Jaccard)':<22} {'':>14} {overlap:>13.1f}% "
        f"{MAIN_EXPERIMENT_REF['overlap_pct']:>11.1f}% "
        f"{overlap - MAIN_EXPERIMENT_REF['overlap_pct']:>+11.1f}"
    )
    print(
        f"{'False-pos avg ret':<22} {'':>14} {fp_stats.get('mean', 0) * 100:>+13.2f}% "
        f"{MAIN_EXPERIMENT_REF['fp_mean_pct']:>+11.2f}% "
        f"{fp_stats.get('mean', 0) * 100 - MAIN_EXPERIMENT_REF['fp_mean_pct']:>+11.2f}"
    )
    print(
        f"{'2020-2022 dCAGR':<22} {'':>14} {delta_2020:>+13.2f}% "
        f"{MAIN_EXPERIMENT_REF['sub_2020_2022_delta_pp']:>+11.2f}% "
        f"{delta_2020 - MAIN_EXPERIMENT_REF['sub_2020_2022_delta_pp']:>+11.2f}"
    )
    print(
        f"{'005620':<22} {a_005620:>14} {b_005620:>14} "
        f"{'AVOIDED':>12} {'':>12}"
    )
    print("=" * 88)


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 3 변형 (2) — require_all_positive=False")
    parser.add_argument("--report-dir", default="docs/reports")
    parser.add_argument(
        "--skip-fp-analysis", action="store_true",
        help="위양성 분석 스킵",
    )
    args = parser.parse_args()

    setup_logging()
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    res_a = run_mode("A", step1=True, step3=False)
    # 변형 (2): require_all_positive=False
    res_b = run_mode("B", step1=True, step3=True, require_all=False)

    if not args.skip_fp_analysis and not res_a.error and not res_b.error:
        logger.info("위양성 분석 — B모드 추가 제거 종목 다음 분기 수익률 계산")
        fp_stats = compute_false_positive_returns(
            res_a.selections_by_date, res_b.selections_by_date,
        )
    else:
        fp_stats = {"count": 0, "mean": 0.0, "median": 0.0}

    report_dir = PROJECT_ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "quality_filter_step3_variant2_results.json"
    payload = {
        "seed": RANDOM_SEED,
        "variant": "require_all_positive=False",
        "period": [BACKTEST_START, BACKTEST_END],
        "market": MARKET,
        "main_experiment_ref": MAIN_EXPERIMENT_REF,
        "false_positive": fp_stats,
        "results": {"A": asdict(res_a), "B": asdict(res_b)},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON 저장: {json_path}")

    md = build_report(res_a, res_b, fp_stats)
    md_path = report_dir / "quality_filter_step3_variant2_analysis.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"보고서 저장: {md_path}")

    print_summary(res_a, res_b, fp_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
