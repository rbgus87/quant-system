"""scripts/analyze_factor_ic_v3.py — 팩터별 IC/IR/Quintile Decay 분석 V3.

2017-2024, 분기 리밸런싱일(3/6/9/12월 말) 기준.
각 분기에서:
1. 유니버스 확정 (금융 제외 + 시총/거래대금 필터 + F-Score≥4, Step1/3·S4 비활성)
2. 팩터 점수 + 하위 지표 점수 계산 (Reporting Lag 적용)
3. 다음 분기 수익률 (close-to-close) 계산
4. Spearman IC, IR, Hit Rate, Quintile Decay 집계

사용:
    python scripts/analyze_factor_ic_v3.py
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path
from typing import Generator

import numpy as np
import pandas as pd
from scipy import stats

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────

BACKTEST_START = "2017-01-01"
BACKTEST_END   = "2024-12-31"
MARKET         = "KOSPI"

REPORT_DIR  = PROJECT_ROOT / "docs" / "reports"
REPORT_PATH = REPORT_DIR / "factor_ic_v3_analysis.md"

FACTOR_KEYS = [
    "PBR_rank", "PCR_rank", "DIV_rank", "Value_composite",
    "Mom_12M1M", "Mom_6M1M", "Momentum_composite",
    "GP_A", "EY", "Quality_composite",
    "Composite_V70M30",
]

FACTOR_LABELS: dict[str, str] = {
    "PBR_rank":           "PBR rank",
    "PCR_rank":           "PCR/PSR rank",
    "DIV_rank":           "DIV rank",
    "Value_composite":    "Value 합산",
    "Mom_12M1M":          "12M-1M 모멘텀",
    "Mom_6M1M":           "6M-1M 모멘텀",
    "Momentum_composite": "Momentum 합산",
    "GP_A":               "GP/A",
    "EY":                 "EY (1/PER)",
    "Quality_composite":  "Quality 합산",
    "Composite_V70M30":   "Composite (V70+M30)",
}

# ── 유니버스 가드 ─────────────────────────────────────────────────────────────

@contextmanager
def ic_universe_guard() -> Generator[None, None, None]:
    """IC 분석용 유니버스: Step1/3·S4 비활성, F-Score≥4 유지, n_stocks=9999."""
    from strategy.screener import MultiFactorScreener

    backup_s4 = settings.universe.sector_diversification_enabled
    backup_op = settings.quality.operating_quality_filter_enabled
    backup_cp = settings.quality.consecutive_profit_filter_enabled
    backup_n  = settings.portfolio.n_stocks

    settings.universe.sector_diversification_enabled = False
    settings.quality.operating_quality_filter_enabled = False
    settings.quality.consecutive_profit_filter_enabled = False
    settings.portfolio.n_stocks = 9999
    MultiFactorScreener._factor_cache.clear()
    try:
        yield
    finally:
        settings.universe.sector_diversification_enabled = backup_s4
        settings.quality.operating_quality_filter_enabled = backup_op
        settings.quality.consecutive_profit_filter_enabled = backup_cp
        settings.portfolio.n_stocks = backup_n
        MultiFactorScreener._factor_cache.clear()


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def get_rebal_dates(start: str, end: str) -> list[pd.Timestamp]:
    from config.calendar import get_krx_month_end_sessions
    sessions = get_krx_month_end_sessions(start, end)
    return [s for s in sessions if s.month in (3, 6, 9, 12)]


# ── 하위 팩터 점수 계산 ───────────────────────────────────────────────────────

def compute_subfactor_scores(
    screener_df: pd.DataFrame,
    fundamentals: pd.DataFrame,
    returns_12m: pd.Series,
    returns_6m: pd.Series,
) -> pd.DataFrame:
    """하위 팩터 점수를 포함한 전체 팩터 점수 DataFrame 구성.

    Args:
        screener_df: screen() 반환값 (index=ticker, value/momentum/quality/composite_score 포함)
        fundamentals: 재무 데이터 (index=ticker, Reporting Lag 적용 후)
        returns_12m: 12M-1M 수익률 Series (index=ticker)
        returns_6m: 6M-1M 수익률 Series (index=ticker)

    Returns:
        DataFrame (index=ticker, columns=FACTOR_KEYS)
    """
    from factors.momentum import MomentumFactor
    from factors.quality import QualityFactor
    from factors.value import ValueFactor

    result = pd.DataFrame(index=screener_df.index)
    fund = fundamentals[fundamentals.index.isin(result.index)].copy()

    # ── Value 하위 ────────────────────────────────────────────────────────────
    if "PBR" in fund.columns:
        pbr = fund["PBR"].copy()
        pbr = pbr[pbr > 0]
        if not pbr.empty:
            pbr = pbr.clip(upper=pbr.quantile(0.99))
            result["PBR_rank"] = ValueFactor._rank_score(1.0 / pbr)

    pcr_col = None
    if "PCR" in fund.columns and fund["PCR"].notna().any():
        pcr_col = "PCR"
    elif "PSR" in fund.columns and fund["PSR"].notna().any():
        pcr_col = "PSR"
    if pcr_col:
        pcr = fund[pcr_col].copy()
        pcr = pcr[pcr > 0]
        if not pcr.empty:
            pcr = pcr.clip(upper=pcr.quantile(0.99))
            result["PCR_rank"] = ValueFactor._rank_score(1.0 / pcr)

    if "DIV" in fund.columns:
        div = fund["DIV"].copy()
        div = div[div >= 0]
        result["DIV_rank"] = ValueFactor._rank_score(div)

    if "value_score" in screener_df.columns:
        result["Value_composite"] = screener_df["value_score"]

    # ── Momentum 하위 ─────────────────────────────────────────────────────────
    uni_12m = returns_12m[returns_12m.index.isin(result.index)]
    uni_6m  = returns_6m[returns_6m.index.isin(result.index)]

    mom_12 = MomentumFactor._single_score(uni_12m)
    mom_6  = MomentumFactor._single_score(uni_6m)
    if not mom_12.empty:
        result["Mom_12M1M"] = mom_12
    if not mom_6.empty:
        result["Mom_6M1M"] = mom_6

    if "momentum_score" in screener_df.columns:
        result["Momentum_composite"] = screener_df["momentum_score"]

    # ── Quality 하위 ─────────────────────────────────────────────────────────
    gpa = QualityFactor._calc_gpa_score(fund)
    ey  = QualityFactor._calc_earnings_yield_score(fund)
    if not gpa.empty:
        result["GP_A"] = gpa
    if not ey.empty:
        result["EY"] = ey

    if "quality_score" in screener_df.columns:
        result["Quality_composite"] = screener_df["quality_score"]

    # ── Composite ────────────────────────────────────────────────────────────
    if "composite_score" in screener_df.columns:
        result["Composite_V70M30"] = screener_df["composite_score"]

    return result


# ── IC / Quintile ─────────────────────────────────────────────────────────────

def compute_ic(factor_scores: pd.Series, period_returns: pd.Series) -> float:
    """Spearman IC (factor_scores vs next-period returns).

    Args:
        factor_scores: 팩터 점수 Series (index=ticker)
        period_returns: 다음 분기 수익률 Series (index=ticker)

    Returns:
        Spearman 상관계수 (float), 유효 데이터 < 10이면 NaN
    """
    aligned = pd.concat([factor_scores, period_returns], axis=1).dropna()
    if len(aligned) < 10:
        return float("nan")
    corr, _ = stats.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    return float(corr)


def compute_quintile_returns(
    factor_scores: pd.Series,
    period_returns: pd.Series,
) -> dict[str, float]:
    """단일 분기 Quintile 평균 수익률.

    Q1 = 팩터 점수 상위 20% (높은 점수 = Q1).

    Returns:
        {'Q1': float, 'Q2': float, ..., 'Q5': float}
    """
    aligned = pd.concat([factor_scores, period_returns], axis=1).dropna()
    aligned.columns = ["score", "ret"]
    nan_result = {f"Q{i}": float("nan") for i in range(1, 6)}

    if len(aligned) < 25:
        return nan_result

    try:
        # 낮은 점수 → Q5, 높은 점수 → Q1 (역순 레이블)
        aligned["q"] = pd.qcut(
            aligned["score"], 5,
            labels=["Q5", "Q4", "Q3", "Q2", "Q1"],
        )
    except ValueError:
        return nan_result

    q_mean = aligned.groupby("q", observed=False)["ret"].mean()
    return {q: float(q_mean.get(q, float("nan"))) for q in [f"Q{i}" for i in range(1, 6)]}


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

@dataclass
class PeriodData:
    rd: pd.Timestamp
    factor_df: pd.DataFrame  # index=ticker, columns=FACTOR_KEYS
    returns: pd.Series        # index=ticker, close-to-close return


def collect_all(rebal_dates: list[pd.Timestamp]) -> list[PeriodData]:
    """32개 분기의 팩터 점수 + 다음 기간 수익률 수집.

    Args:
        rebal_dates: 분기 말 리밸런싱 날짜 목록

    Returns:
        PeriodData 리스트 (기간 수 = len(rebal_dates) - 1)
    """
    from data.storage import DataStorage
    from strategy.screener import MultiFactorScreener

    storage = DataStorage()
    screener = MultiFactorScreener()

    periods: list[PeriodData] = []
    n = len(rebal_dates)

    with ic_universe_guard():
        for i in range(n - 1):
            rd_start = rebal_dates[i]
            rd_end   = rebal_dates[i + 1]
            date_str = rd_start.strftime("%Y%m%d")
            next_str = rd_end.strftime("%Y%m%d")

            logger.info("[%d/%d] %s → %s", i + 1, n - 1, date_str, next_str)

            # 1) 유니버스 + 팩터 스코어 (Step1/3·S4 비활성 상태로 실행)
            scr = screener.screen(date_str, market=MARKET, n_stocks=9999)
            if scr.empty:
                logger.warning("[%s] screener 결과 없음 — 스킵", date_str)
                continue
            tickers = scr.index.tolist()

            # 2) 재무 데이터 (Reporting Lag 적용)
            fund_date = MultiFactorScreener._get_effective_fundamental_date(date_str)
            try:
                fundamentals = screener.collector.get_fundamentals_all(fund_date, MARKET)
            except Exception as e:
                logger.warning("[%s] 펀더멘털 조회 실패: %s", date_str, e)
                fundamentals = pd.DataFrame(index=scr.index)

            # 3) 모멘텀 수익률 (sub-factor용 — 12M·6M 동시 조회)
            try:
                multi_rets = screener.return_calc.get_returns_multi_period(
                    tickers, date_str, [12, 6], skip_months=1
                )
                returns_12m = multi_rets.get(12, pd.Series(dtype=float))
                returns_6m  = multi_rets.get(6,  pd.Series(dtype=float))
            except Exception as e:
                logger.warning("[%s] 모멘텀 수익률 계산 실패: %s", date_str, e)
                returns_12m = pd.Series(dtype=float)
                returns_6m  = pd.Series(dtype=float)

            # 4) 하위 팩터 점수 계산
            try:
                factor_df = compute_subfactor_scores(
                    scr, fundamentals, returns_12m, returns_6m
                )
            except Exception as e:
                logger.warning("[%s] sub-factor 계산 실패: %s", date_str, e)
                continue

            # 5) 다음 분기 close-to-close 수익률 (IC 계산용)
            sd: date_type = rd_start.date()
            ed: date_type = rd_end.date()
            try:
                df_s = storage.load_daily_prices_bulk(tickers, sd, sd)
                df_e = storage.load_daily_prices_bulk(tickers, ed, ed)
            except Exception as e:
                logger.warning("[%s] 가격 데이터 로드 실패: %s", date_str, e)
                continue

            if df_s.empty or df_e.empty:
                logger.warning("[%s] 가격 데이터 없음 — 스킵", date_str)
                continue

            p_start = df_s.groupby("ticker")["close"].first()
            p_end   = df_e.groupby("ticker")["close"].first()
            valid   = p_start.index.intersection(p_end.index)
            valid   = valid[p_start[valid] > 0]
            period_rets = (p_end[valid] / p_start[valid] - 1).dropna()

            if period_rets.empty:
                logger.warning("[%s] 유효 수익률 없음 — 스킵", date_str)
                continue

            logger.info(
                "[%s] 유니버스 %d종목, 수익률 유효 %d종목",
                date_str, len(tickers), len(period_rets),
            )
            periods.append(PeriodData(rd=rd_start, factor_df=factor_df, returns=period_rets))

    logger.info("데이터 수집 완료: %d/%d 기간", len(periods), n - 1)
    return periods


# ── 집계 ─────────────────────────────────────────────────────────────────────

@dataclass
class ICStats:
    mean: float     = field(default=float("nan"))
    std: float      = field(default=float("nan"))
    ir: float       = field(default=float("nan"))
    hit_rate: float = field(default=float("nan"))
    n_periods: int  = 0


def aggregate_ic_stats(periods: list[PeriodData]) -> dict[str, ICStats]:
    """팩터별 IC 통계 (평균, 표준편차, IR, Hit Rate)."""
    ic_by_factor: dict[str, list[float]] = {k: [] for k in FACTOR_KEYS}

    for p in periods:
        for fk in FACTOR_KEYS:
            if fk not in p.factor_df.columns:
                continue
            ic = compute_ic(p.factor_df[fk], p.returns)
            if not np.isnan(ic):
                ic_by_factor[fk].append(ic)

    result: dict[str, ICStats] = {}
    for fk, ics in ic_by_factor.items():
        if not ics:
            result[fk] = ICStats()
            continue
        arr = np.array(ics)
        mean_ic = float(np.mean(arr))
        std_ic  = float(np.std(arr, ddof=1)) if len(arr) > 1 else float("nan")
        ir      = mean_ic / std_ic if (std_ic and not np.isnan(std_ic) and std_ic > 0) else float("nan")
        hit     = float(np.mean(arr > 0))
        result[fk] = ICStats(mean=mean_ic, std=std_ic, ir=ir, hit_rate=hit, n_periods=len(arr))

    return result


def aggregate_quintile_stats(
    periods: list[PeriodData],
) -> dict[str, dict[str, float]]:
    """팩터별 Quintile 평균 분기 수익률."""
    q_accum: dict[str, dict[str, list[float]]] = {
        fk: {f"Q{i}": [] for i in range(1, 6)} for fk in FACTOR_KEYS
    }

    for p in periods:
        for fk in FACTOR_KEYS:
            if fk not in p.factor_df.columns:
                continue
            q_rets = compute_quintile_returns(p.factor_df[fk], p.returns)
            for q, r in q_rets.items():
                if not np.isnan(r):
                    q_accum[fk][q].append(r)

    result: dict[str, dict[str, float]] = {}
    for fk in FACTOR_KEYS:
        result[fk] = {
            q: float(np.mean(vals)) if vals else float("nan")
            for q, vals in q_accum[fk].items()
        }
    return result


def aggregate_quintile_counts(
    periods: list[PeriodData],
) -> dict[str, dict[str, float]]:
    """팩터별 Quintile 평균 종목 수."""
    q_accum: dict[str, dict[str, list[int]]] = {
        fk: {f"Q{i}": [] for i in range(1, 6)} for fk in FACTOR_KEYS
    }

    for p in periods:
        for fk in FACTOR_KEYS:
            if fk not in p.factor_df.columns:
                continue
            aligned = pd.concat([p.factor_df[fk], p.returns], axis=1).dropna()
            if len(aligned) < 25:
                continue
            try:
                q_labels = pd.qcut(
                    aligned.iloc[:, 0], 5,
                    labels=["Q5", "Q4", "Q3", "Q2", "Q1"],
                )
                q_counts = q_labels.value_counts()
                for q in [f"Q{i}" for i in range(1, 6)]:
                    q_accum[fk][q].append(int(q_counts.get(q, 0)))
            except ValueError:
                pass

    result: dict[str, dict[str, float]] = {}
    for fk in FACTOR_KEYS:
        result[fk] = {
            q: float(np.mean(vals)) if vals else float("nan")
            for q, vals in q_accum[fk].items()
        }
    return result


def aggregate_ic_by_year(periods: list[PeriodData]) -> dict[int, dict[str, float]]:
    """연도별 IC (주요 4개 팩터)."""
    keys = ["Value_composite", "Momentum_composite", "Quality_composite", "Composite_V70M30"]
    year_data: dict[int, dict[str, list[float]]] = {}

    for p in periods:
        yr = p.rd.year
        if yr not in year_data:
            year_data[yr] = {k: [] for k in keys}
        for fk in keys:
            if fk not in p.factor_df.columns:
                continue
            ic = compute_ic(p.factor_df[fk], p.returns)
            if not np.isnan(ic):
                year_data[yr][fk].append(ic)

    return {
        yr: {
            fk: float(np.mean(ics)) if ics else float("nan")
            for fk, ics in data.items()
        }
        for yr, data in sorted(year_data.items())
    }


# ── 보고서 ────────────────────────────────────────────────────────────────────

def rating(st: ICStats) -> str:
    """IR + Hit Rate 기반 별점 판정."""
    ir, hr = st.ir, st.hit_rate
    if np.isnan(ir) or np.isnan(hr):
        return "N/A"
    if ir > 0.10 and hr > 0.60:
        return "★★★"
    if ir > 0.05 and hr > 0.55:
        return "★★"
    if ir > 0.02 and hr > 0.50:
        return "★"
    return "✗"


def _is_monotonic(q_means: dict[str, float]) -> str:
    rets = [q_means.get(f"Q{i}", float("nan")) for i in range(1, 6)]
    if any(np.isnan(r) for r in rets):
        return "—"
    return "✅" if all(a >= b for a, b in zip(rets, rets[1:])) else "❌"


def build_report(
    ic_stats: dict[str, ICStats],
    q_stats: dict[str, dict[str, float]],
    q_counts: dict[str, dict[str, float]],
    yr_ic: dict[int, dict[str, float]],
    n_periods: int,
) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _f(v: float, d: int = 3) -> str:
        return f"{v:+.{d}f}" if not np.isnan(v) else "—"

    def _pct(v: float, d: int = 2) -> str:
        return f"{v * 100:.{d}f}%" if not np.isnan(v) else "—"

    lines = [
        "# 팩터 IC/IR/Quintile Decay 분석 V3",
        "",
        f"생성: {now}  ",
        f"기간: {BACKTEST_START} ~ {BACKTEST_END}  ",
        f"시장: {MARKET} | 유효 기간: {n_periods}분기",
        "",
        "## 유니버스 정의",
        "",
        "- 금융주 제외 + 시총/거래대금 필터 + F-Score≥4",
        "- **Step1 (본업 품질)·Step3 (연속 흑자)·S4 (섹터 분산): 비활성**",
        "  → IC는 팩터 순수 예측력 측정이므로 품질 필터 전 유니버스 사용",
        "- Reporting Lag 적용 (재무 데이터 기준일: 리밸런싱 연도 전전/전년 12/31)",
        "",
        "## IC/IR 요약",
        "",
        "| 팩터 | 평균 IC | IC 표준편차 | IR (IC/σ) | Hit Rate | N기간 | 판정 |",
        "|------|---------|-----------|----------|---------|------|------|",
    ]

    for fk in FACTOR_KEYS:
        st = ic_stats.get(fk, ICStats())
        lines.append(
            f"| {FACTOR_LABELS[fk]} "
            f"| {_f(st.mean)} | {_f(st.std)} | {_f(st.ir)} "
            f"| {_pct(st.hit_rate)} | {st.n_periods} | {rating(st)} |"
        )

    lines += [
        "",
        "**판정 기준**:",
        "- ★★★: IR > 0.10 + Hit Rate > 60% (강한 예측력)",
        "- ★★: IR > 0.05 + Hit Rate > 55% (유의미한 예측력)",
        "- ★: IR > 0.02 + Hit Rate > 50% (약한 예측력)",
        "- ✗: IR ≤ 0.02 또는 Hit Rate ≤ 50% (예측력 없음)",
        "",
        "## Quintile Decay",
        "",
    ]

    # 4개 주요 팩터 quintile 테이블
    for fk in ["Value_composite", "Momentum_composite", "Quality_composite", "Composite_V70M30"]:
        q = q_stats.get(fk, {})
        cnt = q_counts.get(fk, {})
        spread = q.get("Q1", float("nan")) - q.get("Q5", float("nan"))

        lines += [
            f"### {FACTOR_LABELS[fk]}",
            "",
            "| Quintile | 평균 분기 수익률 | 평균 종목 수 |",
            "|----------|----------------|------------|",
        ]
        for qi in range(1, 6):
            tag = " (상위 20%)" if qi == 1 else (" (하위 20%)" if qi == 5 else "")
            lines.append(
                f"| Q{qi}{tag} | {_pct(q.get(f'Q{qi}', float('nan')))} "
                f"| {cnt.get(f'Q{qi}', float('nan')):.0f} |"
            )

        lines += [
            f"| **Q1-Q5 Spread** | **{_pct(spread)}** | — |",
            f"| Monotonic | {_is_monotonic(q)} | — |",
            "",
        ]

    # IC 시계열
    lines += [
        "## IC 시계열 (연도별)",
        "",
        "| 연도 | Value IC | Momentum IC | Quality IC | Composite IC |",
        "|------|---------|------------|-----------|-------------|",
    ]
    for yr, ics in yr_ic.items():
        lines.append(
            f"| {yr} "
            f"| {_f(ics.get('Value_composite', float('nan')))} "
            f"| {_f(ics.get('Momentum_composite', float('nan')))} "
            f"| {_f(ics.get('Quality_composite', float('nan')))} "
            f"| {_f(ics.get('Composite_V70M30', float('nan')))} |"
        )

    # 핵심 발견
    lines += ["", "## 핵심 발견 및 권고", ""]

    v_ir = ic_stats.get("Value_composite", ICStats()).ir
    m_ir = ic_stats.get("Momentum_composite", ICStats()).ir
    q_ir = ic_stats.get("Quality_composite", ICStats()).ir

    best_fk = max(
        FACTOR_KEYS,
        key=lambda fk: ic_stats[fk].ir if not np.isnan(ic_stats[fk].ir) else -999,
    )
    best_ir = ic_stats[best_fk].ir

    main_source = max(
        [("Value", v_ir), ("Momentum", m_ir), ("Quality", q_ir)],
        key=lambda x: x[1] if not np.isnan(x[1]) else -999,
    )

    weak = [FACTOR_LABELS[fk] for fk in FACTOR_KEYS if rating(ic_stats.get(fk, ICStats())) == "✗"]
    neg_years = sorted(
        yr for yr, ics in yr_ic.items()
        if not np.isnan(ics.get("Composite_V70M30", float("nan")))
        and ics["Composite_V70M30"] < 0
    )

    lines += [
        f"1. **Alpha 주요 원천**: {main_source[0]} (최고 IR 팩터: {FACTOR_LABELS[best_fk]}, IR={_f(best_ir)})",
        f"2. **예측력 없는 팩터**: {', '.join(weak) if weak else '없음'}",
        (
            f"3. **Composite IC 음전환 연도**: {', '.join(map(str, neg_years))}"
            if neg_years else
            "3. **Composite IC 음전환 연도**: 없음"
        ),
        (
            f"4. **Quality 팩터 복원 가치**: IC/IR 양수 (IR={_f(q_ir)}) → "
            "가중치 복원 검토 가치 있음"
            if (not np.isnan(q_ir) and q_ir > 0) else
            f"4. **Quality 팩터 복원 가치**: IC/IR 비양수 (IR={_f(q_ir)}) → 현재 필터 전용 정책 유지 권장"
        ),
    ]

    return "\n".join(lines) + "\n"


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    logger.info("팩터 IC/IR/Quintile Decay 분석 V3 시작")
    logger.info("기간: %s ~ %s | 시장: %s", BACKTEST_START, BACKTEST_END, MARKET)

    # 1. 분기 리밸런싱 날짜
    rebal_dates = get_rebal_dates(BACKTEST_START, BACKTEST_END)
    logger.info(
        "분기 리밸런싱 날짜: %d개 (%s ~ %s)",
        len(rebal_dates),
        rebal_dates[0].strftime("%Y%m%d"),
        rebal_dates[-1].strftime("%Y%m%d"),
    )

    # 2. 팩터 점수 + 수익률 수집
    periods = collect_all(rebal_dates)
    if not periods:
        logger.error("수집된 기간 없음 — 종료")
        return

    # 3. 분석
    ic_stats = aggregate_ic_stats(periods)
    q_stats  = aggregate_quintile_stats(periods)
    q_counts = aggregate_quintile_counts(periods)
    yr_ic    = aggregate_ic_by_year(periods)

    # 4. 보고서 저장
    report_text = build_report(ic_stats, q_stats, q_counts, yr_ic, len(periods))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    logger.info("보고서 저장: %s", REPORT_PATH)

    # 5. 터미널 출력
    def _f(v: float) -> str:
        return f"{v:+.3f}" if not np.isnan(v) else "    —"

    def _pct(v: float) -> str:
        return f"{v * 100:+.2f}%" if not np.isnan(v) else "    —"

    print("\n" + "=" * 72)
    print("팩터 IC/IR/Quintile Decay 분석 V3  (2017-2024, KOSPI, F-Score≥4)")
    print("=" * 72)
    print()
    print(f"  {'팩터':<22} {'평균IC':>7} {'IR':>7} {'HitRate':>8}  판정")
    print(f"  {'-'*22} {'-'*7} {'-'*7} {'-'*8}  {'-'*4}")
    for fk in FACTOR_KEYS:
        st = ic_stats.get(fk, ICStats())
        print(
            f"  {FACTOR_LABELS[fk]:<22} {_f(st.mean):>7} {_f(st.ir):>7} "
            f"{f'{st.hit_rate*100:.0f}%':>8}  {rating(st)}"
        )

    print()
    print("── Quintile Decay ─────────────────────────────────────────────────────")
    for fk in ["Value_composite", "Momentum_composite", "Quality_composite", "Composite_V70M30"]:
        q = q_stats.get(fk, {})
        spread = q.get("Q1", float("nan")) - q.get("Q5", float("nan"))
        q_str = " → ".join(
            _pct(q.get(f"Q{i}", float("nan"))) for i in range(1, 6)
        )
        mono = _is_monotonic(q)
        print(f"  {FACTOR_LABELS[fk]:<22}: {q_str}  Spread={_pct(spread)}  {mono}")

    print()
    print("── IC 시계열 ──────────────────────────────────────────────────────────")
    print(f"  {'연도':<6} {'Value':>7} {'Mom':>7} {'Quality':>8} {'Composite':>10}")
    print(f"  {'-'*6} {'-'*7} {'-'*7} {'-'*8} {'-'*10}")
    for yr, ics in yr_ic.items():
        print(
            f"  {yr:<6} {_f(ics.get('Value_composite', float('nan'))):>7} "
            f"{_f(ics.get('Momentum_composite', float('nan'))):>7} "
            f"{_f(ics.get('Quality_composite', float('nan'))):>8} "
            f"{_f(ics.get('Composite_V70M30', float('nan'))):>10}"
        )

    # 핵심 발견
    v_ir = ic_stats.get("Value_composite", ICStats()).ir
    m_ir = ic_stats.get("Momentum_composite", ICStats()).ir
    q_ir = ic_stats.get("Quality_composite", ICStats()).ir
    main_src = max(
        [("Value", v_ir), ("Momentum", m_ir), ("Quality", q_ir)],
        key=lambda x: x[1] if not np.isnan(x[1]) else -999,
    )
    print()
    print("── 핵심 발견 ──────────────────────────────────────────────────────────")
    print(f"  Alpha 주요 원천:     {main_src[0]} (IR={_f(main_src[1])})")
    q_val_str = _f(q_ir)
    print(
        f"  Quality 팩터 복원:   {'검토 권장' if not np.isnan(q_ir) and q_ir > 0 else '현행 유지 권장'} "
        f"(Quality IR={q_val_str})"
    )
    print()
    print(f"  보고서: {REPORT_PATH}")


if __name__ == "__main__":
    main()
