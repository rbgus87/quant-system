# data/collector.py
import pandas as pd
from pykrx import stock
from datetime import datetime, date as date_type
from dateutil.relativedelta import relativedelta
import logging
import time
from typing import Optional, Callable, TypeVar
from functools import wraps

from tqdm import tqdm

from config.settings import settings
from data.storage import DataStorage

logger = logging.getLogger(__name__)

T = TypeVar("T")

# pykrx OHLCV 컬럼 매핑 (한글 → 영문)
OHLCV_COLUMNS = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "trading_value",
}

# KRX Open API 응답 필드 매핑 (stk_isu_base_info)
KRX_API_BASE_INFO_COLUMNS = {
    "ISU_SRT_CD": "ticker",
    "ISU_ABBRV": "name",
    "BPS": "BPS",
    "PER": "PER",
    "PBR": "PBR",
    "EPS": "EPS",
    "DVD_YLD": "DIV",
    "DPS": "DPS",
}

# KRX Open API 응답 필드 매핑 (stk_bydd_trd)
KRX_API_DAILY_TRADE_COLUMNS = {
    "ISU_SRT_CD": "ticker",
    "ISU_ABBRV": "name",
    "TDD_OPNPRC": "open",
    "TDD_HGPRC": "high",
    "TDD_LWPRC": "low",
    "TDD_CLSPRC": "close",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "trading_value",
    "MKTCAP": "market_cap",
    "LIST_SHRS": "shares",
}


def _parse_date(date_str: str) -> date_type:
    """YYYYMMDD 또는 YYYY-MM-DD 문자열을 date 객체로 변환"""
    clean = date_str.replace("-", "")
    return datetime.strptime(clean, "%Y%m%d").date()


def retry_on_failure(
    max_retries: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """API 호출 실패 시 지수 백오프 재시도 데코레이터

    Args:
        max_retries: 최대 재시도 횟수
        base_delay: 기본 대기 시간 (초)
        exceptions: 재시도할 예외 타입
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception: Optional[Exception] = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = base_delay * (2**attempt)
                        logger.warning(
                            f"{func.__name__} 실패 (시도 {attempt + 1}/{max_retries + 1}), "
                            f"{delay:.1f}초 후 재시도: {e}"
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"{func.__name__} 최종 실패 ({max_retries + 1}회 시도): {e}"
                        )
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator


class KRXDataCollector:
    """KRX 데이터 수집 (KRX Open API + DART + pykrx + SQLite 캐시)

    데이터 조회 우선순위:
      1. SQLite 캐시 (이전에 저장된 데이터)
      2. KRX Open API (pykrx-openapi, 인증키 필요)
      3. DART OpenAPI (재무제표 기반 EPS/BPS → PER/PBR 계산)
      4. pykrx 폴백 (개별 종목 OHLCV만 가능)
    """

    def __init__(self, request_delay: float = 0.5) -> None:
        """
        Args:
            request_delay: API 호출 간격 (초). 과호출 방지 필수.
        """
        self.delay = request_delay
        self.storage = DataStorage()
        self._krx_api = None
        self._krx_api_checked = False
        self._dart_client = None
        self._dart_client_checked = False
        self._prefetched_dates: set[str] = set()  # 이미 프리페치한 날짜 (중복 방지)
        self._ticker_names: dict[str, str] = {}  # ticker→name 매핑 캐시

    @property
    def krx_api(self) -> Optional[object]:
        """KRX Open API 클라이언트 (lazy init)"""
        if not self._krx_api_checked:
            self._krx_api_checked = True
            api_key = settings.krx_openapi_key
            if api_key:
                try:
                    from pykrx_openapi import KRXOpenAPI

                    self._krx_api = KRXOpenAPI(api_key=api_key)
                    logger.info("KRX Open API 클라이언트 초기화 완료")
                except ImportError:
                    logger.warning(
                        "pykrx-openapi 미설치. pip install pykrx-openapi 실행 필요"
                    )
                except Exception as e:
                    logger.error(f"KRX Open API 초기화 실패: {e}")
            else:
                logger.warning(
                    "KRX_OPENAPI_KEY 미설정. .env 파일에 추가하세요. "
                    "캐시된 데이터만 사용합니다."
                )
        return self._krx_api

    @property
    def dart_client(self) -> Optional[object]:
        """DART OpenAPI 클라이언트 (lazy init)"""
        if not self._dart_client_checked:
            self._dart_client_checked = True
            if settings.dart_api_key:
                try:
                    from data.dart_client import DartClient

                    self._dart_client = DartClient()
                    logger.info("DART OpenAPI 클라이언트 초기화 완료")
                except Exception as e:
                    logger.error(f"DART OpenAPI 초기화 실패: {e}")
            else:
                logger.info("DART_API_KEY 미설정. DART 폴백 비활성화.")
        return self._dart_client

    # ───────────────────────────────────────────────
    # 유니버스
    # ───────────────────────────────────────────────

    def get_universe(self, date: str, market: str = "KOSPI") -> pd.DataFrame:
        """특정 날짜 기준 상장 종목 목록 조회 (생존 편향 방지)

        KRX Open API의 일별 거래 데이터에서 종목 리스트를 추출합니다.

        Args:
            date: 기준 날짜 (YYYYMMDD)
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(columns=[ticker, name, market])
        """
        # KRX Open API에서 일별 거래 데이터로 종목 리스트 추출
        if self.krx_api:
            try:
                method = self._get_daily_trade_method(market)
                data = method(date)
                records = data.get("OutBlock_1", [])
                if records:
                    rows = []
                    for r in records:
                        ticker = self._normalize_ticker(r.get("ISU_CD", ""))
                        name = r.get("ISU_NM", "")
                        if ticker:
                            rows.append(
                                {"ticker": ticker, "name": name, "market": market}
                            )
                    logger.info(f"[{date}] {market} 종목 수: {len(rows)} (KRX API)")
                    return pd.DataFrame(rows)
            except Exception as e:
                logger.warning(f"KRX API 종목 조회 실패: {e}")

        # pykrx 배치 API는 KRX Data Marketplace 로그인 필수화(2025-12-27~)로 차단됨
        logger.warning(f"[{date}] {market} 종목 조회 실패: KRX API 미사용 가능")
        return pd.DataFrame()

    # ───────────────────────────────────────────────
    # OHLCV
    # ───────────────────────────────────────────────

    @retry_on_failure(max_retries=3, base_delay=1.0)
    def get_ohlcv(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """단일 종목 일봉 OHLCV 조회 (캐시 우선)

        Args:
            ticker: 종목코드
            start_date: 시작 날짜 (YYYYMMDD)
            end_date: 종료 날짜 (YYYYMMDD)

        Returns:
            DataFrame(index=date, columns=[open, high, low, close, volume])
        """
        sd = _parse_date(start_date)
        ed = _parse_date(end_date)

        # 1. 캐시 확인 (요청 기간 대비 충분한 데이터가 있는지 검증)
        cached = self.storage.load_daily_prices(ticker, sd, ed)
        if not cached.empty:
            # 요청 기간이 1일이면 캐시 히트로 충분
            request_days = (ed - sd).days
            if request_days <= 1 or len(cached) >= max(request_days * 0.5, 3):
                return cached
            # 캐시 데이터가 부족하면 pykrx로 보충 시도
            logger.debug(
                f"[{ticker}] 캐시 부분 히트 ({len(cached)}건), "
                f"pykrx로 전체 기간 조회 시도"
            )

        # 2. pykrx (개별 OHLCV는 여전히 작동)
        df = stock.get_market_ohlcv(start_date, end_date, ticker)
        if df.empty:
            return cached if not cached.empty else pd.DataFrame()

        df = df.rename(columns=OHLCV_COLUMNS)
        df.index.name = "date"
        result = df[["open", "high", "low", "close", "volume"]]

        # 캐시 저장
        self.storage.save_daily_prices(ticker, result)
        return result

    # ───────────────────────────────────────────────
    # 기본 지표 (Fundamental)
    # ───────────────────────────────────────────────

    def get_fundamentals_all(
        self,
        date: str,
        market: str = "KOSPI",
    ) -> pd.DataFrame:
        """전체 시장 기본 지표 일괄 조회 (캐시 → KRX API → pykrx 순)

        Args:
            date: 기준 날짜 (YYYYMMDD)
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(index=ticker, columns=[BPS, PER, PBR, EPS, DIV])
        """
        dt = _parse_date(date)

        # 1. 캐시 확인 (market별 분리 조회)
        cached = self.storage.load_fundamentals(dt, market=market)
        if not cached.empty:
            logger.info(f"[{date}] 기본 지표 캐시 히트 ({len(cached)}건, {market})")
            return cached

        # 2. KRX Open API (시장별 엔드포인트 분기)
        if self.krx_api:
            try:
                if market.upper() == "KOSDAQ":
                    data = self.krx_api.get_kosdaq_stock_base_info(date)
                else:
                    data = self.krx_api.get_stock_base_info(date)
                records = data.get("OutBlock_1", [])
                if records:
                    df = self._parse_base_info(records)
                    if not df.empty:
                        # KRX API에 PER/PBR 필드가 없을 수 있음 → 유효 데이터 확인
                        has_valid = False
                        for col in ["PER", "PBR", "EPS", "BPS"]:
                            if col in df.columns and df[col].notna().any():
                                has_valid = True
                                break
                        if has_valid:
                            self.storage.save_fundamentals(dt, df, market=market)
                            logger.info(
                                f"[{date}] 기본 지표: {len(df)}건 (KRX API, {market})"
                            )
                            return df
                        else:
                            logger.info(
                                f"[{date}] KRX API 기본 지표: {len(df)}건이나 "
                                "PER/PBR 모두 NaN → DART 폴백 시도"
                            )
            except Exception as e:
                logger.warning(f"[{date}] KRX API 기본 지표 실패: {e}")

        # 3. DART OpenAPI 폴백 (재무제표 기반 PER/PBR 계산)
        dart_df = self._get_fundamentals_via_dart(date, market)
        if not dart_df.empty:
            self.storage.save_fundamentals(dt, dart_df, market=market)
            logger.info(
                f"[{date}] 기본 지표: {len(dart_df)}건 (DART → 캐시 저장, {market})"
            )
            return dart_df

        # pykrx 배치 API는 KRX Data Marketplace 로그인 필수화(2025-12-27~)로 차단됨
        logger.warning(f"[{date}] 기본 지표 데이터 없음")
        return pd.DataFrame()

    # ───────────────────────────────────────────────
    # 시가총액
    # ───────────────────────────────────────────────

    def get_market_cap(
        self,
        date: str,
        market: str = "KOSPI",
    ) -> pd.DataFrame:
        """전체 시장 시가총액 조회 (캐시 → KRX API → pykrx 순)

        Args:
            date: 기준 날짜 (YYYYMMDD)
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(index=ticker, columns=[market_cap, shares])
        """
        dt = _parse_date(date)

        # 1. 캐시 확인 (시장별 분리)
        cached = self.storage.load_market_caps(dt, market=market)
        if not cached.empty:
            logger.info(f"[{date}] 시가총액 캐시 히트 ({len(cached)}건, {market})")
            return cached

        # 2. KRX Open API (일별 거래 데이터에 시가총액 포함)
        if self.krx_api:
            try:
                method = self._get_daily_trade_method(market)
                data = method(date)
                records = data.get("OutBlock_1", [])
                if records:
                    df = self._parse_daily_trade_market_cap(records)
                    if not df.empty:
                        self.storage.save_market_caps(dt, df, market=market)
                        logger.info(
                            f"[{date}] 시가총액: {len(df)}건 (KRX API → 캐시 저장, {market})"
                        )
                        return df
            except Exception as e:
                logger.warning(f"[{date}] KRX API 시가총액 실패: {e}")

        # pykrx 배치 API는 KRX Data Marketplace 로그인 필수화(2025-12-27~)로 차단됨
        return pd.DataFrame()

    # ───────────────────────────────────────────────
    # 일별 거래 데이터 일괄 조회 + 캐시
    # ───────────────────────────────────────────────

    def prefetch_daily_trade(self, date: str, market: str = "KOSPI") -> pd.DataFrame:
        """일별 거래 데이터 일괄 조회 + OHLCV/시가총액 캐시 저장

        백테스트에서 개별 종목 시가/종가 조회 전 호출하면
        API 호출 횟수를 크게 줄일 수 있습니다.

        Args:
            date: 기준 날짜 (YYYYMMDD)
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(index=ticker, columns=[open, high, low, close, volume, market_cap, shares])
        """
        # 중복 프리페치 방지 (인메모리)
        cache_key = f"{date}_{market}"
        if cache_key in self._prefetched_dates:
            logger.debug(f"[{date}] 이미 프리페치됨 (메모리), 스킵")
            return pd.DataFrame()

        dt = _parse_date(date)

        # DB 캐시 확인: 해당 날짜·시장에 충분한 데이터가 이미 있으면 API 스킵
        cached_count = self.storage.load_daily_prices_for_date(dt, market=market)
        if cached_count >= 100:
            self._prefetched_dates.add(cache_key)
            logger.debug(f"[{date}] DB 캐시 히트 ({cached_count}건, {market}), API 스킵")
            return pd.DataFrame()

        if not self.krx_api:
            return pd.DataFrame()

        try:
            method = self._get_daily_trade_method(market)
            data = method(date)
            records = data.get("OutBlock_1", [])
            if not records:
                return pd.DataFrame()

            rows = []
            for r in records:
                ticker = self._normalize_ticker(r.get("ISU_CD", ""))
                if not ticker:
                    continue
                # 종목명 캐시 (배치 로드)
                name = r.get("ISU_NM", "") or r.get("ISU_ABBRV", "")
                if name:
                    self._ticker_names[ticker] = name
                rows.append(
                    {
                        "ticker": ticker,
                        "open": r.get("TDD_OPNPRC"),
                        "high": r.get("TDD_HGPRC"),
                        "low": r.get("TDD_LWPRC"),
                        "close": r.get("TDD_CLSPRC"),
                        "volume": r.get("ACC_TRDVOL"),
                        "market_cap": r.get("MKTCAP"),
                        "shares": r.get("LIST_SHRS"),
                    }
                )

            df = pd.DataFrame(rows).set_index("ticker")

            # OHLCV 캐시 일괄 저장 (시장별 분리)
            ohlcv_cols = ["open", "high", "low", "close", "volume"]
            self.storage.save_daily_prices_bulk(dt, df[ohlcv_cols], market=market)

            # 시가총액 캐시 저장 (시장별 분리)
            cap_cols = [c for c in ["market_cap", "shares"] if c in df.columns]
            if cap_cols:
                self.storage.save_market_caps(dt, df[cap_cols], market=market)

            self._prefetched_dates.add(cache_key)
            logger.info(f"[{date}] 일별 거래 프리페치: {len(df)}건 캐시 저장")
            return df

        except Exception as e:
            logger.warning(f"[{date}] 일별 거래 프리페치 실패: {e}")
            return pd.DataFrame()

    # ───────────────────────────────────────────────
    # 유동성 (평균 거래대금)
    # ───────────────────────────────────────────────

    def get_avg_trading_value(
        self,
        tickers: list[str],
        date: str,
        lookback_days: int = 20,
    ) -> pd.Series:
        """종목별 N일 평균 거래대금 계산 (close × volume) - 벌크 조회

        Args:
            tickers: 종목 코드 리스트
            date: 기준 날짜 (YYYYMMDD)
            lookback_days: 평균 계산 기간 (영업일 기준, 기본 20일)

        Returns:
            Series(index=ticker, values=평균 거래대금)
        """
        base_dt = datetime.strptime(date.replace("-", ""), "%Y%m%d")
        start_dt = base_dt - relativedelta(days=lookback_days * 2)  # 영업일 마진
        start_str = start_dt.strftime("%Y%m%d")

        sd = _parse_date(start_str)
        ed = _parse_date(date)

        # 벌크 DB 조회 (N+1 → 1회 쿼리)
        bulk_df = self.storage.load_daily_prices_bulk(tickers, sd, ed)

        if bulk_df.empty:
            # DB에 데이터 없으면 기존 방식 폴백
            return self._get_avg_trading_value_fallback(tickers, date, lookback_days)

        # 종목별 최근 N일 평균 거래대금 계산 (벡터화)
        bulk_df = bulk_df.sort_values(["ticker", "date"])

        # 종목별 최근 lookback_days개 행만 추출
        recent = bulk_df.groupby("ticker").tail(lookback_days)

        # 거래대금 = close * volume
        recent = recent.copy()
        recent["trading_value"] = recent["close"] * recent["volume"]

        # 종목별 평균
        avg_values = recent.groupby("ticker")["trading_value"].mean()
        avg_values = avg_values[avg_values > 0]

        # 벌크 조회에서 누락된 종목은 개별 조회 폴백
        missing = [t for t in tickers if t not in avg_values.index]
        if missing:
            fallback = self._get_avg_trading_value_fallback(missing, date, lookback_days)
            if not fallback.empty:
                avg_values = pd.concat([avg_values, fallback])

        avg_values.index.name = "ticker"
        logger.info(
            f"[{date}] 평균 거래대금 계산: {len(avg_values)}건 "
            f"(>{lookback_days}일 평균)"
        )
        return avg_values

    def _get_avg_trading_value_fallback(
        self,
        tickers: list[str],
        date: str,
        lookback_days: int = 20,
    ) -> pd.Series:
        """평균 거래대금 개별 조회 폴백 (캐시 미스 종목용)"""
        base_dt = datetime.strptime(date.replace("-", ""), "%Y%m%d")
        start_dt = base_dt - relativedelta(days=lookback_days * 2)
        start_str = start_dt.strftime("%Y%m%d")

        result: dict[str, float] = {}
        for ticker in tickers:
            try:
                ohlcv = self.get_ohlcv(ticker, start_str, date)
                if ohlcv.empty:
                    continue
                recent = ohlcv.tail(lookback_days)
                trading_value = (recent["close"] * recent["volume"]).mean()
                if trading_value > 0:
                    result[ticker] = trading_value
            except Exception as e:
                logger.debug(f"[{ticker}] 거래대금 조회 실패: {e}")
                continue

        return pd.Series(result, dtype=float)

    def get_suspended_tickers(
        self,
        tickers: list[str],
        date: str,
    ) -> set[str]:
        """거래정지 종목 감지 (당일 거래량 0) - 벌크 조회

        Args:
            tickers: 종목 코드 리스트
            date: 기준 날짜 (YYYYMMDD)

        Returns:
            거래정지 종목 코드 집합
        """
        dt = _parse_date(date)

        # 벌크 DB 조회
        bulk_df = self.storage.load_daily_prices_bulk(tickers, dt, dt)

        if bulk_df.empty:
            # 데이터 없으면 판단 불가 → 거래정지 없음으로 처리 (안전 방향)
            logger.warning(
                f"[{date}] 거래정지 판단 불가: DB 데이터 없음 ({len(tickers)}종목)"
            )
            return set()

        # 거래량 > 0인 종목 = 정상 거래
        active = set(bulk_df[bulk_df["volume"] > 0]["ticker"])
        suspended = set(tickers) - active

        if suspended:
            logger.info(f"[{date}] 거래정지 종목: {len(suspended)}개")
        return suspended

    # ───────────────────────────────────────────────
    # 내부 헬퍼
    # ───────────────────────────────────────────────

    def _get_fundamentals_via_dart(
        self, date: str, market: str = "KOSPI"
    ) -> pd.DataFrame:
        """DART 재무제표 + KRX 시세로 펀더멘털 계산

        1. KRX 일별 거래 데이터에서 종가, 발행주식수 추출
        2. DART에서 EPS, 자본총계 조회
        3. PER = 종가/EPS, PBR = 종가/BPS(=자본총계/주식수) 계산

        Args:
            date: 기준 날짜 (YYYYMMDD)
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(index=ticker, columns=[BPS, PER, PBR, EPS, DIV])
        """
        if not self.dart_client:
            return pd.DataFrame()

        # KRX 일별 거래 데이터에서 종가 + 주식수 확보
        trade_data = self.prefetch_daily_trade(date, market)
        if trade_data.empty:
            # prefetch가 빈 DataFrame을 반환하는 경우:
            # DB 캐시 히트로 스킵된 것일 수 있음 → DB에서 직접 로드
            trade_data = self._load_trade_data_from_db(date)
        if trade_data.empty:
            logger.warning(f"[{date}] DART 폴백: KRX 거래 데이터 없음")
            return pd.DataFrame()

        # 종가와 주식수 추출 (숫자 변환)
        close_prices = pd.to_numeric(trade_data["close"], errors="coerce")
        shares_data = pd.to_numeric(trade_data["shares"], errors="coerce")
        tickers = trade_data.index.tolist()

        # DART에서 재무제표 조회 + PER/PBR 계산
        return self.dart_client.get_fundamentals_for_date(
            tickers, date, close_prices, shares_data
        )

    def _load_trade_data_from_db(self, date: str) -> pd.DataFrame:
        """DB 캐시에서 종가 + 주식수 로드 (DART 폴백용)

        Args:
            date: 기준 날짜 (YYYYMMDD)

        Returns:
            DataFrame(index=ticker, columns=[close, shares])
        """
        dt = _parse_date(date)
        with self.storage.engine.connect() as conn:
            from sqlalchemy import text

            # daily_price에서 종가, market_cap에서 주식수 JOIN
            sql = text(
                "SELECT dp.ticker, dp.close, mc.shares "
                "FROM daily_price dp "
                "JOIN market_cap mc ON dp.ticker = mc.ticker AND dp.date = mc.date "
                "WHERE dp.date = :dt AND dp.close > 0 AND mc.shares > 0"
            )
            rows = conn.execute(sql, {"dt": str(dt)}).fetchall()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["ticker", "close", "shares"])
        return df.set_index("ticker")

    def _get_daily_trade_method(self, market: str) -> Callable:
        """시장에 따른 KRX API 일별 거래 메서드 반환"""
        if market.upper() == "KOSDAQ":
            return self.krx_api.get_kosdaq_stock_daily_trade
        return self.krx_api.get_stock_daily_trade

    def _parse_base_info(self, records: list[dict]) -> pd.DataFrame:
        """KRX API get_stock_base_info 응답을 Fundamental DataFrame으로 변환

        주의: 유가증권 종목기본정보 API에는 PER/PBR/EPS/BPS/DIV 필드가
        포함되어 있지 않을 수 있습니다. 해당 필드가 없으면 None으로 채워집니다.
        """
        rows = []
        for r in records:
            ticker = self._normalize_ticker(r.get("ISU_SRT_CD", ""))
            if not ticker:
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "BPS": self._safe_float(r.get("BPS")),
                    "PER": self._safe_float(r.get("PER")),
                    "PBR": self._safe_float(r.get("PBR")),
                    "EPS": self._safe_float(r.get("EPS")),
                    "DIV": self._safe_float(r.get("DVD_YLD")),
                }
            )

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows).set_index("ticker")

    def _parse_daily_trade_market_cap(self, records: list[dict]) -> pd.DataFrame:
        """KRX API get_stock_daily_trade 응답에서 시가총액 추출"""
        rows = []
        for r in records:
            ticker = self._normalize_ticker(r.get("ISU_CD", ""))
            if not ticker:
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "market_cap": self._safe_int(r.get("MKTCAP")),
                    "shares": self._safe_int(r.get("LIST_SHRS")),
                }
            )

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows).set_index("ticker")

    def get_ticker_name(self, ticker: str) -> str:
        """종목명 조회 (프리페치 캐시 우선, pykrx 폴백)

        Args:
            ticker: 종목코드

        Returns:
            종목명 (없으면 ticker 반환)
        """
        if ticker in self._ticker_names:
            return self._ticker_names[ticker]
        try:
            # pykrx 내부 버그: 상폐 종목 조회 실패 시 logging.info(args, kwargs)를
            # 잘못 호출하여 "--- Logging error ---"가 stderr에 출력됨.
            # 이를 억제하기 위해 일시적으로 stderr를 /dev/null로 리다이렉트.
            import io
            import sys
            old_stderr = sys.stderr
            sys.stderr = io.StringIO()
            try:
                name = stock.get_market_ticker_name(ticker)
            finally:
                sys.stderr = old_stderr
            self._ticker_names[ticker] = name or ticker
        except Exception:
            self._ticker_names[ticker] = ticker
        return self._ticker_names[ticker]

    @staticmethod
    def _safe_float(val: object) -> Optional[float]:
        """안전한 float 변환

        빈 문자열이나 None은 None 반환. 0은 유효한 값으로 간주.
        """
        if val is None:
            return None
        if isinstance(val, str) and val.strip() == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val: object) -> Optional[int]:
        """안전한 int 변환"""
        if val is None:
            return None
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _normalize_ticker(raw: object) -> str:
        """KRX API 응답의 ticker를 6자리 zero-padded 문자열로 정규화

        API에 따라 ISU_CD='095570'(str) 또는 ISU_SRT_CD=95570.0(float)으로
        반환되므로 통일된 형식으로 변환합니다.
        """
        if raw is None:
            return ""
        if isinstance(raw, float):
            raw = int(raw)
        return str(raw).strip().zfill(6)


class ReturnCalculator:
    """수익률 계산기 (모멘텀 팩터용)"""

    def __init__(
        self,
        request_delay: float = 0.3,
        collector: Optional[KRXDataCollector] = None,
    ) -> None:
        self.collector = collector or KRXDataCollector(request_delay=request_delay)

    def get_momentum_return(
        self,
        ticker: str,
        base_date: str,
        lookback_months: int = 12,
        skip_months: int = 1,
    ) -> Optional[float]:
        """모멘텀 수익률 계산: (t-skip 가격) / (t-lookback 가격) - 1

        표준 12개월 모멘텀: lookback_months=12, skip_months=1
        → (t-1개월 가격) / (t-12개월 가격) - 1  (최근 1개월 반전 효과 제거)

        Args:
            ticker: 종목코드
            base_date: 기준 날짜 (YYYYMMDD)
            lookback_months: 되돌아볼 기간 (월)
            skip_months: 최근 제외 기간 (월, 반전 효과 제거)

        Returns:
            float 수익률 또는 None (데이터 부족)
        """
        base_dt = datetime.strptime(base_date, "%Y%m%d")
        end_dt = base_dt - relativedelta(months=skip_months)
        start_dt = base_dt - relativedelta(months=lookback_months)

        end_str = end_dt.strftime("%Y%m%d")
        start_str = start_dt.strftime("%Y%m%d")

        try:
            df = self.collector.get_ohlcv(ticker, start_str, end_str)
            if df.empty or len(df) < 10:
                return None

            start_price = df["close"].iloc[0]
            end_price = df["close"].iloc[-1]

            if start_price <= 0:
                return None

            return float(end_price / start_price - 1)
        except Exception as e:
            logger.warning(f"모멘텀 수익률 계산 실패 ({ticker}): {e}")
            return None

    def get_returns_for_universe(
        self,
        tickers: list[str],
        base_date: str,
        lookback_months: int = 12,
        skip_months: int = 1,
    ) -> pd.Series:
        """유니버스 전체 종목 모멘텀 수익률 계산 (벌크 우선)

        Args:
            tickers: 종목코드 리스트
            base_date: 기준 날짜 (YYYYMMDD)
            lookback_months: 되돌아볼 기간 (월)
            skip_months: 최근 제외 기간 (월)

        Returns:
            Series(index=ticker, values=return_rate)
        """
        base_dt = datetime.strptime(base_date, "%Y%m%d")
        end_dt = base_dt - relativedelta(months=skip_months)
        start_dt = base_dt - relativedelta(months=lookback_months)

        end_str = end_dt.strftime("%Y%m%d")
        start_str = start_dt.strftime("%Y%m%d")
        sd = _parse_date(start_str)
        ed = _parse_date(end_str)

        # 벌크 DB 조회 시도
        bulk_df = self.collector.storage.load_daily_prices_bulk(tickers, sd, ed)

        results: dict[str, float] = {}
        remaining: list[str] = []

        if not bulk_df.empty:
            # 벌크 데이터에서 벡터화 수익률 계산
            bulk_df = bulk_df.sort_values(["ticker", "date"])

            for ticker, group in bulk_df.groupby("ticker"):
                if len(group) < 10:
                    remaining.append(str(ticker))
                    continue
                start_price = group["close"].iloc[0]
                end_price = group["close"].iloc[-1]
                if start_price > 0:
                    results[str(ticker)] = float(end_price / start_price - 1)

            # 벌크에서 조회 안 된 종목
            found = set(bulk_df["ticker"].unique())
            remaining.extend([t for t in tickers if t not in found])
        else:
            remaining = list(tickers)

        # 나머지: KRX Open API 프리페치 → DB 벌크 재조회
        total = len(tickers)
        if remaining:
            # 시작일/종료일 근처 영업일 프리페치 (KRX Open API)
            from config.calendar import get_krx_sessions

            sessions = get_krx_sessions(start_str, end_str)
            if len(sessions) >= 2:
                first_day = sessions[0].strftime("%Y%m%d")
                last_day = sessions[-1].strftime("%Y%m%d")
                logger.info(
                    f"모멘텀 벌크 프리페치: {first_day}, {last_day}"
                )
                self.collector.prefetch_daily_trade(first_day, market=settings.universe.market)
                self.collector.prefetch_daily_trade(last_day, market=settings.universe.market)

                # DB 재조회
                bulk_df2 = self.collector.storage.load_daily_prices_bulk(
                    remaining, sd, ed
                )
                if not bulk_df2.empty:
                    bulk_df2["date"] = pd.to_datetime(bulk_df2["date"])
                    bulk_df2 = bulk_df2.sort_values(["ticker", "date"])
                    filled = 0
                    remaining_set = set(remaining)
                    for ticker, group in bulk_df2.groupby("ticker"):
                        if len(group) < 2:
                            continue
                        sp = group["close"].iloc[0]
                        ep = group["close"].iloc[-1]
                        if sp > 0:
                            results[str(ticker)] = float(ep / sp - 1)
                            remaining_set.discard(str(ticker))
                            filled += 1
                    remaining = list(remaining_set)
                    logger.info(
                        f"모멘텀 벌크 프리페치 완료: {filled}개 성공, "
                        f"{len(remaining)}개 미스"
                    )

            # 벌크에서도 못 구한 종목만 개별 조회
            if remaining:
                pbar = tqdm(
                    remaining,
                    desc="모멘텀 개별 조회",
                    unit="종목",
                    bar_format=(
                        "{l_bar}{bar}| {n_fmt}/{total_fmt} "
                        "[{elapsed}<{remaining}]"
                    ),
                )
                found_count = 0
                for ticker in pbar:
                    ret = self.get_momentum_return(
                        ticker, base_date, lookback_months, skip_months
                    )
                    if ret is not None:
                        results[ticker] = ret
                        found_count += 1
                    pbar.set_postfix(유효=found_count, refresh=False)

        logger.info(f"수익률 계산 완료: {len(results)}/{total}개")
        return pd.Series(results, name=f"return_{lookback_months}m")

    def get_returns_multi_period(
        self,
        tickers: list[str],
        base_date: str,
        lookback_months_list: list[int],
        skip_months: int = 1,
    ) -> dict[int, pd.Series]:
        """여러 기간의 수익률을 단일 DB 조회로 계산

        가장 긴 lookback 기간으로 한 번 조회한 뒤,
        짧은 기간은 동일 데이터를 슬라이싱하여 계산합니다.

        Args:
            tickers: 종목코드 리스트
            base_date: 기준 날짜 (YYYYMMDD)
            lookback_months_list: 되돌아볼 기간 목록 (예: [12, 6])
            skip_months: 최근 제외 기간 (월)

        Returns:
            {lookback_months: Series(index=ticker, values=return_rate)} 매핑
        """
        max_lookback = max(lookback_months_list)

        # 가장 긴 기간으로 벌크 데이터 조회
        base_dt = datetime.strptime(base_date, "%Y%m%d")
        end_dt = base_dt - relativedelta(months=skip_months)
        start_dt = base_dt - relativedelta(months=max_lookback)
        end_str = end_dt.strftime("%Y%m%d")
        start_str = start_dt.strftime("%Y%m%d")
        sd = _parse_date(start_str)
        ed = _parse_date(end_str)

        bulk_df = self.collector.storage.load_daily_prices_bulk(tickers, sd, ed)

        # 기간별 시작일 계산 (pd.Timestamp으로 통일 — DB date 컬럼과 비교 호환)
        period_starts: dict[int, pd.Timestamp] = {}
        for months in lookback_months_list:
            period_starts[months] = pd.Timestamp(base_dt - relativedelta(months=months))

        all_results: dict[int, dict[str, float]] = {
            m: {} for m in lookback_months_list
        }
        remaining: list[str] = []

        if not bulk_df.empty:
            bulk_df["date"] = pd.to_datetime(bulk_df["date"])
            bulk_df = bulk_df.sort_values(["ticker", "date"])

            for ticker, group in bulk_df.groupby("ticker"):
                tk = str(ticker)
                end_price_row = group.iloc[-1]
                ep = end_price_row["close"]

                for months in lookback_months_list:
                    ps = period_starts[months]
                    period_data = group[group["date"] >= ps]
                    if len(period_data) < 10:
                        continue
                    sp = period_data["close"].iloc[0]
                    if sp > 0:
                        all_results[months][tk] = float(ep / sp - 1)

            found = set(bulk_df["ticker"].unique())
            remaining = [t for t in tickers if t not in found]
        else:
            remaining = list(tickers)

        # 프리페치 및 폴백: 가장 긴 기간 기준
        if remaining:
            from config.calendar import get_krx_sessions

            sessions = get_krx_sessions(start_str, end_str)
            if len(sessions) >= 2:
                first_day = sessions[0].strftime("%Y%m%d")
                last_day = sessions[-1].strftime("%Y%m%d")
                self.collector.prefetch_daily_trade(first_day, market=settings.universe.market)
                self.collector.prefetch_daily_trade(last_day, market=settings.universe.market)

                bulk_df2 = self.collector.storage.load_daily_prices_bulk(
                    remaining, sd, ed
                )
                if not bulk_df2.empty:
                    bulk_df2["date"] = pd.to_datetime(bulk_df2["date"])
                    bulk_df2 = bulk_df2.sort_values(["ticker", "date"])
                    remaining_set = set(remaining)
                    for ticker, group in bulk_df2.groupby("ticker"):
                        tk = str(ticker)
                        if len(group) < 2:
                            continue
                        ep = group["close"].iloc[-1]
                        for months in lookback_months_list:
                            ps = period_starts[months]
                            period_data = group[group["date"] >= ps]
                            if len(period_data) < 2:
                                continue
                            sp = period_data["close"].iloc[0]
                            if sp > 0:
                                all_results[months][tk] = float(ep / sp - 1)
                        remaining_set.discard(tk)
                    remaining = list(remaining_set)

            # 개별 폴백은 가장 긴 기간으로만 (짧은 기간은 포함됨)
            if remaining:
                for ticker in remaining:
                    for months in lookback_months_list:
                        ret = self.get_momentum_return(
                            ticker, base_date, months, skip_months
                        )
                        if ret is not None:
                            all_results[months][ticker] = ret

        output: dict[int, pd.Series] = {}
        for months in lookback_months_list:
            output[months] = pd.Series(
                all_results[months], name=f"return_{months}m"
            )
            logger.info(
                f"수익률 계산 완료 ({months}M): "
                f"{len(all_results[months])}/{len(tickers)}개"
            )
        return output
