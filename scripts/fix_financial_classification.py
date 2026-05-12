"""fix_financial_classification.py — is_financial 종목명 휴리스틱 기준으로 재정렬.

배경: S4-A 보강 단계에서 DART KSIC 매핑(64/66)이 지주회사·투자업체를 광범위하게
금융주로 분류했으나, baseline 비교 결과 CAGR -1.78%p 손실 확인됨.
종목명 휴리스틱 기준 ~58종목이 정확.

이 스크립트는:
1. stock_sector 테이블의 is_financial=True 모든 행 조회 (1045 종목)
2. 각 ticker의 종목명을 KRXDataCollector.get_ticker_name()으로 조회
3. classify_financial_by_name()으로 재판정 (휴리스틱)
4. 휴리스틱 미매칭 종목은 is_financial=False로 UPDATE
5. sector_name (DART KSIC 결과)은 그대로 보존 (S4-B 섹터 분산 활용)

이 스크립트는 idempotent (재실행 안전).

사용:
    python scripts/fix_financial_classification.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from data.collector import KRXDataCollector  # noqa: E402
from data.storage import DataStorage  # noqa: E402
from factors.composite import classify_financial_by_name  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    storage = DataStorage()
    collector = KRXDataCollector(request_delay=0.0)

    # 1. is_financial=True 인 고유 ticker 조회
    with storage.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT ticker FROM stock_sector WHERE is_financial = 1"
        )).fetchall()
    tickers = [r[0] for r in rows]
    logger.info(f"is_financial=True 고유 ticker: {len(tickers)}종목")

    # 2. 각 ticker 종목명 조회 + 휴리스틱 재판정
    keep_financial: set[str] = set()
    reset_to_nonfinancial: set[str] = set()
    name_map: dict[str, str] = {}

    for tk in tickers:
        name = collector.get_ticker_name(tk)
        name_map[tk] = name
        is_fin, _ = classify_financial_by_name(tk, name)
        if is_fin:
            keep_financial.add(tk)
        else:
            reset_to_nonfinancial.add(tk)

    logger.info(
        f"휴리스틱 매칭 유지: {len(keep_financial)}종목, "
        f"휴리스틱 미매칭 (복원 대상): {len(reset_to_nonfinancial)}종목"
    )

    # 3. 휴리스틱 미매칭 종목 → is_financial=False UPDATE
    updated_rows = 0
    with storage.engine.connect() as conn:
        chunk_size = 500
        ticker_list = list(reset_to_nonfinancial)
        for i in range(0, len(ticker_list), chunk_size):
            chunk = ticker_list[i:i + chunk_size]
            placeholders = ", ".join(f":t{j}" for j in range(len(chunk)))
            params = {f"t{j}": t for j, t in enumerate(chunk)}
            sql = (
                f"UPDATE stock_sector SET is_financial = 0 "
                f"WHERE ticker IN ({placeholders}) AND is_financial = 1"
            )
            r = conn.execute(text(sql), params)
            updated_rows += r.rowcount or 0
        conn.commit()

    logger.info(
        f"UPDATE 완료: {updated_rows:,}행 is_financial 1 → 0 복원"
    )

    # 4. sanity check (최신 date 기준)
    with storage.engine.connect() as conn:
        latest_date = conn.execute(
            text("SELECT MAX(date) FROM stock_sector")
        ).scalar()
        n_fin = conn.execute(text(
            "SELECT COUNT(*) FROM stock_sector "
            "WHERE date = :d AND is_financial = 1"
        ), {"d": latest_date}).scalar() or 0
        n_total = conn.execute(text(
            "SELECT COUNT(*) FROM stock_sector WHERE date = :d"
        ), {"d": latest_date}).scalar() or 0
        # 휴리스틱 유지 종목 샘플
        fin_rows = conn.execute(text(
            "SELECT ticker, sector_name FROM stock_sector "
            "WHERE date = :d AND is_financial = 1 "
            "ORDER BY ticker LIMIT 12"
        ), {"d": latest_date}).fetchall()

    print()
    print("=" * 72)
    print("is_financial 정리 완료 -- sanity check")
    print("=" * 72)
    print(f"  최신 date           : {latest_date}")
    print(f"  총 종목             : {n_total:>5}")
    print(f"  금융주 (휴리스틱)   : {n_fin:>5}")
    print(f"  비금융              : {n_total - n_fin:>5}")
    print(f"  UPDATE 행 수        : {updated_rows:>5}")
    print()
    print("금융주 샘플 (12개):")
    for ticker, sec in fin_rows:
        nm = name_map.get(ticker, "")
        print(f"    {ticker} {(nm or '')[:20]:<22} sector={sec}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
