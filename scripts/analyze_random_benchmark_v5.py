"""scripts/analyze_random_benchmark_v5.py — 랜덤 벤치마크 100회 시뮬레이션 V5.

동일 유니버스(Step1+Step3v2+S4 통과 종목)에서 무작위 동일가중 20종목 포트폴리오를
100회 시뮬레이션하여 전략(v2.1.0)의 CAGR/Sharpe가 랜덤 분포 대비 어디에 위치하는지
측정 → "팩터 스코어링이 무작위보다 얼마나 나은가" (순수 alpha 측정).

핵심 설계:
- 유니버스: screener(n_stocks=9999, S4 비활성)로 Step1+Step3v2+F-Score 통과 전종목
- 기간 수익률: 분기 리밸런싱일 종가 기준 (T 기준 close-to-close)
- 거래비용: 왕복 0.38% 고정 차감 (분기별 100% 턴오버 가정)
  ※ 랜덤에 불리 — 전략은 47.4% 턴오버로 0.18%/분기 부담 (주의 명시)
- 시드: np.random.RandomState(i) for i in range(100)

사용:
    python scripts/analyze_random_benchmark_v5.py
    python scripts/analyze_random_benchmark_v5.py --save-report
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────

BACKTEST_START = "2017-01-01"
BACKTEST_END   = "2024-12-31"
MARKET         = "KOSPI"
N_SIMS         = 100
N_SELECT       = 20
ROUND_TRIP_COST = 0.0038   # ~20bp × 왕복 (수수료×2 + 세금 + 슬리피지×2)

# V4 baseline (~20bp) 전략 성과
STRATEGY_CAGR   = 0.0646
STRATEGY_SHARPE = 0.245
STRATEGY_MDD    = -0.5159

REPORT_DIR  = PROJECT_ROOT / "docs" / "reports"
REPORT_PATH = REPORT_DIR / "random_benchmark_v5_analysis.md"


# ── 유니버스 가드 (n_stocks=9999 + S4 비활성) ─────────────────────────────────

@contextmanager
def universe_guard() -> Generator[None, None, None]:
    """n_stocks 임시 확장 + S4 비활성으로 전체 필터 통과 종목 수집."""
    from strategy.screener import MultiFactorScreener

    backup_n    = settings.portfolio.n_stocks
    backup_s4   = settings.universe.sector_diversification_enabled

    settings.portfolio.n_stocks = 9999
    settings.universe.sector_diversification_enabled = False
    MultiFactorScreener._factor_cache.clear()

    try:
        yield
    finally:
        settings.portfolio.n_stocks = backup_n
        settings.universe.sector_diversification_enabled = backup_s4
        MultiFactorScreener._factor_cache.clear()


# ── 분기 리밸런싱 날짜 생성 ────────────────────────────────────────────────────

def get_quarterly_rebal_dates(start: str, end: str) -> list[pd.Timestamp]:
    from config.calendar import get_krx_month_end_sessions
    all_months = get_krx_month_end_sessions(start, end)
    return [d for d in all_months if d.month in (3, 6, 9, 12)]


# ── 유니버스 수집 (32 분기, 각 ~15초) ─────────────────────────────────────────

def collect_universes(
    rebal_dates: list[pd.Timestamp],
) -> dict[pd.Timestamp, list[str]]:
    """각 분기 리밸런싱일의 필터 통과 전체 유니버스 수집.

    Step1 + Step3v2 + F-Score 통과 종목. S4 제외(유니버스 정의, 포트 구성 아님).
    """
    from strategy.screener import MultiFactorScreener

    screener = MultiFactorScreener()
    universes: dict[pd.Timestamp, list[str]] = {}

    logger.info("유니버스 수집 시작 (%d 분기)", len(rebal_dates))
    with universe_guard():
        for i, rd in enumerate(rebal_dates):
            date_str = rd.strftime("%Y%m%d")
            try:
                df = screener.screen(date_str, market=MARKET, n_stocks=9999)
                tickers = df.index.tolist() if df is not None and not df.empty else []
            except Exception as e:
                logger.warning("[%s] 스크리너 실패: %s", date_str, e)
                tickers = []
            universes[rd] = tickers
            logger.info(
                "[%d/%d] %s: 유니버스 %d종목",
                i + 1, len(rebal_dates), date_str, len(tickers),
            )

    return universes


# ── 기간 수익률 사전계산 ─────────────────────────────────────────────────────

def precompute_period_returns(
    rebal_dates: list[pd.Timestamp],
    universes: dict[pd.Timestamp, list[str]],
    storage,
) -> dict[tuple[pd.Timestamp, pd.Timestamp], pd.Series]:
    """각 (start_rd, end_rd) 기간의 종목별 close-to-close 수익률 계산.

    분기 종가 기준 (T 종가 → T+1 분기 종가).
    데이터 없는 종목은 자동 제외.
    """
    period_returns: dict[tuple, pd.Series] = {}
    n_periods = len(rebal_dates) - 1

    logger.info("기간 수익률 사전계산 (%d 기간)", n_periods)

    for i in range(n_periods):
        rd_start = rebal_dates[i]
        rd_end   = rebal_dates[i + 1]
        tickers  = universes.get(rd_start, [])

        if not tickers:
            logger.warning("[%d] %s: 유니버스 없음", i, rd_start.strftime("%Y%m%d"))
            continue

        sd = rd_start.date() if hasattr(rd_start, "date") else rd_start
        ed = rd_end.date()   if hasattr(rd_end,   "date") else rd_end

        try:
            df_start = storage.load_daily_prices_bulk(tickers, sd, sd)
            df_end   = storage.load_daily_prices_bulk(tickers, ed, ed)
        except Exception as e:
            logger.warning("[%d] bulk load 실패: %s", i, e)
            continue

        if df_start.empty or df_end.empty:
            logger.warning("[%d] 가격 데이터 없음 (%s → %s)", i, sd, ed)
            continue

        def _price_series(df: pd.DataFrame, col: str = "close") -> pd.Series:
            if "ticker" in df.columns:
                return df.groupby("ticker")[col].first()
            return pd.Series(dtype=float)

        p_start = _price_series(df_start)
        p_end   = _price_series(df_end)

        common  = p_start.index.intersection(p_end.index)
        if common.empty:
            logger.warning("[%d] 공통 가격 없음", i)
            continue

        p_s = p_start[common]
        p_e = p_end[common]
        valid = (p_s > 0) & (p_e > 0)
        rets  = (p_e[valid] / p_s[valid] - 1).dropna()

        period_returns[(rd_start, rd_end)] = rets
        logger.debug(
            "[%d] %s→%s: 유효 %d/%d종목 (평균수익 %.2f%%)",
            i, sd, ed, len(rets), len(tickers), rets.mean() * 100,
        )

    return period_returns


# ── 시뮬레이션 ─────────────────────────────────────────────────────────────────

@dataclass
class SimResult:
    sim_id: int
    cagr:   float
    sharpe: float
    mdd:    float
    n_valid_periods: int = 0


def run_simulations(
    rebal_dates: list[pd.Timestamp],
    universes:   dict[pd.Timestamp, list[str]],
    period_returns: dict[tuple, pd.Series],
) -> list[SimResult]:
    """100회 랜덤 시뮬레이션.

    각 시뮬: 분기마다 유니버스에서 N_SELECT 종목 무작위 선택 →
    동일가중 수익률 계산 → 거래비용 0.38% 차감.
    """
    pairs = list(zip(rebal_dates[:-1], rebal_dates[1:]))
    n_years = (
        rebal_dates[-1] - rebal_dates[0]
    ).days / 365.25

    results: list[SimResult] = []

    for sim_id in range(N_SIMS):
        rng = np.random.RandomState(sim_id)
        period_rets: list[float] = []
        n_valid = 0

        for rd_start, rd_end in pairs:
            ret_s = period_returns.get((rd_start, rd_end))
            if ret_s is None or ret_s.empty:
                period_rets.append(-ROUND_TRIP_COST)  # 데이터 없음 → 비용만
                continue

            avail = ret_s.index.tolist()
            n_pick = min(N_SELECT, len(avail))
            if n_pick < 5:
                period_rets.append(-ROUND_TRIP_COST)
                continue

            chosen   = rng.choice(avail, size=n_pick, replace=False)
            gross    = float(ret_s[chosen].mean())
            net      = gross - ROUND_TRIP_COST
            period_rets.append(net)
            n_valid += 1

        # 포트폴리오 가치 계산
        pv = pd.Series(
            np.concatenate([[1.0], np.cumprod([1 + r for r in period_rets])])
        )

        cagr   = _cagr(pv, n_years)
        sharpe = _sharpe_quarterly(np.array(period_rets))
        mdd    = _mdd(pv)

        results.append(SimResult(sim_id=sim_id, cagr=cagr, sharpe=sharpe, mdd=mdd, n_valid_periods=n_valid))

        if (sim_id + 1) % 10 == 0:
            logger.info(
                "시뮬 %3d/%d 완료: CAGR=%.2f%%, Sharpe=%.3f",
                sim_id + 1, N_SIMS, cagr * 100, sharpe,
            )

    return results


# ── 지표 계산 ─────────────────────────────────────────────────────────────────

def _cagr(pv: pd.Series, n_years: float) -> float:
    if n_years <= 0 or pv.iloc[0] <= 0:
        return 0.0
    total = pv.iloc[-1] / pv.iloc[0]
    if total <= 0:
        return -1.0
    return float(total ** (1 / n_years) - 1)


def _sharpe_quarterly(period_rets: np.ndarray, rf_annual: float = 0.03) -> float:
    """분기 수익률 → 연환산 Sharpe."""
    if len(period_rets) < 2:
        return 0.0
    rf_q = (1 + rf_annual) ** 0.25 - 1
    excess = period_rets - rf_q
    std = float(np.std(excess, ddof=1))
    if std < 1e-10:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(4))


def _mdd(pv: pd.Series) -> float:
    rolling_max = pv.cummax()
    dd = (pv - rolling_max) / rolling_max
    return float(dd.min())


# ── 통계 분석 ──────────────────────────────────────────────────────────────────

def percentile_rank(value: float, dist: list[float]) -> float:
    """value가 dist 분포에서 상위 몇 %인지 (0~100)."""
    arr = np.array(dist)
    return float(np.mean(arr < value) * 100)


def analyze(
    results: list[SimResult],
) -> dict[str, dict[str, float]]:
    cagrs   = [r.cagr   for r in results]
    sharpes = [r.sharpe for r in results]
    mdds    = [r.mdd    for r in results]

    def stats(vals: list[float]) -> dict[str, float]:
        a = np.array(vals)
        return {
            "mean":  float(np.mean(a)),
            "median": float(np.median(a)),
            "std":   float(np.std(a, ddof=1)),
            "p5":    float(np.percentile(a, 5)),
            "p25":   float(np.percentile(a, 25)),
            "p75":   float(np.percentile(a, 75)),
            "p95":   float(np.percentile(a, 95)),
        }

    return {
        "cagr":   stats(cagrs),
        "sharpe": stats(sharpes),
        "mdd":    stats(mdds),
        "ranks": {
            "cagr_pctile":   percentile_rank(STRATEGY_CAGR,   cagrs),
            "sharpe_pctile": percentile_rank(STRATEGY_SHARPE, sharpes),
        },
    }


# ── 보고서 ─────────────────────────────────────────────────────────────────────

def _pct(v: float, d: int = 2) -> str:
    return f"{v * 100:.{d}f}%"


def _f(v: float, d: int = 3) -> str:
    return f"{v:.{d}f}"


def verdict(cagr_pctile: float, sharpe_pctile: float) -> str:
    min_p = min(cagr_pctile, sharpe_pctile)
    if min_p >= 95:
        return "✅ **alpha 유의** — 팩터 스코어링이 통계적으로 유의한 alpha 생성 (≥ 95%ile)"
    if min_p >= 75:
        return "⚠️ **alpha 존재 가능** — 유의하나 강하지 않음. 추가 팩터 개선 권장 (75~95%ile)"
    return "❌ **alpha 불확실** — 팩터 스코어링 우위 약함. 전략 근본 재검토 권장 (< 75%ile)"


def build_report(stats: dict, n_valid_avg: float, n_periods: int = 0) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cs  = stats["cagr"]
    ss  = stats["sharpe"]
    ms  = stats["mdd"]
    rk  = stats["ranks"]

    cagr_pctile   = rk["cagr_pctile"]
    sharpe_pctile = rk["sharpe_pctile"]

    lines = [
        "# 랜덤 벤치마크 분석 V5",
        "",
        f"생성: {now}  ",
        f"기간: {BACKTEST_START} ~ {BACKTEST_END}  ",
        f"시장: {MARKET} | 시뮬: {N_SIMS}회 | 선택 종목: {N_SELECT}개",
        "",
        "## 분석 방법",
        "",
        "- 유니버스: Step1 + Step3v2 + F-Score≥4 통과 종목 (S4 제외)",
        "- 기간 수익률: 분기 리밸런싱일 close-to-close",
        "- 거래비용: 왕복 0.38% 고정 차감 (100% 턴오버 가정)",
        "  ※ 전략 실제 턴오버 47.4%/분기 → 비용 0.18%/분기 (랜덤에 1.5× 불리)",
        f"- 시드: RandomState(i) for i in range({N_SIMS})",
        "",
        "## CAGR 분포",
        "",
        "| 통계량 | 랜덤 100회 | 전략 (v2.1.0) |",
        "|--------|-----------|--------------|",
        f"| 평균   | {_pct(cs['mean'])}  | {_pct(STRATEGY_CAGR)} |",
        f"| 중간값 | {_pct(cs['median'])} | — |",
        f"| 표준편차 | {_pct(cs['std'])} | — |",
        f"| 5%ile  | {_pct(cs['p5'])}  | — |",
        f"| 25%ile | {_pct(cs['p25'])} | — |",
        f"| 75%ile | {_pct(cs['p75'])} | — |",
        f"| 95%ile | {_pct(cs['p95'])} | — |",
        f"| **전략 백분위** | — | **{cagr_pctile:.1f}%ile** |",
        "",
        "## Sharpe 분포",
        "",
        "| 통계량 | 랜덤 100회 | 전략 (v2.1.0) |",
        "|--------|-----------|--------------|",
        f"| 평균   | {_f(ss['mean'])}   | {_f(STRATEGY_SHARPE)} |",
        f"| 중간값 | {_f(ss['median'])} | — |",
        f"| 표준편차 | {_f(ss['std'])} | — |",
        f"| 5%ile  | {_f(ss['p5'])}  | — |",
        f"| 25%ile | {_f(ss['p25'])} | — |",
        f"| 75%ile | {_f(ss['p75'])} | — |",
        f"| 95%ile | {_f(ss['p95'])} | — |",
        f"| **전략 백분위** | — | **{sharpe_pctile:.1f}%ile** |",
        "",
        "## MDD 분포",
        "",
        "| 통계량 | 랜덤 100회 | 전략 (v2.1.0) |",
        "|--------|-----------|--------------|",
        f"| 평균   | {_pct(ms['mean'])}  | {_pct(STRATEGY_MDD)} |",
        f"| 중간값 | {_pct(ms['median'])} | — |",
        f"| 95%ile (좋은 방향, 작은 MDD) | {_pct(ms['p5'])} | — |",
        "",
        "## 판정",
        "",
        f"- 전략 CAGR {_pct(STRATEGY_CAGR)} → 랜덤 95%ile ({_pct(cs['p95'])}) {'초과 ✅' if STRATEGY_CAGR > cs['p95'] else '미달 ❌'}",
        f"- 전략 Sharpe {_f(STRATEGY_SHARPE)} → 랜덤 95%ile ({_f(ss['p95'])}) {'초과 ✅' if STRATEGY_SHARPE > ss['p95'] else '미달 ❌'}",
        "",
        verdict(cagr_pctile, sharpe_pctile),
        "",
        "## 해석",
        "",
        f"- 랜덤 평균 CAGR {_pct(cs['mean'])}는 '필터 통과 유니버스 자체의 baseline 수익률'.",
        f"- 전략 {_pct(STRATEGY_CAGR)}가 이를 초과하는 만큼이 팩터 스코어링의 순수 기여.",
        f"- 팩터 효과: {_pct(STRATEGY_CAGR - cs['mean'], 2)}p (CAGR), {_f(STRATEGY_SHARPE - ss['mean'], 3)} (Sharpe).",
        "- 주의: 랜덤은 100% 턴오버 적용 → 연간 비용 ~1.52% (전략 ~0.76%).",
        "  비용 불이익 제거 시 랜덤 CAGR 약 +0.76%p 조정 필요.",
        f"- 유효 기간 평균: {n_valid_avg:.1f}/{n_periods:d}분기",
    ]
    return "\n".join(lines)


# ── ASCII 히스토그램 ──────────────────────────────────────────────────────────

def ascii_hist(values: list[float], label: str, strategy_val: float, n_bins: int = 8) -> str:
    arr = np.array(values)
    lo, hi = arr.min(), arr.max()
    if lo >= hi:
        return f"{label}: 분산 없음"
    bins = np.linspace(lo, hi, n_bins + 1)
    counts, _ = np.histogram(arr, bins=bins)
    max_c = max(counts) or 1

    lines = [f"{label} 분포 (N={len(values)}):"]
    for i in range(n_bins):
        bar_len = int(counts[i] / max_c * 20)
        bar = "█" * bar_len
        mid = (bins[i] + bins[i + 1]) / 2
        marker = " ← 전략" if bins[i] <= strategy_val * 100 < bins[i + 1] else ""
        lines.append(f"  {mid * 100:6.1f}% |{bar:<20}| {counts[i]:3d}{marker}")
    return "\n".join(lines)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 → UTF-8
    parser = argparse.ArgumentParser(description="랜덤 벤치마크 100회 시뮬레이션 V5")
    parser.add_argument("--save-report", action="store_true")
    args = parser.parse_args()

    setup_logging()
    logger.info("랜덤 벤치마크 V5 시작")
    logger.info("기간: %s ~ %s | 시뮬: %d회 | 선택: %d종목", BACKTEST_START, BACKTEST_END, N_SIMS, N_SELECT)

    # 0) 스토리지 준비
    from data.storage import DataStorage
    storage = DataStorage()

    # 1) 분기 리밸런싱 날짜
    rebal_dates = get_quarterly_rebal_dates(BACKTEST_START, BACKTEST_END)
    logger.info("분기 리밸런싱 날짜: %d개 (%s ~ %s)", len(rebal_dates),
                rebal_dates[0].strftime("%Y%m%d"), rebal_dates[-1].strftime("%Y%m%d"))

    # 2) 유니버스 수집 (Step1+Step3v2+F-Score 통과 전종목)
    universes = collect_universes(rebal_dates)
    universe_sizes = [len(v) for v in universes.values()]
    logger.info("유니버스 평균: %.0f종목 (min=%d, max=%d)",
                np.mean(universe_sizes), min(universe_sizes), max(universe_sizes))

    # 3) 기간 수익률 사전계산
    period_returns = precompute_period_returns(rebal_dates, universes, storage)
    logger.info("기간 수익률 계산: %d/%d 기간 성공", len(period_returns), len(rebal_dates) - 1)

    # 4) 100회 시뮬레이션
    logger.info("100회 시뮬레이션 시작...")
    sim_results = run_simulations(rebal_dates, universes, period_returns)
    n_valid_avg = np.mean([r.n_valid_periods for r in sim_results])

    # 5) 통계 분석
    stats = analyze(sim_results)
    rk = stats["ranks"]
    cagr_pctile   = rk["cagr_pctile"]
    sharpe_pctile = rk["sharpe_pctile"]

    # 6) 보고서 생성
    n_periods = len(rebal_dates) - 1
    report_text = build_report(stats, n_valid_avg, n_periods=n_periods)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    logger.info("보고서 저장: %s", REPORT_PATH)

    # ── 최종 출력 ──────────────────────────────────────────────────────────────
    cs = stats["cagr"]
    ss = stats["sharpe"]
    ms = stats["mdd"]

    print("\n" + "=" * 70)
    print("랜덤 벤치마크 결과 (100회 시뮬, 2017-2024, KOSPI, Step1+Step3v2+F-Score)")
    print("=" * 70)
    print()
    print("── CAGR 분포 ────────────────────────────────────────────────────────")
    print(f"  평균:    {_pct(cs['mean'])}")
    print(f"  중간값:  {_pct(cs['median'])}")
    print(f"  표준편차:{_pct(cs['std'])}")
    print(f"  5%ile:   {_pct(cs['p5'])}")
    print(f"  25%ile:  {_pct(cs['p25'])}")
    print(f"  75%ile:  {_pct(cs['p75'])}")
    print(f"  95%ile:  {_pct(cs['p95'])}")
    print(f"  전략({_pct(STRATEGY_CAGR)}) 백분위:  {cagr_pctile:.1f}%ile")
    print()
    print("── Sharpe 분포 ──────────────────────────────────────────────────────")
    print(f"  평균:    {_f(ss['mean'])}")
    print(f"  중간값:  {_f(ss['median'])}")
    print(f"  표준편차:{_f(ss['std'])}")
    print(f"  5%ile:   {_f(ss['p5'])}")
    print(f"  95%ile:  {_f(ss['p95'])}")
    print(f"  전략({_f(STRATEGY_SHARPE)}) 백분위:  {sharpe_pctile:.1f}%ile")
    print()
    print("── MDD 분포 (작은 값이 좋음) ────────────────────────────────────────")
    print(f"  평균:    {_pct(ms['mean'])}")
    print(f"  중간값:  {_pct(ms['median'])}")
    print(f"  전략:    {_pct(STRATEGY_MDD)}")
    print()
    print("── 판정 ─────────────────────────────────────────────────────────────")
    print(f"  CAGR  {_pct(STRATEGY_CAGR)} vs 95%ile {_pct(cs['p95'])}: {'✅ 초과' if STRATEGY_CAGR > cs['p95'] else '❌ 미달'}")
    print(f"  Sharpe {_f(STRATEGY_SHARPE)} vs 95%ile {_f(ss['p95'])}: {'✅ 초과' if STRATEGY_SHARPE > ss['p95'] else '❌ 미달'}")
    print()
    print(verdict(cagr_pctile, sharpe_pctile))
    print()
    print("── 팩터 효과 ────────────────────────────────────────────────────────")
    print(f"  CAGR 초과: {_pct(STRATEGY_CAGR - cs['mean'], 2)}p  "
          f"(랜덤 평균 {_pct(cs['mean'])} vs 전략 {_pct(STRATEGY_CAGR)})")
    print(f"  Sharpe 초과: +{_f(STRATEGY_SHARPE - ss['mean'], 3)}")
    print()
    print(ascii_hist([r.cagr for r in sim_results], "CAGR", STRATEGY_CAGR))
    print()
    print(f"보고서: {REPORT_PATH}")


if __name__ == "__main__":
    main()
