# data/collector.py
import pandas as pd
import numpy as np
from pykrx import stock
from datetime import datetime
from dateutil.relativedelta import relativedelta
import logging
import time
from typing import Optional, Callable, TypeVar
from functools import wraps

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


def retry_on_failure(
    max_retries: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """pykrx API 호출 실패 시 지수 백오프 재시도 데코레이터

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
                        delay = base_delay * (2 ** attempt)
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
    """KRX 데이터 수집 (pykrx 기반)"""

    def __init__(self, request_delay: float = 0.5) -> None:
        """
        Args:
            request_delay: API 호출 간격 (초). 과호출 방지 필수.
        """
        self.delay = request_delay

    # ───────────────────────────────────────────────
    # 유니버스
    # ───────────────────────────────────────────────

    @retry_on_failure(max_retries=3, base_delay=1.0)
    def get_universe(self, date: str, market: str = "KOSPI") -> pd.DataFrame:
        """특정 날짜 기준 상장 종목 목록 조회 (생존 편향 방지)

        Args:
            date: 기준 날짜 (YYYYMMDD) — 해당 날짜의 상장 종목만 반환
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(columns=[ticker, name, market])
        """
        tickers = stock.get_market_ticker_list(date, market=market)
        logger.info(f"[{date}] {market} 종목 수: {len(tickers)}")

        rows: list[dict] = []
        for i, ticker in enumerate(tickers):
            try:
                name = stock.get_market_ticker_name(ticker)
                rows.append({"ticker": ticker, "name": name, "market": market})
                if (i + 1) % 100 == 0:
                    logger.info(f"  진행: {i + 1}/{len(tickers)}")
                    time.sleep(self.delay)
            except Exception as e:
                logger.warning(f"  종목명 조회 실패 ({ticker}): {e}")

        return pd.DataFrame(rows)

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
        """단일 종목 일봉 OHLCV 조회

        Args:
            ticker: 종목코드
            start_date: 시작 날짜 (YYYYMMDD)
            end_date: 종료 날짜 (YYYYMMDD)

        Returns:
            DataFrame(index=date, columns=[open, high, low, close, volume])
        """
        df = stock.get_market_ohlcv(start_date, end_date, ticker)
        if df.empty:
            return pd.DataFrame()
        # 한글 컬럼 → 영문 rename
        df = df.rename(columns=OHLCV_COLUMNS)
        df.index.name = "date"
        return df[["open", "high", "low", "close", "volume"]]

    # ───────────────────────────────────────────────
    # 기본 지표 (Fundamental)
    # ───────────────────────────────────────────────

    @retry_on_failure(max_retries=3, base_delay=1.0)
    def get_fundamentals_all(
        self,
        date: str,
        market: str = "KOSPI",
    ) -> pd.DataFrame:
        """전체 시장 기본 지표 일괄 조회 (배치)

        Args:
            date: 기준 날짜 (YYYYMMDD)
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(index=ticker, columns=[BPS, PER, PBR, EPS, DIV])
        """
        df = stock.get_market_fundamental(date, date, market)
        if df.empty:
            logger.warning(f"[{date}] 기본 지표 데이터 없음")
            return pd.DataFrame()
        df.index.name = "ticker"
        # 컬럼이 한글일 경우 rename (pykrx 버전에 따라 다름)
        df = df.rename(columns=FUNDAMENTAL_COLUMNS)
        return df[["BPS", "PER", "PBR", "EPS", "DIV"]]

    # ───────────────────────────────────────────────
    # 시가총액
    # ───────────────────────────────────────────────

    @retry_on_failure(max_retries=3, base_delay=1.0)
    def get_market_cap(
        self,
        date: str,
        market: str = "KOSPI",
    ) -> pd.DataFrame:
        """전체 시장 시가총액 조회

        Args:
            date: 기준 날짜 (YYYYMMDD)
            market: KOSPI / KOSDAQ

        Returns:
            DataFrame(index=ticker, columns=[market_cap, shares])
        """
        df = stock.get_market_cap(date, market=market)
        if df.empty:
            return pd.DataFrame()
        df.index.name = "ticker"
        df = df.rename(columns=MARKET_CAP_COLUMNS)
        cols = [c for c in ["market_cap", "shares"] if c in df.columns]
        return df[cols]


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
