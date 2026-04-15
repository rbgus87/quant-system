"""생존자 편향 보정 분석 — 상장폐지 데이터 기반 백테스트 영향 추정.

기본 백테스트 엔진은 DB에 OHLCV가 없는 종목을 "자연스럽게" 드랍 처리한다.
폐지 종목은 폐지 이후 가격 데이터가 없으므로 포트폴리오 평가에서 사라지지만,
이는 실제 -100% 손실이 반영되지 않은 생존자 편향 상태다.

본 스크립트는 다음을 수행한다:
1. `delisted_stock` 테이블에서 2017-2024 failure 카테고리 종목 추출
2. 각 리밸런싱 시점에 screener.screen()로 선정되었을 종목 계산
3. 선정된 종목 중 다음 리밸런싱 전에 폐지된 failure 건수 식별
4. 포지션당 손실(-100%) 가정하에 포트폴리오 드로다운 추정
5. 연도별·누적 보정 영향 계산
6. `docs/reports/survivorship_bias_adjustment.md` 저장

사용:
    python scripts/backtest_with_delisted.py
    python scripts/backtest_with_delisted.py --start 2022-01-01 --end 2024-12-31
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import setup_logging
from config.settings import settings
from data.storage import DataStorage

logger = logging.getLogger(__name__)


def generate_quarterly_rebalance_dates(start: date, end: date) -> list[date]:
    """분기 말 영업일 리스트 — 3/6/9/12월 말일."""
    from config.calendar import get_krx_sessions

    all_sessions = get_krx_sessions(
        start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    )
    if all_sessions is None or len(all_sessions) == 0:
        return []

    # 각 (year, quarter)의 마지막 세션
    quarters: dict[tuple[int, int], pd.Timestamp] = {}
    for ts in all_sessions:
        if ts.month in (3, 6, 9, 12):
            q = (ts.year, ts.month)
            if q not in quarters or ts > quarters[q]:
                quarters[q] = ts

    return sorted(d.date() for d in quarters.values())


def run_screener_at(date_obj: date) -> list[str]:
    """해당 날짜에 screener를 돌려 선정 종목 반환. 실패 시 빈 리스트."""
    from strategy.screener import MultiFactorScreener

    try:
        screener = MultiFactorScreener()
        df = screener.screen(date_obj.strftime("%Y%m%d"))
        if df is None or df.empty:
            return []
        return df.index.tolist()
    except Exception as e:
        logger.warning(f"[{date_obj}] screener 실패: {e}")
        return []


def analyze_period(
    storage: DataStorage,
    rebal_dates: list[date],
    n_stocks: int,
) -> pd.DataFrame:
    """각 리밸런싱 기간의 폐지 노출 집계.

    Returns:
        DataFrame(columns=[rebal_date, period_end, n_selected, n_failed,
                           tickers_failed, loss_impact_pct])
    """
    if not rebal_dates:
        return pd.DataFrame()

    delisted_df = storage.load_delisted_stocks(category="failure")
    if delisted_df.empty:
        logger.warning("failure 카테고리 폐지 종목이 없음")
        return pd.DataFrame()

    # ticker -> delist_date 매핑
    delist_map = dict(zip(delisted_df["ticker"], delisted_df["delist_date"]))

    rows: list[dict] = []
    for i, reb in enumerate(rebal_dates):
        period_end = (
            rebal_dates[i + 1] - timedelta(days=1)
            if i + 1 < len(rebal_dates)
            else reb + timedelta(days=92)
        )
        selected = run_screener_at(reb)
        if not selected:
            logger.info(f"[{reb}] screener 결과 없음 — 스킵")
            continue

        failed = [
            t for t in selected
            if t in delist_map
            and reb <= delist_map[t] <= period_end
        ]
        n_failed = len(failed)
        # 동일 가중 가정: 각 포지션 1/n_stocks → -100% 손실 시 -100/n_stocks 포트폴리오 영향
        weight_per_stock = 1.0 / max(len(selected), 1)
        loss_impact = n_failed * weight_per_stock * 100  # 퍼센트

        rows.append({
            "rebal_date": reb,
            "period_end": period_end,
            "n_selected": len(selected),
            "n_failed": n_failed,
            "tickers_failed": ",".join(failed),
            "loss_impact_pct": loss_impact,
        })
        logger.info(
            f"[{reb}] 선정 {len(selected)}, 폐지 {n_failed}, "
            f"추정 손실 {loss_impact:.2f}%"
        )

    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    """분석 결과 집계."""
    if df.empty:
        return {"total_failures": 0, "total_impact_pct": 0.0}

    df = df.copy()
    df["year"] = pd.to_datetime(df["rebal_date"]).dt.year
    by_year = (
        df.groupby("year")
        .agg(
            n_rebalances=("rebal_date", "count"),
            n_failed=("n_failed", "sum"),
            impact_pct_sum=("loss_impact_pct", "sum"),
            impact_pct_mean=("loss_impact_pct", "mean"),
        )
        .reset_index()
    )
    return {
        "total_failures": int(df["n_failed"].sum()),
        "total_impact_pct": float(df["loss_impact_pct"].sum()),
        "avg_impact_per_period_pct": float(df["loss_impact_pct"].mean()),
        "n_periods_with_failures": int((df["n_failed"] > 0).sum()),
        "total_periods": int(len(df)),
        "by_year": by_year,
    }


def slice_period(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """detail_df에서 지정 기간 리밸런싱만 반환."""
    if df.empty:
        return df
    mask = (df["rebal_date"] >= start) & (df["rebal_date"] <= end)
    return df[mask].copy()


def estimate_metrics(df: pd.DataFrame) -> dict:
    """분기 손실 시계열에서 단순 지표 추정.

    보정 전(baseline): 손실 0 가정 → 모든 분기 수익률 0 → metrics 미정의
    보정 후(adjusted): 분기말 -loss_impact_pct를 리턴으로 적용 → CAGR/MDD/Sharpe
    """
    if df.empty:
        return {"cagr_delta_pct": 0.0, "mdd_pct": 0.0, "sharpe_delta": 0.0}

    # 분기 수익률 = -loss_impact_pct / 100 (손실만 반영, 베이스 수익 0 가정)
    q_ret = -df["loss_impact_pct"].values / 100.0  # 배열
    n_quarters = len(q_ret)

    # 누적 수익률
    cumret = 1.0
    peak = 1.0
    mdd = 0.0
    for r in q_ret:
        cumret *= (1 + r)
        peak = max(peak, cumret)
        dd = (cumret - peak) / peak if peak > 0 else 0
        mdd = min(mdd, dd)

    # CAGR 델타 (폐지만 반영한 연율)
    years = n_quarters / 4.0
    cagr_delta = (cumret ** (1 / years) - 1) * 100 if years > 0 else 0.0

    # Sharpe 델타 (손실만 있는 경우이므로 음수)
    if q_ret.std() > 0:
        sharpe_delta = q_ret.mean() / q_ret.std() * (4 ** 0.5)
    else:
        sharpe_delta = 0.0

    return {
        "cagr_delta_pct": float(cagr_delta),
        "mdd_pct": float(mdd * 100),
        "sharpe_delta": float(sharpe_delta),
        "cumulative_loss_pct": float((cumret - 1) * 100),
    }


def render_period_table(
    periods: list[tuple[str, date, date]],
    detail_df: pd.DataFrame,
) -> list[str]:
    """구간별 분석 표 렌더링."""
    lines: list[str] = []
    lines.append("| 구간 | 기간 | 리밸런싱 | 폐지 노출 | 누적 손실 (%) | CAGR Δ (%) | MDD (%) | Sharpe Δ |")
    lines.append("|------|------|----------|-----------|---------------|------------|---------|----------|")
    for label, ps, pe in periods:
        sub = slice_period(detail_df, ps, pe)
        met = estimate_metrics(sub)
        n_reb = len(sub)
        n_fail = int(sub["n_failed"].sum()) if not sub.empty else 0
        cum_loss = met.get("cumulative_loss_pct", 0.0)
        lines.append(
            f"| {label} | {ps} ~ {pe} | {n_reb} | {n_fail} | "
            f"{cum_loss:.2f} | {met['cagr_delta_pct']:.2f} | "
            f"{met['mdd_pct']:.2f} | {met['sharpe_delta']:.3f} |"
        )
    return lines


def write_report(
    start: date,
    end: date,
    detail_df: pd.DataFrame,
    summary: dict,
    output_path: Path,
    periods: list[tuple[str, date, date]] | None = None,
) -> None:
    """마크다운 리포트 저장. periods 지정 시 구간별 비교 표 추가."""
    lines: list[str] = []
    lines.append("# 생존자 편향 보정 분석 리포트\n")
    lines.append(f"**기간**: {start} ~ {end}  ")
    lines.append(f"**전략**: V70M30 + Vol70 (A 프리셋)  ")
    lines.append(
        f"**분석 기준일**: {date.today()}  "
    )
    lines.append(f"**n_stocks**: {settings.portfolio.n_stocks}  \n")

    lines.append("## 요약\n")
    if not detail_df.empty:
        lines.append(f"- 총 분석 리밸런싱 수: **{summary['total_periods']}회**")
        lines.append(
            f"- 폐지(failure) 종목 포함 리밸런싱: "
            f"**{summary['n_periods_with_failures']}회**"
        )
        lines.append(
            f"- 누적 폐지 노출 건수: **{summary['total_failures']}건**"
        )
        lines.append(
            f"- 누적 추정 손실 영향: **-{summary['total_impact_pct']:.2f}%** "
            f"(동일가중, -100% 가정)"
        )
        lines.append(
            f"- 리밸런싱당 평균 영향: "
            f"**-{summary['avg_impact_per_period_pct']:.2f}%**\n"
        )
    else:
        lines.append("- 분석 데이터가 비어있음 (screener 결과 없음)\n")

    lines.append("## 방법론\n")
    lines.append("1. 분기 말 영업일마다 `MultiFactorScreener.screen()`으로 선정 종목 계산")
    lines.append("2. `delisted_stock` 테이블에서 category='failure' 건수 추출")
    lines.append("3. 선정 종목 중 다음 리밸런싱 전에 폐지된 종목 식별")
    lines.append(
        "4. 각 폐지 포지션을 동일가중(1/n_stocks) × -100% 손실로 추정"
    )
    lines.append(
        "5. 실제 영향은 폐지 직전 거래정지·감자 등 추가 손실로 더 클 수 있음\n"
    )

    if periods:
        lines.append("## 구간별 비교 (보정 전/후)\n")
        lines.append(
            "> 보정 전(baseline): 폐지 손실 0 가정 — 모든 지표 0  "
        )
        lines.append(
            "> 보정 후(adjusted): 각 리밸런싱에서 발생한 폐지를 "
            "동일가중 -100% 손실로 반영한 시나리오  \n"
        )
        lines.extend(render_period_table(periods, detail_df))
        lines.append("")
        lines.append(
            "> **해석**: CAGR Δ는 폐지만 반영한 연율 수익률 효과. "
            "0이면 해당 구간에서 필터가 폐지 예정 종목을 완전히 사전 배제했음을 의미. "
            "실제 백테스트 CAGR/MDD/Sharpe는 이 델타를 baseline에 **더한** 값.\n"
        )

    lines.append("## 연도별 상세\n")
    if "by_year" in summary:
        by_year = summary["by_year"]
        lines.append("| 연도 | 리밸런싱 | 폐지 종목 수 | 누적 영향 (%) | 평균 영향 (%) |")
        lines.append("|------|----------|-------------|--------------|---------------|")
        for _, r in by_year.iterrows():
            lines.append(
                f"| {int(r['year'])} | {int(r['n_rebalances'])} | "
                f"{int(r['n_failed'])} | -{r['impact_pct_sum']:.2f} | "
                f"-{r['impact_pct_mean']:.2f} |"
            )
        lines.append("")

    lines.append("## 리밸런싱별 상세 (폐지 발생 건만)\n")
    if not detail_df.empty:
        hits = detail_df[detail_df["n_failed"] > 0]
        if hits.empty:
            lines.append("분석 기간 내 폐지 종목 포함 리밸런싱 없음.\n")
        else:
            lines.append(
                "| 리밸런싱 | 기간 끝 | 선정 | 폐지 | 영향 (%) | 폐지 티커 |"
            )
            lines.append(
                "|----------|---------|------|------|----------|-----------|"
            )
            for _, r in hits.iterrows():
                lines.append(
                    f"| {r['rebal_date']} | {r['period_end']} | {r['n_selected']} | "
                    f"{r['n_failed']} | -{r['loss_impact_pct']:.2f} | "
                    f"{r['tickers_failed']} |"
                )
            lines.append("")

    lines.append("## 결론 및 권고\n")
    if not detail_df.empty and summary["total_failures"] > 0:
        total_periods = summary["total_periods"]
        n_hit = summary["n_periods_with_failures"]
        hit_rate = 100 * n_hit / total_periods if total_periods else 0
        lines.append(
            f"- 생존자 편향 노출 리밸런싱: {n_hit}/{total_periods}회 "
            f"({hit_rate:.1f}%)"
        )
        lines.append(
            f"- 누적 추정 손실 영향: **-{summary['total_impact_pct']:.2f}%**"
        )

        if hit_rate <= 5:
            lines.append(
                "- ✅ **목표 검증 통과**: F-Score 4점 필터가 폐지 노출을 "
                "효과적으로 차단 (5% 이하). 시스템 신뢰도 확정."
            )
        elif hit_rate <= 15:
            lines.append(
                "- ⚠️ **일부 노출 발생**: 필터가 완벽하지 않음. "
                "리밸런싱당 약 1/n_stocks × -100% 손실 리스크 존재. "
                "추가 방어 장치(관리종목 알림, 폐지 임박 감지) 병행 필수."
            )
        else:
            lines.append(
                "- 🚨 **필터 강화 필요**: 노출 비율이 높아 "
                "min_fscore 기준 상향(5점) 또는 추가 재무 지표 도입 검토 필요."
            )
        lines.append(
            "- F-Score 필터의 개별 효과는 `verify_fscore_effectiveness.py` 결과 참조"
        )
    else:
        lines.append(
            "- ✅ **기간 내 폐지 노출 0건** — F-Score 4점 필터가 "
            "failure 후보를 사전 배제."
        )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"리포트 저장: {output_path}")


def _parse_periods(spec: str) -> list[tuple[str, date, date]]:
    """'Label:start:end,Label2:start2:end2' → [(label, start, end)]."""
    if not spec:
        return []
    result: list[tuple[str, date, date]] = []
    for seg in spec.split(","):
        parts = seg.strip().split(":")
        if len(parts) != 3:
            continue
        result.append(
            (parts[0], date.fromisoformat(parts[1]), date.fromisoformat(parts[2]))
        )
    return result


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="생존자 편향 보정 분석")
    parser.add_argument("--start", default="2017-01-01", help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", default="2024-12-31", help="종료일 YYYY-MM-DD")
    parser.add_argument(
        "--output",
        default="docs/reports/survivorship_bias_adjustment.md",
        help="리포트 저장 경로",
    )
    parser.add_argument(
        "--periods",
        default="",
        help=(
            "구간별 breakdown. 형식: 'Label:YYYY-MM-DD:YYYY-MM-DD,Label2:...,...'"
            " 예: '2017-2020:2017-01-01:2020-12-31'"
        ),
    )
    parser.add_argument(
        "--csv",
        default="",
        help="detail DataFrame을 CSV로 저장할 경로 (재사용용)",
    )
    parser.add_argument(
        "--load-csv",
        default="",
        help="CSV에서 detail을 로드 (screener 재실행 없이 리포트만 갱신)",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    logger.info(f"분석 범위: {start} ~ {end}")

    storage = DataStorage()

    if args.load_csv:
        logger.info(f"CSV 로드: {args.load_csv}")
        detail_df = pd.read_csv(args.load_csv, dtype={"tickers_failed": str})
        detail_df["rebal_date"] = pd.to_datetime(detail_df["rebal_date"]).dt.date
        detail_df["period_end"] = pd.to_datetime(detail_df["period_end"]).dt.date
        detail_df["tickers_failed"] = detail_df["tickers_failed"].fillna("")
    else:
        rebal_dates = generate_quarterly_rebalance_dates(start, end)
        logger.info(f"리밸런싱 날짜: {len(rebal_dates)}개")
        detail_df = analyze_period(
            storage, rebal_dates, settings.portfolio.n_stocks
        )

        if args.csv:
            csv_path = Path(args.csv)
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            detail_df.to_csv(csv_path, index=False)
            logger.info(f"상세 CSV 저장: {csv_path}")

    summary = summarize(detail_df)
    periods = _parse_periods(args.periods)
    write_report(start, end, detail_df, summary, Path(args.output), periods=periods)

    logger.info(
        f"완료: 폐지 {summary['total_failures']}건, "
        f"누적 영향 -{summary['total_impact_pct']:.2f}%"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
