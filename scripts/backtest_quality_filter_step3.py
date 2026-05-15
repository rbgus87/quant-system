"""backtest_quality_filter_step3.py — Step 3 연속 흑자 4분기 필터 채택 검증.

A 모드 (현재 운용 baseline, Step 1 ON / Step 3 OFF)  vs
B 모드 (실험, Step 1 + Step 3 모두 ON) 의 2017-2024 KOSPI 백테스트 비교.

판정 기준 (POLICY.md 5조건, #2는 OR 구조로 재정의 후):
  1. CAGR 손실 ≤ -1%p
  2. (a) 005620 회피 OR (b) Alpha 동시 개선 (ΔCAGR > 0 AND ΔSharpe > 0)
  3. Sharpe 하락 < 0.10
  4. 종목 겹침률 ≥ 90% (32분기 평균)
  5. 하위 구간 안정성 (2017-2020/2020-2022/2022-2024 ΔCAGR ≥ -2%p)

**분석 전용** — config.yaml은 변경되지 않음. 런타임 settings 토글만.

사용:
    python scripts/backtest_quality_filter_step3.py
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
    """Step 1 / Step 3 settings 토글 + 컨텍스트 종료 시 원복."""

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

    def apply(self, step1: bool, step3: bool) -> None:
        settings.quality.operating_quality_filter_enabled = step1
        settings.quality.consecutive_profit_filter_enabled = step3
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
    """선정 종목 중 다음 리밸런싱 전 폐지(failure) 종목 매칭."""
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
    rebal_dates: list[pd.Timestamp],
) -> dict:
    """B모드에서 추가로 제거된 종목(A∩B^c)의 다음 분기 평균 수익률.

    음수 = 위양성(false-positive) 의미 있음 (제거가 alpha에 +)
    양수 = alpha 손실 (정상 종목을 잘못 제거)
    """
    from data.collector import KRXDataCollector, ReturnCalculator

    coll = KRXDataCollector(request_delay=0.5)
    calc = ReturnCalculator(collector=coll)

    sorted_dates = sorted(a_selections.keys())
    fp_returns: list[float] = []
    sample_count = 0

    for i, ds in enumerate(sorted_dates):
        if i + 1 >= len(sorted_dates):
            break
        sa = set(a_selections.get(ds, []))
        sb = set(b_selections.get(ds, []))
        removed = sa - sb  # B에서 추가 제거된 종목
        if not removed:
            continue
        next_ds = sorted_dates[i + 1]
        # ds → next_ds 1분기 수익률
        try:
            # 보유 기간 수익률 (시작 ~ 다음 리밸런싱)
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


def run_mode(name: str, step1: bool, step3: bool) -> ModeResult:
    label = f"Step1={'ON' if step1 else 'OFF'}, Step3={'ON' if step3 else 'OFF'}"
    res = ModeResult(
        name=name, label=label, step1_enabled=step1, step3_enabled=step3,
    )

    logger.info("=" * 70)
    logger.info(f"모드 {name}: {label}  ({BACKTEST_START} ~ {BACKTEST_END})")
    logger.info("=" * 70)

    with FilterGuard() as guard:
        guard.apply(step1=step1, step3=step3)
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
    fp_stats: dict,
) -> tuple[bool, list[tuple[str, bool, str]]]:
    """POLICY.md 5조건 평가 (재정의된 #2 OR 구조 적용)."""
    delta_cagr_pp = (res_b.cagr - res_a.cagr) * 100.0
    cond1 = delta_cagr_pp >= THRESHOLD_CAGR_LOSS_PCT
    detail1 = f"ΔCAGR={delta_cagr_pp:+.2f}%p (기준: ≥ {THRESHOLD_CAGR_LOSS_PCT}%p)"

    # 조건 2: (a) 005620 회피 OR (b) alpha 동시 개선
    cond_2a = not res_b.target_005620_in_selection
    cond_2b = (
        res_b.cagr > res_a.cagr and res_b.sharpe > res_a.sharpe
    )
    cond2 = cond_2a or cond_2b
    paths: list[str] = []
    if cond_2a:
        paths.append("(a) 005620 회피 ✅")
    else:
        paths.append("(a) 005620 회피 ❌")
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

    sub_failures: list[str] = []
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
    adopted, conditions = evaluate_adoption(res_a, res_b, overlap_pct, fp_stats)

    lines: list[str] = []
    lines.append("# Step 3: Quality Filter — 연속 흑자 4분기 필터 분석")
    lines.append("")
    lines.append(f"> 생성: {now}  ")
    lines.append(
        f"> 기간: {BACKTEST_START} ~ {BACKTEST_END} (8년) / 시장: {MARKET} / "
        f"프리셋: A / 분기 리밸런싱 / 시드={RANDOM_SEED}"
    )
    lines.append(
        "> 스크립트: `scripts/backtest_quality_filter_step3.py`  \n"
        "> A 모드: Step 1 ON, Step 3 OFF (현재 운용 baseline)  \n"
        "> B 모드: Step 1 ON, Step 3 ON (실험)"
    )
    lines.append("")
    lines.append(
        "**분석 전용** — `config.yaml`은 변경되지 않습니다. "
        "런타임 settings 토글만."
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
    if adopted:
        lines.append(
            "> POLICY.md 5조건 모두 통과. 사용자 최종 확인 후 별도 커밋에서 "
            "`config.yaml` 의 `consecutive_profit_filter_enabled: true` 활성화."
        )
    else:
        failed = [c[0] for c in conditions if not c[1]]
        lines.append(
            f"> POLICY.md 5조건 중 미달: {', '.join(failed)}. "
            "Step 3 필터는 현재 형태로 채택하지 않는다. "
            "변형안 후보: `n_quarters=2` (요건 완화) / "
            "`require_all_positive=False` (평균만 양수) / "
            "`metric='eps'` (EPS NULL 100%이므로 불가)."
        )
    lines.append("")

    # 1. 핵심 비교표
    lines.append("## 1. 핵심 비교 (2017-2024 전 구간)")
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
            "에러": r.error or "",
        })
    lines.append(df_to_md(pd.DataFrame(perf_rows)))
    lines.append("")
    lines.append(
        f"- ΔCAGR (B - A): {(res_b.cagr - res_a.cagr) * 100:+.2f}%p  \n"
        f"- ΔSharpe (B - A): {res_b.sharpe - res_a.sharpe:+.3f}  \n"
        f"- ΔMDD (B - A): {(res_b.mdd - res_a.mdd) * 100:+.2f}%p"
    )
    lines.append("")

    # 2. 회전율·선정·겹침률
    lines.append("## 2. 회전율·선정 종목 수·겹침률")
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

    # 3. 폐지 종목 회피 효과
    lines.append("## 3. 폐지(failure) 종목 회피 효과")
    lines.append("")
    a_expose_set = {(e["rebalance_date"], e["ticker"]) for e in res_a.delist_exposures}
    b_expose_set = {(e["rebalance_date"], e["ticker"]) for e in res_b.delist_exposures}
    avoided = a_expose_set - b_expose_set
    only_b = b_expose_set - a_expose_set
    expose_rows = []
    for r in [res_a, res_b]:
        expose_rows.append({
            "모드": r.name,
            "노출 건수": str(len(r.delist_exposures)),
            "포함 티커": ", ".join(sorted({e["ticker"] for e in r.delist_exposures})) or "(없음)",
        })
    lines.append(df_to_md(pd.DataFrame(expose_rows)))
    lines.append("")
    if avoided:
        lines.append(f"### B모드에서 회피된 폐지 노출 ({len(avoided)}건)")
        lines.append("")
        avoid_rows = []
        for ds, tk in sorted(avoided):
            match = next(
                (e for e in res_a.delist_exposures
                 if e["rebalance_date"] == ds and e["ticker"] == tk),
                None,
            )
            avoid_rows.append({
                "리밸런싱": ds,
                "티커": tk,
                "종목명": match.get("name") if match else "",
                "폐지일": match.get("delist_date") if match else "",
            })
        lines.append(df_to_md(pd.DataFrame(avoid_rows)))
        lines.append("")
    if only_b:
        lines.append(f"### B모드에서 새로 노출된 폐지 ({len(only_b)}건) — 비정상")
        lines.append("")
        lines.append(", ".join(f"{ds}/{tk}" for ds, tk in sorted(only_b)))
        lines.append("")

    # 4. 005620 검증
    lines.append("## 4. 005620 사례 회피 검증")
    lines.append("")
    lines.append(
        "docs/case_studies/005620_lesson.md — 2017-06-30 리밸런싱에서 "
        "F-Score 4점 턱걸이 통과 + 2017-Q1 일회성 흑자 전환 사례."
    )
    lines.append("")
    target_rows = []
    for r in [res_a, res_b]:
        sel = r.selections_by_date.get(TARGET_REBALANCE_DATE, [])
        target_rows.append({
            "모드": r.name,
            "선정 종목 수": str(len(sel)),
            "005620 포함": "❌ 포함됨" if r.target_005620_in_selection else "✅ 미선정",
        })
    lines.append(df_to_md(pd.DataFrame(target_rows)))
    lines.append("")

    # 5. 위양성 분석
    lines.append("## 5. 위양성 분석")
    lines.append("")
    lines.append(
        "B모드에서 추가로 제거된 종목의 *다음 분기 보유 수익률* 평균. "
        "음수면 위양성이 의미 있음 (제거로 손실 회피), 양수면 alpha 손상."
    )
    lines.append("")
    lines.append(
        f"- 표본 수: {fp_stats.get('count', 0)}  \n"
        f"- 평균 수익률: {fp_stats.get('mean', 0.0) * 100:+.2f}%  \n"
        f"- 중간값 수익률: {fp_stats.get('median', 0.0) * 100:+.2f}%"
    )
    lines.append("")

    # 6. 하위 구간
    lines.append("## 6. 하위 구간 안정성")
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

    # 7. 한계
    lines.append("## 7. 한계 및 다음 단계")
    lines.append("")
    lines.append(
        "- DART `fnlttMultiAcnt`가 **분기 보고서에서 EPS 계정을 반환하지 않아** "
        "  Step 3의 `metric='eps'` 모드는 사실상 사용 불가 (NULL 100%). "
        "  `operating_income`을 기본값으로 권장.  \n"
        "- 분기 데이터 보유 종목 비율 ~83% (종목당 평균 29.9/36분기). "
        "  데이터 부족 종목은 `min_data_quarters` 정책상 통과 처리되어 "
        "  강건성 확보. 그러나 폐지 직전 데이터 누락 종목은 사전 차단 불가.  \n"
        "- 본 검증은 KOSPI 단독·프리셋 A 고정. "
        "  실전 활성화 전 Walk-Forward 재검증 권장."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "> 본 분석은 _분석 전용_. POLICY.md 5조건 통과 시에도 "
        "`config.yaml` 변경은 사용자 최종 확인 후 별도 커밋."
    )
    return "\n".join(lines)


def print_summary(res_a: ModeResult, res_b: ModeResult, fp_stats: dict) -> None:
    overlap = jaccard_avg(res_a.selections_by_date, res_b.selections_by_date) * 100.0
    a_005620 = "INCLUDED" if res_a.target_005620_in_selection else "AVOIDED"
    b_005620 = "INCLUDED" if res_b.target_005620_in_selection else "AVOIDED"
    print()
    print("=" * 76)
    print("Step 3 (consecutive profit 4Q) -- Summary")
    print("=" * 76)
    print(f"{'Metric':<28} {'A (Step1)':>20} {'B (Step1+Step3)':>20} {'Delta':>6}")
    print("-" * 76)
    print(
        f"{'CAGR':<28} {res_a.cagr * 100:>19.2f}% {res_b.cagr * 100:>19.2f}% "
        f"{(res_b.cagr - res_a.cagr) * 100:>+6.2f}"
    )
    print(
        f"{'MDD':<28} {res_a.mdd * 100:>19.2f}% {res_b.mdd * 100:>19.2f}% "
        f"{(res_b.mdd - res_a.mdd) * 100:>+6.2f}"
    )
    print(
        f"{'Sharpe':<28} {res_a.sharpe:>20.3f} {res_b.sharpe:>20.3f} "
        f"{res_b.sharpe - res_a.sharpe:>+6.3f}"
    )
    print(f"{'Overlap (Jaccard avg)':<28} {'':>20} {'':>20} {overlap:>+5.1f}%")
    print(
        f"{'005620 at 2017-06-30':<28} {a_005620:>20} {b_005620:>20}"
    )
    print(
        f"{'Delist (failure) exposures':<28} {len(res_a.delist_exposures):>20} "
        f"{len(res_b.delist_exposures):>20}"
    )
    print(
        f"{'False-positive avg ret':<28} {'':>20} "
        f"{fp_stats.get('mean', 0) * 100:>+19.2f}% "
        f"(n={fp_stats.get('count', 0)})"
    )
    print("=" * 76)


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 3 연속 흑자 필터 검증")
    parser.add_argument("--report-dir", default="docs/reports")
    parser.add_argument(
        "--skip-fp-analysis", action="store_true",
        help="위양성 분석 스킵 (수익률 조회 시간 단축)",
    )
    args = parser.parse_args()

    setup_logging()
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    res_a = run_mode("A", step1=True, step3=False)
    res_b = run_mode("B", step1=True, step3=True)

    # 위양성 분석
    if not args.skip_fp_analysis and not res_a.error and not res_b.error:
        from backtest.engine import MultiFactorBacktest

        engine = MultiFactorBacktest()
        rebal_dates = engine._generate_rebalance_dates(
            BACKTEST_START, BACKTEST_END, MARKET,
        )
        logger.info("위양성 분석 — B모드 추가 제거 종목 다음 분기 수익률 계산")
        fp_stats = compute_false_positive_returns(
            res_a.selections_by_date, res_b.selections_by_date, rebal_dates,
        )
    else:
        fp_stats = {"count": 0, "mean": 0.0, "median": 0.0}

    # JSON 저장
    report_dir = PROJECT_ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "quality_filter_step3_results.json"
    payload = {
        "seed": RANDOM_SEED,
        "period": [BACKTEST_START, BACKTEST_END],
        "market": MARKET,
        "sub_periods": SUB_PERIODS,
        "thresholds": {
            "cagr_loss_pct": THRESHOLD_CAGR_LOSS_PCT,
            "sharpe_drop": THRESHOLD_SHARPE_DROP,
            "overlap_pct": THRESHOLD_OVERLAP_PCT,
            "sub_cagr_loss_pct": THRESHOLD_SUB_CAGR_LOSS_PCT,
        },
        "false_positive": fp_stats,
        "results": {"A": asdict(res_a), "B": asdict(res_b)},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON 저장: {json_path}")

    md = build_report(res_a, res_b, fp_stats)
    md_path = report_dir / "quality_filter_step3_analysis.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"보고서 저장: {md_path}")

    print_summary(res_a, res_b, fp_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
