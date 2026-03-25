# tests/test_dart_client.py
"""DART OpenAPI 클라이언트 테스트"""

import json
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from data.dart_client import DartClient, REPRT_CODES


# ───────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────


@pytest.fixture
def dart_client():
    """API 키가 설정된 DartClient"""
    with patch("data.dart_client.settings") as mock_settings:
        mock_settings.dart_api_key = "test_api_key"
        client = DartClient(api_key="test_api_key")
        # corp_code_map 직접 주입 (API 호출 방지)
        client._corp_code_map = {
            "005930": "00126380",  # 삼성전자
            "000660": "00164779",  # SK하이닉스
            "035420": "00266961",  # NAVER
        }
        return client


@pytest.fixture
def sample_dart_response():
    """DART fnlttMultiAcnt 응답 샘플"""
    return {
        "status": "000",
        "message": "정상",
        "list": [
            # 삼성전자 - 연결 EPS
            {
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "stock_code": "005930",
                "reprt_code": "11011",
                "bsns_year": "2024",
                "account_nm": "기본주당이익(손실)",
                "fs_div": "CFS",
                "thstrm_amount": "4,091",
            },
            # 삼성전자 - 연결 자본총계
            {
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "stock_code": "005930",
                "reprt_code": "11011",
                "bsns_year": "2024",
                "account_nm": "자본총계",
                "fs_div": "CFS",
                "thstrm_amount": "361,048,745,000,000",
            },
            # 삼성전자 - 별도 EPS (CFS 우선이므로 무시됨)
            {
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "stock_code": "005930",
                "reprt_code": "11011",
                "bsns_year": "2024",
                "account_nm": "기본주당이익(손실)",
                "fs_div": "OFS",
                "thstrm_amount": "3,500",
            },
            # SK하이닉스 - 연결 EPS
            {
                "corp_code": "00164779",
                "corp_name": "SK하이닉스",
                "stock_code": "000660",
                "reprt_code": "11011",
                "bsns_year": "2024",
                "account_nm": "기본주당이익(손실)",
                "fs_div": "CFS",
                "thstrm_amount": "12,789",
            },
            # SK하이닉스 - 연결 자본총계
            {
                "corp_code": "00164779",
                "corp_name": "SK하이닉스",
                "stock_code": "000660",
                "reprt_code": "11011",
                "bsns_year": "2024",
                "account_nm": "자본총계",
                "fs_div": "CFS",
                "thstrm_amount": "52,876,123,000,000",
            },
        ],
    }


# ───────────────────────────────────────────────
# 보고서 기간 결정 테스트
# ───────────────────────────────────────────────


class TestDetermineReportPeriod:
    def test_january_uses_two_years_ago_annual(self):
        year, code = DartClient._determine_report_period("20250115")
        assert year == "2023"
        assert code == REPRT_CODES["annual"]

    def test_march_uses_two_years_ago_annual(self):
        year, code = DartClient._determine_report_period("20250301")
        assert year == "2023"
        assert code == REPRT_CODES["annual"]

    def test_april_uses_prev_year_annual(self):
        year, code = DartClient._determine_report_period("20250420")
        assert year == "2024"
        assert code == REPRT_CODES["annual"]

    def test_june_uses_current_year_q1(self):
        year, code = DartClient._determine_report_period("20250615")
        assert year == "2025"
        assert code == REPRT_CODES["q1"]

    def test_september_uses_current_year_half(self):
        year, code = DartClient._determine_report_period("20250901")
        assert year == "2025"
        assert code == REPRT_CODES["half"]

    def test_december_uses_current_year_q3(self):
        year, code = DartClient._determine_report_period("20251201")
        assert year == "2025"
        assert code == REPRT_CODES["q3"]


# ───────────────────────────────────────────────
# 금액 파싱 테스트
# ───────────────────────────────────────────────


class TestParseAmount:
    def test_normal_number(self):
        assert DartClient._parse_amount("1234") == 1234.0

    def test_comma_separated(self):
        assert DartClient._parse_amount("1,234,567") == 1234567.0

    def test_negative(self):
        assert DartClient._parse_amount("-500") == -500.0

    def test_empty_string(self):
        assert DartClient._parse_amount("") is None

    def test_dash(self):
        assert DartClient._parse_amount("-") is None

    def test_none_like(self):
        assert DartClient._parse_amount("  ") is None


# ───────────────────────────────────────────────
# 재무항목 추출 테스트
# ───────────────────────────────────────────────


class TestExtractFinancialItems:
    def test_extracts_eps_and_equity(self, dart_client, sample_dart_response):
        items = sample_dart_response["list"]
        eps_map, net_income_map, equity_map, operating_cf_map, *_ = dart_client._extract_financial_items(items)

        assert "005930" in eps_map
        assert eps_map["005930"] == 4091.0  # CFS 우선
        assert "000660" in eps_map
        assert eps_map["000660"] == 12789.0

        assert "005930" in equity_map
        assert equity_map["005930"] == 361048745000000.0

    def test_extracts_net_income(self, dart_client):
        """당기순이익 추출 테스트"""
        items = [
            {
                "stock_code": "005930",
                "account_nm": "당기순이익",
                "fs_div": "CFS",
                "thstrm_amount": "15,487,000,000,000",
            },
        ]
        _, net_income_map, *_ = dart_client._extract_financial_items(items)
        assert "005930" in net_income_map
        assert net_income_map["005930"] == 15487000000000.0

    def test_cfs_preferred_over_ofs(self, dart_client, sample_dart_response):
        """연결재무제표(CFS) 우선, 별도(OFS) 폴백"""
        items = sample_dart_response["list"]
        eps_map, *_ = dart_client._extract_financial_items(items)

        # CFS EPS=4091, OFS EPS=3500 → CFS 선택
        assert eps_map["005930"] == 4091.0

    def test_ofs_fallback_when_no_cfs(self, dart_client):
        """CFS 없으면 OFS 사용"""
        items = [
            {
                "stock_code": "035420",
                "account_nm": "기본주당이익(손실)",
                "fs_div": "OFS",
                "thstrm_amount": "5,000",
            }
        ]
        eps_map, *_ = dart_client._extract_financial_items(items)
        assert eps_map["035420"] == 5000.0

    def test_empty_items(self, dart_client):
        result = dart_client._extract_financial_items([])
        assert len(result) == 7
        for m in result:
            assert len(m) == 0

    def test_extracts_operating_cashflow(self, dart_client):
        """영업활동현금흐름 추출 테스트"""
        items = [
            {
                "stock_code": "005930",
                "account_nm": "영업활동현금흐름",
                "fs_div": "CFS",
                "thstrm_amount": "50,000,000,000,000",
            },
        ]
        _, _, _, operating_cf_map, *_ = dart_client._extract_financial_items(items)
        assert "005930" in operating_cf_map
        assert operating_cf_map["005930"] == 50000000000000.0

    def test_extracts_revenue_and_operating_income(self, dart_client):
        """매출액, 영업이익, 총자산 추출 테스트"""
        items = [
            {
                "stock_code": "005930",
                "account_nm": "매출액",
                "fs_div": "CFS",
                "thstrm_amount": "258,935,494,000,000",
            },
            {
                "stock_code": "005930",
                "account_nm": "영업이익",
                "fs_div": "CFS",
                "thstrm_amount": "6,566,976,000,000",
            },
            {
                "stock_code": "005930",
                "account_nm": "자산총계",
                "fs_div": "CFS",
                "thstrm_amount": "455,905,980,000,000",
            },
        ]
        result = dart_client._extract_financial_items(items)
        revenue_map = result[4]
        oi_map = result[5]
        ta_map = result[6]
        assert revenue_map["005930"] == 258935494000000.0
        assert oi_map["005930"] == 6566976000000.0
        assert ta_map["005930"] == 455905980000000.0


# ───────────────────────────────────────────────
# 펀더멘털 계산 테스트
# ───────────────────────────────────────────────


class TestGetFundamentalsForDate:
    @patch("data.dart_client.requests.get")
    def test_calculates_per_pbr(self, mock_get, dart_client, sample_dart_response):
        """EPS/자본총계 → PER/PBR 계산"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = sample_dart_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        tickers = ["005930", "000660"]
        close_prices = pd.Series({"005930": 70000.0, "000660": 150000.0})
        shares = pd.Series({"005930": 5_969_782_550, "000660": 728_002_365})

        df = dart_client.get_fundamentals_for_date(
            tickers, "20250420", close_prices, shares
        )

        assert not df.empty
        assert "005930" in df.index

        # PER = 70000 / 4091 ≈ 17.1
        assert 15 < df.loc["005930", "PER"] < 20
        # EPS
        assert df.loc["005930", "EPS"] == 4091.0
        # BPS = 361048745000000 / 5969782550 ≈ 60478
        assert df.loc["005930", "BPS"] > 50000
        # PBR = 70000 / BPS
        assert df.loc["005930", "PBR"] > 0

    @patch("data.dart_client.requests.get")
    def test_empty_when_no_api_key(self, mock_get):
        """API 키 없으면 빈 DataFrame 반환"""
        with patch("data.dart_client.settings") as mock_settings:
            mock_settings.dart_api_key = ""
            client = DartClient(api_key="")
            df = client.get_fundamentals_for_date(
                ["005930"], "20250420",
                pd.Series({"005930": 70000.0}),
                pd.Series({"005930": 5_000_000_000}),
            )
            assert df.empty
            mock_get.assert_not_called()

    @patch("data.dart_client.requests.get")
    def test_retry_prev_year_on_empty(self, mock_get, dart_client, sample_dart_response):
        """첫 조회 실패 시 이전 연도로 재시도"""
        empty_resp = MagicMock()
        empty_resp.json.return_value = {"status": "013", "message": "조회 결과 없음"}
        empty_resp.raise_for_status = MagicMock()

        ok_resp = MagicMock()
        ok_resp.json.return_value = sample_dart_response
        ok_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [empty_resp, ok_resp]

        tickers = ["005930"]
        close_prices = pd.Series({"005930": 70000.0})
        shares = pd.Series({"005930": 5_969_782_550})

        df = dart_client.get_fundamentals_for_date(
            tickers, "20250420", close_prices, shares
        )

        assert not df.empty
        assert mock_get.call_count == 2

    @patch("data.dart_client.requests.get")
    def test_eps_from_net_income(self, mock_get, dart_client):
        """EPS 직접 제공 없을 때 당기순이익/주식수로 계산"""
        response = {
            "status": "000",
            "list": [
                {
                    "stock_code": "005930",
                    "account_nm": "당기순이익",
                    "fs_div": "CFS",
                    "thstrm_amount": "15,487,000,000,000",
                },
                {
                    "stock_code": "005930",
                    "account_nm": "자본총계",
                    "fs_div": "CFS",
                    "thstrm_amount": "361,048,745,000,000",
                },
            ],
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        shares_val = 5_969_782_550
        df = dart_client.get_fundamentals_for_date(
            ["005930"], "20250420",
            pd.Series({"005930": 70000.0}),
            pd.Series({"005930": shares_val}),
        )

        assert "005930" in df.index
        # EPS = 15487000000000 / 5969782550 ≈ 2594
        expected_eps = 15487000000000 / shares_val
        assert abs(df.loc["005930", "EPS"] - expected_eps) < 1
        # PER = 70000 / EPS
        assert df.loc["005930", "PER"] > 0
        assert df.loc["005930", "BPS"] > 0
        assert df.loc["005930", "PBR"] > 0

    @patch("data.dart_client.requests.get")
    def test_negative_eps_no_per(self, mock_get, dart_client):
        """적자 기업 (EPS < 0) → PER은 None"""
        response = {
            "status": "000",
            "list": [
                {
                    "stock_code": "005930",
                    "account_nm": "기본주당이익(손실)",
                    "fs_div": "CFS",
                    "thstrm_amount": "-1,000",
                },
                {
                    "stock_code": "005930",
                    "account_nm": "자본총계",
                    "fs_div": "CFS",
                    "thstrm_amount": "100,000,000,000",
                },
            ],
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        df = dart_client.get_fundamentals_for_date(
            ["005930"], "20250420",
            pd.Series({"005930": 70000.0}),
            pd.Series({"005930": 1_000_000_000}),
        )

        assert "005930" in df.index
        assert df.loc["005930", "EPS"] == -1000.0
        assert pd.isna(df.loc["005930", "PER"])  # 적자 → PER 없음
        assert df.loc["005930", "BPS"] > 0
        assert df.loc["005930", "PBR"] > 0


# ───────────────────────────────────────────────
# Corp Code 캐시 테스트
# ───────────────────────────────────────────────


class TestCorpCodeCache:
    def test_direct_injection(self, dart_client):
        """직접 주입한 매핑 확인"""
        assert dart_client.corp_code_map["005930"] == "00126380"
        assert len(dart_client.corp_code_map) == 3

    @patch("data.dart_client.requests.get")
    @patch("data.dart_client.Path")
    def test_downloads_when_no_cache(self, mock_path, mock_get):
        """캐시 없으면 API 다운로드"""
        import io
        import zipfile

        # 캐시 파일 없음
        mock_path_inst = MagicMock()
        mock_path_inst.exists.return_value = False
        mock_path_inst.parent.mkdir = MagicMock()
        mock_path.return_value = mock_path_inst

        # ZIP 응답 생성
        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <result>
            <list><corp_code>00126380</corp_code><corp_name>test</corp_name>
            <stock_code>005930</stock_code><modify_date>20240101</modify_date></list>
        </result>"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("CORPCODE.xml", xml_content)
        buf.seek(0)

        mock_resp = MagicMock()
        mock_resp.content = buf.read()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with patch("data.dart_client.settings") as mock_settings:
            mock_settings.dart_api_key = "test_key"
            client = DartClient(api_key="test_key")
            # open mock for cache write
            m_open = MagicMock()
            with patch("builtins.open", m_open):
                mapping = client._load_corp_codes()

        assert "005930" in mapping
        assert mapping["005930"] == "00126380"
