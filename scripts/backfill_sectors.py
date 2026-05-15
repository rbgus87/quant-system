"""backfill_sectors.py — S4-A 섹터(업종) 정보 백필.

분기 리밸런싱 날짜마다 전체 종목의 섹터 정보를 수집하여
`stock_sector` 테이블에 저장. screener에서 finance_tickers 자동 감지에 사용.

KRX/pykrx/FDR 섹터 API 모두 차단(2025-12-27) 환경 → 종목명 휴리스틱.

사용:
    python scripts/backfill_sectors.py \\
        --start-date 2017-01-01 --end-date 2024-12-31 --market KOSPI

    python scripts/backfill_sectors.py --start-date 2024-01-01 --end-date 2024-03-31  # 빠른 sanity check

옵션:
    --start-date, --end-date: 기간
    --market: KOSPI/KOSDAQ (기본 KOSPI)
    --force: 기존 데이터 있어도 재수집 (idempotent skip 비활성)
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import date as _date
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.calendar import get_krx_month_end_sessions  # noqa: E402
from config.logging_config import setup_logging  # noqa: E402
from data.collector import KRXDataCollector  # noqa: E402
from data.dart_client import DartClient  # noqa: E402
from data.storage import DataStorage  # noqa: E402
from factors.composite import classify_by_ksic  # noqa: E402

logger = logging.getLogger(__name__)


def parse_date_arg(s: str) -> _date:
    s = s.strip().replace("-", "")
    return datetime.strptime(s, "%Y%m%d").date()


def list_quarter_end_dates(
    start: _date, end: _date,
) -> list[str]:
    """기간 내 분기말 KRX 영업일 (3/6/9/12월 마지막 영업일) 목록."""
    sessions = get_krx_month_end_sessions(
        start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
    )
    return [d.strftime("%Y%m%d") for d in sessions if d.month in (3, 6, 9, 12)]


def is_already_filled(
    storage: DataStorage, date_str: str, threshold: int = 100,
) -> bool:
    """해당 date에 stock_sector 행이 threshold 이상 있으면 True."""
    with storage.engine.connect() as conn:
        cnt = conn.execute(
            text("SELECT COUNT(*) FROM stock_sector WHERE date = :d"),
            {"d": date_str},
        ).scalar() or 0
    return cnt >= threshold


def sanity_check(storage: DataStorage) -> None:
    """백필 후 통계 출력."""
    with storage.engine.connect() as conn:
        total = conn.execute(
            text("SELECT COUNT(*) FROM stock_sector")
        ).scalar() or 0
        n_dates = conn.execute(
            text("SELECT COUNT(DISTINCT date) FROM stock_sector")
        ).scalar() or 0
        n_tickers = conn.execute(
            text("SELECT COUNT(DISTINCT ticker) FROM stock_sector")
        ).scalar() or 0

        # 최신 date 기준 섹터 분포
        latest_date = conn.execute(
            text("SELECT MAX(date) FROM stock_sector")
        ).scalar()
        latest_rows: list[tuple] = []
        if latest_date:
            latest_rows = conn.execute(
                text(
                    "SELECT ticker, sector_name, is_financial "
                    "FROM stock_sector WHERE date = :d"
                ),
                {"d": latest_date},
            ).fetchall()

    sector_counter: Counter = Counter()
    n_fin = 0
    fin_examples: list[str] = []
    for row in latest_rows:
        sec = row[1] or "(None)"
        sector_counter[sec] += 1
        if row[2]:
            n_fin += 1
            if len(fin_examples) < 8:
                fin_examples.append(row[0])

    print()
    print("=" * 72)
    print("S4-A sectors backfill -- sanity check")
    print("=" * 72)
    print(f"  총 행 수            : {total:>10,}")
    print(f"  고유 ticker         : {n_tickers:>10,}")
    print(f"  고유 date           : {n_dates:>10,}")
    print(f"  최신 date           : {latest_date}")
    print(f"  최신 date 종목 수   : {len(latest_rows):>10,}")
    print(f"  금융주 (is_financial): {n_fin:>10,}")
    print(f"  비금융              : {len(latest_rows) - n_fin:>10,}")
    print()
    print("  최신 date 섹터 분포 (상위 10):")
    for sec, n in sector_counter.most_common(10):
        marker = "[F]" if sec in {"은행", "증권", "보험", "금융업"} else "   "
        print(f"    {marker} {sec:<20} : {n:>5}")
    if fin_examples:
        print()
        print(f"  금융주 샘플 ticker (최대 8): {fin_examples}")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="섹터 정보 백필")
    parser.add_argument(
        "--start-date", default="2017-01-01", type=parse_date_arg,
    )
    parser.add_argument(
        "--end-date",
        default=datetime.now().date().strftime("%Y-%m-%d"),
        type=parse_date_arg,
    )
    parser.add_argument(
        "--market", default="KOSPI", choices=["KOSPI", "KOSDAQ"],
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-sanity", action="store_true")
    parser.add_argument(
        "--use-dart-company", action="store_true",
        help="DART 기업개황 API로 KSIC 업종코드 수집 + KSIC 매핑 적용",
    )
    args = parser.parse_args()

    setup_logging()
    storage = DataStorage()
    collector = KRXDataCollector(request_delay=0.5)

    # DART 기업개황 사전 수집 (--use-dart-company 시)
    # 모든 분기에 동일 적용 (업종 변경은 드뭄)
    dart_ksic: dict[str, dict] = {}
    if args.use_dart_company:
        logger.info("DART 기업개황 사전 수집 시작 (--use-dart-company)")
        dart = DartClient()
        if not dart.api_key:
            logger.error("DART_API_KEY 미설정 — --use-dart-company 비활성")
            args.use_dart_company = False
        else:
            # 전체 KOSPI 종목 ticker 결정 — DB의 market_cap에서 distinct
            with storage.engine.connect() as conn:
                tickers_to_fetch = [
                    r[0] for r in conn.execute(text(
                        "SELECT DISTINCT ticker FROM market_cap "
                        "WHERE market = :m OR market IS NULL "
                        "ORDER BY ticker"
                    ), {"m": args.market}).fetchall()
                    if r[0]
                ]
            logger.info(
                f"DART 기업개황 대상: {len(tickers_to_fetch)}종목"
            )
            dart_results = dart.fetch_sector_batch(tickers_to_fetch)
            # 매핑 결과 + 매핑 실패 코드 집계
            unmapped_codes: dict[str, int] = {}
            for tk, info in dart_results.items():
                code = info.get("induty_code", "")
                sector, is_fin = classify_by_ksic(code)
                dart_ksic[tk] = {
                    "induty_code": code,
                    "sector_name": sector,
                    "is_financial": is_fin,
                }
                if sector == "기타":
                    unmapped_codes[code[:2] if len(code) >= 2 else code] = (
                        unmapped_codes.get(code[:2] if len(code) >= 2 else code, 0) + 1
                    )
            if unmapped_codes:
                logger.warning(
                    f"KSIC 매핑 실패 코드 (상위 2자리): {dict(sorted(unmapped_codes.items(), key=lambda x: -x[1])[:10])}"
                )
            logger.info(
                f"DART 기업개황 매핑 완료: {len(dart_ksic)}종목 (KSIC 적용)"
            )

    quarter_dates = list_quarter_end_dates(args.start_date, args.end_date)
    if not quarter_dates:
        logger.error("분기말 영업일 없음")
        return 1

    logger.info(
        f"백필 범위: {args.start_date} ~ {args.end_date} ({args.market}) "
        f"분기 {len(quarter_dates)}개"
    )

    total_inserted = 0
    total_updated = 0
    skipped = 0

    for i, date_str in enumerate(quarter_dates, 1):
        tag = f"[{i}/{len(quarter_dates)}] {date_str}"

        # --use-dart-company 시에는 sector_code/sector_name 보강을 위해 항상 갱신
        if not args.force and not args.use_dart_company and is_already_filled(storage, date_str):
            logger.info(f"{tag} skip — 기존 데이터 충분")
            skipped += 1
            continue

        try:
            df = collector.get_stock_sectors(date_str, market=args.market)
        except Exception as e:
            logger.error(f"{tag} 섹터 수집 실패: {e}")
            continue

        if df.empty:
            logger.warning(f"{tag} 응답 비어 있음")
            continue

        # storage upsert rows 구성
        # DART KSIC 결과를 우선 적용:
        # - 금융주 판정: 종목명 휴리스틱(OR) DART KSIC 합집합
        # - sector_name: DART KSIC 우선, 없으면 휴리스틱
        rows = []
        for ticker, row in df.iterrows():
            heur_sector = row.get("sector_name")
            heur_is_fin = bool(row.get("is_financial", False))
            data_source = row.get("data_source", "name_heuristic")

            sector_name = heur_sector
            sector_code: Optional[str] = None
            is_financial = heur_is_fin

            if ticker in dart_ksic:
                d = dart_ksic[ticker]
                # 금융주: 휴리스틱 또는 KSIC = 합집합
                is_financial = heur_is_fin or d.get("is_financial", False)
                # sector_name 결정 우선순위:
                #   1. 휴리스틱 금융주 매칭 (구체적: "은행"/"증권"/"보험") → 유지
                #   2. KSIC 매핑 결과
                #   3. None
                if heur_is_fin and heur_sector:
                    sector_name = heur_sector
                else:
                    sector_name = d.get("sector_name") or heur_sector
                sector_code = d.get("induty_code")
                data_source = "dart_ksic+name_heuristic"

            rows.append({
                "ticker": ticker,
                "date": date_str,
                "sector_name": sector_name,
                "sector_code": sector_code,
                "is_financial": is_financial,
                "data_source": data_source,
            })

        try:
            ins, upd = storage.upsert_stock_sectors(rows)
            total_inserted += ins
            total_updated += upd
            logger.info(
                f"{tag} 저장: {len(rows)}종목 (신규 {ins}, 갱신 {upd})"
            )
        except Exception as e:
            logger.error(f"{tag} DB 저장 실패: {e}")

    logger.info(
        f"섹터 백필 완료: 신규 {total_inserted:,} / 갱신 {total_updated:,} "
        f"/ skip {skipped}"
    )

    if not args.skip_sanity:
        sanity_check(storage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
