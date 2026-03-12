# data/storage.py
import pandas as pd
import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Float,
    Integer,
    Date,
    DateTime,
    Boolean,
    BigInteger,
    Index,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config.settings import settings

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────
# ORM 모델
# ───────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


class DailyPrice(Base):
    """일별 OHLCV 데이터"""

    __tablename__ = "daily_price"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_daily_price_ticker_date"),
        Index("ix_daily_price_date_ticker", "date", "ticker"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(BigInteger)


class Fundamental(Base):
    """기본 지표 데이터 (PBR, PER, EPS, BPS, DIV)"""

    __tablename__ = "fundamental"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_fundamental_ticker_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    bps = Column(Float)
    per = Column(Float)
    pbr = Column(Float)
    eps = Column(Float)
    div = Column(Float)


class MarketCap(Base):
    """시가총액 데이터"""

    __tablename__ = "market_cap"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_market_cap_ticker_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    market_cap = Column(BigInteger)
    shares = Column(BigInteger)


class FactorScore(Base):
    """팩터 스코어"""

    __tablename__ = "factor_score"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_factor_score_ticker_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    value_score = Column(Float)
    momentum_score = Column(Float)
    quality_score = Column(Float)
    composite_score = Column(Float)


class Portfolio(Base):
    """포트폴리오 구성"""

    __tablename__ = "portfolio"
    __table_args__ = (
        UniqueConstraint("ticker", "rebalance_date", name="uq_portfolio_ticker_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    rebalance_date = Column(Date, nullable=False, index=True)
    ticker = Column(String(10), nullable=False)
    name = Column(String(50))
    weight = Column(Float)
    composite_score = Column(Float)


class Trade(Base):
    """거래 이력"""

    __tablename__ = "trade"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, index=True)
    ticker = Column(String(10), nullable=False)
    side = Column(String(4), nullable=False)  # BUY / SELL
    quantity = Column(Integer)
    price = Column(Float)
    amount = Column(Float)
    commission = Column(Float)
    tax = Column(Float)
    is_paper = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ───────────────────────────────────────────────
# DataStorage
# ───────────────────────────────────────────────


class DataStorage:
    """SQLite 데이터 저장/조회 (SQLAlchemy ORM)"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        """
        Args:
            db_path: SQLite DB 경로. None이면 settings.db_path 사용.
        """
        path = db_path or settings.db_path
        self.engine = create_engine(f"sqlite:///{path}", echo=False)

        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        logger.info(f"DB 연결: {path}")

    # ───────────────────────────────────────────────
    # 일별 가격
    # ───────────────────────────────────────────────

    def save_daily_prices(self, ticker: str, df: pd.DataFrame) -> int:
        """OHLCV 데이터 upsert 저장

        Args:
            ticker: 종목코드
            df: DataFrame(index=date, columns=[open, high, low, close, volume])

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        tmp = df.reset_index()
        tmp = tmp.rename(columns={tmp.columns[0]: "date"})
        tmp["ticker"] = ticker
        tmp["date"] = pd.to_datetime(tmp["date"]).dt.date
        rows = tmp[["ticker", "date", "open", "high", "low", "close", "volume"]].to_dict("records")

        with self.SessionLocal() as session:
            stmt = sqlite_insert(DailyPrice).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "date"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                },
            )
            session.execute(stmt)
            session.commit()

        logger.debug(f"일별 가격 저장: {ticker} ({len(rows)}건)")
        return len(rows)

    def load_daily_prices(
        self,
        ticker: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """일별 가격 조회 (pd.read_sql 직접 변환)

        Args:
            ticker: 종목코드
            start_date: 시작 날짜
            end_date: 종료 날짜

        Returns:
            DataFrame(index=date, columns=[open, high, low, close, volume])
        """
        sql = (
            "SELECT date, open, high, low, close, volume "
            "FROM daily_price WHERE ticker = :ticker"
        )
        params: dict = {"ticker": ticker}
        if start_date:
            sql += " AND date >= :sd"
            params["sd"] = str(start_date)
        if end_date:
            sql += " AND date <= :ed"
            params["ed"] = str(end_date)
        sql += " ORDER BY date"

        from sqlalchemy import text

        with self.engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params, parse_dates=["date"])

        if df.empty:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df.set_index("date")

    def load_daily_prices_for_date(self, dt: date) -> int:
        """특정 날짜의 캐시된 OHLCV 종목 수 조회 (프리페치 스킵 판단용)

        Args:
            dt: 기준 날짜

        Returns:
            해당 날짜에 저장된 종목 수
        """
        from sqlalchemy import text

        with self.engine.connect() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM daily_price WHERE date = :dt"),
                {"dt": str(dt)},
            )
            return result.scalar() or 0

    # ───────────────────────────────────────────────
    # 기본 지표
    # ───────────────────────────────────────────────

    def save_fundamentals(self, dt: date, df: pd.DataFrame) -> int:
        """기본 지표 upsert 저장

        Args:
            dt: 기준 날짜
            df: DataFrame(index=ticker, columns=[BPS, PER, PBR, EPS, DIV])

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        tmp = df.reset_index()
        tmp = tmp.rename(columns={tmp.columns[0]: "ticker"})
        tmp["date"] = dt
        col_map = {"BPS": "bps", "PER": "per", "PBR": "pbr", "EPS": "eps", "DIV": "div"}
        for old, new in col_map.items():
            if old in tmp.columns:
                tmp[new] = tmp[old]
            else:
                tmp[new] = None
        rows = tmp[["ticker", "date", "bps", "per", "pbr", "eps", "div"]].to_dict("records")

        with self.SessionLocal() as session:
            stmt = sqlite_insert(Fundamental).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "date"],
                set_={
                    "bps": stmt.excluded.bps,
                    "per": stmt.excluded.per,
                    "pbr": stmt.excluded.pbr,
                    "eps": stmt.excluded.eps,
                    "div": stmt.excluded.div,
                },
            )
            session.execute(stmt)
            session.commit()

        logger.info(f"기본 지표 저장: {dt} ({len(rows)}건)")
        return len(rows)

    def load_fundamentals(self, dt: date) -> pd.DataFrame:
        """기본 지표 조회

        Args:
            dt: 기준 날짜

        Returns:
            DataFrame(index=ticker, columns=[BPS, PER, PBR, EPS, DIV])
        """
        with self.SessionLocal() as session:
            rows = [
                {
                    "ticker": r.ticker,
                    "BPS": r.bps,
                    "PER": r.per,
                    "PBR": r.pbr,
                    "EPS": r.eps,
                    "DIV": r.div,
                }
                for r in session.query(Fundamental).filter_by(date=dt).all()
            ]

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows).set_index("ticker")

    # ───────────────────────────────────────────────
    # 시가총액
    # ───────────────────────────────────────────────

    def save_market_caps(self, dt: date, df: pd.DataFrame) -> int:
        """시가총액 upsert 저장

        Args:
            dt: 기준 날짜
            df: DataFrame(index=ticker, columns=[market_cap, shares])

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        tmp = df.reset_index()
        tmp = tmp.rename(columns={tmp.columns[0]: "ticker"})
        tmp["date"] = dt
        rows = tmp[["ticker", "date", "market_cap", "shares"]].to_dict("records")

        with self.SessionLocal() as session:
            stmt = sqlite_insert(MarketCap).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "date"],
                set_={
                    "market_cap": stmt.excluded.market_cap,
                    "shares": stmt.excluded.shares,
                },
            )
            session.execute(stmt)
            session.commit()

        logger.info(f"시가총액 저장: {dt} ({len(rows)}건)")
        return len(rows)

    def load_market_caps(self, dt: date) -> pd.DataFrame:
        """시가총액 조회

        Args:
            dt: 기준 날짜

        Returns:
            DataFrame(index=ticker, columns=[market_cap, shares])
        """
        with self.SessionLocal() as session:
            rows = [
                {
                    "ticker": r.ticker,
                    "market_cap": r.market_cap,
                    "shares": r.shares,
                }
                for r in session.query(MarketCap).filter_by(date=dt).all()
            ]

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows).set_index("ticker")

    # ───────────────────────────────────────────────
    # 일별 가격 (벌크)
    # ───────────────────────────────────────────────

    def load_daily_prices_bulk(
        self,
        tickers: list[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """여러 종목의 일별 가격 일괄 조회 (pd.read_sql 직접 변환)

        Args:
            tickers: 종목코드 리스트
            start_date: 시작 날짜
            end_date: 종료 날짜

        Returns:
            DataFrame(columns=[ticker, date, open, high, low, close, volume])
        """
        if not tickers:
            return pd.DataFrame()

        from sqlalchemy import text

        # SQLite IN clause 변수 제한 (기본 999) 대응: 청크 분할
        chunk_size = 900
        frames: list[pd.DataFrame] = []

        with self.engine.connect() as conn:
            for i in range(0, len(tickers), chunk_size):
                chunk = tickers[i:i + chunk_size]
                placeholders = ", ".join(f":t{j}" for j in range(len(chunk)))
                sql = (
                    f"SELECT ticker, date, open, high, low, close, volume "
                    f"FROM daily_price WHERE ticker IN ({placeholders})"
                )
                params: dict = {f"t{j}": t for j, t in enumerate(chunk)}
                if start_date:
                    sql += " AND date >= :sd"
                    params["sd"] = str(start_date)
                if end_date:
                    sql += " AND date <= :ed"
                    params["ed"] = str(end_date)
                sql += " ORDER BY ticker, date"

                df = pd.read_sql(text(sql), conn, params=params, parse_dates=["date"])
                if not df.empty:
                    frames.append(df)

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        if "date" in result.columns:
            result["date"] = pd.to_datetime(result["date"]).dt.date
        return result

    def save_daily_prices_bulk(self, dt: date, df: pd.DataFrame) -> int:
        """여러 종목의 일별 가격 일괄 upsert 저장

        Args:
            dt: 기준 날짜
            df: DataFrame(index=ticker, columns=[open, high, low, close, volume])

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        tmp = df.reset_index()
        tmp = tmp.rename(columns={tmp.columns[0]: "ticker"})
        tmp["date"] = dt
        rows = tmp[["ticker", "date", "open", "high", "low", "close", "volume"]].to_dict("records")

        with self.SessionLocal() as session:
            stmt = sqlite_insert(DailyPrice).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "date"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                },
            )
            session.execute(stmt)
            session.commit()

        logger.info(f"일별 가격 일괄 저장: {dt} ({len(rows)}건)")
        return len(rows)

    # ───────────────────────────────────────────────
    # 팩터 스코어
    # ───────────────────────────────────────────────

    def save_factor_scores(self, dt: date, df: pd.DataFrame) -> int:
        """팩터 스코어 upsert 저장

        Args:
            dt: 기준 날짜
            df: DataFrame(index=ticker, columns=[value_score, momentum_score, quality_score, composite_score])

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        tmp = df.reset_index()
        tmp = tmp.rename(columns={tmp.columns[0]: "ticker"})
        tmp["date"] = dt
        rows = tmp[["ticker", "date", "value_score", "momentum_score", "quality_score", "composite_score"]].to_dict("records")

        with self.SessionLocal() as session:
            stmt = sqlite_insert(FactorScore).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "date"],
                set_={
                    "value_score": stmt.excluded.value_score,
                    "momentum_score": stmt.excluded.momentum_score,
                    "quality_score": stmt.excluded.quality_score,
                    "composite_score": stmt.excluded.composite_score,
                },
            )
            session.execute(stmt)
            session.commit()

        logger.info(f"팩터 스코어 저장: {dt} ({len(rows)}건)")
        return len(rows)

    # ───────────────────────────────────────────────
    # 포트폴리오
    # ───────────────────────────────────────────────

    def save_portfolio(self, rebalance_date: date, df: pd.DataFrame) -> int:
        """포트폴리오 upsert 저장

        Args:
            rebalance_date: 리밸런싱 날짜
            df: DataFrame(columns=[ticker, name, weight, composite_score])

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        tmp = df.copy()
        tmp["rebalance_date"] = rebalance_date
        rows = tmp[["rebalance_date", "ticker", "name", "weight", "composite_score"]].to_dict("records")

        with self.SessionLocal() as session:
            stmt = sqlite_insert(Portfolio).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["ticker", "rebalance_date"],
                set_={
                    "name": stmt.excluded.name,
                    "weight": stmt.excluded.weight,
                    "composite_score": stmt.excluded.composite_score,
                },
            )
            session.execute(stmt)
            session.commit()

        logger.info(f"포트폴리오 저장: {rebalance_date} ({len(rows)}건)")
        return len(rows)

    # ───────────────────────────────────────────────
    # 거래 이력
    # ───────────────────────────────────────────────

    def save_trade(
        self,
        trade_date: date,
        ticker: str,
        side: str,
        quantity: int,
        price: float,
        amount: float,
        commission: float = 0.0,
        tax: float = 0.0,
        is_paper: bool = True,
    ) -> None:
        """거래 이력 저장

        Args:
            trade_date: 거래 날짜
            ticker: 종목코드
            side: BUY / SELL
            quantity: 수량
            price: 가격
            amount: 금액
            commission: 수수료
            tax: 거래세
            is_paper: 모의 거래 여부
        """
        with self.SessionLocal() as session:
            session.add(
                Trade(
                    trade_date=trade_date,
                    ticker=ticker,
                    side=side,
                    quantity=quantity,
                    price=price,
                    amount=amount,
                    commission=commission,
                    tax=tax,
                    is_paper=is_paper,
                )
            )
            session.commit()

        logger.info(f"거래 저장: {side} {ticker} {quantity}주 @ {price:,.0f}")

    def load_trades(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """거래 이력 조회

        Args:
            start_date: 시작 날짜
            end_date: 종료 날짜

        Returns:
            DataFrame(columns=[trade_date, ticker, side, quantity, price, amount, ...])
        """
        with self.SessionLocal() as session:
            query = session.query(Trade)
            if start_date:
                query = query.filter(Trade.trade_date >= start_date)
            if end_date:
                query = query.filter(Trade.trade_date <= end_date)
            query = query.order_by(Trade.trade_date)

            rows = [
                {
                    "trade_date": r.trade_date,
                    "ticker": r.ticker,
                    "side": r.side,
                    "quantity": r.quantity,
                    "price": r.price,
                    "amount": r.amount,
                    "commission": r.commission,
                    "tax": r.tax,
                    "is_paper": r.is_paper,
                }
                for r in query.all()
            ]

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows)
