"""backtest_quality_filter_step1.py — Step 1 본업 품질 필터 채택 검증.

배경:
  docs/POLICY.md "방어 장치 도입 5조건" + docs/case_studies/005620_lesson.md
  의 결론을 잇는 후속 실험.

  005620 유형(일회성 이익으로 F-Score 턱걸이 통과한 가치함정)을 차단하기
  위해 영업이익/매출/영업CF 양수 필터를 신규 도입. POLICY.md 5조건을
  통과해야 채택 권고.

목적:
  A 모드 (Baseline, filter OFF)  vs  B 모드 (Step 1 filter ON)
  의 2017-2024 단일 KOSPI 백테스트 전 구간 + 3개 하위 구간 비교.

판정 기준 (POLICY.md):
  1. CAGR 손실 ≤ -1%p (8년 전체)
  2. 005620 미선정 (2017-06-30 리밸런싱에서 회피)
  3. Sharpe 하락 < 0.10
  4. 종목 겹침률 ≥ 90% (32분기 Jaccard 평균)
  5. 하위 구간 (2017-2020 / 2020-2022 / 2022-2024) 모두 CAGR 손실 -2%p 이내

**이 스크립트는 config.yaml / 실전 설정을 영구 변경하지 않습니다.**
런타임 settings 속성만 변경 후 원복.

사용:
    python scripts/backtest_quality_filter_step1.py
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


# ============================================================
# 상수 (POLICY.md 5조건 + 백테스트 파라미터)
# ============================================================

RANDOM_SEED: int = 42  # 시드 고정 (재현성)

# 백테스트 기간
BACKTEST_START: str = "2017-01-01"
BACKTEST_END: str = "2024-12-31"

# 하위 구간 (POLICY.md 조건 5)
SUB_PERIODS: list[tuple[str, str, str]] = [
    ("2017-2020 소형밸류 최악", "2017-01-01", "2020-12-31"),
    ("2020-2022 코로나+회복", "2020-01-01", "2022-12-31"),
    ("2022-2024 정상", "2022-01-01", "2024-12-31"),
]

# 005620 사례 검증 (docs/case_studies/005620_lesson.md)
TARGET_DELIST_TICKER: str = "005620"
TARGET_REBALANCE_DATE: str = "20170630"

# 시장 (Step 1은 KOSPI만)
MARKET: str = "KOSPI"

# 채택 권고 임계값 (POLICY.md 의사결정 원칙)
THRESHOLD_CAGR_LOSS_PCT: float = -1.0  # %p (8년)
THRESHOLD_SHARPE_DROP: float = 0.10
THRESHOLD_OVERLAP_PCT: float = 90.0
THRESHOLD_SUB_CAGR_LOSS_PCT: float = -2.0  # %p (하위 구간)


# ============================================================
# 결과 데이터 클래스
# ============================================================


@dataclass
class ModeResult:
    """단일 모드 (A 또는 B) 백테스트 결과"""

    name: str  # "A" / "B"
    label: str
    filter_enabled: bool

    # 전 구간 지표
    cagr: float = 0.0
    mdd: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    volatility: float = 0.0
    total_return: float = 0.0

    # 하위 구간별 CAGR
    sub_cagr: dict[str, float] = field(default_factory=dict)

    # 회전율·선정 종목 수
    avg_turnover: float = 0.0
    avg_selection_size: float = 0.0
    n_rebalances: int = 0

    # 선정 종목 (날짜별)
    selections_by_date: dict[str, list[str]] = field(default_factory=dict)

    # 폐지 노출
    delist_exposures: list[dict] = field(default_factory=list)

    # 005620 회피 여부 (2017-06-30 리밸런싱)
    target_005620_in_selection: bool = False

    # portfolio_value 시계열 (DataFrame을 JSON으로 직렬화 가능한 형태로)
    portfolio_value_records: list[dict] = field(default_factory=list)

    error: Optional[str] = None


# ============================================================
# 런타임 토글 (settings 임시 수정 + 원복)
# ============================================================


class FilterGuard:
    """settings.quality.operating_quality_filter_enabled 임시 토글.

    컨텍스트 종료 시 원래 값으로 원복 + 팩터 캐시 클리어.
    """

    def __init__(self) -> None:
        q = settings.quality
        self._backup = {
            "operating_quality_filter_enabled": q.operating_quality_filter_enabled,
            "require_op_income_positive": q.require_op_income_positive,
            "require_revenue_positive": q.require_revenue_positive,
            "require_op_cf_positive_if_available": q.require_op_cf_positive_if_available,
        }

    def apply(self, enabled: bool) -> None:
        settings.quality.operating_quality_filter_enabled = enabled
        # 캐시 키에 신규 4개 필드가 모두 포함되어 있어 자동 분리되지만,
        # 안전을 위해 명시적으로 클리어
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


# ============================================================
# 백테스트 실행
# ============================================================


def collect_selections(rebal_dates: list[pd.Timestamp]) -> dict[str, list[str]]:
    """각 리밸런싱 날짜의 선정 종목 수집 (캐시 활용)."""
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
    """portfolio_value 시계열에서 [start, end] 구간의 CAGR 계산.

    Args:
        pv: portfolio_value 시계열 (index=date)
        start: 'YYYY-MM-DD'
        end: 'YYYY-MM-DD'

    Returns:
        해당 구간 CAGR (소수, 예: 0.123 = 12.3%)
    """
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
    selections_by_date: dict[str, list[str]],
    storage,
) -> list[dict]:
    """선정 종목 중 다음 리밸런싱 전에 failure 카테고리로 폐지된 종목 식별.

    Args:
        selections_by_date: {YYYYMMDD: [tickers]}
        storage: DataStorage 인스턴스

    Returns:
        [{rebalance_date, ticker, name, delist_date}]
    """
    dates_sorted = sorted(selections_by_date.keys())
    if not dates_sorted:
        return []

    start_dt = datetime.strptime(dates_sorted[0], "%Y%m%d").date()
    end_dt = datetime.strptime(dates_sorted[-1], "%Y%m%d").date()
    # 마지막 리밸런싱 이후 1년 정도까지 폐지 범위 확장
    from datetime import timedelta
    end_dt = end_dt + timedelta(days=365)

    try:
        delisted = storage.load_delisted_stocks(
            start_date=start_dt, end_date=end_dt, category="failure",
        )
    except Exception as e:
        logger.warning(f"delisted_stock 조회 실패: {e}")
        return []

    if delisted.empty:
        return []

    # ticker → (delist_date, name)
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
            # 보유 구간 (rebal_d, next_d] 사이에 폐지 발생한 경우만
            if rebal_d < ddate <= next_d:
                exposures.append({
                    "rebalance_date": ds,
                    "ticker": tk,
                    "name": dname,
                    "delist_date": str(ddate),
                })
    return exposures


def jaccard_avg(a: dict[str, list[str]], b: dict[str, list[str]]) -> float:
    """일자별 Jaccard 유사도 평균 (0~1)."""
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


def run_mode(name: str, filter_enabled: bool) -> ModeResult:
    """A/B 모드 단일 실행."""
    label = "Filter OFF (Baseline)" if not filter_enabled else "Filter ON (Step 1)"
    res = ModeResult(name=name, label=label, filter_enabled=filter_enabled)

    logger.info("=" * 70)
    logger.info(f"모드 {name}: {label}  ({BACKTEST_START} ~ {BACKTEST_END})")
    logger.info("=" * 70)

    with FilterGuard() as guard:
        guard.apply(filter_enabled)
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

        # 하위 구간 CAGR
        for label_sub, sub_start, sub_end in SUB_PERIODS:
            res.sub_cagr[label_sub] = compute_sub_period_cagr(pv, sub_start, sub_end)

        # 리밸런싱 날짜 + 선정 종목 수집
        rebal_dates = engine._generate_rebalance_dates(
            BACKTEST_START, BACKTEST_END, MARKET,
        )
        res.n_rebalances = len(rebal_dates)

        logger.info(f"[모드 {name}] 선정 종목 수집 ({len(rebal_dates)}회)")
        sel = collect_selections(rebal_dates)
        sizes = [len(v) for v in sel.values() if v]
        res.avg_selection_size = sum(sizes) / len(sizes) if sizes else 0.0
        res.selections_by_date = sel

        # 005620 검증
        target_sel = sel.get(TARGET_REBALANCE_DATE, [])
        res.target_005620_in_selection = TARGET_DELIST_TICKER in target_sel
        logger.info(
            f"[모드 {name}] 2017-06-30 선정 종목 수={len(target_sel)}, "
            f"005620 포함={res.target_005620_in_selection}"
        )

        # 폐지 노출
        res.delist_exposures = detect_delist_exposures(sel, engine.krx.storage)

        # 회전율 (간이 추정: 인접 분기 간 비공통/공통 평균)
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

        # portfolio_value 시계열 보존 (JSON 직렬화)
        pv_records = []
        for dt, val in pv.items():
            pv_records.append({
                "date": str(dt),
                "portfolio_value": float(val) if pd.notna(val) else None,
            })
        res.portfolio_value_records = pv_records

    return res


# ============================================================
# 보고서 (Markdown)
# ============================================================


def fmt_pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


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


def evaluate_adoption(res_a: ModeResult, res_b: ModeResult, overlap_pct: float) -> tuple[bool, list[tuple[str, bool, str]]]:
    """POLICY.md 5조건 평가.

    Returns:
        (전체 통과 여부, [(조건 이름, 통과 여부, 상세)])
    """
    delta_cagr_pp = (res_b.cagr - res_a.cagr) * 100.0
    cond1 = delta_cagr_pp >= THRESHOLD_CAGR_LOSS_PCT
    detail1 = f"ΔCAGR={delta_cagr_pp:+.2f}%p (기준: ≥ {THRESHOLD_CAGR_LOSS_PCT}%p)"

    cond2 = not res_b.target_005620_in_selection
    detail2 = (
        "005620 미선정 ✅" if cond2
        else "005620 여전히 2017-06-30 선정 ❌"
    )

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
        if cond5
        else f"미달: {'; '.join(sub_failures)}"
    )

    conditions = [
        ("1. CAGR 손실 ≤ -1%p (8년)", cond1, detail1),
        ("2. 005620 회피", cond2, detail2),
        ("3. Sharpe 하락 < 0.10", cond3, detail3),
        ("4. 종목 겹침률 ≥ 90%", cond4, detail4),
        ("5. 하위 구간 안정성", cond5, detail5),
    ]
    return all(c[1] for c in conditions), conditions


def build_report(res_a: ModeResult, res_b: ModeResult) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overlap_jacc = jaccard_avg(res_a.selections_by_date, res_b.selections_by_date)
    overlap_pct = overlap_jacc * 100.0

    adopted, conditions = evaluate_adoption(res_a, res_b, overlap_pct)

    lines: list[str] = []
    lines.append("# Step 1 본업 품질 필터 채택 검증 보고서")
    lines.append("")
    lines.append(f"> 생성: {now}  ")
    lines.append(
        f"> 기간: {BACKTEST_START} ~ {BACKTEST_END} (8년) / 시장: {MARKET} / "
        f"프리셋: A / 분기 리밸런싱"
    )
    lines.append(
        "> 스크립트: `scripts/backtest_quality_filter_step1.py` "
        f"(시드={RANDOM_SEED})"
    )
    lines.append("")
    lines.append(
        "**분석 전용** — `config.yaml` / 실전 운용 설정은 변경되지 않습니다. "
        "런타임 `settings.quality.operating_quality_filter_enabled` 만 토글."
    )
    lines.append("")

    # 0. 결론 (POLICY.md 5조건)
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
            "> POLICY.md 5조건 모두 통과. 단, 본 채택 권고는 사용자 최종 확인 후 "
            "별도 커밋에서 `config.yaml`의 `operating_quality_filter_enabled: true` "
            "변경을 진행한다."
        )
    else:
        failed = [c[0] for c in conditions if not c[1]]
        lines.append(
            f"> POLICY.md 5조건 중 미달: {', '.join(failed)}. "
            "Step 1 필터는 현재 형태로 채택하지 않는다."
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

    # 2. 회전율·선정 종목 수·겹침률
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
    lines.append(
        f"- 종목 겹침률 (32분기 Jaccard 평균): **{overlap_pct:.1f}%**"
    )
    lines.append("")

    # 3. 하위 구간 안정성
    lines.append("## 3. 하위 구간 안정성")
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

    # 4. 005620 회피 검증
    lines.append("## 4. 005620 사례 회피 검증")
    lines.append("")
    lines.append(
        "docs/case_studies/005620_lesson.md — 2017-06-30 리밸런싱에서 F-Score 4점 "
        "턱걸이 통과한 가치함정 사례."
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

    # 5. 폐지 노출
    lines.append("## 5. 폐지(failure) 노출 비교")
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
    if res_a.delist_exposures or res_b.delist_exposures:
        lines.append("### 상세 노출 내역")
        lines.append("")
        all_exp = []
        for r in [res_a, res_b]:
            for e in r.delist_exposures:
                all_exp.append({
                    "모드": r.name,
                    "리밸런싱": e["rebalance_date"],
                    "티커": e["ticker"],
                    "종목명": e.get("name") or "",
                    "폐지일": e["delist_date"],
                })
        lines.append(df_to_md(pd.DataFrame(all_exp)))
        lines.append("")

    # 6. 한계점
    lines.append("## 6. 한계점")
    lines.append("")
    lines.append(
        "- 본 백테스트는 KOSPI 단독·프리셋 A 고정. KOSDAQ/타 프리셋 일반화는 별도 검증 필요.  \n"
        "- PCR 데이터는 DART `fnlttMultiAcnt` 응답에 CF 항목 부재로 대부분 NaN. "
        "  Step 1에서는 require_op_cf_positive_if_available=True 정책상 NaN은 통과 처리 → 영업CF 단계 효과 제한적.  \n"
        "- 5조건은 docs/POLICY.md 의사결정 원칙 기반. 실전 운용 도입 전 Walk-Forward 재검증 권고.  \n"
        "- 단일 시드(42) 백테스트. 시드 불변 영역(분기 리밸런싱) 위주이나, "
        "  변동성 필터의 마지막 동률 처리 등 미세 변동 가능."
    )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> 본 분석은 _분석 전용_입니다. POLICY.md 5조건을 통과한 경우에도 "
        "`config.yaml` 변경은 사용자 최종 확인 후 별도 커밋으로 진행합니다."
    )

    return "\n".join(lines)


# ============================================================
# 콘솔 요약 출력 (응답 끝에서 사용자에게 보여주기 위함)
# ============================================================


def print_summary_table(res_a: ModeResult, res_b: ModeResult) -> None:
    # Windows cp949 콘솔 호환을 위해 ASCII 위주로 출력
    overlap_pct = jaccard_avg(res_a.selections_by_date, res_b.selections_by_date) * 100.0

    a_005620 = "INCLUDED" if res_a.target_005620_in_selection else "AVOIDED"
    b_005620 = "INCLUDED" if res_b.target_005620_in_selection else "AVOIDED"

    print()
    print("=" * 72)
    print("Step 1 Quality Filter -- Summary")
    print("=" * 72)
    print(f"{'Metric':<28} {'A (Baseline)':>18} {'B (Filter ON)':>18} {'Delta':>6}")
    print("-" * 72)
    print(
        f"{'CAGR':<28} "
        f"{res_a.cagr * 100:>17.2f}% "
        f"{res_b.cagr * 100:>17.2f}% "
        f"{(res_b.cagr - res_a.cagr) * 100:>+6.2f}"
    )
    print(
        f"{'MDD':<28} "
        f"{res_a.mdd * 100:>17.2f}% "
        f"{res_b.mdd * 100:>17.2f}% "
        f"{(res_b.mdd - res_a.mdd) * 100:>+6.2f}"
    )
    print(
        f"{'Sharpe':<28} "
        f"{res_a.sharpe:>18.3f} "
        f"{res_b.sharpe:>18.3f} "
        f"{res_b.sharpe - res_a.sharpe:>+6.3f}"
    )
    print(f"{'Overlap (Jaccard avg)':<28} {'':>18} {'':>18} {overlap_pct:>+5.1f}%")
    print(
        f"{'005620 at 2017-06-30':<28} "
        f"{a_005620:>18} "
        f"{b_005620:>18}"
    )
    print(
        f"{'Delist (failure) exposures':<28} "
        f"{len(res_a.delist_exposures):>18} "
        f"{len(res_b.delist_exposures):>18}"
    )
    print("=" * 72)


# ============================================================
# 진입점
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 1 본업 품질 필터 채택 검증")
    parser.add_argument("--report-dir", default="docs/reports")
    args = parser.parse_args()

    setup_logging()

    # 재현성 시드 고정
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    # 모드 A (Baseline, filter OFF)
    res_a = run_mode("A", filter_enabled=False)
    # 모드 B (Step 1 filter ON)
    res_b = run_mode("B", filter_enabled=True)

    # JSON 저장
    report_dir = PROJECT_ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "quality_filter_step1_results.json"
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
        "results": {
            "A": asdict(res_a),
            "B": asdict(res_b),
        },
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON 저장: {json_path}")

    # Markdown 보고서
    md = build_report(res_a, res_b)
    md_path = report_dir / "quality_filter_step1_analysis.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"보고서 저장: {md_path}")

    # 콘솔 요약
    print_summary_table(res_a, res_b)

    return 0


if __name__ == "__main__":
    sys.exit(main())
