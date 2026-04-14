# tests/test_monitor_snapshot.py
"""일간 스냅샷 모니터링 테스트"""

import json
import pytest
import requests_mock
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from monitor.storage import MonitorStorage
from monitor.snapshot import take_daily_snapshot
from monitor.benchmark import get_kospi_daily_return


# ── 공통 fixture ──


@pytest.fixture
def mock_balance() -> dict:
    """KiwoomRestClient.get_balance() mock 결과"""
    return {
        "holdings": [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "qty": 10,
                "avg_price": 70000,
                "current_price": 75000,
                "eval_amount": 750000,
                "eval_profit": 50000,
                "profit_rate": 7.14,
            },
            {
                "ticker": "000660",
                "name": "SK하이닉스",
                "qty": 5,
                "avg_price": 150000,
                "current_price": 160000,
                "eval_amount": 800000,
                "eval_profit": 50000,
                "profit_rate": 6.67,
            },
        ],
        "cash": 450000,
        "total_eval_amount": 2000000,
        "total_profit": 100000,
    }


@pytest.fixture
def storage(tmp_path) -> MonitorStorage:
    """임시 SQLite DB로 MonitorStorage 생성"""
    db_path = str(tmp_path / "test_monitor.db")
    return MonitorStorage(db_path=db_path)


# ── MonitorStorage 테스트 ──


class TestMonitorStorage:
    """스냅샷 DB 저장/조회 roundtrip"""

    def _make_snapshot(self, date_str: str = "2026-03-31") -> dict:
        return {
            "date": date_str,
            "portfolio": {
                "total_value": 2000000,
                "total_invested": 1900000,
                "cash": 450000,
                "daily_return_pct": 0.0052,
                "total_return_pct": 0.0526,
                "mdd_pct": -0.03,
            },
            "benchmark": {
                "kospi_daily_return_pct": 0.0031,
                "excess_return_pct": 0.0021,
            },
            "holdings": [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "qty": 10,
                    "avg_price": 70000,
                    "current_price": 75000,
                    "return_pct": 7.14,
                    "weight_pct": 37.5,
                },
            ],
        }

    def test_save_and_get_latest(self, storage) -> None:
        """저장 후 get_latest_snapshot 조회"""
        snap = self._make_snapshot()
        storage.save_snapshot(snap)

        loaded = storage.get_latest_snapshot()
        assert loaded is not None
        assert loaded["date"] == "2026-03-31"
        assert loaded["portfolio"]["total_value"] == 2000000
        assert loaded["portfolio"]["daily_return_pct"] == pytest.approx(0.0052)
        assert loaded["benchmark"]["kospi_daily_return_pct"] == pytest.approx(0.0031)
        assert len(loaded["holdings"]) == 1
        assert loaded["holdings"][0]["ticker"] == "005930"

    def test_upsert_overwrites(self, storage) -> None:
        """동일 날짜 재저장 시 업데이트"""
        snap = self._make_snapshot()
        storage.save_snapshot(snap)

        snap["portfolio"]["total_value"] = 2100000
        storage.save_snapshot(snap)

        loaded = storage.get_latest_snapshot()
        assert loaded["portfolio"]["total_value"] == 2100000

    def test_get_snapshots_since(self, storage) -> None:
        """날짜 범위 조회"""
        storage.save_snapshot(self._make_snapshot("2026-03-28"))
        storage.save_snapshot(self._make_snapshot("2026-03-31"))

        results = storage.get_snapshots_since(date(2026, 3, 1))
        assert len(results) == 2
        assert results[0]["date"] == "2026-03-28"
        assert results[1]["date"] == "2026-03-31"

    def test_get_latest_empty(self, storage) -> None:
        """빈 DB에서 None 반환"""
        assert storage.get_latest_snapshot() is None

    def test_get_snapshots_since_empty(self, storage) -> None:
        """빈 DB에서 빈 리스트 반환"""
        assert storage.get_snapshots_since(date(2026, 1, 1)) == []


# ── take_daily_snapshot 테스트 ──


class TestTakeDailySnapshot:
    """스냅샷 수집 로직 검증"""

    @patch("monitor.snapshot.get_kospi_daily_return", return_value=0.003)
    @patch("monitor.snapshot._load_peak_prev", return_value=(2100000.0, 1990000.0))
    def test_snapshot_calculation(
        self, mock_peak, mock_kospi, mock_balance
    ) -> None:
        """수익률 계산 정확성"""
        snapshot = take_daily_snapshot(mock_balance)

        # 투자원금 = 2000000 - 100000 = 1900000
        assert snapshot["portfolio"]["total_invested"] == 1900000
        assert snapshot["portfolio"]["cash"] == 450000

        # 당일 수익률 = 2000000 / 1990000 - 1
        expected_daily = 2000000 / 1990000 - 1
        assert snapshot["portfolio"]["daily_return_pct"] == pytest.approx(
            expected_daily, abs=1e-5
        )

        # 누적 수익률 = 2000000 / 1900000 - 1
        expected_total = 2000000 / 1900000 - 1
        assert snapshot["portfolio"]["total_return_pct"] == pytest.approx(
            expected_total, abs=1e-5
        )

        # MDD = 2000000 / 2100000 - 1 (고점이 더 높았으므로)
        expected_mdd = 2000000 / 2100000 - 1
        assert snapshot["portfolio"]["mdd_pct"] == pytest.approx(
            expected_mdd, abs=1e-5
        )

        # 벤치마크
        assert snapshot["benchmark"]["kospi_daily_return_pct"] == pytest.approx(0.003)
        expected_excess = expected_daily - 0.003
        assert snapshot["benchmark"]["excess_return_pct"] == pytest.approx(
            expected_excess, abs=1e-5
        )

    @patch("monitor.snapshot.get_kospi_daily_return", return_value=0.003)
    @patch("monitor.snapshot._load_peak_prev", return_value=(2100000.0, 1990000.0))
    def test_holdings_weight(self, mock_peak, mock_kospi, mock_balance) -> None:
        """종목별 비중 계산"""
        snapshot = take_daily_snapshot(mock_balance)
        holdings = snapshot["holdings"]

        assert len(holdings) == 2
        # 삼성전자: 750000 / 2000000 * 100 = 37.5%
        samsung = next(h for h in holdings if h["ticker"] == "005930")
        assert samsung["weight_pct"] == 37.5
        assert samsung["return_pct"] == 7.14

    @patch("monitor.snapshot.get_kospi_daily_return", return_value=0.0)
    @patch("monitor.snapshot._load_peak_prev", return_value=(0.0, 0.0))
    def test_no_prev_value(self, mock_peak, mock_kospi, mock_balance) -> None:
        """전일 데이터 없을 때 당일 수익률 0"""
        snapshot = take_daily_snapshot(mock_balance)
        assert snapshot["portfolio"]["daily_return_pct"] == 0.0


# ── benchmark 테스트 ──


class TestBenchmark:
    """KOSPI 벤치마크 조회 폴백 (Naver → KRX OpenAPI → pykrx → FDR)"""

    @patch("monitor.benchmark._fetch_naver_kospi_closes")
    def test_naver_success(self, mock_naver) -> None:
        """Naver 1차 정상 조회"""
        mock_naver.return_value = [
            ("2026-03-31", 2710.0),
            ("2026-03-28", 2700.0),
        ]
        result = get_kospi_daily_return("2026-03-31")
        expected = 2710.0 / 2700.0 - 1
        assert result == pytest.approx(expected)

    @patch("monitor.benchmark._fetch_naver_kospi_closes", return_value=[])
    def test_krx_openapi_fallback(self, mock_naver) -> None:
        """Naver 실패 시 KRX Open API 폴백 (FLUC_RT 사용)"""
        mock_api = MagicMock()
        mock_api.get_kospi_daily_trade.return_value = {
            "OutBlock_1": [
                {"IDX_NM": "코스피 (외국주포함)", "CLSPRC_IDX": None, "FLUC_RT": None},
                {"IDX_NM": "코스피", "CLSPRC_IDX": 2710.0, "FLUC_RT": 0.37},
                {"IDX_NM": "코스피 200", "CLSPRC_IDX": 360.0, "FLUC_RT": 0.5},
            ]
        }
        mock_module = MagicMock()
        mock_module.KRXOpenAPI.return_value = mock_api
        with patch.dict("sys.modules", {"pykrx_openapi": mock_module}):
            result = get_kospi_daily_return("2026-03-31")
        assert result == pytest.approx(0.0037)

    @patch("monitor.benchmark._fetch_naver_kospi_closes", return_value=[])
    def test_pykrx_fallback(self, mock_naver) -> None:
        """Naver, KRX OpenAPI 실패 시 pykrx 폴백"""
        import pandas as pd

        mock_openapi_module = MagicMock()
        mock_openapi_module.KRXOpenAPI.side_effect = Exception("openapi error")

        with patch.dict("sys.modules", {"pykrx_openapi": mock_openapi_module}):
            with patch("pykrx.stock.get_index_ohlcv_by_date") as mock_pykrx:
                mock_pykrx.return_value = pd.DataFrame(
                    {"종가": [2700.0, 2712.0]},
                    index=pd.to_datetime(["2026-03-28", "2026-03-31"]),
                )
                result = get_kospi_daily_return("2026-03-31")
        expected = 2712.0 / 2700.0 - 1
        assert result == pytest.approx(expected)

    @patch("monitor.benchmark._fetch_naver_kospi_closes", return_value=[])
    @patch("pykrx.stock.get_index_ohlcv_by_date", side_effect=Exception("error"))
    def test_fdr_fallback(self, mock_pykrx, mock_naver) -> None:
        """상위 3소스 실패 시 FDR 폴백"""
        import pandas as pd

        mock_openapi_module = MagicMock()
        mock_openapi_module.KRXOpenAPI.side_effect = Exception("openapi error")

        mock_fdr = MagicMock()
        mock_fdr.DataReader.return_value = pd.DataFrame(
            {"Close": [2700.0, 2715.0]},
            index=pd.to_datetime(["2026-03-28", "2026-03-31"]),
        )
        with patch.dict(
            "sys.modules",
            {"pykrx_openapi": mock_openapi_module, "FinanceDataReader": mock_fdr},
        ):
            result = get_kospi_daily_return("2026-03-31")
        expected = 2715.0 / 2700.0 - 1
        assert result == pytest.approx(expected)

    @patch("monitor.benchmark._fetch_naver_kospi_closes", return_value=[])
    @patch("pykrx.stock.get_index_ohlcv_by_date", side_effect=Exception("err"))
    def test_all_fail_returns_zero(self, mock_pykrx, mock_naver) -> None:
        """모든 소스 실패 시 0.0 반환"""
        mock_openapi_module = MagicMock()
        mock_openapi_module.KRXOpenAPI.side_effect = Exception("openapi error")

        mock_fdr = MagicMock()
        mock_fdr.DataReader.side_effect = Exception("fdr error")
        with patch.dict(
            "sys.modules",
            {"pykrx_openapi": mock_openapi_module, "FinanceDataReader": mock_fdr},
        ):
            result = get_kospi_daily_return("2026-03-31")
        assert result == 0.0


# ── telegram 벤치마크 섹션 테스트 ──


class TestTelegramBenchmarkSection:
    """send_detailed_daily_report의 벤치마크 섹션"""

    @pytest.fixture
    def notifier(self):
        with patch("notify.telegram.settings") as mock_settings:
            mock_settings.telegram_bot_token = "123456:ABC-DEF"
            mock_settings.telegram_chat_id = "987654321"
            mock_settings.is_paper_trading = True
            n = TelegramNotifier()
        return n

    @pytest.fixture
    def api_url(self):
        return "https://api.telegram.org/bot123456:ABC-DEF/sendMessage"

    def test_with_snapshot_includes_benchmark(
        self, notifier, api_url, mock_balance, tmp_path
    ) -> None:
        """snapshot 전달 시 벤치마크 섹션 포함"""
        # peak_value 파일 mock
        peak_file = tmp_path / "peak_value_paper.json"
        peak_file.write_text(json.dumps({"peak_value": 2100000, "prev_value": 1990000}))
        notifier._PROJECT_ROOT = str(tmp_path)
        (tmp_path / "data").mkdir(exist_ok=True)
        peak_in_data = tmp_path / "data" / "peak_value_paper.json"
        peak_in_data.write_text(json.dumps({"peak_value": 2100000, "prev_value": 1990000}))

        snapshot = {
            "date": "2026-03-31",
            "portfolio": {},
            "benchmark": {
                "kospi_daily_return_pct": 0.0031,
                "excess_return_pct": 0.0021,
            },
            "holdings": [],
        }

        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            notifier.send_detailed_daily_report(mock_balance, snapshot=snapshot)
            body = m.last_request.json()
            text = body["text"]
            assert "벤치마크" in text
            assert "KOSPI 당일" in text
            assert "초과수익률" in text

    def test_without_snapshot_no_benchmark(
        self, notifier, api_url, mock_balance, tmp_path
    ) -> None:
        """snapshot=None 시 벤치마크 섹션 없음 (하위 호환)"""
        (tmp_path / "data").mkdir(exist_ok=True)
        peak_in_data = tmp_path / "data" / "peak_value_paper.json"
        peak_in_data.write_text(json.dumps({"peak_value": 2100000, "prev_value": 1990000}))
        notifier._PROJECT_ROOT = str(tmp_path)

        with requests_mock.Mocker() as m:
            m.post(api_url, json={"ok": True}, status_code=200)
            notifier.send_detailed_daily_report(mock_balance)
            body = m.last_request.json()
            text = body["text"]
            assert "벤치마크" not in text


# import for telegram test
from notify.telegram import TelegramNotifier
