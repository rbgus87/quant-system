# tests/test_scheduler.py
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import date

from config.calendar import is_krx_business_day, is_last_krx_business_day_of_month


class TestIsLastBusinessDayOfMonth:
    """KRX 월말 거래일 판정 테스트"""

    def test_last_bday_jan(self) -> None:
        """2024-01-31 (수) = 1월 마지막 KRX 거래일"""
        assert is_last_krx_business_day_of_month(date(2024, 1, 31)) is True

    def test_mid_month_not_last(self) -> None:
        """월 중간은 마지막 거래일이 아님"""
        assert is_last_krx_business_day_of_month(date(2024, 1, 15)) is False

    def test_saturday_not_last(self) -> None:
        """주말은 거래일이 아님"""
        assert is_last_krx_business_day_of_month(date(2024, 2, 3)) is False

    def test_feb_last_bday(self) -> None:
        """2024년 2월 마지막 KRX 거래일 = 2/29 (목, 윤년)"""
        assert is_last_krx_business_day_of_month(date(2024, 2, 29)) is True

    def test_dec_last_bday(self) -> None:
        """12월 마지막 KRX 거래일 = 2024-12-31 (화)"""
        # 12/31이 KRX 마지막 거래일인지는 공휴일 여부에 따라 다름
        # 2024-12-31은 화요일이고 한국 공휴일 아님 → 거래일
        assert is_last_krx_business_day_of_month(date(2024, 12, 31)) is False
        # 2024-12-30은 월요일 → 마지막 거래일
        assert is_last_krx_business_day_of_month(date(2024, 12, 30)) is True


class TestIsBusinessDay:
    """KRX 거래일 판정 테스트 (한국 공휴일 인식)"""

    def test_monday_is_business_day(self) -> None:
        """평일 월요일 = 거래일"""
        assert is_krx_business_day(date(2024, 1, 29)) is True

    def test_saturday_not_business_day(self) -> None:
        """토요일 = 휴장"""
        assert is_krx_business_day(date(2024, 2, 3)) is False

    def test_sunday_not_business_day(self) -> None:
        """일요일 = 휴장"""
        assert is_krx_business_day(date(2024, 2, 4)) is False

    def test_korean_holiday_not_business_day(self) -> None:
        """한국 공휴일(광복절 2024-08-15 목) = 휴장"""
        assert is_krx_business_day(date(2024, 8, 15)) is False

    def test_chuseok_not_business_day(self) -> None:
        """추석 연휴 (2024-09-16~18) = 휴장"""
        assert is_krx_business_day(date(2024, 9, 16)) is False
        assert is_krx_business_day(date(2024, 9, 17)) is False


class TestRunScheduledRebalancing:
    """월별 리밸런싱 작업 함수 테스트"""

    @patch("scheduler.main.is_last_business_day_of_month", return_value=False)
    @patch("scheduler.main.is_business_day", return_value=True)
    def test_skip_if_not_last_bday(self, mock_bday, mock_last) -> None:
        """월말이 아니면 스킵"""
        from scheduler.main import run_scheduled_rebalancing

        # 아무 것도 하지 않아야 함 (TelegramNotifier 미생성)
        with patch("scheduler.main.TelegramNotifier") as mock_notifier:
            run_scheduled_rebalancing()
            mock_notifier.assert_not_called()

    @patch("scheduler.main.is_last_business_day_of_month", return_value=True)
    @patch("scheduler.main.is_business_day", return_value=False)
    def test_skip_if_not_business_day(self, mock_bday, mock_last) -> None:
        """영업일이 아니면 스킵"""
        from scheduler.main import run_scheduled_rebalancing

        with patch("scheduler.main.TelegramNotifier") as mock_notifier:
            run_scheduled_rebalancing()
            mock_notifier.assert_not_called()

    @patch("scheduler.main.is_last_business_day_of_month", return_value=True)
    @patch("scheduler.main.is_business_day", return_value=True)
    def test_rebalancing_error_sends_telegram(self, mock_bday, mock_last) -> None:
        """리밸런싱 실패 시 텔레그램 에러 알림"""
        from scheduler.main import run_scheduled_rebalancing

        mock_notifier_instance = MagicMock()

        with patch(
            "scheduler.main.TelegramNotifier", return_value=mock_notifier_instance
        ):
            with patch(
                "scheduler.main.KiwoomRestClient",
                side_effect=RuntimeError("API 연결 실패"),
            ):
                with patch("time.sleep"):
                    run_scheduled_rebalancing()
                    mock_notifier_instance.send_error.assert_called_once()
                    error_arg = mock_notifier_instance.send_error.call_args[0][0]
                    assert "API 연결 실패" in error_arg


class TestRunDailyDefenseCheck:
    """일별 방어 체크 (15:15) 테스트"""

    @patch("scheduler.main.is_business_day", return_value=False)
    def test_skip_if_not_business_day(self, mock_bday) -> None:
        """영업일이 아니면 스킵"""
        from scheduler.main import run_daily_defense_check

        with patch("scheduler.main.TelegramNotifier") as mock_notifier:
            run_daily_defense_check()
            mock_notifier.assert_not_called()

    @patch("scheduler.main.is_business_day", return_value=True)
    def test_skip_if_no_holdings(self, mock_bday) -> None:
        """보유 종목이 없으면 스킵"""
        from scheduler.main import run_daily_defense_check

        mock_api = MagicMock()
        mock_api.get_balance.return_value = {
            "holdings": [],
            "cash": 10000000,
            "total_eval_amount": 10000000,
        }
        mock_notifier = MagicMock()

        with patch("scheduler.main.TelegramNotifier", return_value=mock_notifier):
            with patch("scheduler.main.KiwoomRestClient", return_value=mock_api):
                with patch("scheduler.main.OrderExecutor") as mock_exec_cls:
                    run_daily_defense_check()
                    # OrderExecutor가 생성되지 않아야 함
                    mock_exec_cls.assert_not_called()

    @patch("scheduler.main.is_business_day", return_value=True)
    def test_no_action_when_normal(self, mock_bday) -> None:
        """이상 없으면 매도 없이 정상 종료"""
        from scheduler.main import run_daily_defense_check

        mock_api = MagicMock()
        mock_api.get_balance.return_value = {
            "holdings": [
                {"ticker": "005930", "qty": 100, "avg_price": 70000, "current_price": 75000},
            ],
            "cash": 5000000,
            "total_eval_amount": 12500000,
        }
        mock_notifier = MagicMock()
        mock_executor = MagicMock()
        mock_executor._check_drawdown.return_value = False
        mock_executor._check_trailing_stops.return_value = []

        with patch("scheduler.main.TelegramNotifier", return_value=mock_notifier):
            with patch("scheduler.main.KiwoomRestClient", return_value=mock_api):
                with patch("scheduler.main.OrderExecutor", return_value=mock_executor):
                    run_daily_defense_check()
                    # 매도 없음
                    mock_api.sell_stock.assert_not_called()
                    mock_notifier.send.assert_not_called()

    @patch("scheduler.main.is_business_day", return_value=True)
    def test_circuit_breaker_triggers_liquidation(self, mock_bday) -> None:
        """MDD 서킷브레이커 발동 → 전량 매도"""
        from scheduler.main import run_daily_defense_check

        mock_api = MagicMock()
        mock_api.get_balance.return_value = {
            "holdings": [
                {"ticker": "005930", "qty": 100, "current_price": 50000},
            ],
            "cash": 1000000,
            "total_eval_amount": 6000000,
        }
        mock_notifier = MagicMock()
        mock_executor = MagicMock()
        mock_executor._check_drawdown.return_value = True
        mock_executor.execute_emergency_liquidation.return_value = ["005930"]

        with patch("scheduler.main.TelegramNotifier", return_value=mock_notifier):
            with patch("scheduler.main.KiwoomRestClient", return_value=mock_api):
                with patch("scheduler.main.OrderExecutor", return_value=mock_executor):
                    run_daily_defense_check()
                    mock_executor.execute_emergency_liquidation.assert_called_once()
                    # 텔레그램 알림
                    mock_notifier.send.assert_called_once()
                    msg = mock_notifier.send.call_args[0][0]
                    assert "서킷브레이커" in msg

    @patch("scheduler.main.is_business_day", return_value=True)
    def test_trailing_stop_sells_ticker(self, mock_bday) -> None:
        """트레일링 스톱 발동 → 해당 종목 매도"""
        from scheduler.main import run_daily_defense_check

        mock_api = MagicMock()
        mock_api.is_paper = True
        mock_api.get_balance.return_value = {
            "holdings": [
                {"ticker": "005930", "qty": 100, "avg_price": 100000, "current_price": 75000},
                {"ticker": "000660", "qty": 50, "avg_price": 150000, "current_price": 160000},
            ],
            "cash": 5000000,
            "total_eval_amount": 20500000,
        }
        mock_api.sell_stock.return_value = {"return_code": 0, "ord_no": "S001"}
        mock_notifier = MagicMock()
        mock_executor = MagicMock()
        mock_executor._check_drawdown.return_value = False
        mock_executor._check_trailing_stops.return_value = ["005930"]
        mock_executor.cfg = MagicMock()
        mock_executor.cfg.commission_rate = 0.00015
        mock_executor.cfg.tax_rate = 0.0018
        mock_executor.storage = MagicMock()

        with patch("scheduler.main.TelegramNotifier", return_value=mock_notifier):
            with patch("scheduler.main.KiwoomRestClient", return_value=mock_api):
                with patch("scheduler.main.OrderExecutor", return_value=mock_executor):
                    run_daily_defense_check()
                    # 005930만 매도
                    mock_api.sell_stock.assert_called_once()
                    sell_call = mock_api.sell_stock.call_args
                    assert sell_call.kwargs.get("ticker") == "005930"
                    # 텔레그램 알림
                    mock_notifier.send.assert_called_once()
                    msg = mock_notifier.send.call_args[0][0]
                    assert "트레일링 스톱" in msg

    @patch("scheduler.main.is_business_day", return_value=True)
    def test_error_sends_telegram(self, mock_bday) -> None:
        """오류 발생 시 텔레그램 에러 알림"""
        from scheduler.main import run_daily_defense_check

        mock_notifier = MagicMock()
        with patch("scheduler.main.TelegramNotifier", return_value=mock_notifier):
            with patch(
                "scheduler.main.KiwoomRestClient",
                side_effect=RuntimeError("API 연결 실패"),
            ):
                run_daily_defense_check()
                mock_notifier.send_error.assert_called_once()


class TestRunDailyReport:
    """일별 리포트 작업 함수 테스트"""

    @patch("scheduler.main.is_business_day", return_value=False)
    def test_skip_if_not_business_day(self, mock_bday) -> None:
        """영업일이 아니면 스킵"""
        from scheduler.main import run_daily_report

        with patch("scheduler.main.TelegramNotifier") as mock_notifier:
            run_daily_report()
            mock_notifier.assert_not_called()

    @patch("scheduler.main.is_business_day", return_value=True)
    def test_daily_report_success(self, mock_bday) -> None:
        """일별 리포트 정상 발송 (상세 리포트)"""
        from scheduler.main import run_daily_report

        mock_notifier_instance = MagicMock()
        mock_notifier_instance.send_detailed_daily_report.return_value = True
        mock_api = MagicMock()
        mock_api.get_balance.return_value = {
            "holdings": [{"ticker": "005930"}],
            "total_eval_amount": 50000000,
            "total_profit": 1000000,
            "cash": 5000000,
        }
        with patch(
            "scheduler.main.TelegramNotifier", return_value=mock_notifier_instance
        ):
            with patch("scheduler.main.KiwoomRestClient", return_value=mock_api):
                run_daily_report()
                mock_notifier_instance.send_detailed_daily_report.assert_called_once()
                balance = mock_notifier_instance.send_detailed_daily_report.call_args[0][0]
                assert balance["total_eval_amount"] == 50000000

    @patch("scheduler.main.is_business_day", return_value=True)
    def test_daily_report_error_sends_telegram(self, mock_bday) -> None:
        """일별 리포트 오류 시 텔레그램 에러 알림"""
        from scheduler.main import run_daily_report

        mock_notifier_instance = MagicMock()
        with patch(
            "scheduler.main.TelegramNotifier", return_value=mock_notifier_instance
        ):
            with patch(
                "scheduler.main.KiwoomRestClient",
                side_effect=RuntimeError("연결 실패"),
            ):
                run_daily_report()
                mock_notifier_instance.send_error.assert_called_once()
