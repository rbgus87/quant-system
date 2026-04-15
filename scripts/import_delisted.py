"""KRX KIND 상장폐지현황 파일을 DB로 임포트.

기본 입력: data/seed/delisted_stocks.xls (HTML 테이블, EUC-KR)
출력: delisted_stock 테이블 upsert

사용:
    python scripts/import_delisted.py
    python scripts/import_delisted.py --file /path/to/other.xls
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import setup_logging
from data.storage import DataStorage

logger = logging.getLogger(__name__)


# 카테고리 분류 키워드 (우선순위 순서)
FAILURE_KEYWORDS = [
    "해산", "자본전액잠식", "감사의견", "의견거절", "계속성",
    "영업활동정지", "부도", "자본잠식", "회생",
]
MERGER_KEYWORDS = [
    "스팩", "피흡수합병", "합병상장", "이전상장",
    "거래소 상장", "코스닥시장 이전", "유가증권시장 상장",
]
VOLUNTARY_KEYWORDS = ["신청에 의한", "자진"]
EXPIRED_KEYWORDS = ["존속기간"]


def classify(name: str, reason: str) -> str:
    """회사명 + 폐지사유 → 카테고리.

    우선순위:
      1. failure: 회사명에 "스팩" 없으면서 사유에 FAILURE_KEYWORDS 포함
      2. merger: 사유에 MERGER_KEYWORDS 포함
      3. voluntary: 사유에 VOLUNTARY_KEYWORDS 포함
      4. expired: 사유에 EXPIRED_KEYWORDS 포함
      5. other

    Args:
        name: 회사명
        reason: 폐지사유

    Returns:
        카테고리 문자열
    """
    name = name or ""
    reason = reason or ""

    is_spac = "스팩" in name
    if not is_spac and any(kw in reason for kw in FAILURE_KEYWORDS):
        return "failure"
    if any(kw in reason for kw in MERGER_KEYWORDS):
        return "merger"
    if any(kw in reason for kw in VOLUNTARY_KEYWORDS):
        return "voluntary"
    if any(kw in reason for kw in EXPIRED_KEYWORDS):
        return "expired"
    return "other"


def parse_file(file_path: Path) -> tuple[list[dict], int]:
    """KIND 상장폐지현황 파일 파싱.

    Args:
        file_path: .xls 파일 경로

    Returns:
        (rows, skipped) — upsert용 row 리스트와 날짜 파싱 실패로 스킵된 건수
    """
    df = pd.read_html(str(file_path), encoding="euc-kr")[0]
    df.columns = ["번호", "회사명", "종목코드", "폐지일자", "폐지사유", "비고"]

    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)
    parsed_date = pd.to_datetime(df["폐지일자"], errors="coerce")

    invalid_mask = parsed_date.isna()
    skipped = int(invalid_mask.sum())
    if skipped:
        logger.warning(f"폐지일자 파싱 실패 {skipped}건 — 스킵")
        for _, r in df[invalid_mask].iterrows():
            logger.warning(f"  스킵: {r['종목코드']} {r['회사명']} ({r['폐지일자']!r})")

    df = df[~invalid_mask].copy()
    df["폐지일자_parsed"] = parsed_date[~invalid_mask]

    # 동일 ticker 중복 시 최신 폐지일자 레코드만 유지
    before = len(df)
    df = df.sort_values("폐지일자_parsed").drop_duplicates(
        subset=["종목코드"], keep="last"
    )
    dup_removed = before - len(df)
    if dup_removed:
        logger.info(f"중복 ticker dedupe: {dup_removed}건 제거 (최신 폐지일 유지)")

    rows: list[dict] = []
    for _, r in df.iterrows():
        name = str(r["회사명"]).strip()
        reason = str(r["폐지사유"]).strip() if pd.notna(r["폐지사유"]) else ""
        memo_val = r["비고"]
        memo = (
            str(memo_val).strip()
            if pd.notna(memo_val) and str(memo_val).strip()
            else None
        )
        rows.append({
            "ticker": str(r["종목코드"]),
            "name": name,
            "delist_date": r["폐지일자_parsed"].date(),
            "reason": reason,
            "category": classify(name, reason),
            "memo": memo,
        })
    return rows, skipped


def summarize(storage: DataStorage, rows: list[dict]) -> None:
    """임포트 결과 요약 출력."""
    import sqlalchemy as sa

    logger.info("─" * 60)
    logger.info("카테고리별 전체 건수 (DB 기준):")
    with storage.engine.connect() as conn:
        cat_rows = conn.execute(
            sa.text(
                "SELECT category, COUNT(*) AS n FROM delisted_stock "
                "GROUP BY category ORDER BY n DESC"
            )
        ).all()
        for cat, n in cat_rows:
            marker = " ★" if cat == "failure" else ""
            logger.info(f"  {cat:<12} {n:>5}건{marker}")

        logger.info("")
        logger.info("연도별 분포 (최근 10년):")
        year_rows = conn.execute(
            sa.text(
                "SELECT strftime('%Y', delist_date) AS year, COUNT(*) AS n, "
                "SUM(CASE WHEN category='failure' THEN 1 ELSE 0 END) AS failures "
                "FROM delisted_stock WHERE delist_date >= '2015-01-01' "
                "GROUP BY year ORDER BY year DESC"
            )
        ).all()
        for year, n, failures in year_rows:
            logger.info(f"  {year}: 전체 {n:>4}건 / failure {failures:>3}건")

        logger.info("")
        logger.info("2017-2024 백테스트 영향 범위:")
        total = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM delisted_stock "
                "WHERE delist_date BETWEEN '2017-01-01' AND '2024-12-31'"
            )
        ).scalar()
        failures = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM delisted_stock "
                "WHERE delist_date BETWEEN '2017-01-01' AND '2024-12-31' "
                "AND category='failure'"
            )
        ).scalar()
        logger.info(f"  전체 폐지 {total}종목 중 failure {failures}종목")
    logger.info("─" * 60)


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="상장폐지 종목 DB 임포트")
    parser.add_argument(
        "--file",
        default="data/seed/delisted_stocks.xls",
        help="KIND 상장폐지현황 .xls 경로",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        logger.error(f"파일을 찾을 수 없음: {file_path}")
        return 1

    logger.info(f"임포트 시작: {file_path}")
    rows, skipped = parse_file(file_path)
    logger.info(f"파싱 완료: 유효 {len(rows)}건, 스킵 {skipped}건")

    storage = DataStorage()
    inserted, updated = storage.upsert_delisted_stocks(rows)
    logger.info(
        f"DB upsert 완료: 신규 추가 {inserted}건 / 업데이트 {updated}건 / "
        f"스킵 {skipped}건"
    )

    summarize(storage, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
