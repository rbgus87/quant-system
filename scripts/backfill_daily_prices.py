"""일별 가격 백필 스크립트 (KRX Open API 기반, 1회성).

1거래일 = KRX Open API 1회 호출 → 전종목 OHLCV DB 저장.
이미 저장된 날짜는 자동 스킵 (idempotent).

실행:
    python scripts/backfill_daily_prices.py \
      --start-date 2016-01-01 --end-date 2024-12-31 --market KOSPI

요구사항:
    .env에 KRX_OPENAPI_KEY 설정 필요.
    약 2,200 거래일 × 0.5초 = 약 18분 (1회성 작업).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.calendar import get_krx_sessions
from data.collector import KRXDataCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def backfill(
    start_date: str,
    end_date: str,
    market: str = "KOSPI",
    delay: float = 0.5,
) -> None:
    """지정 기간의 일별 가격을 DB에 백필.

    Args:
        start_date: 시작일 (YYYY-MM-DD 또는 YYYYMMDD)
        end_date: 종료일
        market: KOSPI / KOSDAQ
        delay: API 호출 간격 (초, rate-limit 준수)
    """
    collector = KRXDataCollector(request_delay=delay)

    if not collector.krx_api:
        logger.error(
            "KRX Open API 클라이언트 초기화 실패. "
            ".env에 KRX_OPENAPI_KEY가 설정되어 있는지 확인하세요."
        )
        sys.exit(1)

    # 날짜 정규화 (YYYY-MM-DD → YYYYMMDD)
    start_clean = start_date.replace("-", "")
    end_clean = end_date.replace("-", "")

    sessions = get_krx_sessions(start_clean, end_clean)
    if sessions.empty:
        logger.error(f"거래일 없음: {start_date} ~ {end_date}")
        return

    logger.info(
        f"백필 시작: {start_date} ~ {end_date} ({market}), "
        f"총 {len(sessions)}거래일"
    )

    skipped = 0
    saved = 0
    failed = 0

    for i, session_dt in enumerate(sessions):
        date_str = session_dt.strftime("%Y%m%d")

        # DB 캐시 확인 — 충분한 데이터가 있으면 스킵 (idempotent)
        cached_count = collector.storage.load_daily_prices_for_date(
            session_dt.date() if hasattr(session_dt, "date") else session_dt,
            market=market,
        )
        if cached_count >= 100:
            skipped += 1
            if (i + 1) % 100 == 0:
                logger.info(
                    f"  [{i + 1}/{len(sessions)}] {date_str} — "
                    f"스킵 {skipped}, 저장 {saved}, 실패 {failed}"
                )
            continue

        # KRX Open API 호출 (prefetch_daily_trade 내부에서 DB 저장)
        try:
            collector.prefetch_daily_trade(date_str, market)
            saved += 1
            time.sleep(delay)
        except Exception as e:
            logger.warning(f"  [{date_str}] 수집 실패: {e}")
            failed += 1

        if (i + 1) % 100 == 0:
            logger.info(
                f"  [{i + 1}/{len(sessions)}] {date_str} — "
                f"스킵 {skipped}, 저장 {saved}, 실패 {failed}"
            )

    logger.info(
        f"백필 완료: 총 {len(sessions)}거래일 — "
        f"스킵 {skipped}, 저장 {saved}, 실패 {failed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="일별 가격 백필 (KRX Open API)")
    parser.add_argument("--start-date", default="2016-01-01", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end-date",   default="2024-12-31", help="종료일 (YYYY-MM-DD)")
    parser.add_argument(
        "--market", default="KOSPI", choices=["KOSPI", "KOSDAQ"],
        help="대상 시장",
    )
    parser.add_argument(
        "--delay", type=float, default=0.5,
        help="API 호출 간격 (초, 기본 0.5)",
    )
    args = parser.parse_args()

    backfill(args.start_date, args.end_date, args.market, args.delay)


if __name__ == "__main__":
    main()
