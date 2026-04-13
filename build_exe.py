"""PyInstaller 빌드 스크립트

실행: python build_exe.py
결과: dist/KoreanQuant.exe
"""

import PyInstaller.__main__
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def build() -> None:
    args = [
        os.path.join(PROJECT_ROOT, "gui", "app.py"),
        "--name=KoreanQuant",
        "--onefile",
        "--windowed",                    # 콘솔 창 숨김
        "--noconfirm",
        # 프로젝트 모듈 포함
        f"--paths={PROJECT_ROOT}",
        # config.yaml 등 데이터 파일 포함
        f"--add-data={os.path.join(PROJECT_ROOT, 'config', 'config.yaml')};config",
        # 히든 임포트 (동적 import 되는 모듈)
        # config
        "--hidden-import=config.settings",
        "--hidden-import=config.calendar",
        "--hidden-import=config.font",
        "--hidden-import=config.logging_config",
        # data
        "--hidden-import=data.collector",
        "--hidden-import=data.storage",
        "--hidden-import=data.dart_client",
        "--hidden-import=data.processor",
        # factors
        "--hidden-import=factors.composite",
        "--hidden-import=factors.value",
        "--hidden-import=factors.momentum",
        "--hidden-import=factors.quality",
        "--hidden-import=factors.utils",
        # strategy
        "--hidden-import=strategy.screener",
        "--hidden-import=strategy.market_regime",
        "--hidden-import=strategy.rebalancer",
        # backtest (리포트/분석용)
        "--hidden-import=backtest.engine",
        "--hidden-import=backtest.metrics",
        "--hidden-import=backtest.report",
        # trading
        "--hidden-import=trading.kiwoom_api",
        "--hidden-import=trading.order",
        # notify
        "--hidden-import=notify.telegram",
        # scheduler
        "--hidden-import=scheduler.main",
        # monitor (장중 리스크 감시 — 누락 시 스케줄러 Job 실패)
        "--hidden-import=monitor.alert",
        "--hidden-import=monitor.benchmark",
        "--hidden-import=monitor.drift",
        "--hidden-import=monitor.risk_guard",
        "--hidden-import=monitor.snapshot",
        "--hidden-import=monitor.storage",
        # dart_notifier (공시 알림)
        "--hidden-import=dart_notifier.filter",
        "--hidden-import=dart_notifier.notifier",
        "--hidden-import=dart_notifier.storage",
        # gui
        "--hidden-import=gui.app",
        "--hidden-import=gui.main_window",
        "--hidden-import=gui.themes",
        "--hidden-import=gui.tray_icon",
        "--hidden-import=gui.widgets.backtest_runner",
        "--hidden-import=gui.widgets.chart_view",
        "--hidden-import=gui.widgets.emergency_panel",
        "--hidden-import=gui.widgets.factor_scores",
        "--hidden-import=gui.widgets.log_handler",
        "--hidden-import=gui.widgets.log_viewer",
        "--hidden-import=gui.widgets.portfolio_view",
        "--hidden-import=gui.widgets.preset_panel",
        "--hidden-import=gui.widgets.rebalance_history",
        "--hidden-import=gui.widgets.scheduler_panel",
        "--hidden-import=gui.widgets.status_bar",
        # 외부 런타임 동적 import (PyInstaller 자동 감지 불가)
        "--hidden-import=yaml",
        "--hidden-import=quantstats",
        "--hidden-import=apscheduler",
        "--hidden-import=finance_datareader",
        "--hidden-import=pykrx_openapi",
        # 빌드 디렉토리
        f"--distpath={os.path.join(PROJECT_ROOT, 'dist')}",
        f"--workpath={os.path.join(PROJECT_ROOT, 'build')}",
        f"--specpath={PROJECT_ROOT}",
    ]

    print("=" * 50)
    print("Korean Quant System - exe 빌드 시작")
    print("=" * 50)

    PyInstaller.__main__.run(args)

    exe_path = os.path.join(PROJECT_ROOT, "dist", "KoreanQuant.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\n빌드 완료: {exe_path} ({size_mb:.1f} MB)")

        # 프로젝트 루트로 복사 (기존 파일 덮어쓰기)
        dest_path = os.path.join(PROJECT_ROOT, "KoreanQuant.exe")
        shutil.copy2(exe_path, dest_path)
        print(f"루트 폴더로 복사 완료: {dest_path}")
    else:
        print("\n빌드 실패!")
        sys.exit(1)


if __name__ == "__main__":
    build()
