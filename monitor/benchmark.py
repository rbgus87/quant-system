# monitor/benchmark.py
"""KOSPI 벤치마크 수익률 조회

pykrx → FinanceDataReader 폴백 구조.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_kospi_daily_return(date_str: str) -> float:
    """KOSPI 당일 수익률 (전일 대비 변동률)을 반환한다.

    Args:
        date_str: 조회일 "YYYY-MM-DD" 또는 "YYYYMMDD"

    Returns:
        당일 수익률 (예: 0.0031 = +0.31%). 조회 실패 시 0.0
    """
    dt = datetime.strptime(date_str.replace("-", ""), "%Y%m%d")
    target = dt.strftime("%Y%m%d")
    # 전일~당일 범위 (영업일 고려하여 여유롭게 5일)
    start = (dt - timedelta(days=7)).strftime("%Y%m%d")

    # 1차: pykrx
    try:
        from pykrx.stock import get_index_ohlcv_by_date

        df = get_index_ohlcv_by_date(start, target, "1001")  # 1001 = KOSPI
        if df is not None and len(df) >= 2:
            closes = df["종가"].values
            prev_close = closes[-2]
            cur_close = closes[-1]
            if prev_close > 0:
                return float((cur_close / prev_close) - 1)
    except Exception as e:
        logger.warning("pykrx KOSPI 지수 조회 실패: %s", e)

    # 2차: FinanceDataReader 폴백
    try:
        import FinanceDataReader as fdr

        df = fdr.DataReader("KS11", start, target)
        if df is not None and len(df) >= 2:
            closes = df["Close"].values
            prev_close = closes[-2]
            cur_close = closes[-1]
            if prev_close > 0:
                return float((cur_close / prev_close) - 1)
    except Exception as e:
        logger.warning("FinanceDataReader KOSPI 지수 조회 실패: %s", e)

    logger.warning("KOSPI 벤치마크 조회 실패 (date=%s), 0.0 반환", date_str)
    return 0.0
