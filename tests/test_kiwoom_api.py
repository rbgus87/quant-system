# tests/test_kiwoom_api.py
import pytest
import requests_mock
from unittest.mock import patch
from datetime import datetime, timedelta

from trading.kiwoom_api import KiwoomRestClient


@pytest.fixture
def client():
    """모의투자 모드 KiwoomRestClient (환경변수 mock)"""
    with patch("trading.kiwoom_api.settings") as mock_settings:
        mock_settings.is_paper_trading = True
        mock_settings.kiwoom_app_key = "test_app_key"
        mock_settings.kiwoom_app_secret = "test_app_secret"
        mock_settings.kiwoom_account_no = "1234567890"
        mock_settings.trading.commission_rate = 0.00015
        c = KiwoomRestClient()
    return c


@pytest.fixture
def token_response():
    """토큰 발급 성공 응답"""
    expires = (datetime.now() + timedelta(hours=23)).strftime("%Y%m%d%H%M%S")
    return {
        "token": "mock_token_abc123",
        "expires_dt": expires,
        "token_type": "Bearer",
        "return_code": 0,
        "return_msg": "정상처리",
    }


class TestTokenManagement:
    """토큰 발급 및 자동 갱신 테스트"""

    def test_issue_token_success(self, client, token_response) -> None:
        """토큰 정상 발급"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            client._issue_token()
            assert client._token == "mock_token_abc123"
            assert client._token_expires_at is not None

    def test_token_property_auto_issue(self, client, token_response) -> None:
        """token 프로퍼티 접근 시 자동 발급"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            token = client.token
            assert token == "mock_token_abc123"
            assert m.called

    def test_token_reuse_when_valid(self, client, token_response) -> None:
        """유효한 토큰이면 재발급하지 않음"""
        client._token = "existing_token"
        client._token_expires_at = datetime.now() + timedelta(hours=1)
        assert client.token == "existing_token"

    def test_token_refresh_before_expiry(self, client, token_response) -> None:
        """만료 10분 전 자동 갱신"""
        client._token = "old_token"
        client._token_expires_at = datetime.now() + timedelta(minutes=5)  # 10분 이내
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            token = client.token
            assert token == "mock_token_abc123"
            assert m.called

    def test_token_issue_failure(self, client) -> None:
        """토큰 발급 실패 시 RuntimeError"""
        error_response = {
            "return_code": -1,
            "return_msg": "인증 실패",
        }
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=error_response)
            with pytest.raises(RuntimeError, match="토큰 발급 실패"):
                client._issue_token()

    def test_token_issue_network_error(self, client) -> None:
        """네트워크 오류 시 예외 전파"""
        with requests_mock.Mocker() as m:
            m.post(
                "https://mockapi.kiwoom.com/oauth2/token",
                exc=ConnectionError("Connection refused"),
            )
            with pytest.raises(ConnectionError):
                client._issue_token()

    def test_token_expires_dt_fallback(self, client) -> None:
        """expires_dt 없을 때 23시간 fallback"""
        response = {
            "token": "fallback_token",
            "expires_dt": "",
            "return_code": 0,
            "return_msg": "정상처리",
        }
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=response)
            client._issue_token()
            assert client._token == "fallback_token"
            # fallback: 약 23시간 후
            diff = client._token_expires_at - datetime.now()
            assert diff.total_seconds() > 22 * 3600


class TestHeaders:
    """요청 헤더 생성 테스트"""

    def test_headers_contain_auth(self, client, token_response) -> None:
        """Authorization 헤더에 Bearer 토큰 포함"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            headers = client._headers("ka10001")
            assert headers["Authorization"] == "Bearer mock_token_abc123"
            assert headers["api-id"] == "ka10001"
            assert headers["Content-Type"] == "application/json;charset=UTF-8"


class TestCurrentPrice:
    """현재가 조회 테스트"""

    def test_get_current_price_success(self, client, token_response) -> None:
        """모의투자: pykrx 폴백으로 현재가 조회"""
        import pandas as pd
        from unittest.mock import patch

        mock_df = pd.DataFrame(
            {"종가": [70000]},
            index=pd.DatetimeIndex(["2026-03-13"]),
        )
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            with patch("trading.kiwoom_api.pykrx_stock") as mock_stock:
                mock_stock.get_market_ohlcv.return_value = mock_df
                result = client.get_current_price("005930")
                assert result["ticker"] == "005930"
                assert result["current_price"] == 70000

    def test_get_current_price_failure(self, client, token_response) -> None:
        """모의투자: pykrx 폴백 실패 시 빈 dict 반환"""
        import pandas as pd
        from unittest.mock import patch

        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            with patch("trading.kiwoom_api.pykrx_stock") as mock_stock:
                mock_stock.get_market_ohlcv.return_value = pd.DataFrame()
                result = client.get_current_price("005930")
                assert result == {}


class TestOrders:
    """매수/매도 주문 테스트"""

    def test_buy_stock_success(self, client, token_response) -> None:
        """시장가 매수 성공"""
        order_response = {
            "return_code": 0,
            "return_msg": "정상처리",
            "ord_no": "ORD001",
        }
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post("https://mockapi.kiwoom.com/api/dostk/ordr", json=order_response)
            result = client.buy_stock("005930", qty=10, order_type="3", exchange="KRX")
            assert result["return_code"] == 0
            assert result["ord_no"] == "ORD001"

    def test_buy_stock_sends_correct_body(self, client, token_response) -> None:
        """매수 요청 본문 검증"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post(
                "https://mockapi.kiwoom.com/api/dostk/ordr",
                json={"return_code": 0},
            )
            client.buy_stock(
                "005930", qty=5, price=70000, order_type="0", exchange="KRX"
            )
            body = m.last_request.json()
            assert body["stk_cd"] == "005930"
            assert body["ord_qty"] == "5"
            assert body["ord_uv"] == "70000"
            assert body["trde_tp"] == "0"  # 지정가

    def test_sell_stock_success(self, client, token_response) -> None:
        """시장가 매도 성공"""
        order_response = {
            "return_code": 0,
            "return_msg": "정상처리",
            "ord_no": "ORD002",
        }
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post("https://mockapi.kiwoom.com/api/dostk/ordr", json=order_response)
            result = client.sell_stock("005930", qty=10)
            assert result["return_code"] == 0

    def test_sell_stock_uses_kt10001(self, client, token_response) -> None:
        """매도 주문은 api-id: kt10001 사용"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post(
                "https://mockapi.kiwoom.com/api/dostk/ordr",
                json={"return_code": 0},
            )
            client.sell_stock("005930", qty=10)
            assert m.last_request.headers["api-id"] == "kt10001"

    def test_buy_stock_failure(self, client, token_response) -> None:
        """매수 실패 시 빈 dict 반환"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post(
                "https://mockapi.kiwoom.com/api/dostk/ordr",
                exc=ConnectionError("timeout"),
            )
            result = client.buy_stock("005930", qty=10)
            assert result == {}

    def test_order_failure_return_code(self, client, token_response) -> None:
        """주문 실패 return_code != 0"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post(
                "https://mockapi.kiwoom.com/api/dostk/ordr",
                json={"return_code": -1, "return_msg": "주문 실패"},
            )
            result = client.buy_stock("005930", qty=10)
            assert result["return_code"] == -1


class TestBalance:
    """잔고 조회 테스트"""

    def test_get_balance_success(self, client, token_response) -> None:
        """잔고 정상 조회 (POST 방식)"""
        balance_response = {
            "acnt_evlt_remn_indv_tot": [
                {
                    "stk_cd": "005930",
                    "stk_nm": "삼성전자",
                    "rmnd_qty": "100",
                    "avg_prc": "68000",
                    "cur_prc": "70000",
                    "evlt_amt": "7000000",
                    "evlt_pfls": "200000",
                    "pfls_rt": "2.94",
                }
            ],
            "prsm_dpst_aset_amt": "17000000",
            "tot_evlt_amt": "12000000",
            "tot_evlt_pl": "200000",
            "return_code": 0,
            "return_msg": "success",
        }
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post("https://mockapi.kiwoom.com/api/dostk/acnt", json=balance_response)
            result = client.get_balance()
            assert result["cash"] == 5000000  # 17000000 - 12000000
            assert len(result["holdings"]) == 1
            assert result["holdings"][0]["ticker"] == "005930"
            assert result["holdings"][0]["qty"] == 100

    def test_get_balance_failure(self, client, token_response) -> None:
        """잔고 조회 실패 시 기본값 반환"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post(
                "https://mockapi.kiwoom.com/api/dostk/acnt",
                exc=ConnectionError("timeout"),
            )
            result = client.get_balance()
            assert result["cash"] == 0
            assert result["holdings"] == []


class TestPing:
    """연결 확인 테스트"""

    def test_ping_success(self, client, token_response) -> None:
        """연결 성공"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            assert client.ping() is True

    def test_ping_failure(self, client) -> None:
        """연결 실패"""
        with requests_mock.Mocker() as m:
            m.post(
                "https://mockapi.kiwoom.com/oauth2/token",
                exc=ConnectionError("refused"),
            )
            assert client.ping() is False


class TestBaseUrlSelection:
    """모의/실전 도메인 선택 테스트"""

    def test_paper_trading_uses_mock_url(self) -> None:
        """모의투자 시 mockapi.kiwoom.com 사용"""
        with patch("trading.kiwoom_api.settings") as mock_settings:
            mock_settings.is_paper_trading = True
            mock_settings.kiwoom_app_key = "key"
            mock_settings.kiwoom_app_secret = "secret"
            mock_settings.kiwoom_account_no = "acnt"
            c = KiwoomRestClient()
            assert c.base_url == "https://mockapi.kiwoom.com"

    def test_real_trading_uses_api_url(self) -> None:
        """실전투자 시 api.kiwoom.com 사용"""
        with patch("trading.kiwoom_api.settings") as mock_settings:
            mock_settings.is_paper_trading = False
            mock_settings.kiwoom_app_key = "key"
            mock_settings.kiwoom_app_secret = "secret"
            mock_settings.kiwoom_account_no = "acnt"
            c = KiwoomRestClient()
            assert c.base_url == "https://api.kiwoom.com"


class TestRetry:
    """재시도 로직 테스트"""

    def test_get_current_price_pykrx_fallback(self, client, token_response) -> None:
        """모의투자: pykrx 폴백으로 종가 조회 성공"""
        import pandas as pd
        from unittest.mock import patch

        mock_df = pd.DataFrame(
            {"종가": [68000, 69000, 70000]},
            index=pd.DatetimeIndex(["2026-03-11", "2026-03-12", "2026-03-13"]),
        )
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            with patch("trading.kiwoom_api.pykrx_stock") as mock_stock:
                mock_stock.get_market_ohlcv.return_value = mock_df
                result = client.get_current_price("005930")
                assert result["current_price"] == 70000  # 최신 종가

    def test_buy_stock_retry_exhausted(self, client, token_response) -> None:
        """매수 주문 재시도 소진 시 빈 dict"""
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post(
                "https://mockapi.kiwoom.com/api/dostk/ordr",
                [
                    {"exc": ConnectionError("fail 1")},
                    {"exc": ConnectionError("fail 2")},
                    {"exc": ConnectionError("fail 3")},
                ],
            )
            result = client.buy_stock("005930", qty=10)
            assert result == {}


class TestCancelOrder:
    """주문 취소 테스트"""

    def test_cancel_order_success(self, client, token_response) -> None:
        """주문 취소 성공"""
        cancel_response = {
            "return_code": 0,
            "return_msg": "정상처리",
        }
        with requests_mock.Mocker() as m:
            m.post("https://mockapi.kiwoom.com/oauth2/token", json=token_response)
            m.post("https://mockapi.kiwoom.com/api/dostk/ordr", json=cancel_response)
            result = client.cancel_order("ORD001", "005930", 10)
            assert result["return_code"] == 0
