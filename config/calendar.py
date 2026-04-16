# config/calendar.py
"""KRX (한국거래소) 영업일 캘린더 유틸리티

한국 공휴일(설날, 추석, 광복절 등)을 인식하는 영업일 판단 및 생성.
exchange_calendars 라이브러리의 XKRX 캘린더 사용.
초기화 실패 시 한국 공휴일 하드코딩 폴백.
"""

import pandas as pd
from datetime import date, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 한국 공휴일 폴백 (exchange_calendars 실패 시)
# ────────────────────────────────────────────

# 고정 공휴일 (매년 반복)
_FIXED_HOLIDAYS_MD = [
    (1, 1),   # 신정
    (3, 1),   # 삼일절
    (5, 1),   # 근로자의 날 (KRX 휴장)
    (5, 5),   # 어린이날
    (6, 6),   # 현충일
    (8, 15),  # 광복절
    (10, 3),  # 개천절
    (10, 9),  # 한글날
    (12, 25), # 크리스마스
    (12, 31), # 연말 KRX 휴장
]

# 음력 기반 공휴일 (연도별 하드코딩, 2024~2028)
_LUNAR_HOLIDAYS: dict[int, list[tuple[int, int]]] = {
    2024: [
        (2, 9), (2, 10), (2, 11), (2, 12),  # 설날 연휴
        (5, 15),                              # 부처님 오신 날
        (9, 16), (9, 17), (9, 18),           # 추석 연휴
    ],
    2025: [
        (1, 28), (1, 29), (1, 30),           # 설날 연휴
        (5, 5),                               # 부처님 오신 날 (어린이날과 겹침)
        (10, 5), (10, 6), (10, 7), (10, 8),  # 추석 연휴 + 대체공휴일
    ],
    2026: [
        (2, 16), (2, 17), (2, 18),           # 설날 연휴
        (5, 24),                              # 부처님 오신 날
        (9, 24), (9, 25), (9, 26),           # 추석 연휴
    ],
    2027: [
        (2, 6), (2, 7), (2, 8), (2, 9),     # 설날 연휴 + 대체공휴일
        (5, 13),                              # 부처님 오신 날
        (10, 14), (10, 15), (10, 16),        # 추석 연휴
    ],
    2028: [
        (1, 26), (1, 27), (1, 28),           # 설날 연휴
        (5, 2),                               # 부처님 오신 날
        (10, 2), (10, 3), (10, 4),           # 추석 연휴
    ],
}

# KRX 임시 휴장일 (선거일 등, 확정 시 추가)
_KRX_EXTRA_HOLIDAYS: set[date] = set()


def _is_korean_holiday_fallback(dt: date) -> bool:
    """exchange_calendars 없이 한국 공휴일 판단 (폴백)"""
    # 주말
    if dt.weekday() >= 5:
        return True
    # 고정 공휴일
    if (dt.month, dt.day) in _FIXED_HOLIDAYS_MD:
        return True
    # 음력 기반 공휴일
    lunar = _LUNAR_HOLIDAYS.get(dt.year, [])
    if (dt.month, dt.day) in lunar:
        return True
    # 임시 휴장일
    if dt in _KRX_EXTRA_HOLIDAYS:
        return True
    return False


def _is_krx_business_day_fallback(dt: date) -> bool:
    """폴백: 한국 공휴일 + 주말 제외"""
    return not _is_korean_holiday_fallback(dt)


# ────────────────────────────────────────────
# exchange_calendars 캘린더 (주 소스)
# ────────────────────────────────────────────

_krx_cal = None
_cal_init_failed = False


def _get_krx_calendar():
    """KRX 캘린더 lazy 초기화 (재시도 포함)"""
    global _krx_cal, _cal_init_failed
    if _krx_cal is not None:
        return _krx_cal
    if _cal_init_failed:
        return None
    try:
        import exchange_calendars as xcals

        _krx_cal = xcals.get_calendar("XKRX")
    except Exception as e:
        _cal_init_failed = True
        logger.warning(
            f"KRX 캘린더 초기화 실패, 한국 공휴일 하드코딩 폴백 사용: {e}"
        )
        # 텔레그램 알림 (CRITICAL — 폴백 정확도 한계)
        try:
            from notify.telegram import TelegramNotifier

            TelegramNotifier().send(
                f"⚠️ KRX 캘린더 초기화 실패\n"
                f"한국 공휴일 하드코딩 폴백 사용 중\n"
                f"오류: {e}"
            )
        except Exception:
            pass
    return _krx_cal


def _is_within_calendar_range(ts: pd.Timestamp) -> bool:
    """exchange_calendars의 유효 범위 내인지 확인"""
    cal = _get_krx_calendar()
    if cal is None:
        return False
    return cal.first_session <= ts <= cal.last_session


# ────────────────────────────────────────────
# 공개 API
# ────────────────────────────────────────────


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
            if _is_within_calendar_range(ts):
                return cal.is_session(ts)
        except Exception:
            pass

    # 폴백: 한국 공휴일 하드코딩
    return _is_krx_business_day_fallback(dt)


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
            if not _is_within_calendar_range(ts):
                raise ValueError("out of calendar range")
            if not cal.is_session(ts):
                return False
            month_end = ts + pd.offsets.MonthEnd(0)
            # month_end가 캘린더 범위를 초과하면 클램프
            if month_end > cal.last_session:
                month_end = cal.last_session
            last_session = cal.date_to_session(month_end, direction="previous")
            return ts == last_session
        except Exception:
            pass

    # 폴백: 해당 월 말일부터 거꾸로 첫 영업일 탐색
    if not _is_krx_business_day_fallback(dt):
        return False
    month_end = (dt.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    d = month_end
    while not _is_krx_business_day_fallback(d):
        d -= timedelta(days=1)
    return dt == d


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
            if _is_within_calendar_range(prev_day):
                return cal.date_to_session(prev_day, direction="previous")
        except Exception:
            pass

    # 폴백: 하루씩 뒤로 가며 영업일 탐색
    d = dt - timedelta(days=1)
    while not _is_krx_business_day_fallback(d):
        d -= timedelta(days=1)
    return pd.Timestamp(d)


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
            next_day = ts + pd.Timedelta(days=1)
            if _is_within_calendar_range(next_day):
                return cal.date_to_session(next_day, direction="next")
        except Exception:
            pass

    # 폴백: 하루씩 앞으로 가며 영업일 탐색
    d = dt + timedelta(days=1)
    while not _is_krx_business_day_fallback(d):
        d += timedelta(days=1)
    return pd.Timestamp(d)


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
            # 캘린더 범위로 클램프
            clamped_start = max(start, cal.first_session)
            clamped_end = min(end, cal.last_session)
            if clamped_start > clamped_end:
                raise ValueError("date range outside calendar bounds")

            sessions = cal.sessions_in_range(clamped_start, clamped_end)
            if sessions.empty:
                return []
            monthly_last = sessions.to_series().groupby(
                sessions.to_period("M")
            ).max()
            return sorted(monthly_last.tolist())
        except Exception:
            pass

    # 폴백: 월별로 마지막 영업일 탐색
    result: list[pd.Timestamp] = []
    d = start.date().replace(day=1)
    end_d = end.date()
    while d <= end_d:
        # 해당 월 말일
        month_end = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if month_end > end_d:
            month_end = end_d
        # 거꾸로 첫 영업일 탐색
        dd = month_end
        while dd >= d and not _is_krx_business_day_fallback(dd):
            dd -= timedelta(days=1)
        if dd >= d and _is_krx_business_day_fallback(dd):
            ts = pd.Timestamp(dd)
            if ts >= start:
                result.append(ts)
        # 다음 달
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


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
            clamped_start = max(start, cal.first_session)
            clamped_end = min(end, cal.last_session)
            if clamped_start > clamped_end:
                raise ValueError("date range outside calendar bounds")
            return cal.sessions_in_range(clamped_start, clamped_end)
        except Exception:
            pass

    # 폴백: 하루씩 순회
    days = []
    d = start.date()
    end_d = end.date()
    while d <= end_d:
        if _is_krx_business_day_fallback(d):
            days.append(pd.Timestamp(d))
        d += timedelta(days=1)
    return pd.DatetimeIndex(days)
