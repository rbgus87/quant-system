# trading/tick_size.py
"""한국 주식 호가단위 유틸리티 (KRX 규정).

가격대별 호가단위:
    < 2,000원      → 1원
    < 5,000원      → 5원
    < 20,000원     → 10원
    < 50,000원     → 50원
    < 200,000원    → 100원
    < 500,000원    → 500원
    ≥ 500,000원    → 1,000원

KOSPI/KOSDAQ 일반 주식 동일 (2023년 1월 25일 KRX 호가단위 개편 기준).
"""
from __future__ import annotations

import math


def tick_size(price: float) -> int:
    """가격대별 호가단위(원) 반환.

    Args:
        price: 주가 (원)

    Returns:
        해당 가격대의 호가단위 (1, 5, 10, 50, 100, 500, 1000)
    """
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def round_to_tick(price: float, direction: str = "buy") -> float:
    """호가단위로 가격을 반올림 (불리한 방향으로).

    매수 시 올림, 매도 시 내림으로 보수적 체결가를 시뮬레이션합니다.
    가격이 0 이하이면 그대로 반환 (무의미한 호가 처리 방지).

    Args:
        price: 원본 주가 (원)
        direction: "buy" 또는 "sell"

    Returns:
        호가단위로 정렬된 주가

    Raises:
        ValueError: direction이 "buy"/"sell"이 아닐 때
    """
    if direction not in ("buy", "sell"):
        raise ValueError(f"direction은 'buy' 또는 'sell'이어야 합니다: {direction}")
    if price <= 0:
        return float(price)
    tick = tick_size(price)
    if direction == "buy":
        return float(math.ceil(price / tick) * tick)
    return float(math.floor(price / tick) * tick)
