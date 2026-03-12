# config/calendar.py
"""KRX (한국거래소) 영업일 캘린더 유틸리티

한국 공휴일(설날, 추석, 광복절 등)을 인식하는 영업일 판단 및 생성.
exchange_calendars 라이브러리의 XKRX 캘린더 사용.
"""
import pandas as pd
from datetime import date
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# 모듈 로드 시 KRX 캘린더 싱글톤 생성
_krx_cal = None


def _get_krx_calendar():
    """KRX 캘린더 lazy 초기화 (테스트 간 모듈 재로드 안전)"""
    global _krx_cal
    if _krx_cal is not None:
        return _krx_cal
    try:
        import exchange_calendars as xcals

        _krx_cal = xcals.get_calendar("XKRX")
    except Exception as e:
        logger.warning(f"KRX 캘린더 초기화 실패, pandas BDay 폴백 사용: {e}")
    return _krx_cal


def is_krx_business_day(dt: Optional[date] = None) -> bool:
    """해당 날짜가 KRX 거래일인지 확인

    Args:
        dt: 확인할 날짜 (None이면 오늘)

    Returns:
        True=거래일, False=휴장일(공휴일/주말)
    """
    if dt is None:
        dt = date.today()
    ts = pd.Timestamp(dt)

    cal = _get_krx_calendar()
    if cal is not None:
        try:
            return cal.is_session(ts)
        except Exception:
            pass

    # 폴백: 주말만 제외
    return ts.weekday() < 5


def is_last_krx_business_day_of_month(dt: Optional[date] = None) -> bool:
    """해당 날짜가 이번 달 마지막 KRX 거래일인지 확인

    Args:
        dt: 확인할 날짜 (None이면 오늘)

    Returns:
        True=마지막 거래일, False=아님
    """
    if dt is None:
        dt = date.today()
    ts = pd.Timestamp(dt)

    cal = _get_krx_calendar()
    if cal is not None:
        try:
            if not cal.is_session(ts):
                return False
            # 이번 달의 마지막 날
            month_end = ts + pd.offsets.MonthEnd(0)
            # month_end 이하의 마지막 거래일 찾기
            last_session = cal.date_to_session(month_end, direction="previous")
            return ts == last_session
        except Exception:
            pass

    # 폴백: pandas BMonthEnd
    last_bday = ts + pd.offsets.BMonthEnd(0)
    return ts == last_bday


def previous_krx_business_day(dt: date) -> pd.Timestamp:
    """직전 KRX 거래일 반환

    Args:
        dt: 기준 날짜

    Returns:
        직전 거래일 Timestamp
    """
    ts = pd.Timestamp(dt)

    cal = _get_krx_calendar()
    if cal is not None:
        try:
            prev_day = ts - pd.Timedelta(days=1)
            return cal.date_to_session(prev_day, direction="previous")
        except Exception:
            pass

    return ts - pd.offsets.BDay(1)


def next_krx_business_day(dt: date) -> pd.Timestamp:
    """다음 KRX 거래일 반환

    Args:
        dt: 기준 날짜

    Returns:
        다음 거래일 Timestamp
    """
    ts = pd.Timestamp(dt)

    cal = _get_krx_calendar()
    if cal is not None:
        try:
            # 다음 날부터 시작해서 가장 가까운 거래일 찾기
            next_day = ts + pd.Timedelta(days=1)
            return cal.date_to_session(next_day, direction="next")
        except Exception:
            pass

    return ts + pd.offsets.BDay(1)


def get_krx_month_end_sessions(
    start_date: str, end_date: str
) -> list[pd.Timestamp]:
    """기간 내 매월 마지막 KRX 거래일 목록 반환

    Args:
        start_date: 시작일 (YYYY-MM-DD 또는 YYYYMMDD)
        end_date: 종료일

    Returns:
        월말 거래일 Timestamp 리스트
    """
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    cal = _get_krx_calendar()
    if cal is not None:
        try:
            sessions = cal.sessions_in_range(start, end)
            if sessions.empty:
                return []
            # 월별 그룹핑 후 각 월의 마지막 세션 추출
            monthly_last = sessions.to_series().groupby(
                sessions.to_period("M")
            ).max()
            return sorted(monthly_last.tolist())
        except Exception:
            pass

    # 폴백: pandas BMonthEnd
    dates = pd.date_range(start, end, freq=pd.offsets.BMonthEnd())
    return list(dates)


def get_krx_sessions(start_date: str, end_date: str) -> pd.DatetimeIndex:
    """기간 내 KRX 거래일 목록 반환

    Args:
        start_date: 시작일
        end_date: 종료일

    Returns:
        거래일 DatetimeIndex
    """
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    cal = _get_krx_calendar()
    if cal is not None:
        try:
            return cal.sessions_in_range(start, end)
        except Exception:
            pass

    return pd.bdate_range(start, end)
