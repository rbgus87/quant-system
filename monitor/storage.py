# monitor/storage.py
"""일간 스냅샷 DB 저장 모듈

별도 monitor.db를 사용하여 quant.db와의 write contention을 방지한다.
"""

import logging
import os
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Column,
    Date,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────
# ORM 모델
# ───────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


class DailySnapshot(Base):
    """일별 포트폴리오 스냅샷"""

    __tablename__ = "daily_snapshots"

    date = Column(Date, primary_key=True)
    total_value = Column(Integer, nullable=False)
    total_invested = Column(Integer, nullable=False)
    cash = Column(Integer, nullable=False, default=0)
    daily_return_pct = Column(Float, nullable=False, default=0.0)
    total_return_pct = Column(Float, nullable=False, default=0.0)
    kospi_daily_return_pct = Column(Float, nullable=False, default=0.0)
    excess_return_pct = Column(Float, nullable=False, default=0.0)
    mdd_pct = Column(Float, nullable=False, default=0.0)


class DailyHolding(Base):
    """일별 보유 종목 상세"""

    __tablename__ = "daily_holdings"
    __table_args__ = (
        UniqueConstraint("date", "ticker", name="uq_daily_holding_date_ticker"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    ticker = Column(String(10), nullable=False)
    name = Column(String(50), nullable=True)
    qty = Column(Integer, nullable=False, default=0)
    avg_price = Column(Integer, nullable=False, default=0)
    current_price = Column(Integer, nullable=False, default=0)
    return_pct = Column(Float, nullable=False, default=0.0)
    weight_pct = Column(Float, nullable=False, default=0.0)


# ───────────────────────────────────────────────
# Storage 클래스
# ───────────────────────────────────────────────

_DEFAULT_DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


class MonitorStorage:
    """일간 스냅샷 전용 DB (monitor.db)"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            db_path = os.path.join(_DEFAULT_DB_DIR, "monitor.db")

        self.db_path = db_path
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(dbapi_conn: object, _: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def save_snapshot(self, snapshot: dict) -> None:
        """스냅샷 dict를 daily_snapshots + daily_holdings에 저장한다.

        Args:
            snapshot: take_daily_snapshot() 반환값
        """
        dt = datetime.strptime(snapshot["date"], "%Y-%m-%d").date()
        portfolio = snapshot["portfolio"]
        benchmark = snapshot.get("benchmark", {})

        # daily_snapshots upsert
        snap_row = {
            "date": dt,
            "total_value": portfolio["total_value"],
            "total_invested": portfolio["total_invested"],
            "cash": portfolio["cash"],
            "daily_return_pct": portfolio["daily_return_pct"],
            "total_return_pct": portfolio["total_return_pct"],
            "kospi_daily_return_pct": benchmark.get("kospi_daily_return_pct", 0.0),
            "excess_return_pct": benchmark.get("excess_return_pct", 0.0),
            "mdd_pct": portfolio["mdd_pct"],
        }

        with self.SessionLocal() as session:
            stmt = sqlite_insert(DailySnapshot).values([snap_row])
            stmt = stmt.on_conflict_do_update(
                index_elements=["date"],
                set_={
                    k: getattr(stmt.excluded, k)
                    for k in snap_row
                    if k != "date"
                },
            )
            session.execute(stmt)

            # daily_holdings: 기존 데이터 삭제 후 삽입
            session.execute(
                text("DELETE FROM daily_holdings WHERE date = :dt"),
                {"dt": dt},
            )
            for h in snapshot.get("holdings", []):
                hold_row = {
                    "date": dt,
                    "ticker": h["ticker"],
                    "name": h.get("name", ""),
                    "qty": h["qty"],
                    "avg_price": h["avg_price"],
                    "current_price": h["current_price"],
                    "return_pct": h["return_pct"],
                    "weight_pct": h["weight_pct"],
                }
                session.execute(sqlite_insert(DailyHolding).values([hold_row]))

            session.commit()

        logger.info("스냅샷 저장 완료: %s (%d 종목)", dt, len(snapshot.get("holdings", [])))

    def get_latest_snapshot(self) -> Optional[dict]:
        """가장 최근 스냅샷을 반환한다.

        Returns:
            스냅샷 dict 또는 None
        """
        with self.SessionLocal() as session:
            row = (
                session.query(DailySnapshot)
                .order_by(DailySnapshot.date.desc())
                .first()
            )
            if row is None:
                return None

            holdings = (
                session.query(DailyHolding)
                .filter(DailyHolding.date == row.date)
                .all()
            )

            return self._to_dict(row, holdings)

    def get_snapshots_since(self, start_date: date) -> list[dict]:
        """특정 날짜 이후 스냅샷 목록을 반환한다.

        Args:
            start_date: 시작일 (포함)

        Returns:
            스냅샷 dict 리스트
        """
        with self.SessionLocal() as session:
            rows = (
                session.query(DailySnapshot)
                .filter(DailySnapshot.date >= start_date)
                .order_by(DailySnapshot.date)
                .all()
            )

            result = []
            for row in rows:
                holdings = (
                    session.query(DailyHolding)
                    .filter(DailyHolding.date == row.date)
                    .all()
                )
                result.append(self._to_dict(row, holdings))

            return result

    @staticmethod
    def _to_dict(snap: DailySnapshot, holdings: list[DailyHolding]) -> dict:
        """ORM 객체 → dict 변환"""
        return {
            "date": snap.date.strftime("%Y-%m-%d"),
            "portfolio": {
                "total_value": snap.total_value,
                "total_invested": snap.total_invested,
                "cash": snap.cash,
                "daily_return_pct": snap.daily_return_pct,
                "total_return_pct": snap.total_return_pct,
                "mdd_pct": snap.mdd_pct,
            },
            "benchmark": {
                "kospi_daily_return_pct": snap.kospi_daily_return_pct,
                "excess_return_pct": snap.excess_return_pct,
            },
            "holdings": [
                {
                    "ticker": h.ticker,
                    "name": h.name,
                    "qty": h.qty,
                    "avg_price": h.avg_price,
                    "current_price": h.current_price,
                    "return_pct": h.return_pct,
                    "weight_pct": h.weight_pct,
                }
                for h in holdings
            ],
        }
