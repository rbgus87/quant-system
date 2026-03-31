# monitor/drift.py
"""가중치 드리프트 모니터링

리밸런싱 시점의 목표 비중과 현재 실제 비중을 비교하여
드리프트(이탈)를 계산한다.
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func

from data.storage import DataStorage, Portfolio

logger = logging.getLogger(__name__)


def calculate_drift(snapshot: dict) -> Optional[dict]:
    """목표 비중 대비 현재 비중 드리프트를 계산한다.

    Args:
        snapshot: take_daily_snapshot() 반환값

    Returns:
        드리프트 dict 또는 None (Portfolio 데이터 없을 때)
    """
    storage = DataStorage()

    # 가장 최근 리밸런싱 날짜의 목표 비중 조회
    with storage.SessionLocal() as session:
        max_date_row = session.query(
            func.max(Portfolio.rebalance_date)
        ).scalar()

        if max_date_row is None:
            logger.info("Portfolio 데이터 없음 — 드리프트 계산 스킵")
            return None

        rebalance_date = max_date_row
        rows = (
            session.query(Portfolio)
            .filter(Portfolio.rebalance_date == rebalance_date)
            .all()
        )

    if not rows:
        return None

    # 목표 비중 {ticker: weight_pct}  (DB는 0~1 비율 → 퍼센트로 변환)
    target_weights: dict[str, tuple[str, float]] = {}
    for row in rows:
        weight_pct = (row.weight or 0.0) * 100
        target_weights[row.ticker] = (row.name or row.ticker, weight_pct)

    # 현재 비중 {ticker: weight_pct}
    current_weights: dict[str, tuple[str, float]] = {}
    for h in snapshot.get("holdings", []):
        ticker = h["ticker"]
        current_weights[ticker] = (h.get("name", ticker), h["weight_pct"])

    # 드리프트 계산
    holdings_drift: list[dict] = []

    # 목표에 있는 종목
    for ticker, (name, target_w) in target_weights.items():
        if ticker in current_weights:
            cur_name, current_w = current_weights[ticker]
            drift_pct = current_w - target_w
            display_name = cur_name or name
        else:
            # 목표에 있지만 현재 없는 종목 → 완전 이탈
            current_w = 0.0
            drift_pct = -target_w
            display_name = name

        holdings_drift.append({
            "ticker": ticker,
            "name": display_name,
            "target_weight_pct": round(target_w, 2),
            "current_weight_pct": round(current_w, 2),
            "drift_pct": round(drift_pct, 2),
        })

    # 현재 있지만 목표에 없는 종목 → 무시 (요구사항)

    if not holdings_drift:
        return None

    # |drift| 내림차순 정렬
    holdings_drift.sort(key=lambda x: abs(x["drift_pct"]), reverse=True)

    # 집계
    abs_drifts = [abs(d["drift_pct"]) for d in holdings_drift]
    avg_abs_drift = sum(abs_drifts) / len(abs_drifts) if abs_drifts else 0.0
    total_drift_score = sum(abs_drifts)
    max_drift_item = holdings_drift[0]

    snapshot_date = snapshot.get("date", datetime.now().strftime("%Y-%m-%d"))
    rebalance_str = rebalance_date.strftime("%Y-%m-%d")

    # 경과일 계산
    snap_dt = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    days_since = (snap_dt - rebalance_date).days

    return {
        "rebalance_date": rebalance_str,
        "snapshot_date": snapshot_date,
        "days_since_rebalance": days_since,
        "avg_abs_drift_pct": round(avg_abs_drift, 2),
        "max_drift": {
            "ticker": max_drift_item["ticker"],
            "name": max_drift_item["name"],
            "target_weight_pct": max_drift_item["target_weight_pct"],
            "current_weight_pct": max_drift_item["current_weight_pct"],
            "drift_pct": max_drift_item["drift_pct"],
        },
        "total_drift_score": round(total_drift_score, 2),
        "holdings_drift": holdings_drift,
    }
