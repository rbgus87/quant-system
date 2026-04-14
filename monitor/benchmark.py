# monitor/benchmark.py
"""KOSPI 벤치마크 수익률 조회

Naver Finance → KRX Open API → pykrx → FinanceDataReader 폴백 구조.
2025-12-27 KRX Data Marketplace 로그인 전환 이후 pykrx/FDR 의 KOSPI 지수 조회는
대부분 실패하므로, Naver Finance 스크래핑을 1차 소스로 사용한다.
KRX Open API(`get_kospi_daily_trade`)는 T+1 지연으로 당일 데이터 부재 시 실패하지만
인증된 공식 데이터라 2차 폴백으로 적합하다.
"""

import logging
import re
import urllib.request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_NAVER_URL = "https://finance.naver.com/sise/sise_index_day.naver?code=KOSPI"
_NAVER_ROW_RE = re.compile(
    r'<td class="date">([^<]+)</td>[\s\S]*?<td class="number_1">([^<]+)</td>'
)


def _fetch_naver_kospi_closes(target_yyyymmdd: str) -> list[tuple[str, float]]:
    """Naver Finance 에서 KOSPI 지수 일별 종가를 최신순으로 가져온다.

    Args:
        target_yyyymmdd: 조회 기준일 "YYYYMMDD" (페이지 수 계산용)

    Returns:
        [(date_str "YYYY-MM-DD", close), ...] 최신순 리스트. 실패 시 빈 리스트.
    """
    try:
        req = urllib.request.Request(
            _NAVER_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("euc-kr", errors="ignore")
    except Exception as e:
        logger.warning("Naver KOSPI 페이지 조회 실패: %s", e)
        return []

    rows = _NAVER_ROW_RE.findall(html)
    result: list[tuple[str, float]] = []
    for date_raw, close_raw in rows:
        try:
            date_str = date_raw.strip().replace(".", "-")
            close = float(close_raw.strip().replace(",", ""))
            result.append((date_str, close))
        except ValueError:
            continue
    return result


def get_kospi_daily_return(date_str: str) -> float:
    """KOSPI 당일 수익률 (전일 대비 변동률)을 반환한다.

    Args:
        date_str: 조회일 "YYYY-MM-DD" 또는 "YYYYMMDD"

    Returns:
        당일 수익률 (예: 0.0031 = +0.31%). 조회 실패 시 0.0
    """
    dt = datetime.strptime(date_str.replace("-", ""), "%Y%m%d")
    target = dt.strftime("%Y%m%d")
    target_iso = dt.strftime("%Y-%m-%d")
    # 전일~당일 범위 (영업일 고려하여 여유롭게 7일)
    start = (dt - timedelta(days=7)).strftime("%Y%m%d")

    # 1차: Naver Finance (KRX 로그인 전환 이후 가장 안정적)
    rows = _fetch_naver_kospi_closes(target)
    if len(rows) >= 2:
        # 최신순 리스트에서 target_iso 이하 가장 최근 2개 선택
        eligible = [(d, c) for d, c in rows if d <= target_iso]
        if len(eligible) >= 2:
            cur_close = eligible[0][1]
            prev_close = eligible[1][1]
            if prev_close > 0:
                return float((cur_close / prev_close) - 1)

    # 2차: KRX Open API (get_kospi_daily_trade, T+1 지연으로 당일은 None일 수 있음)
    try:
        from pykrx_openapi import KRXOpenAPI

        api = KRXOpenAPI()
        resp = api.get_kospi_daily_trade(target)
        block = resp.get("OutBlock_1", []) if isinstance(resp, dict) else []
        kospi_row = next(
            (r for r in block if (r.get("IDX_NM") or "").strip() == "코스피"),
            None,
        )
        if kospi_row is not None:
            fluc_rt = kospi_row.get("FLUC_RT")
            if fluc_rt is not None:
                return float(fluc_rt) / 100.0
    except Exception as e:
        logger.warning("KRX Open API KOSPI 지수 조회 실패: %s", e)

    # 3차: pykrx (KRX 로그인 전환 이후 대부분 실패)
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

    # 4차: FinanceDataReader 폴백
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
