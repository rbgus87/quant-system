# data/processor.py
import pandas as pd
import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DataProcessor:
    """데이터 전처리 및 이상치 처리"""

    @staticmethod
    def clean_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
        """기본 지표 이상치 제거 및 정제

        처리 내용:
        - PBR, PER: 0 이하 제거 (의미 없음)
        - DIV: 음수 제거
        - BPS: 자본잠식(<=0) NaN 처리
        - 각 컬럼 상위 1% Winsorize (이상치 클리핑)

        Args:
            df: 기본 지표 DataFrame (index=ticker, columns=[BPS, PER, PBR, EPS, DIV])

        Returns:
            정제된 DataFrame
        """
        cleaned = df.copy()

        # PBR, PER: 0 이하 → NaN + 상위 1% Winsorize (하위는 유지 — 극저PBR 가치주 보존)
        for col in ["PBR", "PER"]:
            if col in cleaned.columns:
                cleaned[col] = cleaned[col].where(cleaned[col] > 0, np.nan)
                upper = cleaned[col].quantile(0.99)
                cleaned[col] = cleaned[col].clip(upper=upper)

        # DIV: 음수 → NaN
        if "DIV" in cleaned.columns:
            cleaned["DIV"] = cleaned["DIV"].where(cleaned["DIV"] >= 0, np.nan)

        # BPS: 자본잠식(<=0) → NaN
        if "BPS" in cleaned.columns:
            cleaned["BPS"] = cleaned["BPS"].where(cleaned["BPS"] > 0, np.nan)

        logger.info(f"전처리 후 유효 종목: {cleaned.dropna(how='all').shape[0]}")
        return cleaned

    @staticmethod
    def filter_universe(
        tickers: list[str],
        market_cap: pd.DataFrame,
        fundamentals: Optional[pd.DataFrame],
        min_cap_percentile: float = 10.0,
        finance_tickers: Optional[list[str]] = None,
        avg_trading_value: Optional[pd.Series] = None,
        min_avg_trading_value: int = 0,
        suspended_tickers: Optional[set[str]] = None,
    ) -> list[str]:
        """유니버스 필터 적용

        필터 순서:
        1. 거래정지 종목 제외
        2. 시가총액 하위 N% 제외
        3. 금융주 제외
        4. 기본 지표 전무 종목 제외
        5. 유동성 필터 (20일 평균 거래대금 하한)

        Args:
            tickers: 전체 종목 코드 리스트
            market_cap: 시가총액 DataFrame (index=ticker)
            fundamentals: 기본 지표 DataFrame (index=ticker)
            min_cap_percentile: 시가총액 하위 N% 제외 기준
            finance_tickers: 금융주 종목 코드 리스트
            avg_trading_value: 종목별 평균 거래대금 Series (index=ticker)
            min_avg_trading_value: 평균 거래대금 하한 (원)
            suspended_tickers: 거래정지 종목 코드 집합

        Returns:
            필터링된 종목 코드 리스트
        """
        valid = set(tickers)
        initial_count = len(valid)

        # 1. 거래정지 종목 제외
        if suspended_tickers:
            before = len(valid)
            valid -= suspended_tickers
            logger.info(f"거래정지 필터: {before} → {len(valid)}")

        # 2. 시가총액 필터
        if not market_cap.empty and "market_cap" in market_cap.columns:
            threshold = market_cap["market_cap"].quantile(min_cap_percentile / 100)
            large_caps = set(market_cap[market_cap["market_cap"] >= threshold].index)
            before = len(valid)
            valid &= large_caps
            logger.info(f"시가총액 필터: {before} → {len(valid)}")

        # 3. 금융주 제외
        if finance_tickers:
            before = len(valid)
            valid -= set(finance_tickers)
            logger.info(f"금융주 제외: {before} → {len(valid)}")

        # 4. 기본 지표 전무 종목 제외
        if fundamentals is not None and not fundamentals.empty:
            has_data = set(fundamentals.dropna(how="all").index)
            before = len(valid)
            valid &= has_data
            logger.info(f"기본 지표 필터: {before} → {len(valid)}")

        # 5. 유동성 필터 (평균 거래대금)
        if avg_trading_value is not None and min_avg_trading_value > 0:
            liquid = set(
                avg_trading_value[avg_trading_value >= min_avg_trading_value].index
            )
            before = len(valid)
            valid &= liquid
            logger.info(
                f"유동성 필터 (>={min_avg_trading_value:,}원): {before} → {len(valid)}"
            )

        logger.info(f"유니버스 필터 완료: {initial_count} → {len(valid)}")
        return sorted(valid)
