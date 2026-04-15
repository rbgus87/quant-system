"""F-Score 필터 효과 검증 — 폐지 종목의 폐지 1년 전 F-Score 분포 분석.

가설: F-Score 4점 이상 필터를 통과한 종목은 향후 폐지(failure) 확률이 낮다.

절차:
1. `delisted_stock` 테이블에서 category='failure' 종목 추출 (2017-2024 기간)
2. 각 종목의 폐지 1년 전(±60일 윈도우) 시점에서 펀더멘털 조회
3. `QualityFactor.calc_fscore()`로 F-Score 계산
4. F-Score 분포 확인:
   - 4점 이상 비율 = 필터를 통과했을 비율 = "필터 돌파 확률"
5. 결론:
   - 돌파 확률 < 20%: 필터 효과적
   - 돌파 확률 >= 20%: 필터 기준 강화 필요

출력: `docs/reports/fscore_validation.md`
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import setup_logging
from data.storage import DataStorage

logger = logging.getLogger(__name__)


def load_fundamentals_near(
    storage: DataStorage,
    ticker: str,
    target_date: date,
    window_days: int = 60,
) -> pd.DataFrame:
    """target_date ±window_days 윈도우 내 가장 가까운 펀더멘털 스냅샷 반환.

    Returns:
        DataFrame (index=[ticker], columns=EPS/BPS/PER/PBR/DIV) — 없으면 empty
    """
    start = target_date - timedelta(days=window_days)
    end = target_date + timedelta(days=window_days)

    import sqlalchemy as sa

    with storage.engine.connect() as conn:
        query = sa.text(
            "SELECT date, ticker, per, pbr, eps, bps, div "
            "FROM fundamental WHERE ticker = :ticker "
            "AND date BETWEEN :start AND :end "
            "ORDER BY ABS(julianday(date) - julianday(:target)) LIMIT 1"
        )
        row = conn.execute(
            query,
            {
                "ticker": ticker,
                "start": start,
                "end": end,
                "target": target_date,
            },
        ).fetchone()

    if row is None:
        return pd.DataFrame()

    return pd.DataFrame(
        [{
            "EPS": row[4] if row[4] is not None else np.nan,
            "BPS": row[5] if row[5] is not None else np.nan,
            "PER": row[2] if row[2] is not None else np.nan,
            "PBR": row[3] if row[3] is not None else np.nan,
            "DIV": row[6] if row[6] is not None else 0,
        }],
        index=[ticker],
    )


def compute_fscore_for_delisted(
    storage: DataStorage,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """기간 내 failure 폐지 종목의 폐지 1년 전 F-Score 계산.

    Returns:
        DataFrame(columns=[ticker, name, delist_date, snapshot_date,
                           fscore, data_available])
    """
    from factors.quality import QualityFactor

    failures = storage.load_delisted_stocks(
        start_date=start_date, end_date=end_date, category="failure"
    )
    if failures.empty:
        return pd.DataFrame()

    logger.info(f"failure 폐지 종목: {len(failures)}개")

    rows: list[dict] = []
    skipped = 0
    for _, r in failures.iterrows():
        target_date = r["delist_date"] - timedelta(days=365)
        fund_df = load_fundamentals_near(storage, r["ticker"], target_date)

        if fund_df.empty:
            skipped += 1
            rows.append({
                "ticker": r["ticker"],
                "name": r["name"],
                "delist_date": r["delist_date"],
                "snapshot_date": None,
                "fscore": None,
                "data_available": False,
            })
            continue

        fscore_series = QualityFactor.calc_fscore(fund_df)
        fscore_val = int(fscore_series.iloc[0]) if not fscore_series.empty else None

        rows.append({
            "ticker": r["ticker"],
            "name": r["name"],
            "delist_date": r["delist_date"],
            "snapshot_date": target_date,
            "fscore": fscore_val,
            "data_available": True,
        })

    logger.info(f"F-Score 계산 완료: 데이터 있음 {len(failures) - skipped}개, 없음 {skipped}개")
    return pd.DataFrame(rows)


def analyze_distribution(df: pd.DataFrame) -> dict:
    """F-Score 분포 및 통계 계산."""
    valid = df[df["data_available"] & df["fscore"].notna()]
    if valid.empty:
        return {"n_total": len(df), "n_with_data": 0}

    dist = valid["fscore"].value_counts().sort_index().to_dict()
    n_pass_filter = int((valid["fscore"] >= 4).sum())
    n_valid = len(valid)
    pass_rate = n_pass_filter / n_valid if n_valid else 0.0

    return {
        "n_total": len(df),
        "n_with_data": n_valid,
        "n_no_data": int(len(df) - n_valid),
        "distribution": {int(k): int(v) for k, v in dist.items()},
        "mean_fscore": float(valid["fscore"].mean()),
        "median_fscore": float(valid["fscore"].median()),
        "n_pass_min4": n_pass_filter,
        "pass_rate_min4": pass_rate,
    }


def write_report(
    start: date,
    end: date,
    df: pd.DataFrame,
    stats: dict,
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# F-Score 필터 효과 검증 리포트\n")
    lines.append(f"**기간**: {start} ~ {end}  ")
    lines.append(f"**분석 기준일**: {date.today()}  ")
    lines.append(
        "**방법**: failure 폐지 종목의 폐지 1년 전 시점 F-Score 계산 후 "
        "4점 이상 비율(=필터 돌파율) 산출  \n"
    )

    lines.append("## 결과\n")
    if stats.get("n_with_data", 0) == 0:
        lines.append("- 분석 가능한 데이터가 없음. 펀더멘털 DB를 확인하세요.")
        lines.append("")
    else:
        lines.append(f"- 전체 failure 폐지 종목: **{stats['n_total']}개**")
        lines.append(
            f"- 분석 가능 (폐지 1년 전 펀더멘털 확보): "
            f"**{stats['n_with_data']}개** "
            f"(데이터 부재: {stats['n_no_data']}개)"
        )
        lines.append(f"- 평균 F-Score: **{stats['mean_fscore']:.2f}**")
        lines.append(f"- 중앙값 F-Score: **{stats['median_fscore']:.0f}**")
        lines.append(
            f"- F-Score 4점 이상 (= min_fscore=4 필터 통과): "
            f"**{stats['n_pass_min4']}개 "
            f"({stats['pass_rate_min4'] * 100:.1f}%)**\n"
        )

        lines.append("## F-Score 분포\n")
        lines.append("| F-Score | 건수 |")
        lines.append("|---------|------|")
        for score in sorted(stats["distribution"].keys()):
            n = stats["distribution"][score]
            marker = " ← 필터 통과" if score >= 4 else ""
            lines.append(f"| {score} | {n}{marker} |")
        lines.append("")

        lines.append("## 결론\n")
        pass_rate_pct = stats["pass_rate_min4"] * 100
        if pass_rate_pct < 20:
            lines.append(
                f"✅ **F-Score 4점 필터는 효과적이다.**"
            )
            lines.append(
                f"  - 폐지 종목의 {pass_rate_pct:.1f}%만이 필터를 통과했을 것 "
                f"(= 대부분 사전 배제)"
            )
            lines.append(
                f"  - 목표 기준 (20% 미만) 달성 — 현재 min_fscore=4 유지 권고"
            )
        else:
            lines.append(
                f"⚠️ **F-Score 필터 기준 강화 권고.**"
            )
            lines.append(
                f"  - 폐지 종목의 {pass_rate_pct:.1f}%가 필터를 통과 — "
                f"목표 기준 (20% 미만) 미달"
            )
            lines.append(
                "  - min_fscore=5로 상향하거나 추가 재무 필터 검토 필요"
            )
        lines.append("")

    lines.append("## 한계점\n")
    lines.append("- 현 F-Score는 5점 만점 간소화 버전 (원본 피오트로스키는 9점)")
    lines.append(
        "- 폐지 1년 전 펀더멘털이 DART 등에서 확보 안 되는 종목은 제외되어 "
        "표본 편향 가능성 있음"
    )
    lines.append(
        "- F-Score는 **필터일 뿐**이며 폐지를 100% 예측하지 못함 — "
        "다른 리스크 관리(관리종목 알림, 드로다운 모니터링)와 병행 필요"
    )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"리포트 저장: {output_path}")


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="F-Score 필터 효과 검증")
    parser.add_argument("--start", default="2017-01-01", help="시작 폐지일")
    parser.add_argument("--end", default="2024-12-31", help="종료 폐지일")
    parser.add_argument(
        "--output",
        default="docs/reports/fscore_validation.md",
        help="리포트 저장 경로",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    logger.info(f"분석 범위: {start} ~ {end}")

    storage = DataStorage()
    df = compute_fscore_for_delisted(storage, start, end)
    stats = analyze_distribution(df)

    logger.info(f"통계: {stats}")
    write_report(start, end, df, stats, Path(args.output))

    return 0


if __name__ == "__main__":
    sys.exit(main())
