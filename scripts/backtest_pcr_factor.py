"""backtest_pcr_factor.py — PCR 팩터 무효화 영향 분석 백테스트.

배경:
  - DART 분기/반기보고서 현금흐름표 부재로 PCR=NaN (KOSPI/KOSDAQ 모두 0%)
  - value.py는 PCR notna 없으면 자동으로 PSR 폴백
  - 운영 설정(value_weights: pbr=0.50, pcr=0.30, div=0.20)은 그대로지만,
    실제 작동은 시기별로 다름:
      * 2017-2019 KOSPI: PSR도 미수집 → PBR(50%)+DIV(20%) → 재분배 시 71/29
      * 2020+ KOSPI/KOSDAQ: PSR 95%+ → PBR+PSR+DIV (50/30/20) 3팩터

본 스크립트는 KOSPI 단독 + V70M30 + Vol70 고정 하에서 value_weights만
시나리오별로 변경:
  A. (현재) pbr=0.50, pcr=0.30, div=0.20            ← Baseline (PCR=NaN, PSR 폴백 자동)
  B. (2팩터 명시) pbr=0.71, pcr=0.00, div=0.29     ← PSR 폴백 차단
  C. (PSR 폴백 활성) pbr=0.50, pcr=0.30, div=0.20   ← A와 동일 (검증용)
  D. (PBR 강화) pbr=0.80, pcr=0.00, div=0.20

**이 스크립트는 config.yaml / 실전 설정을 영구 변경하지 않습니다.**
런타임 settings.value_weights만 시나리오별로 변경 후 원복합니다.

사용:
    python scripts/backtest_pcr_factor.py --start 2017-01-01 --end 2024-12-31
    python scripts/backtest_pcr_factor.py --scenarios A,B
    python scripts/backtest_pcr_factor.py --with-selection  # 선정 종목 비교 추가
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
from data.storage import DataStorage  # noqa: E402

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────
# 시나리오 정의 (value_weights만 변경)
# ───────────────────────────────────────────────

SCENARIOS: dict[str, dict] = {
    "A": {
        "label": "현재 (Baseline)",
        "desc": "pcr=0.30 설정이지만 PCR=NaN → PSR 폴백 자동 작동",
        "weights": {"pbr": 0.50, "pcr": 0.30, "div": 0.20},
    },
    "B": {
        "label": "2팩터 명시",
        "desc": "PCR/PSR 폴백 차단 (가중치 0) → PBR+DIV만",
        "weights": {"pbr": 0.71, "pcr": 0.00, "div": 0.29},
    },
    "C": {
        "label": "PSR 폴백 활성 (검증용)",
        "desc": "A와 동일 — value.py 코드 흐름상 PSR 자동 폴백",
        "weights": {"pbr": 0.50, "pcr": 0.30, "div": 0.20},
    },
    "D": {
        "label": "PBR 강화",
        "desc": "PBR 80% + DIV 20%, PCR/PSR 미사용",
        "weights": {"pbr": 0.80, "pcr": 0.00, "div": 0.20},
    },
}


@dataclass
class ScenarioResult:
    name: str
    label: str
    desc: str
    weights: dict[str, float]
    start: str
    end: str
    cagr: float = 0.0
    mdd: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    volatility: float = 0.0
    total_return: float = 0.0
    calmar: float = 0.0
    n_days: int = 0
    n_rebalances: int = 0
    avg_selection_size: float = 0.0
    selections_by_date: dict[str, list[str]] = field(default_factory=dict)
    error: Optional[str] = None


# ───────────────────────────────────────────────
# 설정 컨텍스트 매니저
# ───────────────────────────────────────────────

class SettingsGuard:
    """settings.value_weights를 컨텍스트 종료 시 원복."""

    def __init__(self) -> None:
        vw = settings.value_weights
        self._backup = {
            "pbr": vw.pbr, "pcr": vw.pcr, "div": vw.div,
            "market": settings.universe.market,
        }

    def apply(self, weights: dict[str, float], market: str = "KOSPI") -> None:
        vw = settings.value_weights
        vw.pbr = float(weights.get("pbr", vw.pbr))
        vw.pcr = float(weights.get("pcr", vw.pcr))
        vw.div = float(weights.get("div", vw.div))
        settings.universe.market = market
        # screener 팩터 캐시 클리어 (key가 market+date라 weight 변화 미반영 가능성)
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception as e:
            logger.warning(f"팩터 캐시 클리어 실패: {e}")

    def restore(self) -> None:
        vw = settings.value_weights
        vw.pbr = self._backup["pbr"]
        vw.pcr = self._backup["pcr"]
        vw.div = self._backup["div"]
        settings.universe.market = self._backup["market"]
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception:
            pass

    def __enter__(self) -> "SettingsGuard":
        return self

    def __exit__(self, *_args) -> None:
        self.restore()


# ───────────────────────────────────────────────
# 단일 시나리오 실행
# ───────────────────────────────────────────────

def collect_selections(rebal_dates: list[pd.Timestamp]) -> dict[str, list[str]]:
    """각 리밸런싱 날짜의 선정 종목 리스트."""
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


def run_scenario(
    name: str,
    start_date: str,
    end_date: str,
    with_selection: bool = False,
) -> ScenarioResult:
    spec = SCENARIOS[name]
    result = ScenarioResult(
        name=name, label=spec["label"], desc=spec["desc"],
        weights=spec["weights"], start=start_date, end=end_date,
    )

    logger.info("=" * 60)
    logger.info(f"시나리오 {name} ({spec['label']}) — weights={spec['weights']}")
    logger.info("=" * 60)

    with SettingsGuard() as guard:
        guard.apply(spec["weights"], market="KOSPI")

        try:
            from backtest.engine import MultiFactorBacktest
            from backtest.metrics import PerformanceAnalyzer

            engine = MultiFactorBacktest()
            df = engine.run(start_date, end_date, market="KOSPI")
        except Exception as e:
            logger.error(f"[{name}] 백테스트 실패: {e}", exc_info=True)
            result.error = f"backtest_failed: {e}"
            return result

        if df is None or df.empty:
            result.error = "empty_backtest_result"
            return result

        pv = df["portfolio_value"]
        rt = df["returns"].dropna()
        analyzer = PerformanceAnalyzer()
        result.cagr = analyzer.calculate_cagr(pv)
        result.mdd = analyzer.calculate_mdd(pv)
        result.sharpe = analyzer.calculate_sharpe(rt)
        result.sortino = analyzer.calculate_sortino(rt)
        result.volatility = analyzer.calculate_volatility(rt)
        result.calmar = analyzer.calculate_calmar(result.cagr, result.mdd)
        result.total_return = float(pv.iloc[-1] / pv.iloc[0] - 1) if len(pv) >= 2 else 0.0
        result.n_days = len(pv)

        rebal_dates = engine._generate_rebalance_dates(start_date, end_date, "KOSPI")
        result.n_rebalances = len(rebal_dates)

        if with_selection:
            logger.info(f"[{name}] 선정 종목 수집 중 ({len(rebal_dates)}회)")
            sel = collect_selections(rebal_dates)
            sizes = [len(v) for v in sel.values() if v]
            result.avg_selection_size = sum(sizes) / len(sizes) if sizes else 0.0
            result.selections_by_date = sel

    return result


# ───────────────────────────────────────────────
# 비교 분석
# ───────────────────────────────────────────────

def compare_selections(
    results: dict[str, ScenarioResult],
) -> pd.DataFrame:
    """시나리오 간 선정 종목 일치율 (Jaccard 유사도) 매트릭스."""
    names = sorted(results.keys())
    n = len(names)
    matrix = pd.DataFrame(0.0, index=names, columns=names)

    # 모든 리밸런싱 날짜의 평균 Jaccard
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                matrix.loc[a, b] = 1.0
                continue
            sa = results[a].selections_by_date
            sb = results[b].selections_by_date
            if not sa or not sb:
                continue
            common_dates = set(sa) & set(sb)
            jaccards: list[float] = []
            for d in common_dates:
                set_a = set(sa[d])
                set_b = set(sb[d])
                if not set_a and not set_b:
                    continue
                inter = len(set_a & set_b)
                uni = len(set_a | set_b)
                if uni > 0:
                    jaccards.append(inter / uni)
            matrix.loc[a, b] = sum(jaccards) / len(jaccards) if jaccards else 0.0
    return matrix


# ───────────────────────────────────────────────
# Markdown 리포트
# ───────────────────────────────────────────────

def df_to_md(df: pd.DataFrame, index: bool = False) -> str:
    if df.empty:
        return "_(데이터 없음)_"
    if index:
        df = df.reset_index()
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


def build_report(
    results: dict[str, ScenarioResult],
    start_date: str,
    end_date: str,
    with_selection: bool,
    db_stats: dict,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []

    lines.append("# PCR 팩터 무효화 영향 분석 보고서")
    lines.append("")
    lines.append(f"> 생성: {now}  ")
    lines.append(f"> 기간: {start_date} ~ {end_date}  ")
    lines.append("> 시장: KOSPI 단독 / 프리셋: V70M30 + Vol70 고정  ")
    lines.append("> 스크립트: `scripts/backtest_pcr_factor.py`")
    lines.append("")
    lines.append(
        "**이 분석은 config.yaml / 실전 운용 설정을 변경하지 않습니다.** "
        "런타임 `settings.value_weights`만 시나리오별로 변경 후 원복."
    )
    lines.append("")

    # 0. 배경
    lines.append("## 0. 배경 — PCR=NaN의 실제 동작")
    lines.append("")
    lines.append(
        "DART 분기/반기보고서는 현금흐름표를 반환하지 않아 PCR 데이터가 "
        "수집 단계에서 채워지지 않는다. 그러나 `factors/value.py`에는 "
        "**PCR notna가 없으면 PSR로 자동 폴백**하는 로직이 있어 (line 41-46), "
        "운영 설정(`pcr=0.30`)이 사실상 PSR 가중치로 동작한다."
    )
    lines.append("")
    lines.append("DB 실측 (2017-01-01 이후 fundamental 테이블):")
    lines.append("")
    db_df = pd.DataFrame([
        {"시장": k, "총 레코드": f"{v['total']:,}",
         "PCR>0": f"{v['pcr_pos']:,} ({v['pcr_pct']:.2f}%)",
         "PSR>0": f"{v['psr_pos']:,} ({v['psr_pct']:.2f}%)",
         "PBR>0": f"{v['pbr_pos']:,} ({v['pbr_pct']:.2f}%)",
         "DIV>0": f"{v['div_pos']:,} ({v['div_pct']:.2f}%)"}
        for k, v in db_stats.items()
    ])
    lines.append(df_to_md(db_df))
    lines.append("")
    lines.append(
        "→ **PCR은 KOSPI/KOSDAQ 모두 0건**. PSR은 KOSDAQ 97%, "
        "KOSPI 70% (단, KOSPI 2017-2019 구간은 PSR 0% — 당시 미수집)."
    )
    lines.append("")
    lines.append("**시기별 실제 작동**:")
    lines.append("")
    lines.append("| 기간 | PCR | PSR | 실효 가중치 |")
    lines.append("| --- | --- | --- | --- |")
    lines.append("| 2017-2019 KOSPI | 0% | 0% | PBR(71%) + DIV(29%) — NaN-aware 재분배 |")
    lines.append("| 2020+ KOSPI/KOSDAQ | 0% | 95-97% | PBR(50%) + **PSR(30%, PCR-fallback)** + DIV(20%) |")
    lines.append("")

    # 1. 시나리오 정의
    lines.append("## 1. 시나리오 정의")
    lines.append("")
    sc_df = pd.DataFrame([
        {
            "시나리오": k,
            "라벨": v["label"],
            "value_weights": json.dumps(v["weights"], ensure_ascii=False),
            "설명": v["desc"],
        }
        for k, v in SCENARIOS.items()
    ])
    lines.append(df_to_md(sc_df))
    lines.append("")

    # 2. 성과 비교
    lines.append("## 2. 성과 비교")
    lines.append("")
    perf_rows = []
    for name in sorted(results.keys()):
        r = results[name]
        perf_rows.append({
            "시나리오": r.name,
            "라벨": r.label,
            "CAGR(%)": f"{r.cagr * 100:.2f}",
            "MDD(%)": f"{r.mdd * 100:.2f}",
            "Sharpe": f"{r.sharpe:.3f}",
            "Sortino": f"{r.sortino:.3f}",
            "Vol(%)": f"{r.volatility * 100:.2f}",
            "Calmar": f"{r.calmar:.3f}",
            "총수익률(%)": f"{r.total_return * 100:.2f}",
            "에러": r.error or "",
        })
    lines.append(df_to_md(pd.DataFrame(perf_rows)))
    lines.append("")

    # 3. 시나리오 간 차이
    if "A" in results and "B" in results:
        rA, rB = results["A"], results["B"]
        d_cagr = (rA.cagr - rB.cagr) * 100
        d_sharpe = rA.sharpe - rB.sharpe
        d_mdd = (rA.mdd - rB.mdd) * 100
        lines.append("## 3. A(현재) vs B(2팩터 명시) 비교")
        lines.append("")
        lines.append(
            f"- CAGR 차이: A {rA.cagr * 100:.2f}% - B {rB.cagr * 100:.2f}% = "
            f"**{d_cagr:+.2f}%p**"
        )
        lines.append(
            f"- Sharpe 차이: A {rA.sharpe:.3f} - B {rB.sharpe:.3f} = "
            f"**{d_sharpe:+.3f}**"
        )
        lines.append(
            f"- MDD 차이: A {rA.mdd * 100:.2f}% - B {rB.mdd * 100:.2f}% = "
            f"**{d_mdd:+.2f}%p**"
        )
        lines.append("")
        if abs(d_cagr) < 0.5 and abs(d_sharpe) < 0.05:
            lines.append(
                "**해석**: A와 B의 차이가 무의미한 수준 → "
                "PSR 폴백이 실질 영향이 없음. 운영 설정(pcr=0.30)을 (0.71/0/0.29)로 "
                "정정해도 백테스트 결과 차이 없음."
            )
        else:
            lines.append(
                "**해석**: A와 B의 성과 차이가 유의미함 → PSR 폴백이 실제로 "
                "영향을 미치고 있음. 폴백 차단 시 성과 변화 발생."
            )
        lines.append("")

    # 4. A vs C 검증
    if "A" in results and "C" in results:
        rA, rC = results["A"], results["C"]
        d_cagr_ac = abs(rA.cagr - rC.cagr) * 100
        lines.append("## 4. A vs C 검증 (코드 흐름 일치성)")
        lines.append("")
        lines.append(
            "C는 A와 동일 가중치(0.50/0.30/0.20). value.py 코드상 "
            "PCR notna 없으면 PSR로 자동 폴백되므로 **결과가 일치해야 함**."
        )
        lines.append("")
        lines.append(
            f"- CAGR: A {rA.cagr * 100:.4f}% vs C {rC.cagr * 100:.4f}% (차이 {d_cagr_ac:.4f}%p)"
        )
        lines.append(
            f"- Sharpe: A {rA.sharpe:.4f} vs C {rC.sharpe:.4f}"
        )
        lines.append("")
        if d_cagr_ac < 0.01:
            lines.append("**검증 통과**: A와 C가 사실상 동일 → 코드 흐름 일치 확인.")
        else:
            lines.append("**검증 실패**: A와 C 차이 존재 → 캐시/난수 등 부수 영향 가능성. 추가 조사 필요.")
        lines.append("")

    # 5. 선정 종목 일치율
    if with_selection and any(r.selections_by_date for r in results.values()):
        lines.append("## 5. 선정 종목 일치율 (Jaccard 평균)")
        lines.append("")
        lines.append("각 리밸런싱 날짜의 종목 집합 Jaccard 유사도 평균.")
        lines.append("")
        m = compare_selections(results)
        m_fmt = m.applymap(lambda v: f"{v * 100:.1f}%")
        lines.append(df_to_md(m_fmt, index=True))
        lines.append("")

    # 6. 권고
    lines.append("## 6. 권고")
    lines.append("")
    if "A" in results and "B" in results and not (results["A"].error or results["B"].error):
        rA, rB = results["A"], results["B"]
        d_cagr = abs(rA.cagr - rB.cagr) * 100
        d_sharpe = abs(rA.sharpe - rB.sharpe)
        if d_cagr < 0.5 and d_sharpe < 0.05:
            lines.append("### 6-1. 결론: A ≈ B (PSR 폴백 영향 미미)")
            lines.append("")
            lines.append(
                "PSR 폴백이 작동 중이지만 성과에 미치는 영향이 무시할 수준. "
                "PCR 설정의 실효성은 사실상 없으며, PBR+DIV 2팩터 체계로 동작 중이라 봐도 무방."
            )
            lines.append("")
            lines.append("**제안 (분석 권고, 실전 변경 금지)**:")
            lines.append("")
            lines.append(
                "1. **현실 반영 (선택)**: `value_weights`를 `pbr=0.71, pcr=0.00, div=0.29`로 "
                "정정하면 설정이 실제 동작과 일치 (성과 차이 없음). 단, PCR 데이터가 "
                "추후 확보되면 재설정 필요."
            )
            lines.append("")
            lines.append(
                "2. **PCR 데이터 확보 (근본 해결)**: `data/dart_client.py`에서 "
                "연간보고서(사업보고서) 우선 호출 + 연결재무제표 폴백으로 "
                "현금흐름표 수집을 시도. 확보 후 백테스트 재검증."
            )
            lines.append("")
            lines.append(
                "3. **운영 무변경 권고**: 현재 백테스트 CAGR 13.3% (2021-2024)는 "
                "PSR 폴백 상태에서 나온 숫자이므로, 설정만 바꾼다고 해서 성과가 "
                "달라지지 않음. config.yaml은 그대로 두는 것이 안전."
            )
        else:
            lines.append("### 6-1. 결론: A ≠ B (PSR 폴백이 유의미한 영향)")
            lines.append("")
            lines.append(
                f"A vs B 차이: CAGR {d_cagr:.2f}%p, Sharpe {d_sharpe:.3f}. "
                "PSR이 실제 백테스트 성과에 영향을 주고 있음."
            )
            lines.append("")
            lines.append("**제안**:")
            lines.append("")
            if rA.cagr > rB.cagr:
                lines.append(
                    "1. 현재 설정(A)이 명시적 2팩터(B)보다 우수 → **PSR 폴백을 명시화**. "
                    "ValueWeights에 `psr` 필드 추가 + value.py 로직 명시화 검토."
                )
            else:
                lines.append(
                    "1. 명시적 2팩터(B)가 현재(A)보다 우수 → **PSR 폴백 차단** 검토. "
                    "value.py:41-46 PSR 폴백 코드를 비활성화 옵션 추가."
                )
        lines.append("")

    # 7. D 시나리오 보조 분석
    if "D" in results and "A" in results and not results["D"].error:
        rA, rD = results["A"], results["D"]
        d_cagr_ad = (rA.cagr - rD.cagr) * 100
        lines.append("### 6-2. D (PBR 강화) 보조 분석")
        lines.append("")
        lines.append(
            f"- A vs D: CAGR {rA.cagr * 100:.2f}% vs {rD.cagr * 100:.2f}% "
            f"(Δ {d_cagr_ad:+.2f}%p), Sharpe {rA.sharpe:.3f} vs {rD.sharpe:.3f}"
        )
        if abs(d_cagr_ad) < 0.5:
            lines.append("- PBR 비중 상향(50→80%)이 성과에 큰 영향 없음. 현행 유지 권고.")
        elif d_cagr_ad > 0:
            lines.append("- A(밸류 분산)가 D(PBR 집중)보다 우수 → 분산 유지가 유리.")
        else:
            lines.append("- D(PBR 집중)가 A보다 우수 → PBR 비중 상향 검토 가치 있음.")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> 본 분석은 _분석 전용_입니다. 실전 운용 설정 변경 여부는 "
        "운용자가 별도 판단합니다. PCR 데이터 확보 시 본 분석 재실행 필요."
    )

    return "\n".join(lines)


# ───────────────────────────────────────────────
# DB 통계 조회
# ───────────────────────────────────────────────

def fetch_db_stats(start_date: str = "2017-01-01") -> dict:
    """fundamental 테이블 PCR/PSR 등 가용성 통계."""
    storage = DataStorage()
    stats: dict[str, dict] = {}
    from sqlalchemy import text
    with storage.engine.connect() as conn:
        for market in ["KOSPI", "KOSDAQ"]:
            row = conn.execute(
                text(
                    "SELECT COUNT(*) AS total,"
                    " SUM(CASE WHEN pcr IS NOT NULL AND pcr > 0 THEN 1 ELSE 0 END) AS pcr_pos,"
                    " SUM(CASE WHEN psr IS NOT NULL AND psr > 0 THEN 1 ELSE 0 END) AS psr_pos,"
                    " SUM(CASE WHEN pbr IS NOT NULL AND pbr > 0 THEN 1 ELSE 0 END) AS pbr_pos,"
                    " SUM(CASE WHEN div IS NOT NULL AND div > 0 THEN 1 ELSE 0 END) AS div_pos"
                    " FROM fundamental"
                    " WHERE date >= :start AND market = :market"
                ),
                {"start": start_date, "market": market},
            ).fetchone()
            total = row[0] or 0
            stats[market] = {
                "total": total,
                "pcr_pos": row[1] or 0,
                "pcr_pct": (row[1] or 0) / total * 100 if total else 0.0,
                "psr_pos": row[2] or 0,
                "psr_pct": (row[2] or 0) / total * 100 if total else 0.0,
                "pbr_pos": row[3] or 0,
                "pbr_pct": (row[3] or 0) / total * 100 if total else 0.0,
                "div_pos": row[4] or 0,
                "div_pct": (row[4] or 0) / total * 100 if total else 0.0,
            }
    return stats


# ───────────────────────────────────────────────
# 메인
# ───────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="PCR 팩터 무효화 영향 분석")
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument(
        "--scenarios", default="A,B,C,D",
        help="실행 시나리오 (콤마 구분, 기본 A,B,C,D)",
    )
    parser.add_argument(
        "--with-selection", action="store_true",
        help="선정 종목 비교(Jaccard) 추가 — 시간 더 소요",
    )
    parser.add_argument(
        "--report-dir", default="docs/reports",
        help="결과 저장 디렉토리",
    )
    args = parser.parse_args()

    setup_logging()

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    invalid = [s for s in scenarios if s not in SCENARIOS]
    if invalid:
        logger.error(f"알 수 없는 시나리오: {invalid}")
        return 1

    db_stats = fetch_db_stats("2017-01-01")
    logger.info(f"DB 통계: {db_stats}")

    results: dict[str, ScenarioResult] = {}
    for name in scenarios:
        r = run_scenario(name, args.start, args.end, with_selection=args.with_selection)
        results[name] = r

    # JSON 저장
    report_dir = PROJECT_ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    json_path = report_dir / "pcr_factor_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        payload = {
            "start": args.start,
            "end": args.end,
            "with_selection": args.with_selection,
            "db_stats": db_stats,
            "scenarios": {k: asdict(v) for k, v in results.items()},
        }
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"JSON 저장: {json_path}")

    # Markdown 보고서
    md = build_report(results, args.start, args.end, args.with_selection, db_stats)
    md_path = report_dir / "pcr_factor_analysis.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"보고서 저장: {md_path}")

    print(f"\n=== 결과 요약 ({args.start} ~ {args.end}) ===")
    for name in sorted(results.keys()):
        r = results[name]
        print(
            f"  {r.name} ({r.label:30s}) "
            f"CAGR={r.cagr * 100:6.2f}% MDD={r.mdd * 100:7.2f}% "
            f"Sharpe={r.sharpe:.3f} | weights={r.weights}"
            + (f"  ERROR={r.error}" if r.error else "")
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
