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


def write_report(
    start: date,
    end: date,
    detail_df: pd.DataFrame,
    summary: dict,
    output_path: Path,
) -> None:
    """마크다운 리포트 저장."""
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
        lines.append(
            f"- 분석 기간 동안 생존자 편향으로 과대추정된 누적 수익률은 약 "
            f"**{summary['total_impact_pct']:.2f}%** 수준"
        )
        lines.append(
            "- 월별/분기별 성과 리포트에서 이 영향을 고려해야 함"
        )
        lines.append(
            "- F-Score 4점 필터가 얼마나 이 종목들을 배제하는지는 "
            "`verify_fscore_effectiveness.py` 참조"
        )
    else:
        lines.append("- 기간 내 발생 폐지 건수가 적어 통계적 결론 보류")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"리포트 저장: {output_path}")


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
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    logger.info(f"분석 범위: {start} ~ {end}")

    rebal_dates = generate_quarterly_rebalance_dates(start, end)
    logger.info(f"리밸런싱 날짜: {len(rebal_dates)}개")

    storage = DataStorage()
    detail_df = analyze_period(storage, rebal_dates, settings.portfolio.n_stocks)
    summary = summarize(detail_df)

    write_report(start, end, detail_df, summary, Path(args.output))

    logger.info(
        f"완료: 폐지 {summary['total_failures']}건, "
        f"누적 영향 -{summary['total_impact_pct']:.2f}%"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
