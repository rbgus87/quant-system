"""backfill_quarterly_fundamentals.py — 분기별 재무 시계열 백필 (Step 3).

DART OpenAPI의 fnlttMultiAcnt를 분기 단위로 호출하여
`fundamental_quarterly` 테이블에 EPS / 영업이익 / 매출 시계열을 누적한다.
Step 3 연속 흑자 필터에서 PIT 안전하게 조회하기 위해 필요.

사용:
    python scripts/backfill_quarterly_fundamentals.py \\
        --start-year 2016 --end-year 2024 --market KOSPI

    # 특정 ticker만
    python scripts/backfill_quarterly_fundamentals.py \\
        --start-year 2016 --end-year 2024 --tickers 005620,000020

옵션:
    --start-year:    백필 시작 사업연도 (기본 2016)
    --end-year:      백필 종료 사업연도 (기본 현재년)
    --market:        티커 소스 시장 (KOSPI/KOSDAQ/ALL, 기본 KOSPI)
    --tickers:       콤마 구분 종목 코드 직접 지정 (market 무시)
    --force:         기존 데이터 있어도 재수집 (idempotent skip 비활성)
    --quarters:      분기 코드 콤마 지정 (기본 "11013,11012,11014,11011" 전체)

설계:
- idempotent: (ticker, bsns_year, reprt_code) 기준 80% 이상 캐시 적중 시 skip
- 진행률 로깅: 분기별로 신규/갱신 카운트 출력
- DART rate-limit: 호출 간격은 DartClient.delay에 위임

exit code:
    0 = 정상 완료
    1 = 일부 분기 실패 (로그 확인)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from data.dart_client import DartClient  # noqa: E402
from data.storage import DataStorage  # noqa: E402

logger = logging.getLogger(__name__)


# 분기 코드 → 라벨 (로깅용)
REPRT_LABEL: dict[str, str] = {
    "11013": "Q1",
    "11012": "Half",
    "11014": "Q3",
    "11011": "Annual",
}

# 시작점 보고서 코드 순서 (각 사업연도 내, 시간 순)
DEFAULT_QUARTERS: list[str] = ["11013", "11012", "11014", "11011"]

# 캐시 적중 임계 — 이 비율 이상이면 해당 분기 skip
SKIP_HIT_RATIO: float = 0.80


def parse_args() -> argparse.Namespace:
    """CLI 인자 파싱"""
    parser = argparse.ArgumentParser(description="분기 재무 시계열 백필")
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=datetime.now().year)
    parser.add_argument(
        "--market", default="KOSPI",
        choices=["KOSPI", "KOSDAQ", "ALL"],
    )
    parser.add_argument(
        "--tickers", default="",
        help="콤마 구분 종목코드 (지정 시 market 무시)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="기존 데이터 있어도 재수집",
    )
    parser.add_argument(
        "--quarters", default=",".join(DEFAULT_QUARTERS),
        help="콤마 구분 분기 코드 (기본 전 분기)",
    )
    return parser.parse_args()


def load_market_tickers(storage: DataStorage, market: str) -> list[str]:
    """market_cap 테이블에서 해당 시장의 distinct ticker 목록을 반환.

    최근 데이터가 있는 ticker만 (백필 대상 범위 확보 목적).

    Args:
        storage: DataStorage
        market: KOSPI / KOSDAQ / ALL

    Returns:
        종목코드 리스트
    """
    if market == "ALL":
        sql = (
            "SELECT DISTINCT ticker FROM market_cap "
            "ORDER BY ticker"
        )
        params: dict = {}
    else:
        sql = (
            "SELECT DISTINCT ticker FROM market_cap "
            "WHERE market = :m OR market IS NULL "
            "ORDER BY ticker"
        )
        params = {"m": market}

    with storage.engine.connect() as conn:
        result = conn.execute(text(sql), params)
        tickers = [row[0] for row in result if row[0]]

    logger.info(f"티커 소스 ({market}): {len(tickers)}종목")
    return tickers


def count_existing(
    storage: DataStorage, bsns_year: str, reprt_code: str,
) -> int:
    """해당 분기에 이미 저장된 ticker 수."""
    with storage.engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(DISTINCT ticker) FROM fundamental_quarterly "
                "WHERE bsns_year = :y AND reprt_code = :r"
            ),
            {"y": bsns_year, "r": reprt_code},
        )
        return result.scalar() or 0


def main() -> int:
    args = parse_args()
    setup_logging()

    if args.start_year > args.end_year:
        logger.error(
            f"start-year({args.start_year}) > end-year({args.end_year})"
        )
        return 1

    storage = DataStorage()
    dart = DartClient()

    # 티커 결정
    if args.tickers:
        tickers = [t.strip().zfill(6) for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = load_market_tickers(storage, args.market)

    if not tickers:
        logger.error("백필 대상 티커 없음 — market_cap 데이터 확인 필요")
        return 1

    # DART corp_code_map 사전 로드 (반복 호출 방지)
    cmap = dart.corp_code_map
    if not cmap:
        logger.error("DART corp_code_map 로드 실패")
        return 1

    # 매핑 가능한 티커만 (DART에 등록된 것)
    mappable = [t for t in tickers if t in cmap]
    logger.info(
        f"DART corp_code 매핑: {len(mappable)}/{len(tickers)}종목"
    )
    if not mappable:
        logger.error("DART에 매핑된 티커 없음")
        return 1

    quarters = [q.strip() for q in args.quarters.split(",") if q.strip()]
    for q in quarters:
        if q not in REPRT_LABEL:
            logger.error(f"알 수 없는 분기 코드: {q}")
            return 1

    # 분기 단위로 (start_year ~ end_year) × quarters 순회
    total_pairs = (args.end_year - args.start_year + 1) * len(quarters)
    logger.info(
        f"백필 범위: {args.start_year}~{args.end_year} × {len(quarters)}분기 "
        f"= 총 {total_pairs} (year, reprt) 페어"
    )
    logger.info(
        f"DART 호출 추산: 약 {total_pairs * (len(mappable) // 100 + 1)}회 "
        f"(rate-limit {dart.delay}초 대기 포함)"
    )

    failed_quarters: list[tuple[str, str]] = []
    processed = 0

    for year in range(args.end_year, args.start_year - 1, -1):  # 최신부터
        for reprt in quarters:
            processed += 1
            year_s = str(year)
            label = REPRT_LABEL[reprt]
            tag = f"[{processed}/{total_pairs}] {year_s} {label}"

            # idempotent skip — 임계값 최소 1로 보장 (단일 종목 백필 시 0 = 항상 skip 버그 방지)
            if not args.force:
                existing = count_existing(storage, year_s, reprt)
                threshold = max(1, int(len(mappable) * SKIP_HIT_RATIO))
                if existing >= threshold:
                    logger.info(
                        f"{tag} skip — 기존 {existing}/{len(mappable)} "
                        f"(>= {SKIP_HIT_RATIO:.0%}, 임계 {threshold})"
                    )
                    continue

            try:
                rows = dart.fetch_quarterly_series(
                    mappable, year, reprt, n_quarters=1,
                )
            except Exception as e:
                logger.error(f"{tag} DART 수집 실패: {e}")
                failed_quarters.append((year_s, reprt))
                continue

            if not rows:
                logger.warning(f"{tag} 응답 비어 있음 — 0건")
                continue

            try:
                inserted, updated = storage.upsert_fundamentals_quarterly(rows)
                logger.info(
                    f"{tag} 저장: 총 {len(rows)}행 "
                    f"(신규 {inserted}, 갱신 {updated})"
                )
            except Exception as e:
                logger.error(f"{tag} DB upsert 실패: {e}")
                failed_quarters.append((year_s, reprt))

    if failed_quarters:
        logger.warning(
            f"실패한 분기 {len(failed_quarters)}개: {failed_quarters}"
        )
        return 1

    logger.info(
        f"분기 백필 완료: {args.start_year}~{args.end_year} × "
        f"{len(quarters)}분기 ({total_pairs} 페어)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
