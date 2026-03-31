# monitor/snapshot.py
"""일간 포트폴리오 스냅샷 수집

balance (KiwoomRestClient.get_balance() 결과)로부터
포트폴리오 상태 + 벤치마크 대비 수익률을 계산한다.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings
from monitor.benchmark import get_kospi_daily_return

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _peak_value_path() -> str:
    """peak/prev value JSON 파일 경로 (telegram.py와 동일 위치)"""
    mode = "paper" if settings.is_paper_trading else "live"
    return os.path.join(_PROJECT_ROOT, "data", f"peak_value_{mode}.json")


def _load_peak_prev() -> tuple[float, float]:
    """peak_value, prev_value를 로드한다.

    Returns:
        (peak_value, prev_value) 튜플. 파일 없으면 (0.0, 0.0)
    """
    try:
        data = json.loads(Path(_peak_value_path()).read_text())
        peak = float(data.get("peak_value") or data.get("peak") or 0)
        prev = float(data.get("prev_value", 0))
        return peak, prev
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 0.0, 0.0


def take_daily_snapshot(balance: dict) -> dict:
    """일간 포트폴리오 스냅샷을 수집한다.

    Args:
        balance: KiwoomRestClient.get_balance() 결과
            {holdings, cash, total_eval_amount, total_profit}

    Returns:
        스냅샷 dict (MonitorStorage.save_snapshot()에 전달 가능)
    """
    holdings = balance.get("holdings", [])
    cash = balance.get("cash", 0)
    total_eval = balance.get("total_eval_amount", 0)
    total_profit = balance.get("total_profit", 0)

    # 투자원금 = 평가금액 - 손익 (telegram.py 동일)
    invested = total_eval - total_profit if total_profit else total_eval

    # 당일 수익률 (전일 대비)
    peak, prev_value = _load_peak_prev()
    daily_return = (total_eval / prev_value - 1) if prev_value and prev_value > 0 else 0.0

    # MDD 계산 (고점 대비 하락률)
    if total_eval > peak:
        peak = total_eval
    mdd = (total_eval / peak - 1) if peak > 0 else 0.0

    # 누적 수익률
    total_return = (total_eval / invested - 1) if invested > 0 else 0.0

    # KOSPI 벤치마크
    today_str = datetime.now().strftime("%Y-%m-%d")
    kospi_return = get_kospi_daily_return(today_str)
    excess_return = daily_return - kospi_return

    # 종목별 상세
    holdings_list = []
    for h in holdings:
        eval_amount = h.get("eval_amount", 0)
        weight = (eval_amount / total_eval * 100) if total_eval > 0 else 0.0
        holdings_list.append({
            "ticker": h.get("ticker", ""),
            "name": h.get("name", h.get("ticker", "")),
            "qty": h.get("qty", 0),
            "avg_price": h.get("avg_price", 0),
            "current_price": h.get("current_price", 0),
            "return_pct": h.get("profit_rate", 0.0),
            "weight_pct": round(weight, 2),
        })

    return {
        "date": today_str,
        "portfolio": {
            "total_value": int(total_eval),
            "total_invested": int(invested),
            "cash": int(cash),
            "daily_return_pct": round(daily_return, 6),
            "total_return_pct": round(total_return, 6),
            "mdd_pct": round(mdd, 6),
        },
        "benchmark": {
            "kospi_daily_return_pct": round(kospi_return, 6),
            "excess_return_pct": round(excess_return, 6),
        },
        "holdings": holdings_list,
    }
