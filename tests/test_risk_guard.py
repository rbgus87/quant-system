# tests/test_risk_guard.py
"""리스크 감시 모듈 테스트"""

import pytest
from unittest.mock import patch, MagicMock

from monitor.risk_guard import RiskGuard
from monitor.alert import send_risk_alerts


# ── 공통 fixture ──


@pytest.fixture
def guard() -> RiskGuard:
    """기본 설정의 RiskGuard 인스턴스"""
    cfg = MagicMock()
    cfg.enabled = True
    cfg.stop_loss_pct = -20.0
    cfg.max_drawdown_alert_pct = -15.0
    return RiskGuard(cfg=cfg)


@pytest.fixture
def balance_normal() -> dict:
    """정상 잔고 (손절 기준 미달)"""
    return {
        "holdings": [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "qty": 10,
                "avg_price": 70000,
                "current_price": 65000,
                "profit_rate": -7.14,
            },
        ],
        "cash": 300000,
        "total_eval_amount": 950000,
        "total_profit": -50000,
    }


@pytest.fixture
def balance_stop_loss() -> dict:
    """손절 기준 도달 잔고"""
    return {
        "holdings": [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "qty": 10,
                "avg_price": 70000,
                "current_price": 56000,
                "profit_rate": -20.0,
            },
            {
                "ticker": "000660",
                "name": "SK하이닉스",
                "qty": 5,
                "avg_price": 150000,
                "current_price": 115000,
                "profit_rate": -23.3,
            },
            {
                "ticker": "035420",
                "name": "NAVER",
                "qty": 3,
                "avg_price": 200000,
                "current_price": 195000,
                "profit_rate": -2.5,
            },
        ],
        "cash": 200000,
        "total_eval_amount": 1545000,
        "total_profit": -155000,
    }


# ── 종목별 손절 경고 테스트 ──


class TestStopLoss:
    """종목별 손절 경고"""

    def test_stop_loss_triggered(self, guard, balance_stop_loss) -> None:
        """profit_rate <= -20% 종목에 경고 발생"""
        alerts = guard._check_stop_loss(balance_stop_loss)
        tickers = {a["ticker"] for a in alerts}
        assert "005930" in tickers  # -20.0%
        assert "000660" in tickers  # -23.3%
        assert "035420" not in tickers  # -2.5% — 기준 미달

    def test_stop_loss_not_triggered(self, guard, balance_normal) -> None:
        """profit_rate > -20% 이면 경고 없음"""
        alerts = guard._check_stop_loss(balance_normal)
        assert len(alerts) == 0

    def test_stop_loss_exact_threshold(self, guard) -> None:
        """profit_rate == -20.0% (경계값) 경고 발생"""
        balance = {
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "qty": 10,
                 "avg_price": 70000, "current_price": 56000,
                 "profit_rate": -20.0},
            ],
            "cash": 0,
            "total_eval_amount": 560000,
            "total_profit": -140000,
        }
        alerts = guard._check_stop_loss(balance)
        assert len(alerts) == 1

    def test_stop_loss_just_above(self, guard) -> None:
        """profit_rate == -19.9% (기준 미달) 경고 없음"""
        balance = {
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "qty": 10,
                 "avg_price": 70000, "current_price": 56070,
                 "profit_rate": -19.9},
            ],
            "cash": 0,
            "total_eval_amount": 560700,
            "total_profit": -139300,
        }
        alerts = guard._check_stop_loss(balance)
        assert len(alerts) == 0

    def test_duplicate_prevention(self, guard, balance_stop_loss) -> None:
        """같은 날 동일 종목 재호출 시 중복 경고 방지"""
        alerts1 = guard._check_stop_loss(balance_stop_loss)
        assert len(alerts1) == 2  # 삼성전자 + SK하이닉스

        alerts2 = guard._check_stop_loss(balance_stop_loss)
        assert len(alerts2) == 0  # 중복 방지


# ── 포트폴리오 드로다운 경고 테스트 ──


class TestPortfolioDrawdown:
    """포트폴리오 드로다운 경고"""

    def test_drawdown_triggered(self, guard) -> None:
        """손실률 <= -15% 이면 경고"""
        balance = {
            "holdings": [],
            "cash": 100000,
            "total_eval_amount": 8300000,
            "total_profit": -1700000,
        }
        alerts = guard._check_portfolio_drawdown(balance)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "drawdown"
        # invested = 8300000 - (-1700000) = 10000000
        # loss_pct = -1700000 / 10000000 * 100 = -17.0
        assert alerts[0]["loss_pct"] == pytest.approx(-17.0)

    def test_drawdown_not_triggered(self, guard, balance_normal) -> None:
        """손실률 > -15% 이면 경고 없음"""
        # invested = 950000 - (-50000) = 1000000
        # loss_pct = -50000 / 1000000 * 100 = -5.0
        alerts = guard._check_portfolio_drawdown(balance_normal)
        assert len(alerts) == 0

    def test_drawdown_once_per_day(self, guard) -> None:
        """하루 1회만 발송"""
        balance = {
            "holdings": [],
            "cash": 0,
            "total_eval_amount": 8000000,
            "total_profit": -2000000,
        }
        alerts1 = guard._check_portfolio_drawdown(balance)
        assert len(alerts1) == 1

        alerts2 = guard._check_portfolio_drawdown(balance)
        assert len(alerts2) == 0


# ── 관리종목 경고 테스트 ──


class TestDelisting:
    """관리종목 감시"""

    def test_delisting_detected(self, guard) -> None:
        """보유 종목이 관리종목 목록에 있으면 경고"""
        today = guard._reset_if_new_day()
        guard._delisting_cache[today] = {"005930", "999999"}

        balance = {
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "qty": 10,
                 "current_price": 70000},
                {"ticker": "000660", "name": "SK하이닉스", "qty": 5,
                 "current_price": 150000},
            ],
            "cash": 0,
            "total_eval_amount": 1450000,
            "total_profit": 0,
        }
        alerts = guard._check_delisting(balance)
        assert len(alerts) == 1
        assert alerts[0]["ticker"] == "005930"
        assert alerts[0]["type"] == "delisting"

    def test_no_cache_no_alert(self, guard) -> None:
        """캐시 없으면 경고 없음 (false positive 방지)"""
        balance = {
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "qty": 10,
                 "current_price": 70000},
            ],
            "cash": 0,
            "total_eval_amount": 700000,
            "total_profit": 0,
        }
        alerts = guard._check_delisting(balance)
        assert len(alerts) == 0


# ── check_all 통합 테스트 ──


class TestCheckAll:
    """check_all 통합"""

    def test_check_all_combines_alerts(self, guard, balance_stop_loss) -> None:
        """여러 종류 경고가 합쳐서 반환"""
        today = guard._reset_if_new_day()
        guard._delisting_cache[today] = {"005930"}

        alerts = guard.check_all(balance_stop_loss)
        types = {a["type"] for a in alerts}
        assert "stop_loss" in types
        # 005930은 stop_loss + delisting 둘 다 발생
        assert "delisting" in types

    def test_disabled_returns_empty(self) -> None:
        """enabled=False 이면 빈 리스트"""
        cfg = MagicMock()
        cfg.enabled = False
        guard = RiskGuard(cfg=cfg)
        alerts = guard.check_all({"holdings": [], "cash": 0,
                                   "total_eval_amount": 0, "total_profit": 0})
        assert alerts == []


# ── alert 발송 테스트 ──


class TestSendRiskAlerts:
    """send_risk_alerts 메시지 포맷 + 발송"""

    @patch("monitor.alert.TelegramNotifier")
    def test_stop_loss_message(self, mock_notifier_cls) -> None:
        """손절 경고 메시지 포맷"""
        mock_notifier = MagicMock()
        mock_notifier.send.return_value = True
        mock_notifier_cls.return_value = mock_notifier

        alerts = [{
            "type": "stop_loss",
            "ticker": "000660",
            "name": "SK하이닉스",
            "current_price": 85000,
            "avg_price": 110000,
            "profit_rate": -22.7,
            "threshold": -20.0,
        }]
        sent = send_risk_alerts(alerts)
        assert sent == 1

        msg = mock_notifier.send.call_args[0][0]
        assert "손절 경고" in msg
        assert "SK하이닉스" in msg
        assert "000660" in msg
        assert "-22.7%" in msg
        assert "자동 매도 아님" in msg

    @patch("monitor.alert.TelegramNotifier")
    def test_drawdown_message(self, mock_notifier_cls) -> None:
        """드로다운 경고 메시지 포맷"""
        mock_notifier = MagicMock()
        mock_notifier.send.return_value = True
        mock_notifier_cls.return_value = mock_notifier

        alerts = [{
            "type": "drawdown",
            "ticker": "",
            "name": "",
            "total_eval": 8300000,
            "invested": 10000000,
            "loss_pct": -17.0,
            "threshold": -15.0,
        }]
        sent = send_risk_alerts(alerts)
        assert sent == 1

        msg = mock_notifier.send.call_args[0][0]
        assert "드로다운 경고" in msg
        assert "8,300,000" in msg
        assert "전체 점검 권장" in msg

    @patch("monitor.alert.TelegramNotifier")
    def test_delisting_message(self, mock_notifier_cls) -> None:
        """관리종목 경고 메시지 포맷"""
        mock_notifier = MagicMock()
        mock_notifier.send.return_value = True
        mock_notifier_cls.return_value = mock_notifier

        alerts = [{
            "type": "delisting",
            "ticker": "123456",
            "name": "ABC기업",
            "qty": 50,
            "current_price": 5000,
        }]
        sent = send_risk_alerts(alerts)
        assert sent == 1

        msg = mock_notifier.send.call_args[0][0]
        assert "관리종목" in msg
        assert "ABC기업" in msg
        assert "매도 검토 필요" in msg

    @patch("monitor.alert.TelegramNotifier")
    def test_empty_alerts(self, mock_notifier_cls) -> None:
        """빈 경고 리스트 → 발송 0건"""
        sent = send_risk_alerts([])
        assert sent == 0
        mock_notifier_cls.assert_not_called()
