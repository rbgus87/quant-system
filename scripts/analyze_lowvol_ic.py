"""scripts/analyze_lowvol_ic.py — S7 Low-Volatility 팩터 IC/IR 분석 (Part 1).

2017Q1~2024Q4, 31분기 기준으로 Low-vol 팩터의 예측력을 측정한다.
60 / 90 / 120일 lookback 3가지를 단일 패스로 비교.

판정 기준:
    최적 lookback의 IR > 0.05 → Part 2 (가중치 탐색) 진행
    IR ≤ 0              → Low-vol 폐기, 다른 팩터 후보 탐색

사용:
    python scripts/analyze_lowvol_ic.py
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from datetime import date as date_type
from pathlib import Path
from typing import Generator

import numpy as np
import pandas as pd
from scipy import stats

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings             # noqa: E402

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────

BACKTEST_START = "2017-01-01"
BACKTEST_END   = "2024-12-31"
MARKET         = "KOSPI"

LOOKBACKS: list[int] = [60, 90, 120]  # 분석할 변동성 lookback (일)

REPORT_DIR  = PROJECT_ROOT / "docs" / "reports"
REPORT_PATH = REPORT_DIR / "lowvol_ic_s7_analysis.md"

# Part 2 진행 기준 (최적 lookback의 IR)
IR_PROCEED_THRESHOLD = 0.05
IR_ABORT_THRESHOLD   = 0.0


# ── 유니버스 가드 ─────────────────────────────────────────────────────────────

@contextmanager
def ic_universe_guard() -> Generator[None, None, None]:
    """IC 분석용 유니버스: Step1/3·S4 비활성, F-Score≥4 유지, n_stocks=9999.

    low_vol 가중치도 0으로 강제 (팩터 점수 계산만, composite 반영 제외).
    """
    from strategy.screener import MultiFactorScreener

    backup_s4  = settings.universe.sector_diversification_enabled
    backup_op  = settings.quality.operating_quality_filter_enabled
    backup_cp  = settings.quality.consecutive_profit_filter_enabled
    backup_n   = settings.portfolio.n_stocks
    backup_lv  = settings.factor_weights.low_vol

    settings.universe.sector_diversification_enabled = False
    settings.quality.operating_quality_filter_enabled = False
    settings.quality.consecutive_profit_filter_enabled = False
    settings.portfolio.n_stocks = 9999
    settings.factor_weights.low_vol = 0.0   # composite 오염 방지
    MultiFactorScreener._factor_cache.clear()
    try:
        yield
    finally:
        settings.universe.sector_diversification_enabled = backup_s4
        settings.quality.operating_quality_filter_enabled = backup_op
        settings.quality.consecutive_profit_filter_enabled = backup_cp
        settings.portfolio.n_stocks = backup_n
        settings.factor_weights.low_vol = backup_lv
        MultiFactorScreener._factor_cache.clear()


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def get_rebal_dates(start: str, end: str) -> list[pd.Timestamp]:
    """분기말 KRX 거래일 목록 (3/6/9/12월)."""
    from config.calendar import get_krx_month_end_sessions
    sessions = get_krx_month_end_sessions(start, end)
    return [s for s in sessions if s.month in (3, 6, 9, 12)]


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


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def collect_all(rebal_dates: list[pd.Timestamp]) -> pd.DataFrame:
    """31분기 × 3 lookback IC 값 수집.

    Args:
        rebal_dates: 분기말 날짜 목록 (32개 → 31개 기간)

    Returns:
        DataFrame(index=period_str, columns=["lb60", "lb90", "lb120", "n_stocks"])
    """
    from data.storage import DataStorage
    from factors.volatility import VolatilityFactor
    from strategy.screener import MultiFactorScreener

    storage = DataStorage()
    screener = MultiFactorScreener()
    vol_factor = VolatilityFactor()

    records: list[dict] = []
    n = len(rebal_dates)

    with ic_universe_guard():
        for i in range(n - 1):
            rd_start = rebal_dates[i]
            rd_end   = rebal_dates[i + 1]
            date_str  = rd_start.strftime("%Y%m%d")
            next_str  = rd_end.strftime("%Y%m%d")
            period_lbl = f"{rd_start.year}Q{(rd_start.month - 1) // 3 + 1}"

            logger.info("[%d/%d] %s → %s", i + 1, n - 1, date_str, next_str)

            # 1) 유니버스 (Step1/3·S4 비활성)
            scr = screener.screen(date_str, market=MARKET, n_stocks=9999)
            if scr.empty:
                logger.warning("[%s] screener 결과 없음 — 스킵", date_str)
                continue
            tickers = scr.index.tolist()

            # 2) Low-vol 점수 (3가지 lookback 동시 계산)
            vol_scores: dict[int, pd.Series] = {}
            for lb in LOOKBACKS:
                try:
                    vol_scores[lb] = vol_factor.calc_volatility_score(
                        date=date_str,
                        tickers=tickers,
                        storage=storage,
                        lookback_days=lb,
                    )
                except Exception as e:
                    logger.warning("[%s] lb=%d vol 계산 실패: %s", date_str, lb, e)
                    vol_scores[lb] = pd.Series(np.nan, index=tickers)

            # 3) 다음 분기 close-to-close 수익률
            sd: date_type = rd_start.date()
            ed: date_type = rd_end.date()
            try:
                df_s = storage.load_daily_prices_bulk(tickers, sd, sd)
                df_e = storage.load_daily_prices_bulk(tickers, ed, ed)
            except Exception as e:
                logger.warning("[%s] 가격 로드 실패: %s", date_str, e)
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
                logger.warning("[%s] 수익률 데이터 없음 — 스킵", date_str)
                continue

            # 4) IC 계산
            row: dict = {"period": period_lbl, "n_stocks": len(period_rets)}
            for lb in LOOKBACKS:
                row[f"lb{lb}"] = compute_ic(vol_scores[lb], period_rets)

            records.append(row)

    df = pd.DataFrame(records).set_index("period")
    return df


# ── IR 요약 ───────────────────────────────────────────────────────────────────

def summarize_ic(ic_df: pd.DataFrame) -> pd.DataFrame:
    """IC 시계열 → 평균 IC, std IC, IR(=mean/std*√N), t-stat, Hit Rate 요약.

    Args:
        ic_df: collect_all() 반환값

    Returns:
        DataFrame(index=lookback_label, columns=[mean_ic, std_ic, ir, t_stat, hit_rate, n])
    """
    rows = []
    for lb in LOOKBACKS:
        col = f"lb{lb}"
        ic_series = ic_df[col].dropna()
        if ic_series.empty:
            rows.append({
                "lookback": f"{lb}d",
                "mean_ic": np.nan, "std_ic": np.nan,
                "ir": np.nan, "t_stat": np.nan,
                "hit_rate": np.nan, "n": 0,
            })
            continue
        n = len(ic_series)
        mean_ic  = float(ic_series.mean())
        std_ic   = float(ic_series.std())
        ir       = mean_ic / std_ic * np.sqrt(n) if std_ic > 0 else np.nan
        t_stat   = mean_ic / (std_ic / np.sqrt(n)) if std_ic > 0 else np.nan
        hit_rate = float((ic_series > 0).mean())
        rows.append({
            "lookback": f"{lb}d",
            "mean_ic": mean_ic, "std_ic": std_ic,
            "ir": ir, "t_stat": t_stat,
            "hit_rate": hit_rate, "n": n,
        })
    return pd.DataFrame(rows).set_index("lookback")


# ── 보고서 생성 ───────────────────────────────────────────────────────────────

def build_report(
    ic_df: pd.DataFrame,
    summary_df: pd.DataFrame,
) -> str:
    """마크다운 보고서 작성.

    Args:
        ic_df: 분기별 IC 시계열
        summary_df: IR 요약 표

    Returns:
        마크다운 문자열
    """
    # 최적 lookback 판정
    valid_summary = summary_df.dropna(subset=["ir"])
    if valid_summary.empty:
        best_lb_label = "N/A"
        best_ir = float("nan")
    else:
        best_lb_label = str(valid_summary["ir"].idxmax())
        best_ir = float(valid_summary["ir"].max())

    if best_ir > IR_PROCEED_THRESHOLD:
        verdict = f"✅ **PROCEED** — IR={best_ir:.3f} > {IR_PROCEED_THRESHOLD} ({best_lb_label} 채택)"
        decision = "Part 2 진행: `backtest_lowvol_weights_s7.py` 실행"
    elif np.isnan(best_ir) or best_ir <= IR_ABORT_THRESHOLD:
        verdict = f"❌ **ABORT** — IR={best_ir:.3f} ≤ {IR_ABORT_THRESHOLD} → Low-vol 팩터 폐기"
        decision = "다른 팩터 후보 탐색 필요"
    else:
        verdict = f"⚠️ **MARGINAL** — IR={best_ir:.3f} (0~{IR_PROCEED_THRESHOLD}) → 신중한 검토 권장"
        decision = f"Part 2 조건부 진행 가능 ({best_lb_label} 기준)"

    # 분기별 IC 표 (전치: row=period, col=lookback)
    ic_display = ic_df[[f"lb{lb}" for lb in LOOKBACKS] + ["n_stocks"]].copy()
    ic_display.columns = [f"IC({lb}d)" for lb in LOOKBACKS] + ["N"]
    ic_table = ic_display.round(4).to_markdown()

    # 요약 표
    summary_display = summary_df.copy()
    summary_display.columns = ["Mean IC", "Std IC", "IR", "t-stat", "Hit Rate", "N"]
    summary_table = summary_display.round(4).to_markdown()

    lines = [
        "# S7 Low-Volatility 팩터 IC/IR 분석 (Part 1)",
        "",
        f"**분석 기간:** 2017Q1 ~ 2024Q4 ({len(ic_df)}분기)  ",
        f"**시장:** KOSPI  ",
        f"**유니버스:** F-Score≥4, Step1/3·S4 비활성, n=9999  ",
        f"**Lookback 비교:** {', '.join(str(lb) + 'd' for lb in LOOKBACKS)}  ",
        "",
        "---",
        "",
        "## 판정",
        "",
        verdict,
        "",
        f"**액션:** {decision}",
        "",
        f"> 최적 lookback: **{best_lb_label}**, IR: **{best_ir:.4f}**",
        "",
        "---",
        "",
        "## IR 요약",
        "",
        summary_table,
        "",
        "---",
        "",
        "## 분기별 IC 시계열",
        "",
        ic_table,
        "",
        "---",
        "",
        "## 해석 가이드",
        "",
        "| 지표 | 의미 |",
        "|------|------|",
        "| Mean IC | 분기 평균 Spearman 상관계수 (낮은 변동성 → 높은 수익률?) |",
        "| Std IC | IC 안정성 (낮을수록 예측력 일관) |",
        "| IR | 정보비율 = Mean IC / Std IC × √N (|IR| > 0.5 = 강한 신호) |",
        "| t-stat | 통계적 유의성 (|t| > 2.0 = p<0.05) |",
        "| Hit Rate | IC > 0인 분기 비율 (> 0.6 = 안정적 양의 예측력) |",
    ]
    return "\n".join(lines)


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging()

    logger.info("S7 Low-Volatility IC/IR 분석 시작 (lookbacks=%s)", LOOKBACKS)

    rebal_dates = get_rebal_dates(BACKTEST_START, BACKTEST_END)
    logger.info("분기 날짜: %d개 (기간: %d)", len(rebal_dates), len(rebal_dates) - 1)

    ic_df = collect_all(rebal_dates)

    if ic_df.empty:
        logger.error("IC 데이터 수집 실패 — 보고서 생성 불가")
        return

    summary_df = summarize_ic(ic_df)

    report = build_report(ic_df, summary_df)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("보고서 저장: %s", REPORT_PATH)

    # 콘솔 요약 출력
    print("\n" + "=" * 60)
    print("S7 Low-Volatility IC/IR 분석 결과")
    print("=" * 60)
    print(summary_df.round(4).to_string())
    print("=" * 60)

    # 판정 출력
    valid_summary = summary_df.dropna(subset=["ir"])
    if not valid_summary.empty:
        best_lb = valid_summary["ir"].idxmax()
        best_ir = float(valid_summary.loc[best_lb, "ir"])
        print(f"\n최적 lookback: {best_lb}  IR: {best_ir:.4f}")
        if best_ir > IR_PROCEED_THRESHOLD:
            print(f"→ PROCEED: Part 2 진행 (BEST_LOOKBACK={best_lb[:-1]}, LOWVOL_IR_BEST={best_ir:.4f})")
        elif best_ir <= IR_ABORT_THRESHOLD:
            print("→ ABORT: Low-vol 팩터 폐기")
        else:
            print(f"→ MARGINAL: 신중 검토 ({best_lb} 기준)")


if __name__ == "__main__":
    main()
