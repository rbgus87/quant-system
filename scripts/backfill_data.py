"""backfill_data.py — 일별 데이터 공백 백필 스크립트.

scheduler의 `run_daily_data_collection` Job이 수집하는 것과 동일한
prefetch_daily_trade + get_fundamentals_all을 지정 기간에 대해 반복 호출하여
daily_price / market_cap / fundamental 테이블을 메운다.

사용:
    python scripts/backfill_data.py --start 20260331 --end 20260413
    python scripts/backfill_data.py --start 20260331 --end 20260413 --market KOSPI
    python scripts/backfill_data.py --missing-only        # 최근 30일에서 누락 날짜만
    python scripts/backfill_data.py --missing-only --start 20260101 --end 20260414
    python scripts/backfill_data.py --retry-failed logs/backfill_failed_20260414.txt

exit code:
    0 = 모든 날짜 성공
    1 = 하나라도 실패 (실패 목록은 logs/backfill_failed_YYYYMMDD.txt에 기록)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.calendar import get_krx_sessions, is_krx_business_day  # noqa: E402
from config.logging_config import setup_logging  # noqa: E402
from data.collector import KRXDataCollector  # noqa: E402
from data.storage import DataStorage  # noqa: E402
from sqlalchemy import text  # noqa: E402

logger = logging.getLogger(__name__)

# 한 날짜가 "수집된 것으로 간주" 되려면 테이블당 최소 종목 수
# KOSPI 일일 ~950 종목 기준, API 오류 없이 받아왔다면 800+ 나옴
MIN_TICKERS_PER_DATE = 500


def parse_date_arg(s: str) -> date:
    """YYYYMMDD 혹은 YYYY-MM-DD 문자열을 date로 변환."""
    s = s.strip().replace("-", "")
    return datetime.strptime(s, "%Y%m%d").date()


def list_business_days(start: date, end: date) -> list[date]:
    """start~end 사이 KRX 영업일 리스트 (양 끝 포함)."""
    sessions = get_krx_sessions(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    return [d.date() for d in sessions]


def find_missing_dates(
    business_days: list[date], markets: list[str]
) -> list[date]:
    """DB 기준 누락/부족 날짜를 반환.

    각 market에 대해 daily_price / market_cap / fundamental 세 테이블에서
    ticker 수가 MIN_TICKERS_PER_DATE 미만이면 누락으로 간주.
    단, fundamental은 DART 기반이라 부족할 수 있어 market_cap/daily_price만 판정하고
    fundamental은 별도 로그로만 알린다.
    """
    if not business_days:
        return []

    storage = DataStorage()
    missing: set[date] = set()

    with storage.engine.connect() as conn:
        for tbl in ("daily_price", "market_cap"):
            rows = conn.execute(
                text(
                    f"SELECT date, market, COUNT(DISTINCT ticker) AS c "
                    f"FROM {tbl} "
                    f"WHERE date BETWEEN :s AND :e "
                    f"GROUP BY date, market"
                ),
                {"s": business_days[0], "e": business_days[-1]},
            ).fetchall()
            # (date, market) -> count
            counts: dict[tuple[date, str], int] = {
                (r[0] if isinstance(r[0], date) else datetime.strptime(str(r[0]), "%Y-%m-%d").date(),
                 (r[1] or "KOSPI")): r[2]
                for r in rows
            }

            for bd in business_days:
                for m in markets:
                    if counts.get((bd, m), 0) < MIN_TICKERS_PER_DATE:
                        missing.add(bd)

    return sorted(missing)


def write_failed_log(failed: list[date]) -> Path:
    """실패 날짜를 logs/backfill_failed_YYYYMMDD.txt로 저장 후 경로 반환."""
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    today_tag = datetime.now().strftime("%Y%m%d")
    path = logs_dir / f"backfill_failed_{today_tag}.txt"
    with open(path, "w", encoding="utf-8") as f:
        for d in failed:
            f.write(d.strftime("%Y%m%d") + "\n")
    return path


def read_failed_log(path: str) -> list[date]:
    """실패 로그 파일에서 날짜 리스트를 복원."""
    dates: list[date] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            dates.append(parse_date_arg(line))
    return dates


def backfill_one_date(
    collector: KRXDataCollector,
    date_str: str,
    markets: list[str],
    price_only: bool = False,
) -> tuple[int, int]:
    """단일 날짜 수집.

    Args:
        collector: KRXDataCollector 인스턴스
        date_str: YYYYMMDD
        markets: 대상 시장 리스트
        price_only: True면 prefetch_daily_trade만 호출 (DART 펀더멘털 스킵)

    Returns:
        (prefetch_total_rows, fundamental_total_rows) — price_only면 fund=0
    """
    pf_total = 0
    fund_total = 0
    for market in markets:
        pf_df = collector.prefetch_daily_trade(date_str, market=market)
        pf_total += len(pf_df) if pf_df is not None else 0

        if not price_only:
            fund_df = collector.get_fundamentals_all(date_str, market=market)
            fund_total += len(fund_df) if fund_df is not None else 0
    return pf_total, fund_total


def main() -> int:
    parser = argparse.ArgumentParser(description="일별 데이터 공백 백필")
    parser.add_argument("--start", type=str, help="시작일 (YYYYMMDD)")
    parser.add_argument("--end", type=str, help="종료일 (YYYYMMDD)")
    parser.add_argument(
        "--market",
        type=str,
        default="KOSPI",
        choices=["KOSPI", "KOSDAQ", "ALL"],
        help="수집 시장 (기본: KOSPI). ALL=KOSPI+KOSDAQ",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="DB에서 누락된 영업일만 자동 감지하여 수집",
    )
    parser.add_argument(
        "--retry-failed",
        type=str,
        help="backfill_failed_*.txt 파일 경로 — 기록된 날짜만 재시도",
    )
    parser.add_argument(
        "--price-only",
        action="store_true",
        help=(
            "prefetch_daily_trade만 호출하고 DART 펀더멘털 수집 스킵. "
            "KOSDAQ 12년치처럼 DART 호출이 병목인 경우 사용 "
            "(약 64초/일 → 2초/일로 단축)"
        ),
    )
    args = parser.parse_args()

    setup_logging()

    markets = ["KOSPI", "KOSDAQ"] if args.market == "ALL" else [args.market]

    # 날짜 리스트 확보
    if args.retry_failed:
        target_dates = read_failed_log(args.retry_failed)
        logger.info("실패 로그에서 %d개 날짜 로드: %s", len(target_dates), args.retry_failed)
    else:
        # start/end 기본값: start 미지정 시 오늘-30일, end 미지정 시 오늘
        today = date.today()
        end_d = parse_date_arg(args.end) if args.end else today
        if args.start:
            start_d = parse_date_arg(args.start)
        elif args.missing_only:
            start_d = end_d.replace(day=1)  # 이번 달 1일부터
        else:
            parser.error("--start 또는 --missing-only 중 하나는 필수")

        if start_d > end_d:
            parser.error(f"--start({start_d})가 --end({end_d})보다 이후입니다")

        business_days = list_business_days(start_d, end_d)
        logger.info(
            "범위 %s ~ %s 의 KRX 영업일: %d개", start_d, end_d, len(business_days)
        )

        if args.missing_only:
            target_dates = find_missing_dates(business_days, markets)
            already = len(business_days) - len(target_dates)
            logger.info(
                "누락 감지: %d개 수집 필요 / %d개 이미 DB 존재",
                len(target_dates), already,
            )
        else:
            target_dates = business_days

    if not target_dates:
        logger.info("수집 대상 날짜 없음 — 종료")
        return 0

    # tqdm 진행률 표시 (설치 안 되어 있으면 그냥 순회)
    try:
        from tqdm import tqdm
        iterator = tqdm(target_dates, desc="backfill", unit="day")
    except ImportError:
        logger.info("tqdm 미설치 — 진행률 표시 없이 진행")
        iterator = target_dates

    collector = KRXDataCollector()
    succeeded: list[date] = []
    failed: list[tuple[date, str]] = []
    total_pf = 0
    total_fund = 0

    for d in iterator:
        date_str = d.strftime("%Y%m%d")
        if not is_krx_business_day(d):
            logger.info("[%s] 휴장일 스킵", date_str)
            continue
        try:
            pf, fund = backfill_one_date(
                collector, date_str, markets, price_only=args.price_only,
            )
            # price_only 모드에서는 prefetch만 성공 여부 판정
            if args.price_only:
                if pf == 0:
                    raise RuntimeError("prefetch 결과가 비어 있음")
            elif pf == 0 and fund == 0:
                raise RuntimeError("수집 결과가 비어 있음")
            succeeded.append(d)
            total_pf += pf
            total_fund += fund
            logger.info("[%s] ok — prefetch %d, fundamentals %d", date_str, pf, fund)
        except Exception as e:
            failed.append((d, str(e)))
            logger.error("[%s] 실패: %s", date_str, e)

    # 요약 출력
    print("\n" + "=" * 60)
    print(f"백필 완료: {len(succeeded)} 성공 / {len(failed)} 실패 / {len(target_dates)} 대상")
    print(f"누적 prefetch 행수: {total_pf:,}")
    print(f"누적 fundamentals 행수: {total_fund:,}")

    if failed:
        failed_dates = [d for d, _ in failed]
        log_path = write_failed_log(failed_dates)
        print(f"\n실패 날짜 ({len(failed)}건):")
        for d, err in failed[:10]:
            print(f"  {d.strftime('%Y%m%d')}: {err}")
        if len(failed) > 10:
            print(f"  ... 외 {len(failed) - 10}건")
        print(f"\n실패 로그: {log_path}")
        print(f"재시도: python scripts/backfill_data.py --retry-failed {log_path}")
        return 1

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
