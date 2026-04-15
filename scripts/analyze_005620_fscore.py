"""005620 대성합동지주 F-Score 정밀 분해 — 폐지 사례 분석.

Context: backtest_with_delisted 결과 2017-2024 전 구간에서 F-Score 4점 필터를
통과한 유일한 failure 종목이 005620이었다. 본 스크립트는 해당 시점의 펀더멘털과
F-Score 5항목을 재현 가능하게 출력한다.

사용:
    python scripts/analyze_005620_fscore.py
    python scripts/analyze_005620_fscore.py --ticker 005620 --date 2017-06-30
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import setup_logging
from config.settings import settings
from factors.quality import QualityFactor

logger = logging.getLogger(__name__)


def load_universe_fundamentals(db_path: str, date_str: str) -> pd.DataFrame:
    """해당 날짜의 전체 KOSPI 펀더멘털을 DataFrame으로 반환."""
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT ticker, per, pbr, eps, bps, div FROM fundamental "
            "WHERE date = ?",
            conn,
            params=[date_str],
        )
    df.columns = ["ticker", "PER", "PBR", "EPS", "BPS", "DIV"]
    df = df.set_index("ticker")
    return df


def fetch_delist_record(db_path: str, ticker: str) -> dict | None:
    """delisted_stock 테이블에서 해당 ticker 레코드 조회."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT ticker, name, delist_date, reason, category, memo "
            "FROM delisted_stock WHERE ticker = ?",
            [ticker],
        ).fetchone()
    if row is None:
        return None
    return {
        "ticker": row[0],
        "name": row[1],
        "delist_date": row[2],
        "reason": row[3],
        "category": row[4],
        "memo": row[5],
    }


def fetch_price_timeline(
    db_path: str,
    ticker: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """기간 내 일별 OHLCV 반환."""
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume FROM daily_price "
            "WHERE ticker = ? AND date BETWEEN ? AND ? ORDER BY date",
            conn,
            params=[ticker, start.isoformat(), end.isoformat()],
        )
    return df


def fetch_monthly_fundamentals(
    db_path: str,
    ticker: str,
    year: int,
) -> pd.DataFrame:
    """해당 연도의 월말 펀더멘털 흐름 (재조정 추적용)."""
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT date, per, pbr, eps, bps FROM fundamental "
            "WHERE ticker = ? AND date BETWEEN ? AND ? ORDER BY date",
            conn,
            params=[ticker, f"{year}-01-01", f"{year}-12-31"],
        )
    return df


def fscore_breakdown(
    universe_df: pd.DataFrame,
    ticker: str,
) -> tuple[int, list[dict]]:
    """F-Score 5항목을 개별 계산하여 (총점, 항목 리스트) 반환."""
    if ticker not in universe_df.index:
        return 0, []

    row = universe_df.loc[ticker]
    eps_t = float(row["EPS"]) if pd.notna(row["EPS"]) else float("nan")
    bps_t = float(row["BPS"]) if pd.notna(row["BPS"]) else float("nan")
    per_t = float(row["PER"]) if pd.notna(row["PER"]) else float("nan")
    pbr_t = float(row["PBR"]) if pd.notna(row["PBR"]) else float("nan")
    div_t = float(row["DIV"]) if pd.notna(row["DIV"]) else 0.0

    roe_t = eps_t / bps_t if bps_t > 0 else float("nan")

    # 유니버스 중앙값
    bps = universe_df["BPS"]
    eps = universe_df["EPS"]
    valid_bps = bps > 0
    roe_series = pd.Series(float("nan"), index=universe_df.index)
    roe_series[valid_bps] = eps[valid_bps] / bps[valid_bps]
    roe_median = roe_series[roe_series.notna()].median()

    pbr_valid = universe_df["PBR"][universe_df["PBR"] > 0]
    pbr_median = pbr_valid.median() if not pbr_valid.empty else float("nan")

    items = [
        {
            "name": "1. 수익성 ROE > 0",
            "value": f"ROE={roe_t:.4f}",
            "pass": bool(roe_t > 0) if pd.notna(roe_t) else False,
        },
        {
            "name": "2. 흑자 PER > 0",
            "value": f"PER={per_t:.4f}",
            "pass": bool(per_t > 0) if pd.notna(per_t) else False,
        },
        {
            "name": "3. 배당 DIV > 0",
            "value": f"DIV={div_t}",
            "pass": bool(div_t > 0),
        },
        {
            "name": "4. 가치 PBR < 중앙값",
            "value": f"PBR={pbr_t:.4f} vs median={pbr_median:.4f}",
            "pass": bool(pbr_t > 0 and pbr_t < pbr_median)
            if pd.notna(pbr_t) and pd.notna(pbr_median) else False,
        },
        {
            "name": "5. 수익효율 ROE > 중앙값",
            "value": f"ROE={roe_t:.4f} vs median={roe_median:.4f}",
            "pass": bool(roe_t > roe_median)
            if pd.notna(roe_t) and pd.notna(roe_median) else False,
        },
    ]
    total = sum(1 for it in items if it["pass"])
    return total, items


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="005620 F-Score 5항목 분해")
    parser.add_argument("--ticker", default="005620")
    parser.add_argument("--date", default="2017-06-30", help="기준일 YYYY-MM-DD")
    args = parser.parse_args()

    db_path = settings.db_path
    universe_df = load_universe_fundamentals(db_path, args.date)
    if universe_df.empty:
        logger.error(f"[{args.date}] 유니버스 데이터 없음 — DB 상태 확인 필요")
        return 1

    print(f"분석 기준일: {args.date}")
    print(f"유니버스 크기: {len(universe_df)}개 KOSPI")
    print()

    # 폐지 정보
    print("=" * 60)
    print(f"[1] 폐지 레코드")
    print("=" * 60)
    delist = fetch_delist_record(db_path, args.ticker)
    if delist:
        for k, v in delist.items():
            print(f"  {k:<12}: {v}")
    else:
        print(f"  {args.ticker}: delisted_stock 테이블에 레코드 없음")
    print()

    # 펀더멘털 스냅샷
    print("=" * 60)
    print(f"[2] {args.ticker} 펀더멘털 스냅샷 ({args.date})")
    print("=" * 60)
    if args.ticker not in universe_df.index:
        print(f"  {args.ticker}: 해당 날짜 펀더멘털 없음")
        return 1
    row = universe_df.loc[args.ticker]
    for col in ["PER", "PBR", "EPS", "BPS", "DIV"]:
        v = row[col]
        if pd.isna(v):
            print(f"  {col:<5}: N/A")
        elif col in ("EPS", "BPS"):
            print(f"  {col:<5}: {v:,.0f}")
        else:
            print(f"  {col:<5}: {v:.4f}")
    print()

    # F-Score 분해
    print("=" * 60)
    print(f"[3] F-Score 5항목 분해")
    print("=" * 60)
    total, items = fscore_breakdown(universe_df, args.ticker)
    for it in items:
        mark = "+1" if it["pass"] else "+0"
        status = "PASS" if it["pass"] else "FAIL"
        print(f"  {it['name']:<28} | {it['value']:<40} | {mark} [{status}]")
    min_fscore = settings.quality.min_fscore
    filter_pass = total >= min_fscore
    print()
    print(
        f"  합계: {total}/5점 (min_fscore={min_fscore}) → "
        f"{'필터 통과' if filter_pass else '필터 배제'}"
    )
    print()

    # QualityFactor 결과와 대조 검증
    fscore_all = QualityFactor.calc_fscore(universe_df)
    fscore_val = int(fscore_all.loc[args.ticker])
    match = "일치" if fscore_val == total else f"불일치! ({fscore_val} vs 수동 {total})"
    print(f"  QualityFactor.calc_fscore() 결과: {fscore_val} → {match}")
    print()

    # 월별 재무 지표 변화
    year = int(args.date.split("-")[0])
    monthly = fetch_monthly_fundamentals(db_path, args.ticker, year)
    if not monthly.empty:
        print("=" * 60)
        print(f"[4] {year}년 월별 재무 지표 변화")
        print("=" * 60)
        print(f"  {'날짜':<12} {'PER':>8} {'PBR':>8} {'EPS':>12} {'BPS':>12}")
        for _, r in monthly.iterrows():
            per_s = f"{r['per']:.3f}" if pd.notna(r["per"]) else "N/A"
            pbr_s = f"{r['pbr']:.3f}" if pd.notna(r["pbr"]) else "N/A"
            eps_s = f"{r['eps']:,.0f}" if pd.notna(r["eps"]) else "N/A"
            bps_s = f"{r['bps']:,.0f}" if pd.notna(r["bps"]) else "N/A"
            print(f"  {r['date']:<12} {per_s:>8} {pbr_s:>8} {eps_s:>12} {bps_s:>12}")
        print()

    # 폐지 전 주가
    if delist and delist.get("delist_date"):
        print("=" * 60)
        print(f"[5] 폐지 전 주가 거동 (거래정지 감지)")
        print("=" * 60)
        dd = date.fromisoformat(delist["delist_date"]) if isinstance(delist["delist_date"], str) else delist["delist_date"]
        pre = fetch_price_timeline(db_path, args.ticker, dd - timedelta(days=30), dd)
        if not pre.empty:
            print(f"  {'날짜':<12} {'종가':>10} {'거래량':>12}")
            for _, r in pre.tail(15).iterrows():
                print(f"  {r['date']:<12} {r['close']:>10,.0f} {r['volume']:>12,}")
        print()

    print("=" * 60)
    print("진단")
    print("=" * 60)
    if filter_pass and delist and delist.get("category") == "failure":
        print("  A + C 복합: 계산상 F-Score는 올바르게 계산되었으나,")
        print("  재무제표 급변(분기 보고서 반영) 시점에 턱걸이 통과하여 필터 돌파.")
        print("  Reporting Lag 운용 강화 또는 min_fscore 상향 검토 필요.")
    elif not filter_pass:
        print("  B: F-Score 필터가 정상 작동 — 이 종목은 필터에 걸려 배제됨.")
    else:
        print("  정상 종목 또는 failure 이외 카테고리.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
