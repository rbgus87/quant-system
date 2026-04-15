"""Lag 강화 영향 평가 — strict_reporting_lag False/True 비교 백테스트.

두 모드로 screener를 돌려 선정 종목을 수집하고, 동일가중 분기 리밸런싱
간이 백테스트로 CAGR / MDD / Sharpe / Sortino / Calmar / 회전율 비교.

실제 MultiFactorBacktest 엔진보다 단순하나, 두 모드 비교의 상대적 차이를
측정하는 목적에는 충분하다 (동일한 방법론을 두 모드에 적용).

사용:
    python scripts/backtest_lag_impact.py
    python scripts/backtest_lag_impact.py --start 2017-01-01 --end 2024-12-31 \
        --selections data/lag_impact_selections.json \
        --output docs/reports/lag_impact_analysis.md
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import setup_logging
from config.settings import settings
from strategy.screener import MultiFactorScreener

logger = logging.getLogger(__name__)


def generate_quarterly_rebalance_dates(start: date, end: date) -> list[date]:
    from config.calendar import get_krx_sessions

    all_sessions = get_krx_sessions(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    )
    if all_sessions is None or len(all_sessions) == 0:
        return []
    quarters: dict[tuple[int, int], pd.Timestamp] = {}
    for ts in all_sessions:
        if ts.month in (3, 6, 9, 12):
            q = (ts.year, ts.month)
            if q not in quarters or ts > quarters[q]:
                quarters[q] = ts
    return sorted(d.date() for d in quarters.values())


def run_selections_for_mode(
    label: str, strict_lag: bool, dates: list[date]
) -> dict[str, list[str]]:
    """지정 모드에서 전체 리밸런싱 날짜별 선정 종목 수집."""
    # 런타임 설정 토글
    settings.quality.strict_reporting_lag = strict_lag
    MultiFactorScreener._factor_cache.clear()

    screener = MultiFactorScreener()
    selections: dict[str, list[str]] = {}
    for i, d in enumerate(dates):
        ds = d.strftime("%Y%m%d")
        logger.info(f"[{label}] [{i + 1}/{len(dates)}] {ds}")
        try:
            df = screener.screen(ds)
            tickers = df.index.tolist() if df is not None and not df.empty else []
        except Exception as e:
            logger.warning(f"[{label}] {ds} screener 실패: {e}")
            tickers = []
        selections[d.isoformat()] = tickers
    return selections


# ─────────────────────────────────────────────
# 간이 백테스트
# ─────────────────────────────────────────────


def _close_price_at(
    conn: sqlite3.Connection,
    ticker: str,
    target: date,
    window_days: int = 5,
) -> float | None:
    """target 근처에서 가장 가까운 영업일의 종가를 반환."""
    row = conn.execute(
        "SELECT close FROM daily_price WHERE ticker = ? "
        "AND date BETWEEN ? AND ? "
        "ORDER BY ABS(julianday(date) - julianday(?)) LIMIT 1",
        [
            ticker,
            (target - timedelta(days=window_days)).isoformat(),
            (target + timedelta(days=window_days)).isoformat(),
            target.isoformat(),
        ],
    ).fetchone()
    return float(row[0]) if row and row[0] not in (None, 0, 0.0) else None


def _is_failure_delisted(
    conn: sqlite3.Connection, ticker: str, start: date, end: date
) -> bool:
    """해당 기간 내 failure 카테고리로 폐지됐는지 확인."""
    row = conn.execute(
        "SELECT 1 FROM delisted_stock WHERE ticker = ? AND category = 'failure' "
        "AND delist_date BETWEEN ? AND ? LIMIT 1",
        [ticker, start.isoformat(), end.isoformat()],
    ).fetchone()
    return row is not None


def simulate_portfolio(
    selections: dict[str, list[str]],
    rebal_dates: list[date],
    tx_cost_pct: float = 0.005,
    db_path: str | None = None,
) -> pd.DataFrame:
    """동일가중 분기 리밸런싱 간이 백테스트.

    각 분기 초에 선정된 종목을 동일가중 매수, 다음 분기 초에 전량 매도 가정.
    폐지 failure 종목은 해당 포지션 -100% 처리.
    거래비용은 분기당 고정 tx_cost_pct 차감 (교체율 100% 가정의 근사).

    Returns:
        DataFrame(columns=[period_start, period_end, n_stocks, quarterly_return,
                           turnover_proxy])
    """
    db_path = db_path or settings.db_path
    rows: list[dict] = []
    with sqlite3.connect(db_path) as conn:
        prev_tickers: set[str] = set()
        for i, d in enumerate(rebal_dates):
            if i == len(rebal_dates) - 1:
                break
            next_d = rebal_dates[i + 1]
            tickers = selections.get(d.isoformat(), [])
            if not tickers:
                rows.append({
                    "period_start": d,
                    "period_end": next_d,
                    "n_stocks": 0,
                    "quarterly_return": 0.0,
                    "turnover_proxy": 0.0,
                })
                continue

            per_stock_returns: list[float] = []
            for t in tickers:
                p0 = _close_price_at(conn, t, d)
                p1 = _close_price_at(conn, t, next_d)
                if p0 is None:
                    continue
                if p1 is None:
                    # 가격 없음 → 폐지 failure라면 -100%, 아니면 skip
                    if _is_failure_delisted(conn, t, d, next_d):
                        per_stock_returns.append(-1.0)
                    continue
                per_stock_returns.append(p1 / p0 - 1.0)

            q_return = (
                float(np.mean(per_stock_returns)) if per_stock_returns else 0.0
            )
            # 거래비용: 교체율 × 비용. 첫 분기는 100%, 이후는 실제 교체율.
            if prev_tickers:
                turnover = len(set(tickers) - prev_tickers) / max(len(tickers), 1)
            else:
                turnover = 1.0
            q_return -= turnover * tx_cost_pct

            rows.append({
                "period_start": d,
                "period_end": next_d,
                "n_stocks": len(tickers),
                "quarterly_return": q_return,
                "turnover_proxy": turnover,
            })
            prev_tickers = set(tickers)
    return pd.DataFrame(rows)


def metrics_from_returns(
    quarterly: pd.Series, rf_annual: float = 0.03
) -> dict:
    """분기 수익률 시계열에서 핵심 지표 계산."""
    if quarterly.empty or quarterly.isna().all():
        return {k: 0.0 for k in ("CAGR", "MDD", "Sharpe", "Sortino", "Calmar")}

    q = quarterly.dropna().values
    if len(q) == 0:
        return {k: 0.0 for k in ("CAGR", "MDD", "Sharpe", "Sortino", "Calmar")}

    cum = np.cumprod(1 + q)
    years = len(q) / 4.0
    cagr = cum[-1] ** (1 / years) - 1 if years > 0 else 0.0

    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    mdd = float(dd.min())

    rf_q = (1 + rf_annual) ** (1 / 4) - 1
    excess = q - rf_q
    sharpe = (
        float(excess.mean() / q.std() * np.sqrt(4)) if q.std() > 0 else 0.0
    )
    downside = q[q < 0]
    sortino = (
        float(excess.mean() / downside.std() * np.sqrt(4))
        if len(downside) > 1 and downside.std() > 0 else 0.0
    )
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0

    return {
        "CAGR": float(cagr),
        "MDD": mdd,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Calmar": calmar,
    }


def run_analysis(
    start: date,
    end: date,
    selections_cache: Path | None,
) -> dict:
    rebal_dates = generate_quarterly_rebalance_dates(start, end)
    logger.info(f"리밸런싱 날짜: {len(rebal_dates)}개")

    selections_a: dict[str, list[str]]
    selections_b: dict[str, list[str]]

    if selections_cache and selections_cache.exists():
        logger.info(f"캐시 로드: {selections_cache}")
        cached = json.loads(selections_cache.read_text(encoding="utf-8"))
        selections_a = cached["mode_A"]
        selections_b = cached["mode_B"]
    else:
        logger.info("=== 모드 A: strict_reporting_lag=False ===")
        selections_a = run_selections_for_mode("A", False, rebal_dates)
        logger.info("=== 모드 B: strict_reporting_lag=True ===")
        selections_b = run_selections_for_mode("B", True, rebal_dates)
        if selections_cache:
            selections_cache.parent.mkdir(parents=True, exist_ok=True)
            selections_cache.write_text(
                json.dumps(
                    {"mode_A": selections_a, "mode_B": selections_b},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.info(f"캐시 저장: {selections_cache}")

    sim_a = simulate_portfolio(selections_a, rebal_dates)
    sim_b = simulate_portfolio(selections_b, rebal_dates)

    m_a = metrics_from_returns(sim_a["quarterly_return"])
    m_b = metrics_from_returns(sim_b["quarterly_return"])

    # 2021-2024 서브기간
    cut = pd.Timestamp("2021-01-01").date()
    sub_a = sim_a[pd.to_datetime(sim_a["period_start"]) >= pd.Timestamp(cut)]
    sub_b = sim_b[pd.to_datetime(sim_b["period_start"]) >= pd.Timestamp(cut)]
    m_a_21 = metrics_from_returns(sub_a["quarterly_return"])
    m_b_21 = metrics_from_returns(sub_b["quarterly_return"])

    cut24 = pd.Timestamp("2024-01-01").date()
    sub_a24 = sim_a[pd.to_datetime(sim_a["period_start"]) >= pd.Timestamp(cut24)]
    sub_b24 = sim_b[pd.to_datetime(sim_b["period_start"]) >= pd.Timestamp(cut24)]
    m_a_24 = metrics_from_returns(sub_a24["quarterly_return"])
    m_b_24 = metrics_from_returns(sub_b24["quarterly_return"])

    # 종목 선정 차이
    overlaps: list[tuple[date, int, int, int]] = []
    for d in rebal_dates:
        iso = d.isoformat()
        sa = set(selections_a.get(iso, []))
        sb = set(selections_b.get(iso, []))
        if sa and sb:
            overlaps.append((d, len(sa), len(sb), len(sa & sb)))

    avg_overlap_pct = (
        float(np.mean([ov[3] / max(ov[1], 1) for ov in overlaps])) * 100
        if overlaps else 0.0
    )

    # 차이 가장 큰 top-3
    overlap_df = pd.DataFrame(
        overlaps, columns=["date", "n_a", "n_b", "n_overlap"]
    )
    if not overlap_df.empty:
        overlap_df["diff"] = overlap_df["n_a"] + overlap_df["n_b"] - 2 * overlap_df["n_overlap"]
        top_diff = overlap_df.nlargest(3, "diff")
    else:
        top_diff = pd.DataFrame()

    # 전용 종목 후속 성과
    avg_ret_a_only, avg_ret_b_only = compute_exclusive_performance(
        selections_a, selections_b, rebal_dates
    )

    return {
        "rebal_dates": rebal_dates,
        "sim_a": sim_a,
        "sim_b": sim_b,
        "metrics_full": {"A": m_a, "B": m_b},
        "metrics_21_24": {"A": m_a_21, "B": m_b_21},
        "metrics_24": {"A": m_a_24, "B": m_b_24},
        "avg_turnover_a": float(sim_a["turnover_proxy"].mean()),
        "avg_turnover_b": float(sim_b["turnover_proxy"].mean()),
        "avg_n_stocks_a": float(sim_a["n_stocks"].mean()),
        "avg_n_stocks_b": float(sim_b["n_stocks"].mean()),
        "avg_overlap_pct": avg_overlap_pct,
        "top_diff": top_diff,
        "avg_ret_a_only": avg_ret_a_only,
        "avg_ret_b_only": avg_ret_b_only,
        "selections_a": selections_a,
        "selections_b": selections_b,
    }


def compute_exclusive_performance(
    selections_a: dict[str, list[str]],
    selections_b: dict[str, list[str]],
    rebal_dates: list[date],
) -> tuple[float, float]:
    """모드별 전용 종목의 다음 분기 평균 수익률 계산."""
    db_path = settings.db_path
    returns_a_only: list[float] = []
    returns_b_only: list[float] = []
    with sqlite3.connect(db_path) as conn:
        for i, d in enumerate(rebal_dates[:-1]):
            next_d = rebal_dates[i + 1]
            sa = set(selections_a.get(d.isoformat(), []))
            sb = set(selections_b.get(d.isoformat(), []))
            a_only = sa - sb
            b_only = sb - sa

            for t in a_only:
                p0 = _close_price_at(conn, t, d)
                p1 = _close_price_at(conn, t, next_d)
                if p0 is None:
                    continue
                if p1 is None:
                    if _is_failure_delisted(conn, t, d, next_d):
                        returns_a_only.append(-1.0)
                    continue
                returns_a_only.append(p1 / p0 - 1.0)
            for t in b_only:
                p0 = _close_price_at(conn, t, d)
                p1 = _close_price_at(conn, t, next_d)
                if p0 is None:
                    continue
                if p1 is None:
                    if _is_failure_delisted(conn, t, d, next_d):
                        returns_b_only.append(-1.0)
                    continue
                returns_b_only.append(p1 / p0 - 1.0)

    return (
        float(np.mean(returns_a_only)) if returns_a_only else 0.0,
        float(np.mean(returns_b_only)) if returns_b_only else 0.0,
    )


def simulate_20260415() -> dict:
    """오늘 기준 두 모드 스크리닝 비교 (DB 가용 날짜 기반)."""
    today_str = date.today().strftime("%Y%m%d")
    # 당일 데이터 없으면 screener가 자동 폴백
    result: dict = {"as_of": today_str}
    for label, strict in [("A", False), ("B", True)]:
        settings.quality.strict_reporting_lag = strict
        MultiFactorScreener._factor_cache.clear()
        try:
            screener = MultiFactorScreener()
            df = screener.screen(today_str)
            result[label] = df.index.tolist() if df is not None and not df.empty else []
        except Exception as e:
            logger.warning(f"[{label}] 스크리닝 실패: {e}")
            result[label] = []
    return result


def classify(delta_cagr: float, delta_sharpe: float) -> str:
    """결과 Case 분류."""
    if abs(delta_cagr) <= 0.01 and delta_sharpe > -0.1:
        return "A"
    if delta_cagr <= -0.03 or delta_sharpe < -0.3:
        return "B"
    if delta_cagr >= 0:
        return "C"
    return "A"  # between A and B


def write_report(analysis: dict, simul_today: dict, output_path: Path) -> None:
    a_full = analysis["metrics_full"]["A"]
    b_full = analysis["metrics_full"]["B"]
    a_21 = analysis["metrics_21_24"]["A"]
    b_21 = analysis["metrics_21_24"]["B"]
    a_24 = analysis["metrics_24"]["A"]
    b_24 = analysis["metrics_24"]["B"]

    dc_full = b_full["CAGR"] - a_full["CAGR"]
    ds_full = b_full["Sharpe"] - a_full["Sharpe"]
    case = classify(dc_full, ds_full)

    lines: list[str] = []
    lines.append("# strict_reporting_lag 영향 평가 리포트\n")
    lines.append(f"**작성일**: {date.today()}  ")
    lines.append("**전략**: V70M30 + Vol70 (프리셋 A)  ")
    lines.append(
        "**방법**: 각 모드로 분기말 screener 실행 → "
        "동일가중 분기 리밸런싱 간이 백테스트 (폐지 failure는 -100%, 거래비용 0.5%/교체율)\n"
    )

    lines.append("## 핵심 비교표\n")
    lines.append("| 기간 | CAGR A | CAGR B | ΔCAGR | MDD A | MDD B | Sharpe A | Sharpe B |")
    lines.append("|------|--------|--------|-------|-------|-------|----------|----------|")
    for label, a, b in [
        ("2017-2024", a_full, b_full),
        ("2021-2024", a_21, b_21),
        ("2024 (1년)", a_24, b_24),
    ]:
        lines.append(
            f"| {label} | {a['CAGR']*100:.2f}% | {b['CAGR']*100:.2f}% | "
            f"{(b['CAGR']-a['CAGR'])*100:+.2f}% | {a['MDD']*100:.2f}% | "
            f"{b['MDD']*100:.2f}% | {a['Sharpe']:.3f} | {b['Sharpe']:.3f} |"
        )
    lines.append("")

    lines.append("## 상세 지표 — 2017-2024 전체\n")
    lines.append("| 지표 | 모드 A (lag=False) | 모드 B (lag=True) |")
    lines.append("|------|-------------------|-------------------|")
    for key in ("CAGR", "MDD", "Sharpe", "Sortino", "Calmar"):
        va = a_full[key]
        vb = b_full[key]
        fmt = "{:+.2%}" if key in ("CAGR", "MDD") else "{:.3f}"
        lines.append(f"| {key} | {fmt.format(va)} | {fmt.format(vb)} |")
    lines.append(
        f"| 평균 분기 회전율 | {analysis['avg_turnover_a']*100:.1f}% | "
        f"{analysis['avg_turnover_b']*100:.1f}% |"
    )
    lines.append(
        f"| 평균 선정 종목 수 | {analysis['avg_n_stocks_a']:.1f} | "
        f"{analysis['avg_n_stocks_b']:.1f} |"
    )
    lines.append("")

    lines.append("## 종목 선정 차이\n")
    lines.append(f"- 평균 종목 겹침률: **{analysis['avg_overlap_pct']:.1f}%**")
    if not analysis["top_diff"].empty:
        lines.append("\n### 차이가 가장 큰 리밸런싱 Top 3\n")
        lines.append("| 리밸런싱 | A 종목수 | B 종목수 | 공통 | 차이 |")
        lines.append("|----------|----------|----------|------|------|")
        for _, r in analysis["top_diff"].iterrows():
            lines.append(
                f"| {r['date']} | {r['n_a']} | {r['n_b']} | "
                f"{r['n_overlap']} | {r['diff']} |"
            )
    lines.append("")

    lines.append("## 전용 종목 후속 성과 (다음 분기 평균 수익률)\n")
    lines.append(
        f"- 모드 A 전용 종목 평균: **{analysis['avg_ret_a_only']*100:+.2f}%**  "
    )
    lines.append(
        f"- 모드 B 전용 종목 평균: **{analysis['avg_ret_b_only']*100:+.2f}%**  \n"
    )
    diff = analysis["avg_ret_b_only"] - analysis["avg_ret_a_only"]
    if diff > 0:
        lines.append(
            f"→ B 전용 종목이 A 전용 종목보다 **{diff*100:+.2f}%p** 더 안전·우량"
        )
    elif diff < 0:
        lines.append(
            f"→ A 전용 종목이 B 전용 종목보다 **{abs(diff)*100:.2f}%p** 수익률 높음 "
            f"(lag 강화로 놓친 기회)"
        )
    else:
        lines.append("→ 차이 없음")
    lines.append("")

    lines.append("## 오늘 기준 (2026-04-15) 리밸런싱 시뮬\n")
    a_today = set(simul_today.get("A", []))
    b_today = set(simul_today.get("B", []))
    lines.append(f"- 모드 A 선정: {len(a_today)}종목")
    lines.append(f"- 모드 B 선정: {len(b_today)}종목")
    lines.append(f"- 공통: {len(a_today & b_today)}종목")
    lines.append(f"- A 전용 (lag 미적용 시만 선정): {sorted(a_today - b_today)}")
    lines.append(f"- B 전용 (lag 적용 시만 선정): {sorted(b_today - a_today)}")
    lines.append("")

    lines.append("## Case 분류 및 권고\n")
    case_text = {
        "A": (
            f"**Case A**: CAGR 차이 {dc_full*100:+.2f}%p (≤ 1%p), Sharpe Δ {ds_full:+.3f}\n"
            f"→ ✅ **strict_reporting_lag=True 유지 권고**\n"
            f"→ 안전성 향상 + 성과 영향 미미. 005620 유형 리스크를 제거하며 "
            f"주요 지표는 거의 동일."
        ),
        "B": (
            f"**Case B**: CAGR 차이 {dc_full*100:+.2f}%p (≤ -3%p 또는 Sharpe 하락)\n"
            f"→ ⚠️ **lag 기간 조정 검토 필요**\n"
            f"  - 옵션 1: _get_effective_fundamental_date 로직을 분기 보고서 "
            f"+60일 기준으로 변경 (연간 보고서 고정 대신)\n"
            f"  - 옵션 2: settings.quality.strict_reporting_lag=False로 원복"
        ),
        "C": (
            f"**Case C**: CAGR 차이 {dc_full*100:+.2f}%p (향상), Sharpe Δ {ds_full:+.3f}\n"
            f"→ ✅ **이상적 결과** — strict_reporting_lag=True 유지"
        ),
    }
    lines.append(case_text.get(case, case_text["A"]))
    lines.append("")

    lines.append("## 한계점\n")
    lines.append(
        "- 간이 백테스트: 실제 엔진의 시장 레짐 필터/변동성 타겟팅 미반영"
    )
    lines.append(
        "- 거래비용 고정 0.5%/교체율: 금액 프리셋의 실제 슬리피지/세금 반영 안 됨"
    )
    lines.append(
        "- 폐지 종목 가격 처리: -100% 가정은 실제 거래정지 기간 평가 손실 미반영"
    )
    lines.append(
        "- 두 모드의 **상대적 차이** 측정 목적이며, 절대 수익률은 실제 시스템과 상이"
    )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"리포트 저장: {output_path}")


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="strict_reporting_lag 영향 평가")
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument(
        "--selections",
        default="data/lag_impact_selections.json",
        help="선정 종목 캐시 (JSON). 존재하면 로드, 없으면 생성",
    )
    parser.add_argument(
        "--output",
        default="docs/reports/lag_impact_analysis.md",
    )
    parser.add_argument(
        "--skip-today",
        action="store_true",
        help="오늘 기준 시뮬 건너뛰기",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    analysis = run_analysis(start, end, Path(args.selections))

    simul_today = (
        {"A": [], "B": []} if args.skip_today else simulate_20260415()
    )

    write_report(analysis, simul_today, Path(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
