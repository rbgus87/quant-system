"""Low-Volatility 팩터.

저변동성 종목에 높은 점수(0~100)를 부여.
일별 수익률의 rolling std(연율화)를 역순위로 변환.
낮은 변동성 = 높은 점수 = Q1 선호.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class VolatilityFactor:
    """저변동성 팩터: 연율화 변동성의 역순위를 0~100 점수로 반환."""

    def calc_volatility_score(
        self,
        date: str,
        tickers: list[str],
        storage,
        lookback_days: int = 60,
        min_data_ratio: float = 0.7,
    ) -> pd.Series:
        """각 종목의 저변동성 점수 계산 (0~100, 높을수록 저변동성).

        Args:
            date: 기준일 (YYYYMMDD)
            tickers: 종목 리스트
            storage: DataStorage 인스턴스 (load_daily_prices_bulk 호출)
            lookback_days: 변동성 계산 기간 (거래일 수, 기본 60일 ≈ 3개월)
            min_data_ratio: 최소 데이터 비율 (기본 0.7 = 42/60일 이상 필요)

        Returns:
            pd.Series(index=ticker, values=0~100).
            데이터 부족 종목은 NaN.
        """
        if not tickers:
            return pd.Series(dtype=float, name="low_vol_score")

        ann_vol = self._compute_ann_vol(date, tickers, storage, lookback_days, min_data_ratio)
        valid_vol = ann_vol.dropna()

        if valid_vol.empty:
            logger.warning("저변동성 팩터: 유효 종목 없음 [%s]", date)
            return pd.Series(np.nan, index=tickers, name="low_vol_score")

        # 역순위: 낮은 변동성 → 높은 점수
        # ascending=False: 작은 값(저변동)이 높은 rank(pct=1.0) → score=100
        scores = valid_vol.rank(pct=True, ascending=False) * 100.0

        logger.info(
            "저변동성 팩터 [%s]: %d/%d 종목 유효 (lookback=%dd)",
            date, len(scores), len(tickers), lookback_days,
        )

        result = pd.Series(np.nan, index=tickers, name="low_vol_score")
        result.update(scores)
        return result

    def get_raw_volatilities(
        self,
        date: str,
        tickers: list[str],
        storage,
        lookback_days: int = 60,
        min_data_ratio: float = 0.7,
    ) -> dict[str, float]:
        """종목별 연율화 변동성(σ) 원본값 반환.

        calc_volatility_score()의 중간 결과(점수 변환 전)를 반환한다.
        inverse-vol 포지션 사이징에서 σ 값이 필요할 때 사용.

        Args:
            date: 기준일 (YYYYMMDD)
            tickers: 종목 리스트
            storage: DataStorage 인스턴스 (load_daily_prices_bulk 호출)
            lookback_days: 변동성 계산 기간 (거래일 수, 기본 60일)
            min_data_ratio: 최소 데이터 비율 (기본 0.7)

        Returns:
            {ticker: annualized_vol}. 데이터 부족 종목은 키 없음.
        """
        if not tickers:
            return {}
        ann_vol = self._compute_ann_vol(date, tickers, storage, lookback_days, min_data_ratio)
        return ann_vol.dropna().to_dict()

    def _compute_ann_vol(
        self,
        date: str,
        tickers: list[str],
        storage,
        lookback_days: int,
        min_data_ratio: float,
    ) -> pd.Series:
        """종목별 연율화 변동성(σ) 계산 (내부 공통 메서드).

        Args:
            date: 기준일 (YYYYMMDD)
            tickers: 종목 리스트
            storage: DataStorage 인스턴스
            lookback_days: 변동성 계산 기간 (거래일 수)
            min_data_ratio: 최소 데이터 비율

        Returns:
            pd.Series(index=ticker, values=annualized_vol). 데이터 부족 시 NaN.
        """
        end_dt = datetime.strptime(date, "%Y%m%d")
        start_dt = end_dt - timedelta(days=int(lookback_days * 1.5))
        sd = start_dt.date()
        ed = end_dt.date()

        try:
            bulk_df = storage.load_daily_prices_bulk(tickers, sd, ed)
        except Exception as exc:
            logger.warning("변동성 계산: 가격 데이터 조회 실패 [%s]: %s", date, exc)
            return pd.Series(np.nan, index=tickers)

        if bulk_df.empty:
            logger.warning("변동성 계산: 가격 데이터 없음 [%s]", date)
            return pd.Series(np.nan, index=tickers)

        bulk_df = bulk_df.sort_values(["ticker", "date"])
        pivot = bulk_df.pivot_table(index="date", columns="ticker", values="close")
        daily_returns = pivot.pct_change(fill_method=None)

        valid_counts = daily_returns.count()
        ann_vol = daily_returns.std() * math.sqrt(252)

        min_data = int(lookback_days * min_data_ratio)
        valid_mask = valid_counts >= min_data
        result = ann_vol.copy()
        result[~valid_mask] = np.nan

        full = pd.Series(np.nan, index=tickers)
        full.update(result)
        return full
