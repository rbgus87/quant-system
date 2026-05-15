"""scripts/analyze_cost_sensitivity_v4.py — 거래비용 Sensitivity 분석 V4.

시장충격(Square-Root Model)을 고정 편도 비용으로 대체하여 비용 수준을 정확히 제어.
수수료(0.015%)·거래세(0.15%)는 고정; flat_impact만 변화.

시나리오:
  ~5bp  : flat_impact=0.00%  (수수료+세금만, 최적)
  ~15bp : flat_impact=0.05%
  ~20bp : flat_impact=0.10%  (현재 baseline)
  ~30bp : flat_impact=0.20%
  ~50bp : flat_impact=0.40%  (최악)

조건: Step1 + Step3v2 + S4 모두 ON (Preset A), KOSPI 2017-2024, seed=42.

사용:
    python scripts/analyze_cost_sensitivity_v4.py
    python scripts/analyze_cost_sensitivity_v4.py --save-report
"""

from __future__ import annotations

import logging
import random
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Optional
from unittest.mock import patch

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────

RANDOM_SEED: int = 42
BACKTEST_START: str = "2017-01-01"
BACKTEST_END: str = "2024-12-31"
MARKET: str = "KOSPI"
REPORT_DIR = PROJECT_ROOT / "docs" / "reports"
REPORT_PATH = REPORT_DIR / "cost_sensitivity_v4_analysis.md"


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────

@dataclass
class ScenarioConfig:
    label: str
    bp_approx: int       # 근사 편도 비용 레이블
    slippage: float      # settings.trading.slippage 주입값
    flat_impact: float   # Rebalancer.estimate_market_impact 고정 반환값


@dataclass
class ScenarioResult:
    scenario: ScenarioConfig
    cagr: float = 0.0
    sharpe: float = 0.0
    mdd: float = 0.0
    sortino: float = 0.0
    volatility: float = 0.0
    total_return: float = 0.0
    avg_turnover_per_rebal: float = 0.0
    annual_turnover: float = 0.0
    n_rebalances: int = 0
    annual_cost_pct: float = 0.0   # 연간 거래비용 이론값
    error: Optional[str] = None


# ── 시나리오 정의 ─────────────────────────────────────────────────────────────

SCENARIOS: list[ScenarioConfig] = [
    ScenarioConfig("~5bp",          5,   0.0000, 0.0000),
    ScenarioConfig("~15bp",        15,   0.0005, 0.0005),
    ScenarioConfig("~20bp (현재)", 20,   0.0010, 0.0010),
    ScenarioConfig("~30bp",        30,   0.0020, 0.0020),
    ScenarioConfig("~50bp",        50,   0.0040, 0.0040),
]

BASELINE_LABEL = "~20bp (현재)"


# ── 필터 가드 ─────────────────────────────────────────────────────────────────

@contextmanager
def cost_guard(scenario: ScenarioConfig) -> Generator[None, None, None]:
    """settings.trading.slippage 임시 교체 + estimate_market_impact 고정값 패치.

    engine.py 변경 없이 외부에서 비용 수준을 제어하기 위해
    Rebalancer.estimate_market_impact를 scenario.flat_impact를 반환하는
    고정 함수로 교체한다.
    """
    from strategy.rebalancer import Rebalancer
    from strategy.screener import MultiFactorScreener

    original_slippage = settings.trading.slippage
    _impact_val = scenario.flat_impact

    def _fixed_impact(
        self: "Rebalancer",
        order_qty: int,
        avg_daily_volume: float,
        participation_rate: float = 0.1,
    ) -> float:
        return _impact_val

    settings.trading.slippage = scenario.slippage
    MultiFactorScreener._factor_cache.clear()

    try:
        with patch.object(Rebalancer, "estimate_market_impact", _fixed_impact):
            yield
    finally:
        settings.trading.slippage = original_slippage
        MultiFactorScreener._factor_cache.clear()


# ── 시나리오 실행 ─────────────────────────────────────────────────────────────

def run_scenario(scenario: ScenarioConfig) -> ScenarioResult:
    """단일 시나리오 백테스트 실행."""
    res = ScenarioResult(scenario=scenario)

    logger.info("=" * 70)
    logger.info(
        "시나리오 %-15s: flat_impact=%.2f%%  slippage=%.2f%%",
        scenario.label, scenario.flat_impact * 100, scenario.slippage * 100,
    )
    logger.info("=" * 70)

    with cost_guard(scenario):
        try:
            from backtest.engine import MultiFactorBacktest
            from backtest.metrics import PerformanceAnalyzer

            np.random.seed(RANDOM_SEED)
            random.seed(RANDOM_SEED)

            engine = MultiFactorBacktest()
            df = engine.run(BACKTEST_START, BACKTEST_END, market=MARKET)
        except Exception as e:
            logger.error("백테스트 실패: %s", e, exc_info=True)
            res.error = str(e)
            return res

        if df is None or df.empty:
            res.error = "백테스트 결과 없음"
            return res

        pv = df["portfolio_value"]
        rt = (
            df["returns"].dropna()
            if "returns" in df.columns
            else pv.pct_change().dropna()
        )
        analyzer = PerformanceAnalyzer()

        res.cagr = analyzer.calculate_cagr(pv)
        res.mdd = analyzer.calculate_mdd(pv)
        res.sharpe = analyzer.calculate_sharpe(rt)
        res.sortino = analyzer.calculate_sortino(rt)
        res.volatility = analyzer.calculate_volatility(rt)
        res.total_return = float(pv.iloc[-1] / pv.iloc[0] - 1) if len(pv) >= 2 else 0.0

        turnover_log = df.attrs.get("turnover_log", [])
        if turnover_log:
            res.n_rebalances = len(turnover_log)
            res.avg_turnover_per_rebal = (
                sum(t["turnover_rate"] for t in turnover_log) / len(turnover_log)
            )
            n_years = len(df) / 252
            if n_years > 0:
                res.annual_turnover = res.avg_turnover_per_rebal * len(turnover_log) / n_years

            cfg = settings.trading
            round_trip = cfg.commission_rate * 2 + cfg.tax_rate + scenario.flat_impact * 2
            res.annual_cost_pct = res.annual_turnover * round_trip

    return res


# ── KOSPI 벤치마크 ────────────────────────────────────────────────────────────

def _kospi_metrics(closes: pd.Series) -> tuple[float, float, float]:
    """KOSPI 종가 Series로 CAGR·MDD·Sharpe 계산."""
    from backtest.metrics import PerformanceAnalyzer
    analyzer = PerformanceAnalyzer()
    pv = closes / closes.iloc[0] * 10_000_000
    rt = closes.pct_change().dropna()
    return float(analyzer.calculate_cagr(pv)), float(analyzer.calculate_mdd(pv)), float(analyzer.calculate_sharpe(rt))


def fetch_kospi_bah() -> tuple[float, float, float]:
    """KOSPI Buy-and-Hold CAGR, MDD, Sharpe 반환 (2017-2024).

    소스 우선순위:
      1. DB 캐시 (DataStorage — 백테스트 실행으로 이미 적재됨)
      2. Naver Finance 시계열 (데이터 충분성 검증 포함)
      3. FinanceDataReader 'KS11' (LOGOUT 에러 잦음)

    Returns:
        (cagr, mdd, sharpe) 튜플. 실패 시 (0, 0, 0).
    """
    sd = BACKTEST_START.replace("-", "")
    ed = BACKTEST_END.replace("-", "")
    min_expected_days = 1500  # 2017-2024 ≈ 1980 영업일, 75% 이상 확보 필요

    # ── 1. DB 캐시 우선 ──────────────────────────────────────
    try:
        from datetime import datetime as _dt

        from data.storage import DataStorage
        storage = DataStorage()
        s_date = _dt.strptime(sd, "%Y%m%d").date()
        e_date = _dt.strptime(ed, "%Y%m%d").date()
        cached = storage.load_daily_prices("KOSPI", s_date, e_date)
        if not cached.empty and len(cached) >= min_expected_days:
            closes = cached["close"].sort_index().dropna()
            logger.info("KOSPI B&H: DB 캐시 사용 (%d일)", len(closes))
            return _kospi_metrics(closes)
        logger.info("KOSPI B&H: DB 캐시 부족 (%d일) — 외부 조회", len(cached))
    except Exception as e:
        logger.info("KOSPI B&H: DB 캐시 조회 실패 (%s) — 외부 조회", e)

    # ── 2. Naver Finance 시계열 (충분성 검증 포함) ──────────────
    try:
        from data.kospi_index import fetch_kospi_index_series
        df = fetch_kospi_index_series(sd, ed)
        if not df.empty and len(df) >= min_expected_days:
            closes = df["close"].sort_index().dropna()
            logger.info("KOSPI B&H: Naver 사용 (%d일)", len(closes))
            return _kospi_metrics(closes)
        logger.info("KOSPI B&H: Naver 데이터 부족 (%d일)", len(df))
    except Exception as e:
        logger.warning("KOSPI B&H: Naver 조회 실패: %s", e)

    # ── 3. FinanceDataReader 폴백 ─────────────────────────────
    try:
        import FinanceDataReader as fdr
        kospi = fdr.DataReader("KS11", BACKTEST_START, BACKTEST_END)
        closes = kospi["Close"].dropna()
        if len(closes) >= min_expected_days and 500 < float(closes.mean()) < 10000:
            logger.info("KOSPI B&H: FDR 사용 (%d일)", len(closes))
            return _kospi_metrics(closes)
        logger.warning("KOSPI B&H: FDR 데이터 불충분 또는 비정상 (n=%d, 평균=%.1f)", len(closes), float(closes.mean()) if len(closes) else 0)
    except Exception as e:
        logger.warning("KOSPI B&H: FDR 조회 실패: %s", e)

    logger.warning("KOSPI B&H: 모든 소스 실패 — 0.0 반환")
    return 0.0, 0.0, 0.0


# ── 필터 활성 확인 ────────────────────────────────────────────────────────────

def check_filters() -> bool:
    """Step1 + Step3v2 + S4 활성화 여부 확인. 모두 ON이면 True."""
    q = settings.quality
    u = settings.universe
    checks = [
        (getattr(q, "operating_quality_filter_enabled", False), "Step1"),
        (getattr(q, "consecutive_profit_filter_enabled", False), "Step3v2"),
        (getattr(u, "sector_diversification_enabled", False), "S4"),
    ]
    logger.info("필터 상태:")
    for enabled, name in checks:
        logger.info("  %s: %s", name, "✅ ON" if enabled else "❌ OFF")
    return all(c[0] for c in checks)


# ── 텍스트 출력 ───────────────────────────────────────────────────────────────

def _pct(v: float, decimals: int = 2) -> str:
    return f"{v * 100:.{decimals}f}%"


def _fmt_float(v: float, decimals: int = 3) -> str:
    return f"{v:.{decimals}f}"


def print_table(results: list[ScenarioResult]) -> str:
    header = (
        "| 편도 비용 (bp) | 슬리피지 | CAGR | Sharpe | MDD | Sortino |"
    )
    sep = "|---|---|---|---|---|---|"
    rows = [header, sep]
    for r in results:
        if r.error:
            row = (
                f"| {r.scenario.label} | {_pct(r.scenario.slippage)} "
                f"| ERROR | ERROR | ERROR | ERROR |"
            )
        else:
            row = (
                f"| {r.scenario.label} "
                f"| {_pct(r.scenario.slippage)} "
                f"| {_pct(r.cagr)} "
                f"| {_fmt_float(r.sharpe)} "
                f"| {_pct(r.mdd)} "
                f"| {_fmt_float(r.sortino)} |"
            )
        rows.append(row)
    return "\n".join(rows)


def find_breakeven(
    results: list[ScenarioResult], kospi_cagr: float = 0.0
) -> dict[str, str]:
    """선형 보간으로 무너지는 지점 탐색."""
    bps = [r.scenario.bp_approx for r in results if not r.error]
    cagrs = [r.cagr for r in results if not r.error]
    sharpes = [r.sharpe for r in results if not r.error]

    def _interp(xs: list[int], ys: list[float], threshold: float) -> str:
        for i in range(len(ys) - 1):
            y0, y1 = ys[i], ys[i + 1]
            if (y0 >= threshold >= y1) or (y0 <= threshold <= y1):
                x0, x1 = xs[i], xs[i + 1]
                if abs(y1 - y0) < 1e-9:
                    return f"~{x0}bp"
                frac = (threshold - y0) / (y1 - y0)
                bp_val = x0 + frac * (x1 - x0)
                return f"~{bp_val:.0f}bp"
        if all(y >= threshold for y in ys):
            return f"> {xs[-1]}bp (범위 내 미도달)"
        if all(y <= threshold for y in ys):
            return f"< {xs[0]}bp"
        return "알 수 없음"

    return {
        "sharpe_zero": _interp(bps, sharpes, 0.0),
        "cagr_zero": _interp(bps, cagrs, 0.0),
        "cagr_vs_kospi": _interp(bps, cagrs, kospi_cagr) if kospi_cagr > 0 else "N/A",
    }


def print_ascii_chart(results: list[ScenarioResult]) -> str:
    valid = [r for r in results if not r.error]
    if len(valid) < 2:
        return "(차트 그리기 불가 — 유효 결과 부족)"

    cagrs = [r.cagr * 100 for r in valid]
    bps = [r.scenario.bp_approx for r in valid]
    min_c = min(cagrs)
    max_c = max(cagrs)
    range_c = max_c - min_c if max_c > min_c else 1.0
    height = 6
    width = len(bps)

    grid = [[" "] * width for _ in range(height)]
    for col, c in enumerate(cagrs):
        row_idx = int((max_c - c) / range_c * (height - 1) + 0.5)
        row_idx = max(0, min(height - 1, row_idx))
        grid[row_idx][col] = "■"

    y_labels = [
        f"{max_c - i * range_c / (height - 1):5.1f}% |" for i in range(height)
    ]

    lines = ["CAGR vs 거래비용:"]
    for i, row in enumerate(grid):
        lines.append(y_labels[i] + "  ".join(row))
    x_axis = "       +" + "--+" * width + "->"
    lines.append(x_axis)
    x_labels = "        " + "  ".join(f"{b:2d}" for b in bps) + " bp"
    lines.append(x_labels)
    return "\n".join(lines)


def build_turnover_section(baseline: ScenarioResult) -> str:
    if baseline.error or baseline.n_rebalances == 0:
        return "턴오버 데이터 없음"

    freq = settings.portfolio.rebalance_frequency
    months_per_rebal = 3 if freq == "quarterly" else 1
    avg_hold_months = (
        1 / baseline.avg_turnover_per_rebal * months_per_rebal
        if baseline.avg_turnover_per_rebal > 0 else 0
    )
    cfg = settings.trading
    # 현재 설정 기준 편도 왕복 비용
    round_trip = cfg.commission_rate * 2 + cfg.tax_rate + cfg.slippage * 2
    annual_cost = baseline.annual_turnover * round_trip

    lines = [
        f"평균 분기 턴오버: {baseline.avg_turnover_per_rebal:.1%} ({freq})",
        f"연간 환산 턴오버: {baseline.annual_turnover:.0%}",
        f"평균 보유 기간: {avg_hold_months:.1f}개월",
        f"리밸런싱 횟수 ({BACKTEST_START[:4]}~{BACKTEST_END[:4]}): {baseline.n_rebalances}회",
        "",
        "연간 거래비용 부담 (baseline ~20bp 기준):",
        f"  턴오버({baseline.annual_turnover:.0%}) × 왕복비용({round_trip:.4%}) = {annual_cost:.2%}/년",
    ]
    return "\n".join(lines)


def build_verdict(results: list[ScenarioResult], kospi_cagr: float) -> str:
    bp30 = next((r for r in results if r.scenario.bp_approx == 30), None)
    if bp30 is None or bp30.error:
        return "30bp 결과 없음 — 판정 불가"

    verdict_parts = []
    if bp30.sharpe > 0:
        verdict_parts.append("30bp에서 Sharpe > 0 → **실거래 가능** 수준")
    else:
        verdict_parts.append("30bp에서 Sharpe ≤ 0 → **위험** 수준")

    if bp30.cagr > kospi_cagr and kospi_cagr > 0:
        verdict_parts.append(f"30bp에서 KOSPI({_pct(kospi_cagr)}) 대비 초과수익 유지")
    elif kospi_cagr > 0:
        verdict_parts.append(f"30bp에서 KOSPI({_pct(kospi_cagr)}) 대비 초과수익 소멸")

    return "; ".join(verdict_parts) if verdict_parts else "판정 불가"


# ── 보고서 생성 ────────────────────────────────────────────────────────────────

def build_report(
    results: list[ScenarioResult],
    kospi_cagr: float,
    kospi_mdd: float,
    kospi_sharpe: float,
    breakevens: dict[str, str] | None = None,
) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    baseline = next(
        (r for r in results if r.scenario.label == BASELINE_LABEL), results[2]
    )
    if breakevens is None:
        breakevens = find_breakeven(results, kospi_cagr)
    turnover_section = build_turnover_section(baseline)
    verdict = build_verdict(results, kospi_cagr)
    ascii_chart = print_ascii_chart(results)

    # 비용 단계별 Cost Impact (기준 대비 CAGR 차이)
    baseline_cagr = baseline.cagr if not baseline.error else 0.0
    cost_impact_rows = []
    for r in results:
        if r.error:
            cost_impact_rows.append(f"| {r.scenario.label} | {_pct(r.scenario.slippage)} | ERROR | — | — |")
        else:
            delta = (r.cagr - baseline_cagr) * 100
            annual_cost = r.annual_cost_pct
            cost_impact_rows.append(
                f"| {r.scenario.label} | {_pct(r.scenario.slippage)} "
                f"| {_pct(r.cagr)} ({delta:+.2f}%p) | {_pct(r.annual_cost_pct)} "
                f"| {_fmt_float(r.sharpe)} |"
            )

    # 메인 표
    main_table = print_table(results)

    lines = [
        "# 거래비용 Sensitivity 분석 V4",
        "",
        f"생성: {now}  ",
        f"기간: {BACKTEST_START} ~ {BACKTEST_END}  ",
        f"시장: {MARKET} | 프리셋: A (Step1 + Step3v2 + S4) | Seed: {RANDOM_SEED}",
        "",
        "## 분석 방법",
        "",
        "수수료(0.015%)·거래세(0.15%)를 고정하고, 시장충격(Square-Root Model)을",
        "`flat_impact` 고정값으로 대체하여 편도 비용 수준을 정밀하게 제어.",
        "`engine.py` 코드 변경 없이 `Rebalancer.estimate_market_impact`를 외부에서 패치.",
        "",
        "## 거래비용 Sensitivity (2017-2024, KOSPI, 프리셋 A)",
        "",
        main_table,
        "",
        f"**KOSPI Buy-and-Hold 기준**: CAGR={_pct(kospi_cagr)}, MDD={_pct(kospi_mdd)}, Sharpe={_fmt_float(kospi_sharpe)}",
        "",
        "## 비용 단계별 Impact (vs baseline ~20bp)",
        "",
        "| 편도 비용 | 슬리피지 | CAGR (Δ vs baseline) | 연간비용 부담 | Sharpe |",
        "|---|---|---|---|---|",
        *cost_impact_rows,
        "",
        "## 무너지는 지점 (Break-even)",
        "",
        f"- Sharpe < 0 지점: {breakevens['sharpe_zero']}",
        f"- CAGR < 0% 지점: {breakevens['cagr_zero']}",
        f"- CAGR < KOSPI Buy-and-Hold({_pct(kospi_cagr)}) 지점: {breakevens.get('cagr_vs_kospi', 'N/A')}",
        "",
        "## 자동 판정",
        "",
        verdict,
        "",
        "## 턴오버 분석",
        "",
        "```",
        turnover_section,
        "```",
        "",
        "## CAGR 추이 (ASCII 차트)",
        "",
        "```",
        ascii_chart,
        "```",
        "",
        "## 해석",
        "",
        "- 거래비용이 증가할수록 CAGR·Sharpe가 단조 감소.",
        "- 실거래 예상 비용 ~20bp에서 현재 성능 확인.",
        "- 50bp(최악)에서도 Sharpe > 0 유지 시 전략 강건성 높음.",
    ]
    return "\n".join(lines)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="거래비용 민감도 분석 V4")
    parser.add_argument("--save-report", action="store_true", help="보고서 파일 저장")
    parser.add_argument("--quick", action="store_true", help="5bp·50bp만 실행 (빠른 테스트)")
    args = parser.parse_args()

    setup_logging()

    logger.info("거래비용 Sensitivity 분석 V4 시작")
    logger.info("기간: %s ~ %s | 시장: %s | 시드: %d", BACKTEST_START, BACKTEST_END, MARKET, RANDOM_SEED)

    filters_ok = check_filters()
    if not filters_ok:
        logger.warning("Step1/Step3v2/S4 중 일부 비활성 — Preset A 로드 확인 필요")

    scenarios = SCENARIOS if not args.quick else [SCENARIOS[0], SCENARIOS[-1]]

    results: list[ScenarioResult] = []
    for scenario in scenarios:
        res = run_scenario(scenario)
        results.append(res)
        if res.error:
            logger.error("시나리오 %s 실패: %s", scenario.label, res.error)
        else:
            logger.info(
                "완료 %s: CAGR=%.2f%%, Sharpe=%.3f, MDD=%.2f%%",
                scenario.label, res.cagr * 100, res.sharpe, res.mdd * 100,
            )

    logger.info("KOSPI 벤치마크 조회 중...")
    kospi_cagr, kospi_mdd, kospi_sharpe = fetch_kospi_bah()
    logger.info(
        "KOSPI Buy-and-Hold: CAGR=%.2f%%, MDD=%.2f%%, Sharpe=%.3f",
        kospi_cagr * 100, kospi_mdd * 100, kospi_sharpe,
    )

    breakevens = find_breakeven(results, kospi_cagr)
    report = build_report(results, kospi_cagr, kospi_mdd, kospi_sharpe, breakevens)

    if args.save_report or True:  # 기본으로 항상 저장
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report, encoding="utf-8")
        logger.info("보고서 저장: %s", REPORT_PATH)

    # ── 최종 출력 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("거래비용 Sensitivity (2017-2024, KOSPI, 프리셋 A)")
    print("=" * 70)
    print()
    print(print_table(results))
    print()
    print(f"KOSPI Buy-and-Hold: CAGR={_pct(kospi_cagr)}, MDD={_pct(kospi_mdd)}, Sharpe={_fmt_float(kospi_sharpe)}")
    print()

    if not args.quick:
        breakevens = find_breakeven(results, kospi_cagr)
        print("── 무너지는 지점 ────────────────────────────────")
        print(f"  Sharpe < 0 지점:               {breakevens['sharpe_zero']}")
        print(f"  CAGR < 0% 지점:                {breakevens['cagr_zero']}")
        print(f"  CAGR < KOSPI({_pct(kospi_cagr)}) 지점:  {breakevens.get('cagr_vs_kospi', 'N/A')}")
        print()

    baseline = next(
        (r for r in results if r.scenario.label == BASELINE_LABEL),
        results[0] if results else None,
    )
    if baseline and not baseline.error:
        print("── 턴오버 분석 (baseline ~20bp) ──────────────────")
        print(build_turnover_section(baseline))
        print()

    print("── 자동 판정 ────────────────────────────────────")
    print(build_verdict(results, kospi_cagr))
    print()

    if not args.quick:
        print("── CAGR vs 거래비용 ─────────────────────────────")
        print(print_ascii_chart(results))
        print()

    print(f"보고서: {REPORT_PATH}")


if __name__ == "__main__":
    main()
