# tests/test_telegram.py
import pytest
import requests_mock
from unittest.mock import patch

from notify.telegram import TelegramNotifier


@pytest.fixture
def notifier():
    """TelegramNotifier with mocked settings"""
    with patch("notify.telegram.settings") as mock_settings:
        mock_settings.telegram_bot_token = "123456:ABC-DEF"
        mock_settings.telegram_chat_id = "987654321"
        n = TelegramNotifier()
    return n


@pytest.fixture
def api_url():
    return "https://api.telegram.org/bot123456:ABC-DEF/sendMessage"


class TestSend:
    """기본 메시지 발송 테스트"""

    def test_send_success(self, notifier, api_url) -> None:
        """정상 발송"""
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            result = notifier.send("테스트 메시지")
            assert result is True
            assert m.called

    def test_send_payload_correct(self, notifier, api_url) -> None:
        """요청 본문에 chat_id, text, parse_mode 포함"""
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            notifier.send("hello", parse_mode="HTML")
            body = m.last_request.json()
            assert body["chat_id"] == "987654321"
            assert body["text"] == "hello"
            assert body["parse_mode"] == "HTML"

    def test_send_failure_status(self, notifier, api_url) -> None:
        """HTTP 에러 시 False 반환 (예외 전파 없음)"""
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": False}, status_code=400)
            result = notifier.send("테스트")
            assert result is False

    def test_send_network_error(self, notifier, api_url) -> None:
        """네트워크 오류 시 False 반환 (예외 전파 없음)"""
        with requests_mock.Mocker() as m:
            m.post(api_url, exc=ConnectionError("timeout"))
            result = notifier.send("테스트")
            assert result is False

    def test_send_no_token(self) -> None:
        """토큰 없으면 False 반환"""
        with patch("notify.telegram.settings") as mock_settings:
            mock_settings.telegram_bot_token = ""
            mock_settings.telegram_chat_id = "123"
            n = TelegramNotifier()
            result = n.send("test")
            assert result is False

    def test_send_no_chat_id(self) -> None:
        """chat_id 없으면 False 반환"""
        with patch("notify.telegram.settings") as mock_settings:
            mock_settings.telegram_bot_token = "token"
            mock_settings.telegram_chat_id = ""
            n = TelegramNotifier()
            result = n.send("test")
            assert result is False


class TestMessageSplit:
    """4096자 초과 시 분할 발송"""

    def test_long_message_split(self, notifier, api_url) -> None:
        """4096자 초과 메시지 분할 발송"""
        long_msg = "A" * 5000
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            result = notifier.send(long_msg)
            assert result is True
            # 2번 이상 호출 (분할 발송)
            assert m.call_count >= 2

    def test_exact_4096_no_split(self, notifier, api_url) -> None:
        """정확히 4096자는 분할하지 않음"""
        msg = "A" * 4096
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            notifier.send(msg)
            assert m.call_count == 1


class TestRebalancingReport:
    """리밸런싱 결과 알림 테스트"""

    def test_send_rebalancing_report(self, notifier, api_url) -> None:
        """리밸런싱 리포트: 매수/매도 상세 표시"""
        from unittest.mock import patch

        mock_trades = [
            {"ticker": "005930", "side": "SELL", "quantity": 10,
             "price": 70000, "amount": 700000, "name": "삼성전자"},
            {"ticker": "035720", "side": "BUY", "quantity": 5,
             "price": 50000, "amount": 250000, "name": "카카오"},
        ]
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            with patch.object(notifier, "_load_today_trades", return_value=mock_trades):
                result = notifier.send_rebalancing_report(
                    sell_done=["005930"],
                    buy_done=["035720"],
                    total_value=50000000,
                    balance={"cash": 5000000, "total_eval_amount": 45000000},
                )
            assert result is True
            text = m.last_request.json()["text"]
            assert "리밸런싱" in text
            assert "삼성전자" in text
            assert "10주" in text
            assert "예수금" in text

    def test_report_empty_trades(self, notifier, api_url) -> None:
        """매매 없을 때 '없음' 표시"""
        from unittest.mock import patch

        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            with patch.object(notifier, "_load_today_trades", return_value=[]):
                notifier.send_rebalancing_report(
                    sell_done=[], buy_done=[], total_value=10000000,
                )
            text = m.last_request.json()["text"]
            assert "없음" in text


class TestDailyReport:
    """일별 리포트 테스트"""

    def test_positive_return(self, notifier, api_url) -> None:
        """양수 수익률"""
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            result = notifier.send_daily_report(
                daily_return=0.015, total_value=51000000
            )
            assert result is True

    def test_negative_return(self, notifier, api_url) -> None:
        """음수 수익률"""
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            notifier.send_daily_report(daily_return=-0.02, total_value=49000000)
            text = m.last_request.json()["text"]
            assert "-" in text


class TestErrorNotification:
    """오류 알림 테스트"""

    def test_send_error(self, notifier, api_url) -> None:
        """에러 메시지 발송"""
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            result = notifier.send_error("ConnectionError: timeout")
            assert result is True
            text = m.last_request.json()["text"]
            assert "오류" in text
            assert "ConnectionError" in text

    def test_error_message_truncated(self, notifier, api_url) -> None:
        """긴 에러 메시지 잘림"""
        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            long_error = "X" * 1000
            notifier.send_error(long_error)
            text = m.last_request.json()["text"]
            # 원본 500자 + 마크다운 오버헤드 < 4096
            assert len(text) < 4096
