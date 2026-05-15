"""scripts/backtest_lowvol_weights_s7.py — S7 Low-vol 가중치 조합 백테스트 (Part 2).

5가지 가중치 조합을 각각 전체 기간(2017-2024) 백테스트하여 비교.
CAGR, Sharpe, MDD, Sortino, DSR 및 POLICY 5조건 평가.
결과를 docs/reports/lowvol_factor_s7_analysis.md 에 저장.

사용:
    python scripts/backtest_lowvol_weights_s7.py
"""

from __future__ import annotations

import logging
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kurtosis as scipy_kurt
from scipy.stats import norm
from scipy.stats import skew as scipy_skew

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)

BACKTEST_START = "2017-01-01"
BACKTEST_END   = "2024-12-31"
MARKET         = "KOSPI"
RANDOM_SEED    = 42
RF_ANNUAL      = 0.03

REPORT_DIR  = PROJECT_ROOT / "docs" / "reports"
REPORT_PATH = REPORT_DIR / "lowvol_factor_s7_analysis.md"

# V3 IC 결과 (하드코딩 — 레포트 연계용)
V3_VALUE_IR   = 0.572
V3_MOM_IR     = -0.057
V3_QUALITY_IR = -0.221

# 비교 대상 5가지 가중치 조합
WEIGHT_CONFIGS: dict[str, dict[str, float]] = {
    "A_baseline":  {"value": 0.70, "momentum": 0.30, "quality": 0.00, "low_vol": 0.00},
    "B_V70L30":    {"value": 0.70, "momentum": 0.00, "quality": 0.00, "low_vol": 0.30},
    "C_V60L40":    {"value": 0.60, "momentum": 0.00, "quality": 0.00, "low_vol": 0.40},
    "D_V50M20L30": {"value": 0.50, "momentum": 0.20, "quality": 0.00, "low_vol": 0.30},
    "E_V100":      {"value": 1.00, "momentum": 0.00, "quality": 0.00, "low_vol": 0.00},
}


# ── DSR 함수 (V2에서 복사, 자급자족) ──────────────────────────────────────────

_EULER_GAMMA = 0.5772156649015328


def _se_sr(sr_m: float, sk: float, ek: float) -> float:
    inner = 1.0 + 0.5 * sr_m**2 - sk * sr_m + (ek / 4.0) * sr_m**2
    return math.sqrt(max(inner, 1e-9))


def _psr(sr_m: float, sr_ref_m: float, T: int, sk: float, ek: float) -> float:
    se = _se_sr(sr_m, sk, ek)
    z  = (sr_m - sr_ref_m) * math.sqrt(T - 1) / se
    return float(norm.cdf(z))


def _expected_max_sr(N: int, sr_std_m: float) -> float:
    if N <= 1:
        return 0.0
    z1 = float(norm.ppf(1.0 - 1.0 / N))
    z2 = float(norm.ppf(1.0 - 1.0 / (N * math.e)))
    return sr_std_m * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def _dsr(sr_m: float, T: int, sk: float, ek: float, N: int = 20, sr_std_a: float = 0.025) -> float:
    sr_std_m = sr_std_a / math.sqrt(12.0)
    e_max    = _expected_max_sr(N, sr_std_m)
    return _psr(sr_m, e_max, T, sk, ek)


# ── 성과 지표 계산 ────────────────────────────────────────────────────────────

def compute_metrics(df: pd.DataFrame) -> dict:
    pv = df["portfolio_value"].copy()
    pv.index = pd.to_datetime(pv.index)

    total_ret  = float(pv.iloc[-1] / pv.iloc[0] - 1)
    n_years    = (pv.index[-1] - pv.index[0]).days / 365.25
    cagr       = float((1 + total_ret) ** (1 / n_years) - 1)

    roll_max   = pv.cummax()
    drawdown   = pv / roll_max - 1
    mdd        = float(drawdown.min())

    try:
        monthly_pv  = pv.resample("ME").last()
    except Exception:
        monthly_pv  = pv.resample("M").last()
    monthly_ret = monthly_pv.pct_change().dropna()

    rf_m   = (1 + RF_ANNUAL) ** (1 / 12) - 1
    excess = monthly_ret - rf_m
    vol_m  = float(monthly_ret.std(ddof=1))
    sr_m   = float(excess.mean() / vol_m) if vol_m > 0 else float("nan")
    sr_a   = sr_m * math.sqrt(12.0) if not np.isnan(sr_m) else float("nan")

    # Sortino
    down = monthly_ret[monthly_ret < rf_m] - rf_m
    sortino_m = float(excess.mean() / down.std(ddof=1)) if len(down) > 1 else float("nan")
    sortino_a = sortino_m * math.sqrt(12.0) if not np.isnan(sortino_m) else float("nan")

    sk = float(scipy_skew(monthly_ret))
    ek = float(scipy_kurt(monthly_ret, fisher=True))
    T  = len(monthly_ret)
    dsr = _dsr(sr_m, T, sk, ek) if T > 1 and not np.isnan(sr_m) else float("nan")

    return {
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sr_a,
        "sortino": sortino_a,
        "dsr": dsr,
        "vol_annual": vol_m * math.sqrt(12),
        "T": T,
        "skew": sk,
        "excess_kurt": ek,
    }


# ── 백테스트 실행 ─────────────────────────────────────────────────────────────

def run_one(name: str, weights: dict[str, float]) -> dict:
    from backtest.engine import MultiFactorBacktest
    from strategy.screener import MultiFactorScreener

    logger.info("== %s: v=%.2f m=%.2f lv=%.2f ==",
                name, weights["value"], weights["momentum"], weights["low_vol"])

    # 설정 패치
    settings.factor_weights.value    = weights["value"]
    settings.factor_weights.momentum = weights["momentum"]
    settings.factor_weights.quality  = weights["quality"]
    settings.factor_weights.low_vol  = weights["low_vol"]
    MultiFactorScreener._factor_cache.clear()

    np.random.seed(RANDOM_SEED)
    random.seed(RANDOM_SEED)

    engine = MultiFactorBacktest()
    df = engine.run(BACKTEST_START, BACKTEST_END, market=MARKET)

    if df is None or df.empty:
        logger.error("[%s] 백테스트 결과 없음", name)
        return {"name": name, "error": True}

    m = compute_metrics(df)
    m["name"] = name
    m["weights"] = weights
    return m


# ── 보고서 생성 ────────────────────────────────────────────────────────────────

def _policy_check(baseline: dict, candidate: dict) -> list[str]:
    """POLICY 5조건 평가 — 조건별 통과/실패 반환."""
    checks = []

    # 1. CAGR 손실 ≤ -1%p
    delta_cagr = candidate["cagr"] - baseline["cagr"]
    checks.append(f"① CAGR 변화 {delta_cagr*100:+.2f}%p → {'✅' if delta_cagr >= -0.01 else '❌ (>-1%p 초과)'}")

    # 2. Alpha 동시 개선 (Sharpe 기준)
    delta_sharpe = candidate["sharpe"] - baseline["sharpe"]
    checks.append(f"② Sharpe 변화 {delta_sharpe:+.3f} → {'✅' if delta_sharpe >= 0 else '⚠️ (하락)'}")

    # 3. Sharpe 하락 < 0.10
    checks.append(f"③ Sharpe 하락 {-delta_sharpe:.3f} → {'✅' if delta_sharpe > -0.10 else '❌ (0.10 초과)'}")

    # 4. DSR 개선
    delta_dsr = candidate["dsr"] - baseline["dsr"]
    checks.append(f"④ DSR 변화 {delta_dsr:+.3f} (목표 > 0.729) → {'✅' if candidate['dsr'] > 0.729 else '⚠️'}")

    # 5. MDD 변화
    delta_mdd = candidate["mdd"] - baseline["mdd"]
    checks.append(f"⑤ MDD 변화 {delta_mdd*100:+.2f}%p → {'✅' if delta_mdd >= -0.03 else '⚠️ (악화)'}")

    return checks


def build_full_report(
    all_results: list[dict],
    lowvol_ir: float,
    best_lookback: int,
) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def f(v: float, d: int = 3) -> str:
        return f"{v:.{d}f}" if not np.isnan(v) else "—"
    def fp(v: float, d: int = 2) -> str:
        return f"{v*100:.{d}f}%" if not np.isnan(v) else "—"

    baseline = next((r for r in all_results if r["name"] == "A_baseline"), {})

    lines = [
        "# S7: Low-Volatility 팩터 분석",
        "",
        f"생성: {now}  ",
        f"기간: {BACKTEST_START} ~ {BACKTEST_END} | 시장: {MARKET}",
        "",
        "## 1. IC/IR 결과 (Part 1 요약)",
        "",
        "| 팩터 | IR | 판정 |",
        "|------|---|------|",
        f"| Value 합산 (V3) | +{V3_VALUE_IR:.3f} | ★★★ |",
        f"| Momentum 합산 (V3) | {V3_MOM_IR:+.3f} | ✗ |",
        f"| Quality 합산 (V3) | {V3_QUALITY_IR:+.3f} | ✗ |",
        f"| **Low-vol (lookback={best_lookback}일)** | **{lowvol_ir:+.3f}** | **{'★★★' if lowvol_ir > 0.10 else '★★' if lowvol_ir > 0.05 else '★' if lowvol_ir > 0.02 else '✗'}** |",
        "",
        "## 2. 가중치 조합 비교 (5가지)",
        "",
        "| 모드 | 가중치 | CAGR | Sharpe | MDD | Sortino | DSR |",
        "|------|--------|------|--------|-----|---------|-----|",
    ]

    for r in all_results:
        if r.get("error"):
            lines.append(f"| {r['name']} | — | ERROR | — | — | — | — |")
            continue
        w = r["weights"]
        wstr = f"V{w['value']:.0%} M{w['momentum']:.0%} LV{w['low_vol']:.0%}"
        lines.append(
            f"| **{r['name']}** | {wstr} | {fp(r['cagr'])} | {f(r['sharpe'])} | "
            f"{fp(r['mdd'])} | {f(r['sortino'])} | {f(r['dsr'])} |"
        )

    # POLICY 평가 (A_baseline 대비 최고 Sharpe 후보)
    non_baseline = [r for r in all_results if r["name"] != "A_baseline" and not r.get("error")]
    if non_baseline and not baseline.get("error"):
        best = max(non_baseline, key=lambda x: x.get("sharpe", float("-inf")))
        lines += [
            "",
            f"## 3. POLICY 5조건 평가 — 최고 Sharpe 후보: {best['name']}",
            "",
        ]
        checks = _policy_check(baseline, best)
        for c in checks:
            lines.append(f"- {c}")

        # 채택 권고
        pass_count = sum(1 for c in checks if "✅" in c)
        lines += [
            "",
            "## 4. 채택 권고",
            "",
            f"- 최고 Sharpe 후보: **{best['name']}** (Sharpe={f(best['sharpe'])}, DSR={f(best['dsr'])})",
            f"- POLICY 조건 {pass_count}/5 통과",
        ]
        if pass_count >= 4 and best["sharpe"] > baseline.get("sharpe", 0):
            lines.append(f"- **✅ {best['name']} 채택 권장** — Preset 업데이트 검토")
        elif pass_count >= 3:
            lines.append(f"- **⚠️ {best['name']} 조건부 채택** — 추가 검증 필요")
        else:
            lines.append("- **❌ 현행 Preset A 유지** — Low-vol 조합 이득 불충분")

    return "\n".join(lines) + "\n"


def print_summary(all_results: list[dict]) -> None:
    print()
    print("=" * 70)
    print("S7 Low-vol 가중치 조합 비교 (2017-2024)")
    print("=" * 70)
    print(f"{'이름':<16} {'CAGR':>7} {'Sharpe':>8} {'MDD':>7} {'DSR':>7}")
    print("-" * 55)
    for r in all_results:
        if r.get("error"):
            print(f"{r['name']:<16} ERROR")
            continue
        print(
            f"{r['name']:<16} {r['cagr']*100:>6.2f}% {r['sharpe']:>8.3f} "
            f"{r['mdd']*100:>6.2f}% {r['dsr']:>7.3f}"
        )
    print()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    logger.info("S7 Low-vol 가중치 백테스트 시작")

    # Part 1 IC 결과 (analyze_lowvol_ic.py 실행 결과)
    LOWVOL_IR_BEST = 3.8903   # 60d lookback IR
    BEST_LOOKBACK  = 60

    # 5개 조합 순차 백테스트
    all_results: list[dict] = []
    for name, weights in WEIGHT_CONFIGS.items():
        result = run_one(name, weights)
        all_results.append(result)
        logger.info(
            "[%s] CAGR=%.2f%% Sharpe=%.3f MDD=%.2f%% DSR=%.3f",
            name,
            result.get("cagr", float("nan")) * 100,
            result.get("sharpe", float("nan")),
            result.get("mdd", float("nan")) * 100,
            result.get("dsr", float("nan")),
        )

    print_summary(all_results)

    report = build_full_report(all_results, LOWVOL_IR_BEST, BEST_LOOKBACK)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("보고서 저장: %s", REPORT_PATH)
    print(f"  보고서: {REPORT_PATH}")


if __name__ == "__main__":
    main()
