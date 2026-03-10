# tests/test_scheduler.py
from unittest.mock import patch, MagicMock
from datetime import datetime

import pandas as pd


class TestIsLastBusinessDayOfMonth:
    """월말 영업일 판정 테스트"""

    def test_last_bday_jan(self) -> None:
        """2024-01-31 (수) = 1월 마지막 영업일"""
        today = pd.Timestamp("2024-01-31")
        last_bday = today + pd.offsets.BMonthEnd(0)
        assert today == last_bday

    def test_mid_month_not_last(self) -> None:
        """월 중간은 마지막 영업일이 아님"""
        today = pd.Timestamp("2024-01-15")
        last_bday = today + pd.offsets.BMonthEnd(0)
        assert today != last_bday

    def test_saturday_not_last(self) -> None:
        """주말은 영업일이 아님"""
        today = pd.Timestamp("2024-02-03")  # 토요일
        last_bday = today + pd.offsets.BMonthEnd(0)
        # 토요일 + BMonthEnd(0) → 직전 금요일(1/31)이므로 다름
        assert today != last_bday

    def test_feb_last_bday(self) -> None:
        """2024년 2월 마지막 영업일 = 2/29 (목, 윤년)"""
        today = pd.Timestamp("2024-02-29")
        last_bday = today + pd.offsets.BMonthEnd(0)
        assert today == last_bday

    def test_dec_last_bday(self) -> None:
        """12월 마지막 영업일 = 2024-12-31 (화)"""
        today = pd.Timestamp("2024-12-31")
        last_bday = today + pd.offsets.BMonthEnd(0)
        assert today == last_bday


class TestIsBusinessDay:
    """영업일 판정 테스트"""

    def test_monday_is_business_day(self) -> None:
        """월요일 = 영업일"""
        from scheduler.main import is_business_day

        with patch("scheduler.main.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 29)  # 월요일
            assert is_business_day() is True

    def test_saturday_not_business_day(self) -> None:
        """토요일 ≠ 영업일"""
        from scheduler.main import is_business_day

        with patch("scheduler.main.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 2, 3)  # 토요일
            assert is_business_day() is False

    def test_sunday_not_business_day(self) -> None:
        """일요일 ≠ 영업일"""
        from scheduler.main import is_business_day

        with patch("scheduler.main.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 2, 4)  # 일요일
            assert is_business_day() is False


class TestRunMonthlyRebalancing:
    """월별 리밸런싱 작업 함수 테스트"""

    @patch("scheduler.main.is_last_business_day_of_month", return_value=False)
    @patch("scheduler.main.is_business_day", return_value=True)
    def test_skip_if_not_last_bday(self, mock_bday, mock_last) -> None:
        """월말이 아니면 스킵"""
        from scheduler.main import run_monthly_rebalancing

        # 아무 것도 하지 않아야 함 (TelegramNotifier 미생성)
        with patch("scheduler.main.TelegramNotifier") as mock_notifier:
            run_monthly_rebalancing()
            mock_notifier.assert_not_called()

    @patch("scheduler.main.is_last_business_day_of_month", return_value=True)
    @patch("scheduler.main.is_business_day", return_value=False)
    def test_skip_if_not_business_day(self, mock_bday, mock_last) -> None:
        """영업일이 아니면 스킵"""
        from scheduler.main import run_monthly_rebalancing

        with patch("scheduler.main.TelegramNotifier") as mock_notifier:
            run_monthly_rebalancing()
            mock_notifier.assert_not_called()

    @patch("scheduler.main.is_last_business_day_of_month", return_value=True)
    @patch("scheduler.main.is_business_day", return_value=True)
    def test_rebalancing_error_sends_telegram(self, mock_bday, mock_last) -> None:
        """리밸런싱 실패 시 텔레그램 에러 알림"""
        from scheduler.main import run_monthly_rebalancing

        mock_notifier_instance = MagicMock()
        with patch(
            "scheduler.main.TelegramNotifier", return_value=mock_notifier_instance
        ):
            with patch(
                "scheduler.main.KiwoomRestClient",
                side_effect=RuntimeError("API 연결 실패"),
            ):
                run_monthly_rebalancing()
                mock_notifier_instance.send_error.assert_called_once()
                error_arg = mock_notifier_instance.send_error.call_args[0][0]
                assert "API 연결 실패" in error_arg


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
        """일별 리포트 정상 발송"""
        from scheduler.main import run_daily_report

        mock_notifier_instance = MagicMock()
        mock_api = MagicMock()
        mock_api.get_balance.return_value = {
            "holdings": [{"ticker": "005930"}],
            "total_eval_amount": 50000000,
            "cash": 5000000,
        }
        with patch(
            "scheduler.main.TelegramNotifier", return_value=mock_notifier_instance
        ):
            with patch("scheduler.main.KiwoomRestClient", return_value=mock_api):
                run_daily_report()
                mock_notifier_instance.send.assert_called_once()
                msg = mock_notifier_instance.send.call_args[0][0]
                assert "50,000,000" in msg

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
