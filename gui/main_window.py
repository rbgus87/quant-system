# gui/main_window.py
"""메인 윈도우 — 전체 레이아웃 조합"""

import logging
from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.tray_icon import TrayIcon
from gui.widgets.log_viewer import LogViewer
from gui.widgets.portfolio_view import PortfolioView
from gui.widgets.preset_panel import PresetPanel
from gui.widgets.scheduler_panel import SchedulerPanel
from gui.widgets.status_bar import StatusBarWidget

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """퀀트 시스템 메인 윈도우"""

    def __init__(self) -> None:
        super().__init__()
        self.force_quit = False
        self._setup_ui()
        self._setup_tray()
        self._connect_signals()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Korean Quant System")
        self.setMinimumSize(QSize(900, 650))
        self.resize(1100, 750)

        # 중앙 위젯
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # 좌측 패널 (프리셋 + 스케줄러)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._preset_panel = PresetPanel()
        left_layout.addWidget(self._preset_panel)

        self._scheduler_panel = SchedulerPanel()
        left_layout.addWidget(self._scheduler_panel)

        left_layout.addStretch()
        left_panel.setMaximumWidth(350)
        left_panel.setMinimumWidth(280)

        # 우측 패널 (탭: 포트폴리오 + 로그)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()

        # 포트폴리오 탭
        self._portfolio_view = PortfolioView()
        self._tabs.addTab(self._portfolio_view, "포트폴리오")

        # 로그 탭
        self._log_viewer = LogViewer()
        self._tabs.addTab(self._log_viewer, "로그")

        right_layout.addWidget(self._tabs)

        # 스플리터로 좌우 결합
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter)

        # 상태 바
        self._status_widget = StatusBarWidget()
        status_bar = QStatusBar()
        status_bar.addPermanentWidget(self._status_widget, 1)
        self.setStatusBar(status_bar)

    def _setup_tray(self) -> None:
        """시스템 트레이 아이콘 설정"""
        self._tray = TrayIcon(self)
        self._tray.show()

    def _connect_signals(self) -> None:
        """시그널 연결"""
        # 스케줄러 로그 → 로그 뷰어
        self._scheduler_panel.log_output.connect(self._log_viewer.append_log)

        # 스케줄러 상태 → 상태 바
        self._scheduler_panel.status_changed.connect(
            self._status_widget.set_scheduler_status
        )

        # 스케줄러 상태 → 트레이 툴팁
        self._scheduler_panel.status_changed.connect(self._update_tray_tooltip)

    def _update_tray_tooltip(self, running: bool) -> None:
        status = "실행 중" if running else "중지"
        self._tray.setToolTip(f"Korean Quant System - 스케줄러: {status}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """창 닫기 시 트레이로 최소화 (force_quit이면 종료)"""
        if self.force_quit:
            self._scheduler_panel.cleanup()
            self._tray.hide()
            event.accept()
        else:
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "Korean Quant System",
                "트레이에서 실행 중입니다. 더블클릭으로 열 수 있습니다.",
                TrayIcon.MessageIcon.Information,
                2000,
            )
