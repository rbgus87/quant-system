"""backfill_fundamentals_quarterly.py — 분기말 시점만 DART 펀더멘털 수집.

`backfill_data.py --price-only`와 세트로 사용. 가격/시총은 일별 전량 수집하되,
DART 펀더멘털은 분기 리밸런싱 시점만 수집하여 DART API 호출을 95% 절감.

전제:
  - 기본 설정 `strict_reporting_lag=False` 기준 screener는 리밸런싱 날짜 당일의
    fundamental을 조회. 그러므로 분기말(3/6/9/12월 말 영업일) 32~40개만 수집해도
    screener 캐시 히트 가능.
  - 만약 `strict_reporting_lag=True`로 전환 시 추가로 매년 12월 말 영업일만 따로
    수집 필요 (`--reporting-lag-aware`).

사용:
    python scripts/backfill_fundamentals_quarterly.py \\
        --start 2017-01-01 --end 2024-12-31 --market KOSDAQ
    python scripts/backfill_fundamentals_quarterly.py \\
        --start 2017-01-01 --end 2024-12-31 --market ALL
    python scripts/backfill_fundamentals_quarterly.py \\
        --start 2017-01-01 --end 2024-12-31 --reporting-lag-aware

exit code:
    0 = 모든 날짜 성공 (또는 전부 캐시 히트)
    1 = 하나라도 실패
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.calendar import get_krx_sessions, previous_krx_business_day  # noqa: E402
from config.logging_config import setup_logging  # noqa: E402
from data.collector import KRXDataCollector  # noqa: E402
from data.storage import DataStorage  # noqa: E402

logger = logging.getLogger(__name__)


def parse_date_arg(s: str) -> date:
    s = s.strip().replace("-", "")
    return datetime.strptime(s, "%Y%m%d").date()


def quarterly_rebalance_dates(start: date, end: date) -> list[date]:
    """3/6/9/12월 말 KRX 영업일 리스트."""
    import pandas as pd

    sessions = get_krx_sessions(
        start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
    )
    if sessions is None or len(sessions) == 0:
        return []

    # 각 (year, quarter_month)의 마지막 영업일
    quarters: dict[tuple[int, int], pd.Timestamp] = {}
    for ts in sessions:
        if ts.month in (3, 6, 9, 12):
            key = (ts.year, ts.month)
            if key not in quarters or ts > quarters[key]:
                quarters[key] = ts
    return sorted(d.date() for d in quarters.values())


def effective_fund_dates_for_reporting_lag(
    rebal_dates: list[date],
) -> list[date]:
    """strict_reporting_lag=True일 때 screener가 조회하는 날짜 집합.

    12월 결산 기업 기준 effective_year = (rebal_year - 1) 또는 (rebal_year - 2).
    해당 연도 12월 말 영업일.
    """
    effective: set[date] = set()
    for rd in rebal_dates:
        effective_year = rd.year - 2 if rd.month <= 3 else rd.year - 1
        try:
            target = date(effective_year, 12, 31)
            prev = previous_krx_business_day(target)
            effective.add(prev)
        except Exception:
            # 캘린더 실패 시 12/28로 폴백 (거의 항상 거래일)
            effective.add(date(effective_year, 12, 28))
    return sorted(effective)


def already_cached(
    storage: DataStorage, date_obj: date, market: str
) -> bool:
    """이미 DB에 해당 날짜/시장 fund 데이터가 있는지."""
    df = storage.load_fundamentals(date_obj, market=market)
    return df is not None and not df.empty


def main() -> int:
    parser = argparse.ArgumentParser(description="분기말 펀더멘털 선택 수집")
    parser.add_argument("--start", type=str, default="2017-01-01",
                        help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2024-12-31",
                        help="종료일 (YYYY-MM-DD)")
    parser.add_argument(
        "--market", type=str, default="KOSDAQ",
        choices=["KOSPI", "KOSDAQ", "ALL"],
        help="수집 시장 (기본 KOSDAQ). ALL=KOSPI+KOSDAQ",
    )
    parser.add_argument(
        "--reporting-lag-aware", action="store_true",
        help=(
            "strict_reporting_lag=True 모드용: 분기말 대신 각 연도 12/말 영업일만 수집. "
            "(기본 False 모드에서는 불필요)"
        ),
    )
    parser.add_argument(
        "--skip-cached", action="store_true", default=True,
        help="이미 캐시된 날짜 스킵 (기본 True)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="캐시 무시하고 전량 재수집",
    )
    args = parser.parse_args()

    setup_logging()

    start = parse_date_arg(args.start)
    end = parse_date_arg(args.end)
    if start > end:
        parser.error(f"--start({start})가 --end({end})보다 이후")

    markets = ["KOSPI", "KOSDAQ"] if args.market == "ALL" else [args.market]

    # 수집 대상 날짜
    rebal_dates = quarterly_rebalance_dates(start, end)
    if not rebal_dates:
        logger.error("해당 기간에 분기말 영업일 없음")
        return 1

    if args.reporting_lag_aware:
        target_dates = effective_fund_dates_for_reporting_lag(rebal_dates)
        logger.info(
            "Reporting Lag 모드: 분기말 %d개 → effective 연도말 %d개",
            len(rebal_dates), len(target_dates),
        )
    else:
        target_dates = rebal_dates
        logger.info("분기말 %d개 날짜 대상", len(target_dates))

    storage = DataStorage()
    collector = KRXDataCollector()

    # tqdm 있으면 진행률 표시
    try:
        from tqdm import tqdm
        iterator = tqdm(target_dates, desc="fund-backfill", unit="day")
    except ImportError:
        iterator = target_dates

    succeeded: list[tuple[date, str, int]] = []
    skipped: list[tuple[date, str]] = []
    failed: list[tuple[date, str, str]] = []
    t_start = time.monotonic()

    for d in iterator:
        ds = d.strftime("%Y%m%d")
        for m in markets:
            # 캐시 확인
            if not args.force and args.skip_cached and already_cached(storage, d, m):
                skipped.append((d, m))
                logger.debug("[%s %s] 캐시 히트 — 스킵", ds, m)
                continue
            try:
                df = collector.get_fundamentals_all(ds, m)
                n = len(df) if df is not None else 0
                succeeded.append((d, m, n))
                logger.info("[%s %s] 수집 완료: %d건", ds, m, n)
            except Exception as e:
                failed.append((d, m, str(e)))
                logger.error("[%s %s] 실패: %s", ds, m, e)

    elapsed = time.monotonic() - t_start

    # 요약 출력
    print("\n" + "=" * 60)
    print(
        f"펀더멘털 백필 완료: {len(succeeded)} 신규 / "
        f"{len(skipped)} 캐시스킵 / {len(failed)} 실패"
    )
    print(f"대상 날짜: {len(target_dates)}개 × 시장 {len(markets)}개")
    print(f"소요 시간: {elapsed:.1f}초 ({elapsed / 60:.1f}분)")

    if succeeded:
        total_rows = sum(n for _, _, n in succeeded)
        print(f"신규 저장 행수: {total_rows:,}")

    if failed:
        print(f"\n실패 ({len(failed)}건):")
        for d, m, err in failed[:10]:
            print(f"  {d.strftime('%Y%m%d')} {m}: {err}")
        if len(failed) > 10:
            print(f"  ... 외 {len(failed) - 10}건")
        print("=" * 60)
        return 1

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
