"""backtest_debt_ratio_s2.py — S2 부채비율 상한 필터 임계값 비교.

baseline: Step 1 + Step 3 변형 (2) 활성화 상태 (현재 운용)
실험: 위에 더해 S2 부채비율 필터 추가, 임계값 200/300/400% 비교.

A: baseline           (debt_ratio_filter OFF)
B: max_debt_ratio=200 (보수적)
C: max_debt_ratio=300 (중간)
D: max_debt_ratio=400 (느슨)

판정 기준 (POLICY.md 5조건, #2는 OR 구조):
  1. CAGR 손실 ≤ -1%p
  2. (a) 폐지/자본잠식 회피 OR (b) Alpha 동시 개선
  3. Sharpe 하락 < 0.10
  4. 종목 겹침률 ≥ 90% (32분기 평균)
  5. 하위 구간 안정성 (3구간 ΔCAGR ≥ -2%p)

3가지 모두 미달 시: 부채비율 필터 자체 폐기 (S4로 진행).
1개 이상 통과 시: 가장 좋은 임계값으로 채택 권고.

**분석 전용** — config.yaml 변경 없음.

사용:
    python scripts/backtest_debt_ratio_s2.py
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
    debt_filter_enabled: bool
    max_debt_ratio: float
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
    error: Optional[str] = None


class FilterGuard:
    """S2 settings 토글 + 컨텍스트 종료 시 원복.

    Step 1 / Step 3 변형(2)는 운용 활성 상태이므로 그대로 둠.
    """

    def __init__(self) -> None:
        q = settings.quality
        self._backup = {
            "debt_ratio_filter_enabled": q.debt_ratio_filter_enabled,
            "max_debt_ratio": q.max_debt_ratio,
            "exclude_capital_impairment": q.exclude_capital_impairment,
        }

    def apply(self, enabled: bool, max_debt: float = 200.0) -> None:
        settings.quality.debt_ratio_filter_enabled = enabled
        settings.quality.max_debt_ratio = max_debt
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
                    "rebalance_date": ds, "ticker": tk,
                    "name": dname, "delist_date": str(ddate),
                })
    return exposures


def jaccard_avg(a: dict[str, list[str]], b: dict[str, list[str]]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    vals = []
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
        except Exception:
            continue

    if not fp_returns:
        return {"count": 0, "mean": 0.0, "median": 0.0}
    return {
        "count": sample_count,
        "mean": float(np.mean(fp_returns)),
        "median": float(np.median(fp_returns)),
    }


def run_mode(name: str, enabled: bool, max_debt: float) -> ModeResult:
    label = (
        "S2 OFF (baseline)" if not enabled
        else f"S2 ON, max_debt_ratio={max_debt:.0f}%"
    )
    res = ModeResult(
        name=name, label=label,
        debt_filter_enabled=enabled, max_debt_ratio=max_debt,
    )

    logger.info("=" * 70)
    logger.info(f"모드 {name}: {label}  ({BACKTEST_START} ~ {BACKTEST_END})")
    logger.info("=" * 70)

    with FilterGuard() as guard:
        guard.apply(enabled=enabled, max_debt=max_debt)
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

        turnovers = []
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

    return res


def fmt_pct(x: float) -> str:
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
    detail1 = f"ΔCAGR={delta_cagr_pp:+.2f}%p"

    # 폐지/자본잠식 회피: A 노출 - B 노출 > 0 또는 005620 회피
    cond_2a = (
        len(res_b.delist_exposures) < len(res_a.delist_exposures)
        or (res_a.target_005620_in_selection and not res_b.target_005620_in_selection)
    )
    cond_2b = res_b.cagr > res_a.cagr and res_b.sharpe > res_a.sharpe
    cond2 = cond_2a or cond_2b
    paths = []
    if cond_2a:
        paths.append("(a) 폐지 회피 ✅")
    else:
        paths.append("(a) 폐지 회피 ❌")
    if cond_2b:
        paths.append("(b) Alpha 동시 개선 ✅")
    else:
        paths.append("(b) Alpha 개선 ❌")
    detail2 = " | ".join(paths)

    sharpe_drop = res_a.sharpe - res_b.sharpe
    cond3 = sharpe_drop < THRESHOLD_SHARPE_DROP
    detail3 = f"ΔSharpe={-sharpe_drop:+.3f}"

    cond4 = overlap_pct >= THRESHOLD_OVERLAP_PCT
    detail4 = f"겹침률={overlap_pct:.1f}%"

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
        ("1. CAGR 손실 ≤ -1%p", cond1, detail1),
        ("2. (a) 폐지 회피 OR (b) Alpha 개선", cond2, detail2),
        ("3. Sharpe 하락 < 0.10", cond3, detail3),
        ("4. 종목 겹침률 ≥ 90%", cond4, detail4),
        ("5. 하위 구간 안정성", cond5, detail5),
    ]
    return all(c[1] for c in conditions), conditions


def build_report(results: list[ModeResult], fp_stats: dict[str, dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    res_a = results[0]

    # 가장 좋은 임계값 결정 (CAGR + Sharpe 기준으로 단순 점수)
    candidates = [(r, evaluate_adoption(
        res_a, r,
        jaccard_avg(res_a.selections_by_date, r.selections_by_date) * 100.0,
    )) for r in results[1:]]
    passing = [(r, cond) for r, (ok, cond) in candidates if ok]
    if passing:
        # 통과 후보 중 CAGR 최대
        best = max(passing, key=lambda x: x[0].cagr)
        best_res, best_cond = best
        verdict = f"✅ 채택 권고: {best_res.name} (max_debt={best_res.max_debt_ratio:.0f}%)"
    else:
        best_res = None
        best_cond = None
        verdict = "❌ 미채택 (3 임계값 모두 5조건 미달) — S4로 진행 권고"

    lines: list[str] = []
    lines.append("# S2: 부채비율 상한 필터 분석")
    lines.append("")
    lines.append(f"> 생성: {now}  ")
    lines.append(
        f"> 기간: {BACKTEST_START} ~ {BACKTEST_END} (8년) / 시장: {MARKET} / "
        f"프리셋: A / 시드={RANDOM_SEED}"
    )
    lines.append(
        "> baseline (A 모드): Step 1 + Step 3 변형(2) 활성 / S2 OFF"
    )
    lines.append("")
    lines.append(
        "**분석 전용** — config.yaml은 변경되지 않습니다."
    )
    lines.append("")

    # 0. 결론
    lines.append("## 0. 결론")
    lines.append("")
    lines.append(f"**판정**: {verdict}")
    lines.append("")
    if best_res is not None and best_cond is not None:
        lines.append("### 최적 임계값 5조건 평가")
        lines.append("")
        lines.append("| 조건 | 통과 | 상세 |")
        lines.append("| --- | --- | --- |")
        for name, ok, detail in best_cond:
            flag = "✅" if ok else "❌"
            lines.append(f"| {name} | {flag} | {detail} |")
        lines.append("")

    # 1. 4모드 비교
    lines.append("## 1. 4모드 핵심 비교")
    lines.append("")
    rows = []
    for r in results:
        rows.append({
            "모드": r.name,
            "라벨": r.label,
            "CAGR": fmt_pct(r.cagr),
            "MDD": fmt_pct(r.mdd),
            "Sharpe": f"{r.sharpe:.3f}",
            "Sortino": f"{r.sortino:.3f}",
            "Calmar": f"{r.calmar:.3f}",
            "Vol": fmt_pct(r.volatility),
        })
    lines.append(df_to_md(pd.DataFrame(rows)))
    lines.append("")

    # 2. 운영 지표
    lines.append("## 2. 회전율·선정·겹침률·폐지 노출")
    lines.append("")
    op_rows = []
    for r in results:
        overlap_pct = jaccard_avg(
            res_a.selections_by_date, r.selections_by_date,
        ) * 100.0
        op_rows.append({
            "모드": r.name,
            "평균 회전율": fmt_pct(r.avg_turnover),
            "평균 선정": f"{r.avg_selection_size:.1f}",
            "겹침률 vs A": f"{overlap_pct:.1f}%",
            "폐지 노출": str(len(r.delist_exposures)),
            "005620 포함": "❌" if r.target_005620_in_selection else "✅",
        })
    lines.append(df_to_md(pd.DataFrame(op_rows)))
    lines.append("")

    # 3. 하위 구간
    lines.append("## 3. 하위 구간 안정성 (ΔCAGR vs A)")
    lines.append("")
    sub_rows = []
    for label_sub, sub_start, sub_end in SUB_PERIODS:
        row = {"구간": label_sub, "기간": f"{sub_start} ~ {sub_end}"}
        a = res_a.sub_cagr.get(label_sub, 0.0)
        row["CAGR A"] = fmt_pct(a)
        for r in results[1:]:
            b = r.sub_cagr.get(label_sub, 0.0)
            row[f"ΔCAGR {r.name}(%p)"] = f"{(b - a) * 100:+.2f}"
        sub_rows.append(row)
    lines.append(df_to_md(pd.DataFrame(sub_rows)))
    lines.append("")

    # 4. 위양성 분석
    lines.append("## 4. 위양성 분석 (B/C/D 추가 제거 종목의 다음 분기 평균 수익률)")
    lines.append("")
    fp_rows = []
    for r in results[1:]:
        s = fp_stats.get(r.name, {})
        fp_rows.append({
            "모드": r.name,
            "표본 수": str(s.get("count", 0)),
            "평균 수익률": f"{s.get('mean', 0.0) * 100:+.2f}%",
            "중간값 수익률": f"{s.get('median', 0.0) * 100:+.2f}%",
        })
    lines.append(df_to_md(pd.DataFrame(fp_rows)))
    lines.append("")
    lines.append(
        "음수면 위양성 차단(정상), 양수면 alpha 손실."
    )
    lines.append("")

    # 5. 005620 검증
    lines.append("## 5. 005620 사례 회피 검증")
    lines.append("")
    t_rows = []
    for r in results:
        sel = r.selections_by_date.get(TARGET_REBALANCE_DATE, [])
        t_rows.append({
            "모드": r.name,
            "선정 종목 수": str(len(sel)),
            "005620 포함": "❌ 포함됨" if r.target_005620_in_selection else "✅ 미선정",
        })
    lines.append(df_to_md(pd.DataFrame(t_rows)))
    lines.append("")

    # 6. 한계
    lines.append("## 6. 한계")
    lines.append("")
    lines.append(
        "- 부채비율 = 부채총계/자본총계 (연결재무제표 우선). 자본잠식 종목은 "
        "  debt_ratio NaN으로 별도 자본잠식 플래그(`exclude_capital_impairment`)에서 차단.\n"
        "- DART의 fnlttMultiAcnt는 PIT 안전 (`_determine_report_period` lag 적용).\n"
        "- 본 분석은 KOSPI 단독·프리셋 A 고정. 실전 활성화 전 Walk-Forward 재검증 권장."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "> 분석 전용. POLICY.md 5조건 통과 시에도 config.yaml 변경은 "
        "사용자 최종 확인 후 별도 커밋."
    )
    return "\n".join(lines)


def print_summary(results: list[ModeResult], fp_stats: dict[str, dict]) -> None:
    res_a = results[0]
    print()
    print("=" * 88)
    print("S2: debt_ratio filter -- Summary")
    print("=" * 88)
    print(
        f"{'Mode':<18} {'CAGR':>10} {'MDD':>10} {'Sharpe':>10} "
        f"{'Overlap':>10} {'Delist':>8} {'005620':>10}"
    )
    print("-" * 88)
    for r in results:
        overlap = (
            "-" if r is res_a
            else f"{jaccard_avg(res_a.selections_by_date, r.selections_by_date) * 100:.1f}%"
        )
        target = "INCL" if r.target_005620_in_selection else "AVOID"
        print(
            f"{r.name + ' ' + r.label:<18} "
            f"{r.cagr * 100:>9.2f}% "
            f"{r.mdd * 100:>9.2f}% "
            f"{r.sharpe:>10.3f} "
            f"{overlap:>10} "
            f"{len(r.delist_exposures):>8} "
            f"{target:>10}"
        )
    print()
    print("False-positive analysis (next-quarter holding returns):")
    for r in results[1:]:
        s = fp_stats.get(r.name, {})
        print(
            f"  {r.name}: n={s.get('count', 0)}, "
            f"mean={s.get('mean', 0) * 100:+.2f}%, "
            f"median={s.get('median', 0) * 100:+.2f}%"
        )
    print("=" * 88)


def main() -> int:
    parser = argparse.ArgumentParser(description="S2 부채비율 상한 검증")
    parser.add_argument("--report-dir", default="docs/reports")
    parser.add_argument(
        "--skip-fp-analysis", action="store_true",
        help="위양성 분석 스킵",
    )
    args = parser.parse_args()

    setup_logging()
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    # 4 모드 실행
    results: list[ModeResult] = []
    results.append(run_mode("A", enabled=False, max_debt=0.0))
    results.append(run_mode("B", enabled=True, max_debt=200.0))
    results.append(run_mode("C", enabled=True, max_debt=300.0))
    results.append(run_mode("D", enabled=True, max_debt=400.0))

    # 위양성 분석 (B/C/D 각각 vs A)
    res_a = results[0]
    fp_stats: dict[str, dict] = {}
    if not args.skip_fp_analysis and not res_a.error:
        for r in results[1:]:
            if r.error:
                fp_stats[r.name] = {"count": 0, "mean": 0.0, "median": 0.0}
                continue
            logger.info(f"위양성 분석 — {r.name} 추가 제거 종목 1분기 수익률")
            fp_stats[r.name] = compute_false_positive_returns(
                res_a.selections_by_date, r.selections_by_date,
            )
    else:
        for r in results[1:]:
            fp_stats[r.name] = {"count": 0, "mean": 0.0, "median": 0.0}

    # 저장
    report_dir = PROJECT_ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "debt_ratio_s2_results.json"
    payload = {
        "seed": RANDOM_SEED,
        "period": [BACKTEST_START, BACKTEST_END],
        "market": MARKET,
        "thresholds": {
            "cagr_loss_pct": THRESHOLD_CAGR_LOSS_PCT,
            "sharpe_drop": THRESHOLD_SHARPE_DROP,
            "overlap_pct": THRESHOLD_OVERLAP_PCT,
            "sub_cagr_loss_pct": THRESHOLD_SUB_CAGR_LOSS_PCT,
        },
        "false_positive": fp_stats,
        "results": {r.name: asdict(r) for r in results},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON 저장: {json_path}")

    md = build_report(results, fp_stats)
    md_path = report_dir / "debt_ratio_s2_analysis.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"보고서 저장: {md_path}")

    print_summary(results, fp_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
