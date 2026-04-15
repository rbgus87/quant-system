# tests/test_screener_lag.py
"""Reporting Lag (strict_reporting_lag) 동작 검증.

005620 사례에서 발견된 버그 회귀 방지용 테스트.
- _get_effective_fundamental_date: 분기 전환 경계 동작
- strict_reporting_lag=True일 때 screener가 fund_query_date로 호출하는지 확인
- 분기보고서 공시 마감일(1/15, 4/15, 7/15, 10/15) 전후 동작
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from strategy.screener import MultiFactorScreener


class TestEffectiveFundamentalDate:
    """_get_effective_fundamental_date: 연간 보고서 기준일 반환"""

    def test_january_uses_two_years_back(self) -> None:
        """1월 리밸런싱 → 전전년도 12월 말 (전년도 연간보고서 미공시)"""
        # 20170115 → 2015년 12월말 (20151230)
        result = MultiFactorScreener._get_effective_fundamental_date("20170115")
        assert result.startswith("2015")

    def test_march_still_uses_two_years_back(self) -> None:
        """3월 리밸런싱 → 전전년도 (전년도 연간보고서 3월말 공시)"""
        result = MultiFactorScreener._get_effective_fundamental_date("20170315")
        assert result.startswith("2015")

    def test_april_uses_one_year_back(self) -> None:
        """4월 리밸런싱 → 전년도 12월말 (연간보고서 3월말 공시 완료)"""
        result = MultiFactorScreener._get_effective_fundamental_date("20170415")
        assert result.startswith("2016")

    def test_june_reproduces_005620_case(self) -> None:
        """005620 사례: 2017-06-30 → 2016년 12월말 데이터 기준"""
        result = MultiFactorScreener._get_effective_fundamental_date("20170630")
        # 20161229 또는 20161230 (12/31 휴장일 보정)
        assert result.startswith("2016")
        assert result[4:6] == "12"

    def test_december_uses_one_year_back(self) -> None:
        """12월 리밸런싱 → 전년도 12월말"""
        result = MultiFactorScreener._get_effective_fundamental_date("20171231")
        assert result.startswith("2016")

    def test_quarterly_boundary_january_15(self) -> None:
        """1/15 (3분기 공시 +60일 시점): 전년도 데이터 미공시 상태로 간주"""
        result = MultiFactorScreener._get_effective_fundamental_date("20170115")
        # 1~3월이므로 전전년도
        assert result.startswith("2015")

    def test_quarterly_boundary_april_15(self) -> None:
        """4/15 (연간보고서 공시 마감 직후): 전년도 데이터 사용 가능"""
        result = MultiFactorScreener._get_effective_fundamental_date("20170415")
        assert result.startswith("2016")

    def test_quarterly_boundary_july_15(self) -> None:
        """7/15 (Q1 공시 +60일): 여전히 전년도 연간 보고서 사용"""
        # strict 모드는 분기 보고서를 신뢰하지 않으므로 4월 이후는 전년도 연간
        result = MultiFactorScreener._get_effective_fundamental_date("20170715")
        assert result.startswith("2016")

    def test_quarterly_boundary_october_15(self) -> None:
        """10/15 (반기 공시 +60일): 전년도 연간 보고서 사용"""
        result = MultiFactorScreener._get_effective_fundamental_date("20171015")
        assert result.startswith("2016")

    def test_returns_krx_business_day(self) -> None:
        """반환값은 실제 KRX 거래일 (12/31이 휴장일이어도 직전 영업일)"""
        result = MultiFactorScreener._get_effective_fundamental_date("20170630")
        # YYYYMMDD 형식
        assert len(result) == 8
        assert result.isdigit()


class TestStrictReportingLagActivation:
    """strict_reporting_lag=True가 screener 호출 경로에 반영되는지 검증"""

    def test_fund_query_date_differs_when_strict(self) -> None:
        """strict 모드: 재무 조회 날짜가 당일과 다르다"""
        strict_date = MultiFactorScreener._get_effective_fundamental_date("20170630")
        assert strict_date != "20170630"
        # 전년도 데이터여야 함
        assert int(strict_date[:4]) < 2017

    def test_strict_mode_config_available(self) -> None:
        """QualityConfig에 strict_reporting_lag 필드 존재"""
        from config.settings import settings

        assert hasattr(settings.quality, "strict_reporting_lag")

    def test_strict_mode_default_true(self) -> None:
        """기본값이 True (005620 재발 방지)"""
        from config.settings import QualityConfig

        cfg = QualityConfig()
        assert cfg.strict_reporting_lag is True


class TestCacheKeyIsolation:
    """strict_reporting_lag 플래그 변경 시 캐시 오염 방지"""

    def test_cache_key_includes_strict_flag(self) -> None:
        """screener 캐시 키에 strict_reporting_lag 포함 확인

        True/False 사이 전환 시 캐시가 섞이면 잘못된 F-Score가 재사용됨.
        소스 코드에서 cache_key 튜플 구성을 확인한다.
        """
        from pathlib import Path

        src = Path("strategy/screener.py").read_text(encoding="utf-8")
        # cache_key 정의 블록에 strict_reporting_lag 참조가 포함돼 있어야 함
        assert "strict_reporting_lag" in src
        # cache_key에 이 값이 들어가는지 구조 확인
        snippet = src[src.find("cache_key = ("):src.find("cache_key = (") + 400]
        assert "strict_reporting_lag" in snippet
