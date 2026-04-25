# tests/test_smoke.py
"""스모크 테스트 — 각 진입점이 실제로 실행 가능한지 검증

단위 테스트(mock 기반)와 달리, 실제 subprocess로 실행하여
import 경로, sys.path, 의존성 누락 등의 문제를 잡아냅니다.
"""

import subprocess
import sys
import importlib


class TestEntrypointImports:
    """각 모듈이 import 에러 없이 로드되는지 확인"""

    def test_import_scheduler_main(self) -> None:
        """scheduler.main 모듈 import 가능"""
        mod = importlib.import_module("scheduler.main")
        assert hasattr(mod, "main")

    def test_import_backtest_engine(self) -> None:
        """backtest.engine 모듈 import 가능"""
        mod = importlib.import_module("backtest.engine")
        assert hasattr(mod, "MultiFactorBacktest")

    def test_import_config_settings(self) -> None:
        """config.settings 모듈 import 가능"""
        mod = importlib.import_module("config.settings")
        assert hasattr(mod, "settings")

    def test_import_trading_kiwoom_api(self) -> None:
        """trading.kiwoom_api 모듈 import 가능"""
        mod = importlib.import_module("trading.kiwoom_api")
        assert hasattr(mod, "KiwoomRestClient")

    def test_import_trading_order(self) -> None:
        """trading.order 모듈 import 가능"""
        mod = importlib.import_module("trading.order")
        assert hasattr(mod, "OrderExecutor")

    def test_import_notify_telegram(self) -> None:
        """notify.telegram 모듈 import 가능"""
        mod = importlib.import_module("notify.telegram")
        assert hasattr(mod, "TelegramNotifier")

    def test_import_strategy_screener(self) -> None:
        """strategy.screener 모듈 import 가능"""
        mod = importlib.import_module("strategy.screener")
        assert hasattr(mod, "MultiFactorScreener")

    def test_import_data_collector(self) -> None:
        """data.collector 모듈 import 가능"""
        mod = importlib.import_module("data.collector")
        assert hasattr(mod, "KRXDataCollector")

    def test_import_data_storage(self) -> None:
        """data.storage 모듈 import 가능"""
        mod = importlib.import_module("data.storage")
        assert hasattr(mod, "DataStorage")


class TestEntrypointExecution:
    """각 진입점이 subprocess로 실제 실행 가능한지 확인"""

    def test_scheduler_dry_run(self) -> None:
        """python scheduler/main.py --dry-run 이 정상 종료"""
        result = subprocess.run(
            [sys.executable, "scheduler/main.py", "--dry-run"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert result.returncode == 0, (
            f"scheduler --dry-run 실패:\n{result.stderr}"
        )
        assert "DRY-RUN" in result.stderr  # logging은 stderr로 출력

    def test_run_backtest_help(self) -> None:
        """python run_backtest.py --help 가 정상 종료"""
        result = subprocess.run(
            [sys.executable, "run_backtest.py", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert result.returncode == 0, (
            f"run_backtest --help 실패:\n{result.stderr}"
        )
        assert "백테스트" in result.stdout or "backtest" in result.stdout.lower()

