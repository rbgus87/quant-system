# check_data.py — 수집된 데이터 현황 확인
"""
사용법:
  python check_data.py                    # 전체 요약
  python check_data.py --table daily_price --ticker 005930
  python check_data.py --table fundamental --date 2024-10-31
  python check_data.py --table market_cap --date 2024-10-31 --top 10
"""

import argparse
import sqlite3
import sys

DB_PATH = "data/quant.db"


def show_summary(cur: sqlite3.Cursor) -> None:
    """전체 데이터 요약"""
    print("=" * 60)
    print("  수집 데이터 현황 (data/quant.db)")
    print("=" * 60)

    tables = {
        "daily_price": "일봉 OHLCV",
        "fundamental": "펀더멘털 (EPS/BPS/PER/PBR)",
        "market_cap": "시가총액",
        "factor_score": "팩터 스코어",
        "portfolio": "포트폴리오",
        "trade": "거래 이력",
    }

    for table, desc in tables.items():
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            if count > 0:
                cur.execute(
                    f"SELECT MIN(date), MAX(date), COUNT(DISTINCT ticker) FROM {table}"
                )
                row = cur.fetchone()
                print(
                    f"\n  [{desc}] {table}"
                    f"\n    건수: {count:,}건"
                    f"\n    기간: {row[0]} ~ {row[1]}"
                    f"\n    종목: {row[2]}개"
                )
            else:
                print(f"\n  [{desc}] {table}: 데이터 없음")
        except Exception:
            print(f"\n  [{desc}] {table}: 테이블 없음")

    # 펀더멘털 유효성
    cur.execute("SELECT COUNT(*) FROM fundamental")
    total = cur.fetchone()[0]
    if total > 0:
        print(f"\n  [펀더멘털 유효성]")
        for col, label in [
            ("eps", "EPS"),
            ("bps", "BPS"),
            ("per", "PER"),
            ("pbr", "PBR"),
            ("div", "DIV"),
        ]:
            cur.execute(f"SELECT COUNT(*) FROM fundamental WHERE {col} IS NOT NULL")
            valid = cur.fetchone()[0]
            bar = "#" * int(valid / max(total, 1) * 30)
            print(f"    {label}: {valid:>7,} / {total:,} ({valid/max(total,1)*100:5.1f}%) {bar}")

    print("\n" + "=" * 60)


def show_daily_price(cur: sqlite3.Cursor, ticker: str, limit: int) -> None:
    """특정 종목 일봉 데이터"""
    cur.execute(
        "SELECT date, open, high, low, close, volume "
        "FROM daily_price WHERE ticker = ? ORDER BY date DESC LIMIT ?",
        (ticker, limit),
    )
    rows = cur.fetchall()
    if not rows:
        print(f"  {ticker}: 데이터 없음")
        return

    print(f"\n  [{ticker}] 일봉 데이터 (최근 {len(rows)}건)")
    print(f"  {'날짜':>12s}  {'시가':>10s}  {'고가':>10s}  {'저가':>10s}  {'종가':>10s}  {'거래량':>12s}")
    print("  " + "-" * 70)
    for r in rows:
        print(
            f"  {r[0]:>12s}  {r[1]:>10,.0f}  {r[2]:>10,.0f}  "
            f"{r[3]:>10,.0f}  {r[4]:>10,.0f}  {r[5]:>12,}"
        )


def show_fundamental(cur: sqlite3.Cursor, date: str, top: int) -> None:
    """특정 날짜 펀더멘털 데이터"""
    cur.execute(
        "SELECT ticker, eps, bps, per, pbr, div "
        "FROM fundamental WHERE date = ? AND per IS NOT NULL "
        "ORDER BY per ASC LIMIT ?",
        (date, top),
    )
    rows = cur.fetchall()
    if not rows:
        # PER 없는 데이터도 표시
        cur.execute(
            "SELECT ticker, eps, bps, per, pbr, div "
            "FROM fundamental WHERE date = ? LIMIT ?",
            (date, top),
        )
        rows = cur.fetchall()

    if not rows:
        print(f"  {date}: 펀더멘털 데이터 없음")
        # 가용 날짜 표시
        cur.execute(
            "SELECT DISTINCT date FROM fundamental ORDER BY date DESC LIMIT 10"
        )
        dates = [r[0] for r in cur.fetchall()]
        if dates:
            print(f"  가용 날짜: {', '.join(dates)}")
        return

    print(f"\n  [{date}] 펀더멘털 데이터 (상위 {len(rows)}건)")
    print(f"  {'종목':>8s}  {'EPS':>10s}  {'BPS':>12s}  {'PER':>8s}  {'PBR':>8s}  {'DIV':>6s}")
    print("  " + "-" * 62)
    for r in rows:
        eps = f"{r[1]:,.0f}" if r[1] else "N/A"
        bps = f"{r[2]:,.0f}" if r[2] else "N/A"
        per = f"{r[3]:.1f}" if r[3] else "N/A"
        pbr = f"{r[4]:.2f}" if r[4] else "N/A"
        div_val = f"{r[5]:.1f}" if r[5] else "N/A"
        print(f"  {r[0]:>8s}  {eps:>10s}  {bps:>12s}  {per:>8s}  {pbr:>8s}  {div_val:>6s}")


def show_market_cap(cur: sqlite3.Cursor, date: str, top: int) -> None:
    """특정 날짜 시가총액 상위"""
    cur.execute(
        "SELECT ticker, market_cap, shares FROM market_cap "
        "WHERE date = ? AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?",
        (date, top),
    )
    rows = cur.fetchall()
    if not rows:
        print(f"  {date}: 시가총액 데이터 없음")
        cur.execute(
            "SELECT DISTINCT date FROM market_cap ORDER BY date DESC LIMIT 10"
        )
        dates = [r[0] for r in cur.fetchall()]
        if dates:
            print(f"  가용 날짜: {', '.join(dates)}")
        return

    print(f"\n  [{date}] 시가총액 상위 {len(rows)}개")
    print(f"  {'종목':>8s}  {'시가총액(억)':>14s}  {'발행주식수':>15s}")
    print("  " + "-" * 45)
    for r in rows:
        cap_billion = r[1] / 100_000_000 if r[1] else 0
        print(f"  {r[0]:>8s}  {cap_billion:>14,.0f}  {r[2]:>15,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="수집 데이터 확인")
    parser.add_argument(
        "--table",
        choices=["daily_price", "fundamental", "market_cap"],
        help="조회할 테이블",
    )
    parser.add_argument("--ticker", type=str, help="종목코드 (daily_price용)")
    parser.add_argument("--date", type=str, help="기준 날짜 YYYY-MM-DD")
    parser.add_argument("--top", type=int, default=20, help="표시 건수 (기본: 20)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    if args.table is None:
        show_summary(cur)
    elif args.table == "daily_price":
        ticker = args.ticker or "005930"
        show_daily_price(cur, ticker, args.top)
    elif args.table == "fundamental":
        if not args.date:
            print("  --date YYYY-MM-DD 필수")
            sys.exit(1)
        show_fundamental(cur, args.date, args.top)
    elif args.table == "market_cap":
        if not args.date:
            print("  --date YYYY-MM-DD 필수")
            sys.exit(1)
        show_market_cap(cur, args.date, args.top)

    conn.close()


if __name__ == "__main__":
    main()
