# tests/test_calendar.py
"""KRX 캘린더 유틸리티 테스트"""

import importlib
import pandas as pd
import pytest
from datetime import date, timedelta
from unittest.mock import patch

from config import calendar as cal_mod
from config.calendar import (
    is_krx_business_day,
    is_last_krx_business_day_of_month,
    previous_krx_business_day,
    next_krx_business_day,
    get_krx_month_end_sessions,
    get_krx_sessions,
    _is_krx_business_day_fallback,
)


class TestIsKrxBusinessDay:
    """is_krx_business_day 테스트"""

    def test_weekday_is_business_day(self) -> None:
        # 2026-04-14 (화)
        assert is_krx_business_day(date(2026, 4, 14)) is True

    def test_saturday_not_business_day(self) -> None:
        # 2026-04-11 (토)
        assert is_krx_business_day(date(2026, 4, 11)) is False

    def test_sunday_not_business_day(self) -> None:
        # 2026-04-12 (일)
        assert is_krx_business_day(date(2026, 4, 12)) is False


class TestKoreanHolidays2026:
    """2026년 KRX 주요 휴장일 검증"""

    @pytest.mark.parametrize(
        "dt,name",
        [
            (date(2026, 1, 1), "신정"),
            (date(2026, 2, 16), "설날 연휴"),
            (date(2026, 2, 17), "설날 연휴"),
            (date(2026, 2, 18), "설날 연휴"),
            (date(2026, 3, 1), "삼일절"),
            (date(2026, 5, 5), "어린이날"),
            (date(2026, 5, 24), "부처님 오신 날"),
            (date(2026, 6, 6), "현충일"),
            (date(2026, 8, 15), "광복절"),
            (date(2026, 9, 24), "추석 연휴"),
            (date(2026, 9, 25), "추석 연휴"),
            (date(2026, 9, 26), "추석 연휴"),
            (date(2026, 10, 3), "개천절"),
            (date(2026, 10, 9), "한글날"),
            (date(2026, 12, 25), "크리스마스"),
        ],
    )
    def test_holiday_not_business_day(self, dt: date, name: str) -> None:
        assert is_krx_business_day(dt) is False, f"{name} ({dt})가 영업일로 판정됨"

    @pytest.mark.parametrize(
        "dt,name",
        [
            (date(2026, 1, 1), "신정"),
            (date(2026, 2, 17), "설날"),
            (date(2026, 5, 24), "석가탄신일"),
            (date(2026, 9, 25), "추석"),
        ],
    )
    def test_fallback_holiday_not_business_day(self, dt: date, name: str) -> None:
        """폴백 함수도 공휴일을 정확히 인식"""
        assert _is_krx_business_day_fallback(dt) is False, (
            f"폴백: {name} ({dt})가 영업일로 판정됨"
        )


class TestPreviousBusinessDay:
    """previous_krx_business_day 테스트"""

    def test_normal_weekday(self) -> None:
        # 2026-04-15 (수) → 4/14 (화)
        result = previous_krx_business_day(date(2026, 4, 15))
        assert result.date() == date(2026, 4, 14)

    def test_skip_weekend(self) -> None:
        # 2026-04-13 (월) → 4/10 (금)
        result = previous_krx_business_day(date(2026, 4, 13))
        assert result.date() == date(2026, 4, 10)

    def test_skip_holiday_chuseok(self) -> None:
        # 2026-09-28 (월) → 9/23 (수) (9/24~26 추석 연휴, 9/27 일)
        result = previous_krx_business_day(date(2026, 9, 28))
        assert result.date() == date(2026, 9, 23)

    def test_skip_seollal(self) -> None:
        # 2026-02-19 (목) → 2/13 (금) (2/14 토, 2/15 일, 2/16~18 설날)
        result = previous_krx_business_day(date(2026, 2, 19))
        assert result.date() == date(2026, 2, 13)


class TestNextBusinessDay:
    """next_krx_business_day 테스트"""

    def test_skip_weekend(self) -> None:
        # 2026-04-10 (금) → 4/13 (월)
        result = next_krx_business_day(date(2026, 4, 10))
        assert result.date() == date(2026, 4, 13)


class TestMonthEndSessions:
    """get_krx_month_end_sessions 테스트"""

    def test_basic_range(self) -> None:
        result = get_krx_month_end_sessions("2026-01-01", "2026-06-30")
        assert len(result) == 6

    def test_each_is_business_day(self) -> None:
        result = get_krx_month_end_sessions("2026-01-01", "2026-12-31")
        for ts in result:
            assert is_krx_business_day(ts.date()), f"{ts} is not a business day"


class TestGetKrxSessions:
    """get_krx_sessions 테스트"""

    def test_no_weekends(self) -> None:
        sessions = get_krx_sessions("2026-04-06", "2026-04-12")
        for ts in sessions:
            assert ts.weekday() < 5


class TestFallbackMode:
    """exchange_calendars 실패 시 폴백 테스트"""

    def test_fallback_business_day(self) -> None:
        """캘린더 없이도 영업일 판단 가능"""
        with patch.object(cal_mod, "_krx_cal", None), \
             patch.object(cal_mod, "_cal_init_failed", True):
            assert is_krx_business_day(date(2026, 4, 14)) is True
            assert is_krx_business_day(date(2026, 4, 11)) is False
            assert is_krx_business_day(date(2026, 1, 1)) is False

    def test_fallback_previous_business_day(self) -> None:
        """캘린더 없이도 직전 영업일 계산 가능"""
        with patch.object(cal_mod, "_krx_cal", None), \
             patch.object(cal_mod, "_cal_init_failed", True):
            result = previous_krx_business_day(date(2026, 4, 13))
            assert result.date() == date(2026, 4, 10)

    def test_fallback_month_end_sessions(self) -> None:
        """캘린더 없이도 월말 세션 계산 가능"""
        with patch.object(cal_mod, "_krx_cal", None), \
             patch.object(cal_mod, "_cal_init_failed", True):
            result = get_krx_month_end_sessions("2026-01-01", "2026-03-31")
            assert len(result) == 3
            for ts in result:
                assert _is_krx_business_day_fallback(ts.date())
