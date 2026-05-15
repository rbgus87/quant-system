"""backfill_debt_ratio.py — S2 부채/자본 데이터 백필.

기존 fundamental 행 중 debt_ratio IS NULL인 (date, market) 페어를 추출하여
DART에서 자본총계·부채총계를 재수집 후 3컬럼만 UPDATE.

기존 컬럼(PBR/PER/PCR/EPS 등)은 손상하지 않음 (UPDATE 대상은 신규 3 컬럼만).
idempotent: 이미 채워진 페어는 자동 skip.

사용:
    python scripts/backfill_debt_ratio.py \\
        --start-date 2017-01-01 --end-date 2024-12-31 --market KOSPI

옵션:
    --start-date, --end-date: 기간 (YYYY-MM-DD 또는 YYYYMMDD)
    --market: KOSPI / KOSDAQ / ALL (기본 KOSPI)
    --force: NULL이 아닌 행도 재수집 (idempotent skip 비활성)

DART API 호출: 페어당 약 9~10 batch (KOSPI ~900종목), rate-limit 0.5s.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from data.dart_client import DartClient  # noqa: E402
from data.storage import DataStorage  # noqa: E402

logger = logging.getLogger(__name__)


def parse_date_arg(s: str) -> _date:
    s = s.strip().replace("-", "")
    return datetime.strptime(s, "%Y%m%d").date()


def list_target_pairs(
    storage: DataStorage, start_date: _date, end_date: _date,
    market_filter: str, force: bool,
) -> list[tuple[_date, str]]:
    """채워야 할 (date, market) 페어 목록 반환.

    debt_ratio IS NULL 인 페어만 (force=False).
    """
    if market_filter == "ALL":
        market_cond = ""
        params: dict = {"sd": str(start_date), "ed": str(end_date)}
    else:
        market_cond = "AND (market = :m OR market IS NULL) "
        params = {"sd": str(start_date), "ed": str(end_date), "m": market_filter}

    if force:
        sql = (
            "SELECT DISTINCT date, market FROM fundamental "
            "WHERE date BETWEEN :sd AND :ed " + market_cond +
            "ORDER BY date"
        )
    else:
        sql = (
            "SELECT DISTINCT f1.date, f1.market FROM fundamental f1 "
            "WHERE f1.date BETWEEN :sd AND :ed " + market_cond +
            "AND EXISTS ("
            "  SELECT 1 FROM fundamental f2 "
            "  WHERE f2.date = f1.date "
            "  AND (f2.market = f1.market OR (f2.market IS NULL AND f1.market IS NULL)) "
            "  AND f2.debt_ratio IS NULL"
            ") "
            "ORDER BY f1.date"
        )

    with storage.engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    out: list[tuple[_date, str]] = []
    for r in rows:
        d = r[0]
        if isinstance(d, str):
            d = datetime.strptime(d, "%Y-%m-%d").date()
        m = r[1] or "KOSPI"
        out.append((d, m))
    return out


def fetch_bs_data(
    dart: DartClient, tickers: list[str], date_str: str,
) -> dict[str, tuple[float | None, float | None, float | None]]:
    """date 시점에 공시된 보고서에서 (total_equity, total_liabilities, debt_ratio) 조회.

    dart_client._determine_report_period로 PIT 안전한 보고서 기간 결정.

    Returns:
        {ticker: (total_equity, total_liabilities, debt_ratio)}
    """
    if not tickers or not dart.api_key:
        return {}

    bsns_year, reprt_code = DartClient._determine_report_period(date_str)
    valid = [t for t in tickers if t in dart.corp_code_map]
    if not valid:
        return {}

    try:
        items = dart._fetch_multi_account_batch(valid, bsns_year, reprt_code)
    except Exception as e:
        logger.warning(f"DART 조회 실패 ({bsns_year}/{reprt_code}): {e}")
        return {}

    if not items:
        return {}

    (
        _eps, _ni, equity_map, _cf,
        _rev, _oi, _ta, total_liabilities_map,
    ) = dart._extract_financial_items(items)

    result: dict[str, tuple[float | None, float | None, float | None]] = {}
    for t in set(equity_map) | set(total_liabilities_map):
        eq = equity_map.get(t)
        ld = total_liabilities_map.get(t)
        dr = None
        if eq is not None and ld is not None and eq > 0:
            dr = ld / eq * 100.0
        result[t] = (eq, ld, dr)
    return result


def update_fundamental_debt(
    storage: DataStorage,
    dt: _date,
    market: str,
    data: dict[str, tuple[float | None, float | None, float | None]],
) -> int:
    """fundamental 행에 total_equity / total_liabilities / debt_ratio UPDATE."""
    if not data:
        return 0
    rows = [
        {
            "ticker": t,
            "dt": str(dt),
            "market": market,
            "te": eq,
            "tl": ld,
            "dr": dr,
        }
        for t, (eq, ld, dr) in data.items()
    ]
    updated = 0
    with storage.engine.connect() as conn:
        for row in rows:
            r = conn.execute(
                text(
                    "UPDATE fundamental SET "
                    "  total_equity = :te, "
                    "  total_liabilities = :tl, "
                    "  debt_ratio = :dr "
                    "WHERE ticker = :ticker AND date = :dt "
                    "AND (market = :market OR market IS NULL)"
                ),
                row,
            )
            updated += r.rowcount or 0
        conn.commit()
    return updated


def sanity_check(storage: DataStorage) -> None:
    """백필 후 통계 출력."""
    with storage.engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM fundamental")).scalar() or 0
        filled = conn.execute(text(
            "SELECT COUNT(*) FROM fundamental WHERE debt_ratio IS NOT NULL"
        )).scalar() or 0
        over_200 = conn.execute(text(
            "SELECT COUNT(*) FROM fundamental WHERE debt_ratio > 200"
        )).scalar() or 0
        over_300 = conn.execute(text(
            "SELECT COUNT(*) FROM fundamental WHERE debt_ratio > 300"
        )).scalar() or 0
        impaired = conn.execute(text(
            "SELECT COUNT(*) FROM fundamental WHERE total_equity <= 0"
        )).scalar() or 0

        # 005620 (2017-06-30 또는 그 근처 폴백)
        r_005620 = conn.execute(text(
            "SELECT date, debt_ratio, total_equity, total_liabilities "
            "FROM fundamental WHERE ticker = '005620' "
            "AND date BETWEEN '2017-06-01' AND '2017-07-31' "
            "AND debt_ratio IS NOT NULL "
            "ORDER BY date DESC LIMIT 1"
        )).fetchone()

    pct = (filled / total * 100.0) if total else 0
    pct200 = (over_200 / filled * 100.0) if filled else 0
    pct300 = (over_300 / filled * 100.0) if filled else 0

    print()
    print("=" * 72)
    print("S2 backfill -- sanity check")
    print("=" * 72)
    print(f"  fundamental 총 행 수      : {total:>10,}")
    print(f"  debt_ratio 채워진 행      : {filled:>10,} ({pct:>5.1f}%)")
    print(f"  debt_ratio > 200% 비율    : {over_200:>10,} ({pct200:>5.1f}% of filled)")
    print(f"  debt_ratio > 300% 비율    : {over_300:>10,} ({pct300:>5.1f}% of filled)")
    print(f"  자본잠식 (TOTAL_EQUITY<=0) : {impaired:>10,}")
    if r_005620:
        dr = r_005620[1]
        te = r_005620[2]
        tl = r_005620[3]
        print(
            f"  005620 ({r_005620[0]})       : "
            f"debt_ratio={dr:.1f}% (te={te:.0f}, tl={tl:.0f})"
        )
    else:
        print("  005620 (2017-06~07월)     : (조회 결과 없음)")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="S2 부채/자본 데이터 백필")
    parser.add_argument(
        "--start-date", default="2017-01-01", type=parse_date_arg,
    )
    parser.add_argument(
        "--end-date",
        default=datetime.now().date().strftime("%Y-%m-%d"),
        type=parse_date_arg,
    )
    parser.add_argument(
        "--market", default="KOSPI",
        choices=["KOSPI", "KOSDAQ", "ALL"],
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--skip-sanity", action="store_true",
        help="백필 후 sanity check 스킵",
    )
    args = parser.parse_args()

    setup_logging()
    storage = DataStorage()
    dart = DartClient()
    if not dart.api_key:
        logger.error("DART_API_KEY 미설정")
        return 1
    _ = dart.corp_code_map  # 사전 로드

    pairs = list_target_pairs(
        storage, args.start_date, args.end_date, args.market, args.force,
    )
    if not pairs:
        logger.info("백필 대상 페어 없음 (모두 채워졌거나 범위 데이터 부재)")
        if not args.skip_sanity:
            sanity_check(storage)
        return 0

    logger.info(
        f"백필 대상: {len(pairs)} (date, market) 페어 "
        f"({args.start_date} ~ {args.end_date}, {args.market})"
    )

    total_updated = 0
    for i, (dt, market) in enumerate(pairs, 1):
        # 해당 (date, market)의 ticker 목록 조회
        with storage.engine.connect() as conn:
            ticker_rows = conn.execute(
                text(
                    "SELECT DISTINCT ticker FROM fundamental "
                    "WHERE date = :dt "
                    "AND (market = :m OR market IS NULL)"
                ),
                {"dt": str(dt), "m": market},
            ).fetchall()
        tickers = [r[0] for r in ticker_rows]
        if not tickers:
            continue

        date_str = dt.strftime("%Y%m%d")
        logger.info(
            f"[{i}/{len(pairs)}] {dt} {market} — {len(tickers)}종목 BS 조회"
        )
        try:
            data = fetch_bs_data(dart, tickers, date_str)
        except Exception as e:
            logger.error(f"BS 조회 실패 ({dt}, {market}): {e}")
            continue

        if not data:
            logger.warning("  응답 비어 있음 — 0건")
            continue

        try:
            n = update_fundamental_debt(storage, dt, market, data)
            total_updated += n
            logger.info(f"  UPDATE: {n}행 (BS 응답 {len(data)}종목)")
        except Exception as e:
            logger.error(f"UPDATE 실패 ({dt}, {market}): {e}")

    logger.info(
        f"백필 완료: {len(pairs)} 페어 / 총 {total_updated:,}행 UPDATE"
    )

    if not args.skip_sanity:
        sanity_check(storage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
