# dart_notifier/storage.py
"""DART 공시 알림 이력 DB (monitor.db 공유)

monitor.db에 dart_disclosures 테이블을 추가하여
중복 알림을 방지한다.
"""

import logging
import os
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Index, String, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)

_DEFAULT_DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


class Base(DeclarativeBase):
    pass


class DartDisclosure(Base):
    """DART 공시 알림 이력"""

    __tablename__ = "dart_disclosures"

    rcept_no = Column(String(20), primary_key=True)
    corp_code = Column(String(10), nullable=False)
    stock_code = Column(String(10), nullable=False, index=True)
    report_nm = Column(String(200), nullable=False)
    pblntf_detail_ty = Column(String(10), nullable=True)
    rcept_dt = Column(String(10), nullable=False, index=True)
    notified_at = Column(String(30), nullable=False)
    category = Column(String(20), nullable=False)  # 'instant' | 'daily_summary'


class DartDisclosureStorage:
    """DART 공시 알림 이력 DB

    monitor.db를 공유하며 dart_disclosures 테이블만 관리한다.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            db_path = os.path.join(_DEFAULT_DB_DIR, "monitor.db")

        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(dbapi_conn: object, _: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def is_notified(self, rcept_no: str) -> bool:
        """이미 알림을 보낸 공시인지 확인한다.

        Args:
            rcept_no: DART 접수번호

        Returns:
            True면 이미 알림 발송됨
        """
        with self.SessionLocal() as session:
            row = session.get(DartDisclosure, rcept_no)
            return row is not None

    def mark_notified(
        self,
        rcept_no: str,
        corp_code: str,
        stock_code: str,
        report_nm: str,
        pblntf_detail_ty: Optional[str],
        rcept_dt: str,
        category: str,
    ) -> bool:
        """공시 알림 이력을 저장한다 (INSERT OR IGNORE).

        Args:
            rcept_no: DART 접수번호
            corp_code: 기업 고유번호
            stock_code: 종목코드 (6자리)
            report_nm: 보고서명
            pblntf_detail_ty: 공시상세유형
            rcept_dt: 접수일 (YYYYMMDD)
            category: 'instant' | 'daily_summary'

        Returns:
            True면 새로 삽입됨 (중복 아님)
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.SessionLocal() as session:
                # INSERT OR IGNORE
                stmt = text(
                    "INSERT OR IGNORE INTO dart_disclosures "
                    "(rcept_no, corp_code, stock_code, report_nm, "
                    " pblntf_detail_ty, rcept_dt, notified_at, category) "
                    "VALUES (:rcept_no, :corp_code, :stock_code, :report_nm, "
                    " :pblntf_detail_ty, :rcept_dt, :notified_at, :category)"
                )
                result = session.execute(
                    stmt,
                    {
                        "rcept_no": rcept_no,
                        "corp_code": corp_code,
                        "stock_code": stock_code,
                        "report_nm": report_nm,
                        "pblntf_detail_ty": pblntf_detail_ty,
                        "rcept_dt": rcept_dt,
                        "notified_at": now,
                        "category": category,
                    },
                )
                session.commit()
                return result.rowcount > 0
        except Exception as e:
            logger.error("DART 공시 이력 저장 실패 (rcept_no=%s): %s", rcept_no, e)
            return False

    def get_unnotified_daily_summaries(self, rcept_dt: str) -> list[dict]:
        """특정 날짜의 일일 요약 대상 공시 목록을 반환한다.

        일일 요약은 category='daily_summary'이며 이미 mark_notified 된 건.

        Args:
            rcept_dt: 접수일 (YYYYMMDD)

        Returns:
            DartDisclosure 레코드 dict 리스트
        """
        with self.SessionLocal() as session:
            rows = (
                session.query(DartDisclosure)
                .filter(
                    DartDisclosure.rcept_dt == rcept_dt,
                    DartDisclosure.category == "daily_summary",
                )
                .all()
            )
            return [
                {
                    "rcept_no": r.rcept_no,
                    "corp_code": r.corp_code,
                    "stock_code": r.stock_code,
                    "report_nm": r.report_nm,
                    "pblntf_detail_ty": r.pblntf_detail_ty,
                    "rcept_dt": r.rcept_dt,
                }
                for r in rows
            ]
