# data/storage.py
import logging
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
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
        Index("ix_daily_price_ticker_date", "ticker", "date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    market = Column(String(10), nullable=False, default="KOSPI", server_default="KOSPI")
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(BigInteger)


class Fundamental(Base):
    """기본 지표 데이터 (PBR, PER, PCR, EPS, BPS, DIV + v2.0 확장)"""

    __tablename__ = "fundamental"
    __table_args__ = (
        UniqueConstraint("ticker", "date", "market", name="uq_fundamental_ticker_date_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    market = Column(String(10), nullable=False, default="KOSPI", server_default="KOSPI")
    bps = Column(Float)
    per = Column(Float)
    pbr = Column(Float)
    pcr = Column(Float)
    eps = Column(Float)
    div = Column(Float)
    # v2.0 확장 필드
    psr = Column(Float)                # 주가매출비율
    revenue = Column(Float)            # 매출액
    operating_income = Column(Float)   # 영업이익
    total_assets = Column(Float)       # 총자산
    opa = Column(Float)                # 영업이익/총자산 (OP/A)
    # S2: 부채비율 필터용
    total_equity = Column(Float)       # 자본총계
    total_liabilities = Column(Float)  # 부채총계
    debt_ratio = Column(Float)         # 부채비율 = 부채총계/자본총계 × 100 (%)
    data_source = Column(String(10), default="DART")  # 데이터 출처


class FundamentalQuarterly(Base):
    """분기별 재무 시계열 (Step 3 연속 흑자 필터용).

    동일 ticker에 대해 (bsns_year, reprt_code) 키로 분기 데이터를 누적한다.
    Fundamental 테이블은 단일 시점 데이터(스크리닝 기준일 1개)인 반면,
    본 테이블은 분기 시계열을 저장하여 연속 흑자 검증을 가능케 한다.

    EPS, 영업이익, 매출만 저장 (현재 필터 목적상 최소 컬럼).
    """

    __tablename__ = "fundamental_quarterly"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "bsns_year", "reprt_code",
            name="uq_fundq_ticker_year_reprt",
        ),
        Index("ix_fundq_ticker_period", "ticker", "bsns_year", "reprt_code"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    bsns_year = Column(String(4), nullable=False)      # "2024"
    reprt_code = Column(String(5), nullable=False)     # 11013/11012/11014/11011
    eps = Column(Float)
    operating_income = Column(Float)
    revenue = Column(Float)
    fs_div = Column(String(3))                          # CFS / OFS
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MarketCap(Base):
    """시가총액 데이터"""

    __tablename__ = "market_cap"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_market_cap_ticker_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    market = Column(String(10), nullable=False, default="KOSPI", server_default="KOSPI")
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
    rebalance_date = Column(Date, nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class StockSector(Base):
    """종목별 섹터(업종) 정보 (S4-A 금융주 제외용)

    업종 분류는 반기~연간 단위로 변경되므로 date 기준으로 관리.
    KRX/pykrx 섹터 API 차단(2025-12-27) 이후 종목명 휴리스틱 매칭 사용.
    """

    __tablename__ = "stock_sector"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_sector_ticker_date"),
        Index("ix_sector_date", "date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False, index=True)
    date = Column(String(8), nullable=False)         # YYYYMMDD
    sector_name = Column(String(50))                  # "은행"/"증권"/"보험"/"금융업" 등 또는 None
    sector_code = Column(String(10))                  # 업종코드 (있으면)
    is_financial = Column(Boolean, default=False, server_default="0")
    data_source = Column(String(20))                  # "krx_api"/"pykrx"/"name_heuristic"
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DelistedStock(Base):
    """상장폐지 종목 (KIND 시스템 기반 — 생존자 편향 보정용)"""

    __tablename__ = "delisted_stock"

    ticker = Column(String(10), primary_key=True)
    name = Column(String(100))
    delist_date = Column(Date, nullable=False, index=True)
    reason = Column(Text)
    category = Column(String(20), index=True)  # failure/merger/voluntary/expired/other
    memo = Column(Text, nullable=True)
    imported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
        self._migrate_fundamental_market_column()
        self._migrate_fundamental_pcr_column()
        self._migrate_fundamental_v2_columns()
        self._migrate_daily_price_market_column()
        self._migrate_market_cap_market_column()
        self._migrate_trade_rebalance_column()
        self._migrate_delisted_stock_table()
        self._migrate_compound_indexes()
        self.SessionLocal = sessionmaker(bind=self.engine)
        # Opt 3: 분기 재무 프리로드 캐시 (rebalancing 시작 전 벌크 로드)
        self._fq_preload: dict[str, pd.DataFrame] = {}
        self._fq_preload_date: Optional[date] = None
        logger.info(f"DB 연결: {path}")

    def _migrate_fundamental_market_column(self) -> None:
        """기존 DB에 fundamental.market 컬럼이 없으면 추가 (하위 호환)"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(fundamental)"))
                columns = [row[1] for row in result]
                if "market" not in columns:
                    conn.execute(
                        text("ALTER TABLE fundamental ADD COLUMN market VARCHAR(10) DEFAULT 'KOSPI'")
                    )
                    conn.commit()
                    logger.info("DB 마이그레이션: fundamental.market 컬럼 추가 완료")
        except Exception as e:
            logger.debug(f"fundamental 마이그레이션 스킵: {e}")

    def _migrate_fundamental_pcr_column(self) -> None:
        """기존 DB에 fundamental.pcr 컬럼이 없으면 추가 (하위 호환)"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(fundamental)"))
                columns = [row[1] for row in result]
                if "pcr" not in columns:
                    conn.execute(
                        text("ALTER TABLE fundamental ADD COLUMN pcr FLOAT")
                    )
                    conn.commit()
                    logger.info("DB 마이그레이션: fundamental.pcr 컬럼 추가 완료")
        except Exception as e:
            logger.debug(f"fundamental pcr 마이그레이션 스킵: {e}")

    def _migrate_daily_price_market_column(self) -> None:
        """기존 DB에 daily_price.market 컬럼이 없으면 추가 (하위 호환)"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(daily_price)"))
                columns = [row[1] for row in result]
                if "market" not in columns:
                    conn.execute(
                        text("ALTER TABLE daily_price ADD COLUMN market VARCHAR(10) DEFAULT 'KOSPI'")
                    )
                    conn.commit()
                    logger.info("DB 마이그레이션: daily_price.market 컬럼 추가 완료")
        except Exception as e:
            logger.debug(f"daily_price 마이그레이션 스킵: {e}")

    def _migrate_market_cap_market_column(self) -> None:
        """기존 DB에 market_cap.market 컬럼이 없으면 추가 (하위 호환)"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(market_cap)"))
                columns = [row[1] for row in result]
                if "market" not in columns:
                    conn.execute(
                        text("ALTER TABLE market_cap ADD COLUMN market VARCHAR(10) DEFAULT 'KOSPI'")
                    )
                    conn.commit()
                    logger.info("DB 마이그레이션: market_cap.market 컬럼 추가 완료")
        except Exception as e:
            logger.debug(f"market_cap 마이그레이션 스킵: {e}")

    def _migrate_fundamental_v2_columns(self) -> None:
        """기존 DB에 v2.0 fundamental 확장 컬럼이 없으면 추가.

        S2 (2026-05-12): total_equity / total_liabilities / debt_ratio 추가.
        모두 nullable. 기존 행 호환 (DB_SCHEMA_POLICY.md 준수).
        """
        v2_cols = {
            "psr": "FLOAT",
            "revenue": "FLOAT",
            "operating_income": "FLOAT",
            "total_assets": "FLOAT",
            "opa": "FLOAT",
            "data_source": "VARCHAR(10) DEFAULT 'DART'",
            # S2 (2026-05-12)
            "total_equity": "FLOAT",
            "total_liabilities": "FLOAT",
            "debt_ratio": "FLOAT",
        }
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(fundamental)"))
                existing = {row[1] for row in result}
                added = []
                for col, col_type in v2_cols.items():
                    if col not in existing:
                        conn.execute(
                            text(f"ALTER TABLE fundamental ADD COLUMN {col} {col_type}")
                        )
                        added.append(col)
                if added:
                    conn.commit()
                    logger.info(f"DB 마이그레이션: fundamental v2 컬럼 추가 ({', '.join(added)})")
        except Exception as e:
            logger.debug(f"fundamental v2 마이그레이션 스킵: {e}")

    def _migrate_trade_rebalance_column(self) -> None:
        """기존 DB에 trade.rebalance_date 컬럼이 없으면 추가"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(trade)"))
                columns = {row[1] for row in result}
                if "rebalance_date" not in columns:
                    conn.execute(
                        text("ALTER TABLE trade ADD COLUMN rebalance_date DATE")
                    )
                    conn.commit()
                    logger.info("DB 마이그레이션: trade.rebalance_date 컬럼 추가 완료")
        except Exception as e:
            logger.debug(f"trade 마이그레이션 스킵: {e}")

    def _migrate_compound_indexes(self) -> None:
        """v2.0 성능 진단에서 발견된 핵심 쿼리에 대한 복합 인덱스 추가.

        대상 쿼리 (스크리너/스케줄러/리포트 핫패스):
          - SELECT ... FROM fundamental WHERE date=? AND market=?
          - SELECT ... FROM market_cap WHERE date=? AND market=?
          - SELECT ... FROM factor_score WHERE date=?
          - SELECT ... FROM trade WHERE trade_date BETWEEN ?
          - SELECT ... FROM portfolio WHERE rebalance_date=?

        IF NOT EXISTS로 멱등성 보장. 기존 인덱스는 유지된다.
        """
        compound_indexes = [
            ("ix_fundamental_date_market", "fundamental", "date, market"),
            ("ix_market_cap_date_market", "market_cap", "date, market"),
            ("ix_factor_score_date", "factor_score", "date"),
            ("ix_trade_date", "trade", "trade_date"),
            ("ix_portfolio_rebalance", "portfolio", "rebalance_date"),
        ]
        try:
            with self.engine.connect() as conn:
                created: list[str] = []
                for name, table, cols in compound_indexes:
                    try:
                        conn.execute(
                            text(
                                f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})"
                            )
                        )
                        created.append(name)
                    except Exception as e:
                        logger.debug(f"인덱스 {name} 생성 스킵: {e}")
                conn.commit()
                if created:
                    # CREATE INDEX IF NOT EXISTS는 멱등이라 매번 created에 5개가
                    # 들어가 INFO로 찍히면 노이즈가 됨. DEBUG로 낮춤.
                    # 실제 컬럼/테이블 추가 마이그레이션은 별도 INFO 로그 유지.
                    logger.debug(
                        f"DB 마이그레이션: 복합 인덱스 확인 ({len(created)}개)"
                    )
        except Exception as e:
            logger.debug(f"복합 인덱스 마이그레이션 스킵: {e}")

    def _migrate_delisted_stock_table(self) -> None:
        """delisted_stock 테이블 초기 생성 (Base.metadata.create_all이 처리하지만,
        기존 DB에서 명시적으로 존재 여부 확인 — 로그 목적)"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='delisted_stock'"
                    )
                )
                if result.fetchone() is None:
                    logger.info("DB 마이그레이션: delisted_stock 테이블 생성 대기 중")
                else:
                    logger.debug("delisted_stock 테이블 확인 완료")
        except Exception as e:
            logger.debug(f"delisted_stock 마이그레이션 스킵: {e}")

    def backup(self) -> str:
        """DB 파일을 backups/ 디렉토리에 타임스탬프로 복사. 최근 5개만 유지.

        Returns:
            백업 파일 경로
        """
        import shutil
        from pathlib import Path

        db_path = Path(str(self.engine.url).replace("sqlite:///", ""))
        if not db_path.exists():
            logger.warning(f"DB 파일이 없습니다: {db_path}")
            return ""

        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{db_path.stem}_{timestamp}{db_path.suffix}"
        shutil.copy2(db_path, backup_path)
        logger.info(f"DB 백업 완료: {backup_path}")

        # 최근 5개만 유지
        backups = sorted(backup_dir.glob(f"{db_path.stem}_*{db_path.suffix}"))
        for old in backups[:-5]:
            old.unlink()
            logger.debug(f"오래된 백업 삭제: {old}")

        return str(backup_path)

    # ───────────────────────────────────────────────
    # 내부 헬퍼
    # ───────────────────────────────────────────────

    def _upsert(
        self,
        model: type[Base],
        rows: list[dict],
        conflict_cols: list[str],
        update_cols: list[str],
    ) -> None:
        """공통 upsert 실행 (SQLite 변수 제한 999 대응 — 자동 청크 분할)

        Args:
            model: SQLAlchemy ORM 모델 클래스
            rows: 삽입할 딕셔너리 리스트
            conflict_cols: 충돌 판단 컬럼 (index_elements)
            update_cols: 충돌 시 갱신할 컬럼
        """
        if not rows:
            return

        # SQLite 변수 제한: 한 행당 컬럼 수 × 행 수 < 999
        cols_per_row = len(rows[0])
        chunk_size = max(1, 900 // cols_per_row)

        with self.SessionLocal() as session:
            for i in range(0, len(rows), chunk_size):
                chunk = rows[i:i + chunk_size]
                stmt = sqlite_insert(model).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=conflict_cols,
                    set_={col: getattr(stmt.excluded, col) for col in update_cols},
                )
                session.execute(stmt)
            session.commit()

    def _df_to_rows(
        self,
        df: pd.DataFrame,
        dt: date,
        columns: list[str],
        index_name: str = "ticker",
    ) -> list[dict]:
        """DataFrame을 rows 딕셔너리 리스트로 변환

        Args:
            df: 변환할 DataFrame (index가 ticker 등)
            dt: 기준 날짜 (date 컬럼으로 추가)
            columns: 추출할 컬럼 리스트 (index_name, date 포함)
            index_name: reset_index 후 첫 컬럼에 부여할 이름

        Returns:
            딕셔너리 리스트
        """
        tmp = df.reset_index()
        tmp = tmp.rename(columns={tmp.columns[0]: index_name})
        tmp["date"] = dt
        return tmp[columns].to_dict("records")

    # ───────────────────────────────────────────────
    # 일별 가격
    # ───────────────────────────────────────────────

    def save_daily_prices(self, ticker: str, df: pd.DataFrame, market: str = "KOSPI") -> int:
        """OHLCV 데이터 upsert 저장

        Args:
            ticker: 종목코드
            df: DataFrame(index=date, columns=[open, high, low, close, volume])
            market: 시장 구분 (KOSPI/KOSDAQ)

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        tmp = df.reset_index()
        tmp = tmp.rename(columns={tmp.columns[0]: "date"})
        tmp["ticker"] = ticker
        tmp["market"] = market
        tmp["date"] = pd.to_datetime(tmp["date"]).dt.date
        rows = tmp[["ticker", "date", "market", "open", "high", "low", "close", "volume"]].to_dict("records")

        self._upsert(
            DailyPrice, rows,
            conflict_cols=["ticker", "date"],
            update_cols=["market", "open", "high", "low", "close", "volume"],
        )

        logger.debug(f"일별 가격 저장: {ticker} {market} ({len(rows)}건)")
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

        with self.engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params, parse_dates=["date"])

        if df.empty:
            return pd.DataFrame()

        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df.set_index("date")

    def load_daily_prices_for_date(self, dt: date, market: str = "KOSPI") -> int:
        """특정 날짜의 캐시된 OHLCV 종목 수 조회 (프리페치 스킵 판단용)

        Args:
            dt: 기준 날짜
            market: 시장 구분 (KOSPI/KOSDAQ)

        Returns:
            해당 날짜·시장에 저장된 종목 수
        """
        with self.engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT COUNT(*) FROM daily_price "
                    "WHERE date = :dt AND (market = :market OR market IS NULL)"
                ),
                {"dt": str(dt), "market": market},
            )
            return result.scalar() or 0

    # ───────────────────────────────────────────────
    # 기본 지표
    # ───────────────────────────────────────────────

    def save_fundamentals(self, dt: date, df: pd.DataFrame, market: str = "KOSPI") -> int:
        """기본 지표 upsert 저장

        Args:
            dt: 기준 날짜
            df: DataFrame(index=ticker, columns=[BPS, PER, PBR, PCR, EPS, DIV])
            market: 시장 구분 (KOSPI/KOSDAQ)

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        tmp = df.reset_index()
        tmp = tmp.rename(columns={tmp.columns[0]: "ticker"})
        tmp["date"] = dt
        tmp["market"] = market
        col_map = {
            "BPS": "bps", "PER": "per", "PBR": "pbr", "PCR": "pcr",
            "EPS": "eps", "DIV": "div",
            "PSR": "psr", "REVENUE": "revenue",
            "OPERATING_INCOME": "operating_income",
            "TOTAL_ASSETS": "total_assets", "OPA": "opa",
            "TOTAL_EQUITY": "total_equity",
            "TOTAL_LIABILITIES": "total_liabilities",
            "DEBT_RATIO": "debt_ratio",
            "DATA_SOURCE": "data_source",
        }
        all_db_cols = ["bps", "per", "pbr", "pcr", "eps", "div",
                       "psr", "revenue", "operating_income", "total_assets", "opa",
                       "total_equity", "total_liabilities", "debt_ratio",
                       "data_source"]
        for old, new in col_map.items():
            if old in tmp.columns:
                tmp[new] = tmp[old]
            elif new not in tmp.columns:
                tmp[new] = None
        present_cols = [c for c in all_db_cols if c in tmp.columns]
        rows = tmp[["ticker", "date", "market"] + present_cols].to_dict("records")

        update_cols = [c for c in present_cols if c != "data_source"]
        if "data_source" in present_cols:
            update_cols.append("data_source")
        self._upsert(
            Fundamental, rows,
            conflict_cols=["ticker", "date", "market"],
            update_cols=update_cols or ["bps", "per", "pbr", "pcr", "eps", "div"],
        )

        logger.info(f"기본 지표 저장: {dt} ({len(rows)}건)")
        return len(rows)

    def load_fundamentals(self, dt: date, market: str = "KOSPI") -> pd.DataFrame:
        """기본 지표 조회

        Args:
            dt: 기준 날짜
            market: 시장 구분 (KOSPI/KOSDAQ). 해당 시장 데이터만 반환.

        Returns:
            DataFrame(index=ticker, columns=[BPS, PER, PBR, PCR, EPS, DIV])
        """
        sql = (
            "SELECT ticker, bps AS BPS, per AS PER, pbr AS PBR, pcr AS PCR, eps AS EPS, div AS DIV,"
            " psr AS PSR, revenue AS REVENUE, operating_income AS OPERATING_INCOME,"
            " total_assets AS TOTAL_ASSETS, opa AS OPA,"
            " total_equity AS TOTAL_EQUITY, total_liabilities AS TOTAL_LIABILITIES,"
            " debt_ratio AS DEBT_RATIO,"
            " data_source AS DATA_SOURCE "
            "FROM fundamental WHERE date = :dt AND (market = :market OR market IS NULL)"
        )
        with self.engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params={"dt": str(dt), "market": market})

        if df.empty:
            return pd.DataFrame()

        return df.set_index("ticker")

    # ───────────────────────────────────────────────
    # 시가총액
    # ───────────────────────────────────────────────

    def save_market_caps(self, dt: date, df: pd.DataFrame, market: str = "KOSPI") -> int:
        """시가총액 upsert 저장

        Args:
            dt: 기준 날짜
            df: DataFrame(index=ticker, columns=[market_cap, shares])
            market: 시장 구분 (KOSPI/KOSDAQ)

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        rows = self._df_to_rows(
            df, dt,
            columns=["ticker", "date", "market_cap", "shares"],
        )
        for row in rows:
            row["market"] = market

        self._upsert(
            MarketCap, rows,
            conflict_cols=["ticker", "date"],
            update_cols=["market", "market_cap", "shares"],
        )

        logger.info(f"시가총액 저장: {dt} {market} ({len(rows)}건)")
        return len(rows)

    def load_market_caps(self, dt: date, market: Optional[str] = None) -> pd.DataFrame:
        """시가총액 조회

        Args:
            dt: 기준 날짜
            market: 시장 구분 (KOSPI/KOSDAQ). None이면 전체 반환.

        Returns:
            DataFrame(index=ticker, columns=[market_cap, shares])
        """
        if market:
            sql = (
                "SELECT ticker, market_cap, shares "
                "FROM market_cap WHERE date = :dt AND (market = :market OR market IS NULL)"
            )
            params: dict = {"dt": str(dt), "market": market}
        else:
            sql = (
                "SELECT ticker, market_cap, shares "
                "FROM market_cap WHERE date = :dt"
            )
            params = {"dt": str(dt)}

        with self.engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params)

        if df.empty:
            return pd.DataFrame()

        return df.set_index("ticker")

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

    def save_daily_prices_bulk(self, dt: date, df: pd.DataFrame, market: str = "KOSPI") -> int:
        """여러 종목의 일별 가격 일괄 upsert 저장

        Args:
            dt: 기준 날짜
            df: DataFrame(index=ticker, columns=[open, high, low, close, volume])
            market: 시장 구분 (KOSPI/KOSDAQ)

        Returns:
            저장된 행 수
        """
        if df.empty:
            return 0

        rows = self._df_to_rows(
            df, dt,
            columns=["ticker", "date", "open", "high", "low", "close", "volume"],
        )
        for row in rows:
            row["market"] = market

        self._upsert(
            DailyPrice, rows,
            conflict_cols=["ticker", "date"],
            update_cols=["market", "open", "high", "low", "close", "volume"],
        )

        logger.info(f"일별 가격 일괄 저장: {dt} {market} ({len(rows)}건)")
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

        rows = self._df_to_rows(
            df, dt,
            columns=["ticker", "date", "value_score", "momentum_score", "quality_score", "composite_score"],
        )

        self._upsert(
            FactorScore, rows,
            conflict_cols=["ticker", "date"],
            update_cols=["value_score", "momentum_score", "quality_score", "composite_score"],
        )

        logger.info(f"팩터 스코어 저장: {dt} ({len(rows)}건)")
        return len(rows)

    def load_factor_scores(self, dt: date) -> pd.DataFrame:
        """팩터 스코어 조회 (스크리너 캐시용)

        Args:
            dt: 기준 날짜

        Returns:
            DataFrame(index=ticker, columns=[value_score, momentum_score, quality_score, composite_score])
        """
        sql = (
            "SELECT ticker, value_score, momentum_score, quality_score, composite_score "
            "FROM factor_score WHERE date = :dt"
        )
        with self.engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params={"dt": str(dt)})

        if df.empty:
            return pd.DataFrame()

        return df.set_index("ticker").sort_values("composite_score", ascending=False)

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

        self._upsert(
            Portfolio, rows,
            conflict_cols=["ticker", "rebalance_date"],
            update_cols=["name", "weight", "composite_score"],
        )

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
        rebalance_date: Optional[date] = None,
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
            rebalance_date: 리밸런싱 신호 날짜 (None이면 미연결)
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
                    rebalance_date=rebalance_date,
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
                    "rebalance_date": r.rebalance_date,
                }
                for r in query.all()
            ]

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows)

    # ───────────────────────────────────────────────
    # 상장폐지 종목 (생존자 편향 보정용)
    # ───────────────────────────────────────────────

    def upsert_delisted_stocks(self, rows: list[dict]) -> tuple[int, int]:
        """상장폐지 종목 upsert

        Args:
            rows: [{"ticker", "name", "delist_date", "reason", "category", "memo"}]

        Returns:
            (신규 추가 건수, 업데이트 건수)
        """
        if not rows:
            return 0, 0

        inserted = 0
        updated = 0
        with self.SessionLocal() as session:
            existing = {
                r.ticker: r for r in session.query(DelistedStock).all()
            }
            for row in rows:
                ticker = row["ticker"]
                if ticker in existing:
                    obj = existing[ticker]
                    changed = False
                    for k in ("name", "delist_date", "reason", "category", "memo"):
                        if k in row and getattr(obj, k) != row[k]:
                            setattr(obj, k, row[k])
                            changed = True
                    if changed:
                        obj.imported_at = datetime.now(timezone.utc)
                        updated += 1
                else:
                    session.add(DelistedStock(**row))
                    inserted += 1
            session.commit()
        return inserted, updated

    def load_delisted_stocks(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        category: Optional[str] = None,
    ) -> pd.DataFrame:
        """상장폐지 종목 조회

        Args:
            start_date: 폐지일 시작
            end_date: 폐지일 종료
            category: 카테고리 필터 (failure/merger/voluntary/expired/other)

        Returns:
            DataFrame(columns=[ticker, name, delist_date, reason, category, memo])
        """
        with self.SessionLocal() as session:
            query = session.query(DelistedStock)
            if start_date:
                query = query.filter(DelistedStock.delist_date >= start_date)
            if end_date:
                query = query.filter(DelistedStock.delist_date <= end_date)
            if category:
                query = query.filter(DelistedStock.category == category)
            query = query.order_by(DelistedStock.delist_date)

            rows = [
                {
                    "ticker": r.ticker,
                    "name": r.name,
                    "delist_date": r.delist_date,
                    "reason": r.reason,
                    "category": r.category,
                    "memo": r.memo,
                }
                for r in query.all()
            ]

        if not rows:
            return pd.DataFrame(
                columns=["ticker", "name", "delist_date", "reason", "category", "memo"]
            )
        return pd.DataFrame(rows)

    # ───────────────────────────────────────────────
    # 종목 섹터 (S4-A 금융주 제외용)
    # ───────────────────────────────────────────────

    def upsert_stock_sectors(self, rows: list[dict]) -> tuple[int, int]:
        """종목별 섹터 정보 upsert.

        Args:
            rows: [{"ticker", "date", "sector_name", "sector_code",
                    "is_financial", "data_source"}]

        Returns:
            (신규 추가 건수, 갱신 건수)
        """
        if not rows:
            return 0, 0

        keys = [(r["ticker"], r["date"]) for r in rows]
        existing_keys: set[tuple[str, str]] = set()
        with self.engine.connect() as conn:
            chunk_size = 400
            for i in range(0, len(keys), chunk_size):
                chunk = keys[i:i + chunk_size]
                placeholders = ", ".join(
                    f"(:t{j}, :d{j})" for j in range(len(chunk))
                )
                params: dict = {}
                for j, (t, d) in enumerate(chunk):
                    params[f"t{j}"] = t
                    params[f"d{j}"] = d
                sql = (
                    f"SELECT ticker, date FROM stock_sector "
                    f"WHERE (ticker, date) IN ({placeholders})"
                )
                for row in conn.execute(text(sql), params):
                    existing_keys.add((row[0], row[1]))

        inserted = sum(1 for k in keys if k not in existing_keys)
        updated = len(keys) - inserted

        normalized: list[dict] = []
        for r in rows:
            normalized.append({
                "ticker": r["ticker"],
                "date": str(r["date"]),
                "sector_name": r.get("sector_name"),
                "sector_code": r.get("sector_code"),
                "is_financial": bool(r.get("is_financial", False)),
                "data_source": r.get("data_source"),
                "fetched_at": datetime.now(timezone.utc),
            })
        self._upsert(
            StockSector, normalized,
            conflict_cols=["ticker", "date"],
            update_cols=[
                "sector_name", "sector_code", "is_financial",
                "data_source", "fetched_at",
            ],
        )
        logger.debug(
            f"섹터 upsert: {len(rows)}건 (신규 {inserted}, 갱신 {updated})"
        )
        return inserted, updated

    def load_stock_sectors(
        self, date: str, market: str = "KOSPI",
    ) -> pd.DataFrame:
        """특정 날짜 기준 전체 종목 섹터 정보 조회.

        정확한 date에 데이터 없으면 가장 가까운 이전 날짜 사용 (최대 180일).

        Args:
            date: 기준일 (YYYYMMDD)
            market: KOSPI/KOSDAQ (현재는 mark 무시, 향후 market 컬럼 추가 시 사용)

        Returns:
            DataFrame(index=ticker, columns=[sector_name, sector_code, is_financial, date])
        """
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        try:
            target_dt = _dt.strptime(str(date), "%Y%m%d")
        except ValueError:
            logger.warning(f"load_stock_sectors: 잘못된 date={date}")
            return pd.DataFrame()

        min_dt = (target_dt - _td(days=180)).strftime("%Y%m%d")

        # 가장 가까운 이전 날짜를 가진 ticker별 한 행 (sqlite max trick)
        sql = (
            "SELECT s.ticker, s.date, s.sector_name, s.sector_code, s.is_financial "
            "FROM stock_sector s "
            "WHERE s.date = ("
            "  SELECT MAX(date) FROM stock_sector s2 "
            "  WHERE s2.ticker = s.ticker "
            "  AND s2.date <= :dt AND s2.date >= :min_dt"
            ") "
            "AND s.date <= :dt AND s.date >= :min_dt"
        )
        with self.engine.connect() as conn:
            df = pd.read_sql(
                text(sql), conn,
                params={"dt": str(date), "min_dt": min_dt},
            )

        if df.empty:
            return pd.DataFrame()

        df["is_financial"] = df["is_financial"].astype(bool)
        return df.set_index("ticker")

    def get_finance_tickers(
        self, date: str, market: str = "KOSPI",
    ) -> list[str]:
        """특정 날짜 기준 금융주 티커 목록 반환 (is_financial=True)."""
        df = self.load_stock_sectors(date, market=market)
        if df.empty:
            return []
        return df[df["is_financial"]].index.tolist()

    # ───────────────────────────────────────────────
    # 분기 재무 시계열 (Step 3 연속 흑자 필터용)
    # ───────────────────────────────────────────────

    # 보고서 코드 → 분기 인덱스 (시간순 정렬용, 1=Q1, 2=Half/Q2, 3=Q3, 4=Annual/Q4)
    _REPRT_QUARTER_IDX: dict[str, int] = {
        "11013": 1,  # Q1
        "11012": 2,  # Half
        "11014": 3,  # Q3
        "11011": 4,  # Annual (Q4)
    }

    # 보고서 코드 → 분기 종료월/일 (period_end 정렬용)
    _REPRT_PERIOD_END: dict[str, tuple[int, int]] = {
        "11013": (3, 31),
        "11012": (6, 30),
        "11014": (9, 30),
        "11011": (12, 31),
    }

    def upsert_fundamentals_quarterly(self, rows: list[dict]) -> tuple[int, int]:
        """분기별 재무 시계열 upsert.

        Args:
            rows: [{"ticker", "bsns_year", "reprt_code", "eps",
                    "operating_income", "revenue", "fs_div"}]

        Returns:
            (신규 추가 건수, 업데이트 건수)
        """
        if not rows:
            return 0, 0

        # 기존 키 조회 (insert/update 분리 카운팅)
        keys = [(r["ticker"], r["bsns_year"], r["reprt_code"]) for r in rows]
        existing_keys: set[tuple[str, str, str]] = set()
        with self.engine.connect() as conn:
            chunk_size = 300  # SQLite 변수 제한 대비
            for i in range(0, len(keys), chunk_size):
                chunk = keys[i:i + chunk_size]
                placeholders = ", ".join(
                    f"(:t{j}, :y{j}, :r{j})" for j in range(len(chunk))
                )
                params: dict = {}
                for j, (t, y, r) in enumerate(chunk):
                    params[f"t{j}"] = t
                    params[f"y{j}"] = y
                    params[f"r{j}"] = r
                sql = (
                    f"SELECT ticker, bsns_year, reprt_code FROM fundamental_quarterly "
                    f"WHERE (ticker, bsns_year, reprt_code) IN ({placeholders})"
                )
                result = conn.execute(text(sql), params)
                for row in result:
                    existing_keys.add((row[0], row[1], row[2]))

        inserted = sum(
            1 for k in keys if k not in existing_keys
        )
        updated = len(keys) - inserted

        # 정규화된 row만 추출
        normalized: list[dict] = []
        for r in rows:
            normalized.append({
                "ticker": r["ticker"],
                "bsns_year": str(r["bsns_year"]),
                "reprt_code": str(r["reprt_code"]),
                "eps": r.get("eps"),
                "operating_income": r.get("operating_income"),
                "revenue": r.get("revenue"),
                "fs_div": r.get("fs_div"),
                "fetched_at": datetime.now(timezone.utc),
            })

        self._upsert(
            FundamentalQuarterly, normalized,
            conflict_cols=["ticker", "bsns_year", "reprt_code"],
            update_cols=["eps", "operating_income", "revenue", "fs_div", "fetched_at"],
        )
        logger.debug(
            f"분기 재무 upsert: {len(rows)}건 "
            f"(신규 {inserted}, 갱신 {updated})"
        )
        return inserted, updated

    @staticmethod
    def _pit_end_period(as_of_date: date) -> tuple[str, str]:
        """as_of_date 시점에 공시된 가장 최근 (bsns_year, reprt_code) 결정.

        dart_client._determine_report_period와 동일한 lag 규칙:
          - 12월 이후: 그해 Q3
          - 9월 이후:  그해 Half
          - 6월 이후:  그해 Q1
          - 4월 이후:  전년 Annual
          - 1~3월:    전전년 Annual

        Args:
            as_of_date: 기준 날짜

        Returns:
            (사업연도 문자열, 보고서코드)
        """
        year = as_of_date.year
        month = as_of_date.month

        if month >= 12:
            return str(year), "11014"
        elif month >= 9:
            return str(year), "11012"
        elif month >= 6:
            return str(year), "11013"
        elif month >= 4:
            return str(year - 1), "11011"
        else:
            return str(year - 2), "11011"

    @classmethod
    def _walk_back_quarters(
        cls, end_year: str, end_reprt: str, n: int,
    ) -> list[tuple[str, str]]:
        """(end_year, end_reprt)부터 과거 n개 분기 키 리스트 반환 (시간 역순).

        역행 순서: 11011 → 11014 → 11012 → 11013 → (전년) 11011 → ...

        Args:
            end_year: 시작 사업연도
            end_reprt: 시작 보고서코드
            n: 가져올 분기 개수

        Returns:
            [(bsns_year, reprt_code), ...] 최신 → 과거 순
        """
        order = ["11011", "11014", "11012", "11013"]
        try:
            idx = order.index(end_reprt)
        except ValueError:
            raise ValueError(f"알 수 없는 reprt_code: {end_reprt}")

        year = int(end_year)
        result: list[tuple[str, str]] = []
        for _ in range(n):
            result.append((str(year), order[idx]))
            idx += 1
            if idx >= len(order):
                idx = 0
                year -= 1
        return result

    def load_fundamentals_quarterly(
        self,
        ticker: str,
        as_of_date: date,
        n_quarters: int = 4,
    ) -> pd.DataFrame:
        """ticker의 최근 n_quarters 분기 데이터를 PIT 안전하게 반환.

        preload_fundamentals_quarterly()로 프리로드된 캐시가 있으면 DB 쿼리 없이 반환.

        as_of_date 시점에 공시된 분기(_pit_end_period 기준)부터 과거 n_quarters만큼
        역행 조회. 동일 (bsns_year, reprt_code) 분기는 1행만 반환.

        Args:
            ticker: 종목코드
            as_of_date: PIT 기준일 — 이 날짜 시점에 공시된 분기만 반환
            n_quarters: 조회할 분기 개수 (기본 4)

        Returns:
            DataFrame(columns=[bsns_year, reprt_code, eps, operating_income,
                              revenue, fs_div, period_end])
            period_end 내림차순 (최신이 위)
        """
        # 프리로드 캐시 히트 (Opt 3: N+1 쿼리 → O(1) dict lookup)
        if self._fq_preload_date == as_of_date and self._fq_preload:
            cached = self._fq_preload.get(ticker)
            return cached if cached is not None else pd.DataFrame()

        end_year, end_reprt = self._pit_end_period(as_of_date)
        target_keys = self._walk_back_quarters(end_year, end_reprt, n_quarters)

        if not target_keys:
            return pd.DataFrame()

        # IN clause로 일괄 조회
        placeholders = ", ".join(
            f"(:y{i}, :r{i})" for i in range(len(target_keys))
        )
        params: dict = {"ticker": ticker}
        for i, (y, r) in enumerate(target_keys):
            params[f"y{i}"] = y
            params[f"r{i}"] = r

        sql = (
            "SELECT ticker, bsns_year, reprt_code, eps, operating_income, "
            "revenue, fs_div "
            "FROM fundamental_quarterly "
            "WHERE ticker = :ticker "
            f"AND (bsns_year, reprt_code) IN ({placeholders})"
        )
        with self.engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params)

        if df.empty:
            return df

        # period_end 컬럼 생성 후 정렬
        def _period_end(row: pd.Series) -> date:
            y = int(row["bsns_year"])
            m, d = self._REPRT_PERIOD_END.get(
                row["reprt_code"], (12, 31)
            )
            return date(y, m, d)

        df["period_end"] = df.apply(_period_end, axis=1)
        df = df.sort_values("period_end", ascending=False).reset_index(drop=True)
        return df

    # ───────────────────────────────────────────────
    # 일별 가격 (종가 행렬)
    # ───────────────────────────────────────────────

    def load_close_matrix(
        self,
        tickers: list[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """종가 pivot matrix 반환 (index=date, columns=ticker, values=close).

        변동성 계산 등 시계열 행렬이 필요한 경우 load_daily_prices_bulk + pivot보다
        이 메서드 한 번 호출이 명시적임.

        Args:
            tickers: 종목코드 리스트
            start_date: 시작 날짜
            end_date: 종료 날짜

        Returns:
            DataFrame(index=date, columns=ticker, values=close).
            데이터 없는 날짜/종목은 NaN.
        """
        bulk = self.load_daily_prices_bulk(tickers, start_date, end_date)
        if bulk.empty:
            return pd.DataFrame()

        bulk["date"] = pd.to_datetime(bulk["date"])
        matrix = bulk.pivot_table(index="date", columns="ticker", values="close")
        matrix.columns.name = None
        return matrix

    # ───────────────────────────────────────────────
    # 분기 재무 벌크 조회 + 프리로드 (Opt 3)
    # ───────────────────────────────────────────────

    def load_fundamentals_quarterly_bulk(
        self,
        as_of_date: date,
        n_quarters: int = 4,
    ) -> dict[str, pd.DataFrame]:
        """전종목의 분기 재무 시계열을 한 번의 SQL로 조회.

        load_fundamentals_quarterly()와 동일한 PIT 필터를 적용하지만
        전종목을 단일 쿼리로 처리.

        Args:
            as_of_date: PIT 기준일
            n_quarters: 각 ticker당 최대 조회 분기 수 (기본 4)

        Returns:
            {ticker: DataFrame(columns=[bsns_year, reprt_code, eps,
                operating_income, revenue, fs_div, period_end])}
            period_end 내림차순 정렬.
        """
        end_year, end_reprt = self._pit_end_period(as_of_date)
        target_keys = self._walk_back_quarters(end_year, end_reprt, n_quarters)

        if not target_keys:
            return {}

        min_year = min(y for y, _ in target_keys)

        # 단일 SQL로 전체 조회 (min_year 이후 전체)
        sql = (
            "SELECT ticker, bsns_year, reprt_code, eps, operating_income, "
            "revenue, fs_div "
            "FROM fundamental_quarterly "
            "WHERE bsns_year >= :min_year "
            "ORDER BY ticker, bsns_year, reprt_code"
        )
        with self.engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params={"min_year": min_year})

        if df.empty:
            return {}

        # target_keys 필터 (merge가 apply보다 빠름)
        target_df = pd.DataFrame(target_keys, columns=["bsns_year", "reprt_code"])
        df = df.merge(target_df, on=["bsns_year", "reprt_code"])

        if df.empty:
            return {}

        def _period_end(row: pd.Series) -> date:
            y = int(row["bsns_year"])
            m, d = self._REPRT_PERIOD_END.get(row["reprt_code"], (12, 31))
            return date(y, m, d)

        df = df.copy()
        df["period_end"] = df.apply(_period_end, axis=1)

        result: dict[str, pd.DataFrame] = {}
        for ticker, group in df.groupby("ticker"):
            result[str(ticker)] = group.sort_values(
                "period_end", ascending=False
            ).reset_index(drop=True)

        logger.debug(
            f"분기 재무 벌크 조회: {len(result)}개 ticker ({as_of_date})"
        )
        return result

    def preload_fundamentals_quarterly(
        self, as_of_date: date, n_quarters: int = 4,
    ) -> int:
        """전종목 분기 재무를 메모리에 프리로드.

        이후 load_fundamentals_quarterly() 호출 시 DB 대신 캐시에서 반환.
        리밸런싱 루프 시작 전 1회 호출하여 N+1 쿼리를 1쿼리로 단축.

        Args:
            as_of_date: PIT 기준일
            n_quarters: 조회할 분기 수

        Returns:
            프리로드된 ticker 수
        """
        self._fq_preload = self.load_fundamentals_quarterly_bulk(as_of_date, n_quarters)
        self._fq_preload_date = as_of_date
        logger.debug(
            f"분기 재무 프리로드 완료: {len(self._fq_preload)}개 ticker ({as_of_date})"
        )
        return len(self._fq_preload)

    def clear_fq_preload(self) -> None:
        """분기 재무 프리로드 캐시 해제."""
        self._fq_preload = {}
        self._fq_preload_date = None
