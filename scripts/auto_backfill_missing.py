"""자동 백필 — 매일 09:00 장 시작 전 실행 (수집 누락 복구).

최근 N영업일 중 daily_price/market_cap이 MIN_TICKERS_PER_DATE 미만인 날짜를 찾아
`backfill_data.py`의 로직을 재사용하여 누락분을 복구한다.

성공·실패·누락 없음 케이스 모두 텔레그램 알림을 보낸다 (운영 가시성).

사용:
    python scripts/auto_backfill_missing.py
    python scripts/auto_backfill_missing.py --lookback 10
    python scripts/auto_backfill_missing.py --no-notify  # 테스트용
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.calendar import is_krx_business_day
from config.logging_config import setup_logging
from config.settings import settings

logger = logging.getLogger(__name__)


def recent_business_days(lookback: int) -> list[date]:
    """오늘 이전 lookback 영업일 리스트 (오늘 제외)."""
    days: list[date] = []
    d = date.today() - timedelta(days=1)
    while len(days) < lookback and (date.today() - d).days < lookback * 3:
        if is_krx_business_day(d):
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def detect_and_backfill(
    lookback: int,
    markets: list[str],
) -> tuple[list[date], list[date], list[date]]:
    """누락 탐지 + 백필 실행.

    Returns:
        (missing_found, recovered, still_missing)
    """
    from scripts.backfill_data import (  # type: ignore
        find_missing_dates,
        backfill_one_date,
    )
    from data.collector import KRXDataCollector

    business_days = recent_business_days(lookback)
    if not business_days:
        return [], [], []

    missing = find_missing_dates(business_days, markets)
    if not missing:
        return [], [], []

    collector = KRXDataCollector()
    recovered: list[date] = []
    still_missing: list[date] = []
    for d in missing:
        date_str = d.strftime("%Y%m%d")
        try:
            pf_total, fund_total = backfill_one_date(collector, date_str, markets)
            if pf_total > 0 or fund_total > 0:
                recovered.append(d)
                logger.info(
                    f"[{d}] 백필 성공 — prefetch {pf_total}, fund {fund_total}"
                )
            else:
                still_missing.append(d)
                logger.warning(f"[{d}] 백필 결과 비어있음 — KRX 데이터 여전히 부재")
        except Exception as e:
            logger.error(f"백필 예외 [{d}]: {e}", exc_info=True)
            still_missing.append(d)

    return missing, recovered, still_missing


def notify_result(
    missing: list[date],
    recovered: list[date],
    still_missing: list[date],
) -> None:
    """텔레그램 알림 발송."""
    try:
        from notify.telegram import TelegramNotifier

        notifier = TelegramNotifier()
    except Exception as e:
        logger.warning(f"TelegramNotifier 초기화 실패: {e}")
        return

    try:
        if not missing:
            notifier.send("✅ 자동 백필 체크: 최근 영업일 누락 없음")
            return

        lines = [
            f"📥 자동 백필 결과",
            f"- 탐지된 누락: {len(missing)}일",
            f"- 복구 완료: {len(recovered)}일",
            f"- 여전히 누락: {len(still_missing)}일",
        ]
        if recovered:
            lines.append("복구: " + ", ".join(str(d) for d in recovered))
        if still_missing:
            lines.append("실패: " + ", ".join(str(d) for d in still_missing))
            lines.append("→ 수동 확인 필요 (backfill_data.py로 재시도)")

        msg = "\n".join(lines)
        if still_missing:
            notifier.send_error(msg)
        else:
            notifier.send(msg)
    except Exception as e:
        logger.warning(f"텔레그램 발송 실패: {e}")


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="자동 백필 (최근 영업일 누락 복구)")
    parser.add_argument(
        "--lookback",
        type=int,
        default=5,
        help="검사할 최근 영업일 수 (기본 5)",
    )
    parser.add_argument(
        "--markets",
        type=str,
        default="",
        help="쉼표 구분 market 리스트 (기본: settings.schedule.daily_data_collection.markets)",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="텔레그램 알림 건너뛰기",
    )
    args = parser.parse_args()

    markets = (
        [m.strip() for m in args.markets.split(",") if m.strip()]
        if args.markets
        else list(settings.schedule.daily_data_collection.markets)
    )

    logger.info(
        f"자동 백필 시작 — lookback={args.lookback}일, markets={markets}"
    )

    missing, recovered, still_missing = detect_and_backfill(
        args.lookback, markets
    )

    logger.info(
        f"완료 — 누락 {len(missing)}일 / 복구 {len(recovered)}일 / "
        f"실패 {len(still_missing)}일"
    )

    if not args.no_notify:
        notify_result(missing, recovered, still_missing)

    return 1 if still_missing else 0


if __name__ == "__main__":
    sys.exit(main())
