# tests/test_drift.py
"""가중치 드리프트 모니터링 테스트"""

import json
import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from data.storage import DataStorage, Portfolio
from monitor.drift import calculate_drift


# ── 공통 fixture ──


@pytest.fixture
def storage(tmp_path) -> DataStorage:
    """임시 DB로 DataStorage 생성"""
    db_path = str(tmp_path / "test_quant.db")
    return DataStorage(db_path=db_path)


@pytest.fixture
def snapshot_20stocks() -> dict:
    """20종목 균등비중 스냅샷 (한 종목 +50% 상승 시뮬레이션)

    목표 비중: 각 5.0% (20종목 균등)
    삼성전자(005930): 가격 +50% → 비중 약 7.14%
    나머지 19종목: 비중 약 4.88% 씩
    """
    # 19종목 × 100만원 + 삼성전자 150만원 = 2,050만원
    total_eval = 20_500_000
    holdings = []

    # 삼성전자: +50% 상승
    holdings.append({
        "ticker": "005930",
        "name": "삼성전자",
        "qty": 10,
        "avg_price": 100000,
        "current_price": 150000,
        "weight_pct": round(1_500_000 / total_eval * 100, 2),  # ~7.32%
    })

    # 나머지 19종목
    for i in range(19):
        ticker = f"00{i:04d}"
        holdings.append({
            "ticker": ticker,
            "name": f"종목{i}",
            "qty": 10,
            "avg_price": 100000,
            "current_price": 100000,
            "weight_pct": round(1_000_000 / total_eval * 100, 2),  # ~4.88%
        })

    return {
        "date": "2026-04-15",
        "portfolio": {
            "total_value": total_eval,
            "total_invested": 20_000_000,
            "cash": 0,
            "daily_return_pct": 0.0,
            "total_return_pct": 0.025,
            "mdd_pct": 0.0,
        },
        "benchmark": {
            "kospi_daily_return_pct": 0.0,
            "excess_return_pct": 0.0,
        },
        "holdings": holdings,
    }


def _insert_portfolio(storage: DataStorage, rebalance_dt: date, tickers: list[str]) -> None:
    """Portfolio 테이블에 균등 비중 데이터 삽입"""
    import pandas as pd

    weight = 1.0 / len(tickers)
    df = pd.DataFrame({
        "ticker": tickers,
        "name": [f"종목{t}" for t in tickers],
        "weight": [weight] * len(tickers),
        "composite_score": [50.0] * len(tickers),
    })
    storage.save_portfolio(rebalance_dt, df)


# ── calculate_drift 테스트 ──


class TestCalculateDrift:
    """드리프트 계산 로직"""

    @patch("monitor.drift.DataStorage")
    def test_drift_with_price_change(self, mock_storage_cls, storage, snapshot_20stocks) -> None:
        """한 종목 +50% 상승 시 드리프트 계산"""
        # Portfolio에 20종목 균등비중 삽입
        tickers = ["005930"] + [f"00{i:04d}" for i in range(19)]
        _insert_portfolio(storage, date(2026, 3, 31), tickers)

        # DataStorage mock → 실제 storage 사용
        mock_storage_cls.return_value = storage

        drift = calculate_drift(snapshot_20stocks)
        assert drift is not None
        assert drift["rebalance_date"] == "2026-03-31"
        assert drift["snapshot_date"] == "2026-04-15"
        assert drift["days_since_rebalance"] == 15

        # 삼성전자: 목표 5.0%, 현재 ~7.32% → drift ≈ +2.32%p
        max_d = drift["max_drift"]
        assert max_d["ticker"] == "005930"
        assert max_d["drift_pct"] > 2.0  # +50% 상승이므로 양의 드리프트

        # 나머지 종목: 목표 5.0%, 현재 ~4.88% → drift ≈ -0.12%p
        avg = drift["avg_abs_drift_pct"]
        assert avg > 0

        # holdings_drift는 |drift| 내림차순
        drifts = drift["holdings_drift"]
        assert len(drifts) == 20
        assert abs(drifts[0]["drift_pct"]) >= abs(drifts[-1]["drift_pct"])

    @patch("monitor.drift.DataStorage")
    def test_missing_holding(self, mock_storage_cls, storage) -> None:
        """목표에 있지만 현재 없는 종목 → drift = -target"""
        _insert_portfolio(storage, date(2026, 3, 31), ["005930", "000660"])
        mock_storage_cls.return_value = storage

        snapshot = {
            "date": "2026-04-15",
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "weight_pct": 100.0},
            ],
        }

        drift = calculate_drift(snapshot)
        assert drift is not None

        # 000660: 목표 50%, 현재 0% → drift = -50.0
        missing = next(d for d in drift["holdings_drift"] if d["ticker"] == "000660")
        assert missing["current_weight_pct"] == 0.0
        assert missing["drift_pct"] == -50.0

    @patch("monitor.drift.DataStorage")
    def test_no_portfolio_returns_none(self, mock_storage_cls, tmp_path) -> None:
        """Portfolio 데이터 없으면 None"""
        empty_storage = DataStorage(db_path=str(tmp_path / "empty.db"))
        mock_storage_cls.return_value = empty_storage

        snapshot = {"date": "2026-04-15", "holdings": []}
        result = calculate_drift(snapshot)
        assert result is None

    @patch("monitor.drift.DataStorage")
    def test_current_not_in_target_ignored(self, mock_storage_cls, storage) -> None:
        """현재 있지만 목표에 없는 종목 → 무시"""
        _insert_portfolio(storage, date(2026, 3, 31), ["005930"])
        mock_storage_cls.return_value = storage

        snapshot = {
            "date": "2026-04-15",
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "weight_pct": 80.0},
                {"ticker": "999999", "name": "신규종목", "weight_pct": 20.0},
            ],
        }

        drift = calculate_drift(snapshot)
        assert drift is not None
        tickers = {d["ticker"] for d in drift["holdings_drift"]}
        assert "005930" in tickers
        assert "999999" not in tickers  # 목표에 없으므로 무시


# ── 리포트 포맷 테스트 ──


class TestDriftReportFormat:
    """send_detailed_daily_report의 드리프트 섹션"""

    @pytest.fixture
    def notifier(self):
        with patch("notify.telegram.settings") as mock_settings:
            mock_settings.telegram_bot_token = "123456:ABC-DEF"
            mock_settings.telegram_chat_id = "987654321"
            mock_settings.is_paper_trading = True
            from notify.telegram import TelegramNotifier
            n = TelegramNotifier()
        return n

    @pytest.fixture
    def api_url(self):
        return "https://api.telegram.org/bot123456:ABC-DEF/sendMessage"

    @pytest.fixture
    def balance(self):
        return {
            "holdings": [
                {"ticker": "005930", "name": "삼성전자", "qty": 10,
                 "avg_price": 70000, "current_price": 75000,
                 "eval_amount": 750000, "eval_profit": 50000, "profit_rate": 7.14},
            ],
            "cash": 250000,
            "total_eval_amount": 1000000,
            "total_profit": 50000,
        }

    def test_drift_section_included(self, notifier, api_url, balance, tmp_path) -> None:
        """drift가 snapshot에 있으면 드리프트 섹션 포함"""
        import requests_mock

        (tmp_path / "data").mkdir(exist_ok=True)
        peak_file = tmp_path / "data" / "peak_value_paper.json"
        peak_file.write_text(json.dumps({"peak_value": 1100000, "prev_value": 990000}))
        notifier._PROJECT_ROOT = str(tmp_path)

        snapshot = {
            "date": "2026-04-15",
            "benchmark": {"kospi_daily_return_pct": 0.003, "excess_return_pct": 0.001},
            "drift": {
                "rebalance_date": "2026-03-31",
                "snapshot_date": "2026-04-15",
                "days_since_rebalance": 15,
                "avg_abs_drift_pct": 0.83,
                "max_drift": {
                    "ticker": "005930", "name": "삼성전자",
                    "target_weight_pct": 5.0, "current_weight_pct": 7.2,
                    "drift_pct": 2.2,
                },
                "total_drift_score": 16.6,
                "holdings_drift": [
                    {"ticker": "005930", "name": "삼성전자",
                     "target_weight_pct": 5.0, "current_weight_pct": 7.2, "drift_pct": 2.2},
                ],
            },
        }

        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            notifier.send_detailed_daily_report(balance, snapshot=snapshot)
            text = m.last_request.json()["text"]
            assert "비중 드리프트" in text
            assert "03/31" in text
            assert "15일 경과" in text
            assert "0.83%p" in text
            assert "삼성전자" in text

    def test_drift_warning_emoji(self, notifier, api_url, balance, tmp_path) -> None:
        """5%p 이상 드리프트 종목 → 경고 이모지"""
        import requests_mock

        (tmp_path / "data").mkdir(exist_ok=True)
        peak_file = tmp_path / "data" / "peak_value_paper.json"
        peak_file.write_text(json.dumps({"peak_value": 1100000, "prev_value": 990000}))
        notifier._PROJECT_ROOT = str(tmp_path)

        snapshot = {
            "date": "2026-04-15",
            "benchmark": {"kospi_daily_return_pct": 0.0, "excess_return_pct": 0.0},
            "drift": {
                "rebalance_date": "2026-03-31",
                "snapshot_date": "2026-04-15",
                "days_since_rebalance": 15,
                "avg_abs_drift_pct": 3.5,
                "max_drift": {
                    "ticker": "005930", "name": "삼성전자",
                    "target_weight_pct": 5.0, "current_weight_pct": 10.5,
                    "drift_pct": 5.5,
                },
                "total_drift_score": 70.0,
                "holdings_drift": [
                    {"ticker": "005930", "name": "삼성전자",
                     "target_weight_pct": 5.0, "current_weight_pct": 10.5, "drift_pct": 5.5},
                ],
            },
        }

        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            notifier.send_detailed_daily_report(balance, snapshot=snapshot)
            text = m.last_request.json()["text"]
            assert "\u26a0\ufe0f" in text  # ⚠️

    def test_no_drift_no_section(self, notifier, api_url, balance, tmp_path) -> None:
        """drift가 None이면 섹션 없음"""
        import requests_mock

        (tmp_path / "data").mkdir(exist_ok=True)
        peak_file = tmp_path / "data" / "peak_value_paper.json"
        peak_file.write_text(json.dumps({"peak_value": 1100000, "prev_value": 990000}))
        notifier._PROJECT_ROOT = str(tmp_path)

        snapshot = {
            "date": "2026-04-15",
            "benchmark": {"kospi_daily_return_pct": 0.0, "excess_return_pct": 0.0},
        }

        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            notifier.send_detailed_daily_report(balance, snapshot=snapshot)
            text = m.last_request.json()["text"]
            assert "비중 드리프트" not in text
