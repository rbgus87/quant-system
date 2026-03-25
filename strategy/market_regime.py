# strategy/market_regime.py
"""시장 레짐 필터 — 하락장 방어 메커니즘

전략 가이드 참고:
  - 듀얼 모멘텀 (2.2): "조건 미충족 시 현금으로 대피"
  - VAA 동적 자산배분 (7.3): "모멘텀 스코어 음수 → 안전 자산 전환"

KOSPI 200일 이동평균 + VAA 모멘텀 스코어를 결합하여
시장 상태에 따라 투자 비중을 동적으로 조절합니다.

KODEX 200 ETF(069500)를 KOSPI 프록시로 사용합니다.
(KRX 인덱스 API 차단으로 직접 지수 조회 불가)
"""
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# KOSPI 프록시 ETF
KOSPI_PROXY_TICKER = "069500"  # KODEX 200


def calc_vol_target_scale(
    recent_values: list[float],
    vol_target: float | None,
    lookback: int,
) -> float:
    """변동성 타겟팅 — 실현 변동성 대비 투자 비중 배율 계산

    실현 변동성이 목표보다 높으면 투자 비중을 축소하고,
    낮으면 비중을 유지(최대 100%)합니다.

    Args:
        recent_values: 최근 N일 포트폴리오/자산 가치 리스트
        vol_target: 목표 연환산 변동성 (None이면 1.0 반환)
        lookback: 변동성 계산에 사용할 기간 (거래일)

    Returns:
        투자 비중 배율 (0.2 ~ 1.0)
    """
    if vol_target is None or vol_target <= 0:
        return 1.0

    if len(recent_values) < max(lookback, 20):
        return 1.0

    values = recent_values[-lookback:]
    returns: list[float] = []
    for j in range(1, len(values)):
        if values[j - 1] > 0:
            returns.append(values[j] / values[j - 1] - 1)

    if len(returns) < 10:
        return 1.0

    realized_vol = float(np.std(returns)) * np.sqrt(252)
    if realized_vol <= 0:
        return 1.0

    scale = vol_target / realized_vol
    result = min(1.0, max(0.2, scale))

    logger.info(
        f"변동성 타겟팅: 실현={realized_vol:.1%}, "
        f"목표={vol_target:.1%} -> 비중 {result:.0%}"
    )
    return result


class MarketRegimeFilter:
    """시장 레짐 기반 투자 비중 조절기

    두 가지 신호를 결합합니다:
    1. 추세 신호: KOSPI가 200일 이동평균 위/아래인지
    2. 모멘텀 신호: VAA 가중 모멘텀 스코어 양수/음수

    둘 다 양호 → 100% 투자
    하나만 불량 → 부분 투자 (기본 50%)
    둘 다 불량 → 최소 투자 (기본 30%)
    """

    def __init__(self, collector: object) -> None:
        """
        Args:
            collector: KRXDataCollector 인스턴스 (OHLCV 조회용)
        """
        self.collector = collector

    def get_invest_ratio(self, date: str) -> float:
        """리밸런싱일 기준 투자 비중 결정

        Args:
            date: 기준 날짜 (YYYYMMDD)

        Returns:
            투자 비중 (0.0 ~ 1.0)
        """
        cfg = settings.market_regime

        if not cfg.enabled:
            return 1.0

        trend_ok = self._check_trend_signal(date, cfg.ma_days)
        momentum_ok = self._check_momentum_signal(date)

        if trend_ok and momentum_ok:
            ratio = 1.0
            regime = "강세"
        elif trend_ok or momentum_ok:
            ratio = cfg.partial_ratio
            regime = "중립"
        else:
            ratio = cfg.defensive_ratio
            regime = "약세"

        logger.info(
            f"[{date}] 시장 레짐: {regime} → 투자 비중 {ratio:.0%} "
            f"(추세={'OK' if trend_ok else 'NG'}, "
            f"모멘텀={'OK' if momentum_ok else 'NG'})"
        )
        return ratio

    def _check_trend_signal(self, date: str, ma_days: int = 200) -> bool:
        """KOSPI(KODEX 200) 이동평균 추세 확인

        Args:
            date: 기준 날짜 (YYYYMMDD)
            ma_days: 이동평균 기간 (기본 200일)

        Returns:
            True = 상승 추세 (종가 > MA), False = 하락 추세
        """
        end_dt = datetime.strptime(date, "%Y%m%d")
        # MA 계산에 충분한 과거 데이터 확보 (영업일 1.5배)
        start_dt = end_dt - timedelta(days=int(ma_days * 1.5))
        start_str = start_dt.strftime("%Y%m%d")

        try:
            df = self.collector.get_ohlcv(KOSPI_PROXY_TICKER, start_str, date)
            if df is None or df.empty or len(df) < ma_days:
                logger.warning(
                    f"[{date}] 추세 신호: 데이터 부족 ({len(df) if df is not None else 0}일)"
                    f" — 상승 추세 가정"
                )
                return True

            closes = df["close"]
            ma = closes.rolling(window=ma_days).mean()
            current_price = closes.iloc[-1]
            current_ma = ma.iloc[-1]

            if pd.isna(current_ma):
                return True

            is_above = current_price > current_ma
            logger.debug(
                f"[{date}] 추세: KODEX200={current_price:,.0f}, "
                f"MA{ma_days}={current_ma:,.0f} → "
                f"{'상승' if is_above else '하락'}"
            )
            return is_above

        except Exception as e:
            logger.warning(f"[{date}] 추세 신호 실패: {e} — 상승 추세 가정")
            return True

    def _check_momentum_signal(self, date: str) -> bool:
        """VAA 가중 모멘텀 스코어 확인

        공식 (전략 가이드 7.3):
          모멘텀 스코어 = 12×(1M수익률) + 4×(3M수익률) + 2×(6M수익률) + 1×(12M수익률)

        Args:
            date: 기준 날짜 (YYYYMMDD)

        Returns:
            True = 모멘텀 양수, False = 모멘텀 음수
        """
        end_dt = datetime.strptime(date, "%Y%m%d")
        start_dt = end_dt - timedelta(days=380)  # 12개월 + 여유
        start_str = start_dt.strftime("%Y%m%d")

        try:
            df = self.collector.get_ohlcv(KOSPI_PROXY_TICKER, start_str, date)
            if df is None or df.empty or len(df) < 20:
                logger.warning(f"[{date}] 모멘텀 신호: 데이터 부족 — 양수 가정")
                return True

            closes = df["close"]
            current = closes.iloc[-1]

            # 각 기간별 수익률 계산
            returns = {}
            for months, label in [(1, "1M"), (3, "3M"), (6, "6M"), (12, "12M")]:
                target_days = months * 21  # 영업일 기준
                if len(closes) > target_days:
                    past_price = closes.iloc[-(target_days + 1)]
                    returns[label] = (current - past_price) / past_price
                else:
                    returns[label] = 0.0

            # VAA 가중 모멘텀 스코어
            score = (
                12 * returns.get("1M", 0)
                + 4 * returns.get("3M", 0)
                + 2 * returns.get("6M", 0)
                + 1 * returns.get("12M", 0)
            )

            is_positive = score > 0
            logger.debug(
                f"[{date}] VAA 모멘텀: score={score:.4f} "
                f"(1M={returns.get('1M', 0):.2%}, 3M={returns.get('3M', 0):.2%}, "
                f"6M={returns.get('6M', 0):.2%}, 12M={returns.get('12M', 0):.2%}) → "
                f"{'양수' if is_positive else '음수'}"
            )
            return is_positive

        except Exception as e:
            logger.warning(f"[{date}] 모멘텀 신호 실패: {e} — 양수 가정")
            return True
