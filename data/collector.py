# data/collector.py
import pandas as pd
from pykrx import stock
from datetime import datetime, date as date_type
from dateutil.relativedelta import relativedelta
import logging
import time
from typing import Optional, Callable, TypeVar
from functools import wraps

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

# pykrx Fundamental 컬럼 매핑
FUNDAMENTAL_COLUMNS = {
    "BPS": "BPS",
    "PER": "PER",
    "PBR": "PBR",
    "EPS": "EPS",
    "DIV": "DIV",
}

# 시가총액 컬럼 매핑
MARKET_CAP_COLUMNS = {
    "시가총액": "market_cap",
    "상장주식수": "shares",
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
    """KRX 데이터 수집 (KRX Open API + pykrx + SQLite 캐시)

    데이터 조회 우선순위:
      1. SQLite 캐시 (이전에 저장된 데이터)
      2. KRX Open API (pykrx-openapi, 인증키 필요)
      3. pykrx 폴백 (개별 종목 OHLCV만 가능)
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
                        ticker = r.get("ISU_SRT_CD", "")
                        name = r.get("ISU_ABBRV", "")
                        if ticker:
                            rows.append(
                                {"ticker": ticker, "name": name, "market": market}
                            )
                    logger.info(f"[{date}] {market} 종목 수: {len(rows)} (KRX API)")
                    return pd.DataFrame(rows)
            except Exception as e:
                logger.warning(f"KRX API 종목 조회 실패: {e}")

        # pykrx 폴백
        try:
            tickers = stock.get_market_ticker_list(date, market=market)
            rows = []
            for ticker in tickers:
                try:
                    name = stock.get_market_ticker_name(ticker)
                    rows.append({"ticker": ticker, "name": name, "market": market})
                except Exception:
                    rows.append({"ticker": ticker, "name": "", "market": market})
            logger.info(f"[{date}] {market} 종목 수: {len(rows)} (pykrx)")
            return pd.DataFrame(rows)
        except Exception as e:
            logger.warning(f"pykrx 종목 조회 실패: {e}")
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

        # 1. 캐시 확인
        cached = self.storage.load_daily_prices(ticker, sd, ed)
        if not cached.empty:
            return cached

        # 2. pykrx (개별 OHLCV는 여전히 작동)
        df = stock.get_market_ohlcv(start_date, end_date, ticker)
        if df.empty:
            return pd.DataFrame()

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

        # 1. 캐시 확인
        cached = self.storage.load_fundamentals(dt)
        if not cached.empty:
            logger.info(f"[{date}] 기본 지표 캐시 히트 ({len(cached)}건)")
            return cached

        # 2. KRX Open API
        if self.krx_api:
            try:
                data = self.krx_api.get_stock_base_info(date)
                records = data.get("OutBlock_1", [])
                if records:
                    df = self._parse_base_info(records)
                    if not df.empty:
                        self.storage.save_fundamentals(dt, df)
                        logger.info(
                            f"[{date}] 기본 지표: {len(df)}건 (KRX API → 캐시 저장)"
                        )
                        return df
            except Exception as e:
                logger.warning(f"[{date}] KRX API 기본 지표 실패: {e}")

        # 3. pykrx 폴백
        try:
            df = stock.get_market_fundamental(date, market=market)
            if not df.empty:
                df.index.name = "ticker"
                df = df.rename(columns=FUNDAMENTAL_COLUMNS)
                cols = [c for c in ["BPS", "PER", "PBR", "EPS", "DIV"] if c in df.columns]
                df = df[cols]
                self.storage.save_fundamentals(dt, df)
                logger.info(f"[{date}] 기본 지표: {len(df)}건 (pykrx → 캐시 저장)")
                return df
        except Exception as e:
            logger.warning(f"[{date}] pykrx 기본 지표 실패: {e}")

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

        # 1. 캐시 확인
        cached = self.storage.load_market_caps(dt)
        if not cached.empty:
            logger.info(f"[{date}] 시가총액 캐시 히트 ({len(cached)}건)")
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
                        self.storage.save_market_caps(dt, df)
                        logger.info(
                            f"[{date}] 시가총액: {len(df)}건 (KRX API → 캐시 저장)"
                        )
                        return df
            except Exception as e:
                logger.warning(f"[{date}] KRX API 시가총액 실패: {e}")

        # 3. pykrx 폴백
        try:
            df = stock.get_market_cap(date, market=market)
            if not df.empty:
                df.index.name = "ticker"
                df = df.rename(columns=MARKET_CAP_COLUMNS)
                cols = [c for c in ["market_cap", "shares"] if c in df.columns]
                df = df[cols]
                self.storage.save_market_caps(dt, df)
                logger.info(f"[{date}] 시가총액: {len(df)}건 (pykrx → 캐시 저장)")
                return df
        except Exception as e:
            logger.warning(f"[{date}] pykrx 시가총액 실패: {e}")

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
        dt = _parse_date(date)

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
                ticker = r.get("ISU_SRT_CD", "")
                if not ticker:
                    continue
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

            # OHLCV 캐시 저장 (종목별)
            ohlcv_cols = ["open", "high", "low", "close", "volume"]
            for ticker in df.index:
                row_df = df.loc[[ticker], ohlcv_cols].copy()
                row_df.index = [dt]
                row_df.index.name = "date"
                self.storage.save_daily_prices(ticker, row_df)

            # 시가총액 캐시 저장
            cap_cols = [c for c in ["market_cap", "shares"] if c in df.columns]
            if cap_cols:
                self.storage.save_market_caps(dt, df[cap_cols])

            logger.info(f"[{date}] 일별 거래 프리페치: {len(df)}건 캐시 저장")
            return df

        except Exception as e:
            logger.warning(f"[{date}] 일별 거래 프리페치 실패: {e}")
            return pd.DataFrame()

    # ───────────────────────────────────────────────
    # 내부 헬퍼
    # ───────────────────────────────────────────────

    def _get_daily_trade_method(self, market: str) -> Callable:
        """시장에 따른 KRX API 일별 거래 메서드 반환"""
        if market.upper() == "KOSDAQ":
            return self.krx_api.get_kosdaq_stock_daily_trade
        return self.krx_api.get_stock_daily_trade

    def _parse_base_info(self, records: list[dict]) -> pd.DataFrame:
        """KRX API get_stock_base_info 응답을 Fundamental DataFrame으로 변환"""
        rows = []
        for r in records:
            ticker = r.get("ISU_SRT_CD", "")
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
            ticker = r.get("ISU_SRT_CD", "")
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

    @staticmethod
    def _safe_float(val: object) -> Optional[float]:
        """안전한 float 변환"""
        if val is None:
            return None
        try:
            result = float(val)
            return result if result != 0 else None
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


class ReturnCalculator:
    """수익률 계산기 (모멘텀 팩터용)"""

    def __init__(self, request_delay: float = 0.3) -> None:
        self.collector = KRXDataCollector(request_delay=request_delay)

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
        """유니버스 전체 종목 모멘텀 수익률 계산

        Args:
            tickers: 종목코드 리스트
            base_date: 기준 날짜 (YYYYMMDD)
            lookback_months: 되돌아볼 기간 (월)
            skip_months: 최근 제외 기간 (월)

        Returns:
            Series(index=ticker, values=return_rate)
        """
        results: dict[str, float] = {}
        total = len(tickers)

        for i, ticker in enumerate(tickers):
            ret = self.get_momentum_return(
                ticker, base_date, lookback_months, skip_months
            )
            if ret is not None:
                results[ticker] = ret

            if (i + 1) % 50 == 0:
                logger.info(f"  수익률 계산 중: {i + 1}/{total}")
                time.sleep(0.3)

        logger.info(f"수익률 계산 완료: {len(results)}/{total}개")
        return pd.Series(results, name=f"return_{lookback_months}m")
