# gui/main_window.py
"""메인 윈도우 — 전체 레이아웃 조합"""

import logging
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.tray_icon import TrayIcon
from gui.widgets.backtest_runner import BacktestRunner
from gui.widgets.chart_view import ChartView
from gui.widgets.emergency_panel import EmergencyPanel
from gui.widgets.log_viewer import LogViewer
from gui.widgets.portfolio_view import PortfolioView
from gui.widgets.preset_panel import PresetPanel
from gui.widgets.scheduler_panel import SchedulerPanel
from gui.widgets.status_bar import StatusBarWidget

logger = logging.getLogger(__name__)

# 통합 스타일시트
_STYLESHEET = """
QMainWindow {
    background-color: #F8F9FA;
}
QGroupBox {
    border: 1px solid #DEE2E6;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 14px;
    font-weight: bold;
    color: #495057;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QTabWidget::pane {
    border: 1px solid #DEE2E6;
    border-radius: 4px;
    background: white;
}
QTabBar::tab {
    padding: 6px 16px;
    margin-right: 2px;
    border: 1px solid #DEE2E6;
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    background: #F1F3F5;
    color: #495057;
}
QTabBar::tab:selected {
    background: white;
    color: #212529;
    font-weight: bold;
}
QPushButton {
    padding: 5px 14px;
    border: 1px solid #CED4DA;
    border-radius: 4px;
    background: white;
    color: #212529;
}
QPushButton:hover {
    background: #E9ECEF;
}
QPushButton:pressed {
    background: #DEE2E6;
}
QPushButton:disabled {
    color: #ADB5BD;
    background: #F8F9FA;
}
QPushButton#startBtn {
    background: #40C057;
    color: white;
    border-color: #37B24D;
    font-weight: bold;
}
QPushButton#startBtn:hover { background: #37B24D; }
QPushButton#stopBtn {
    background: #FA5252;
    color: white;
    border-color: #F03E3E;
    font-weight: bold;
}
QPushButton#stopBtn:hover { background: #F03E3E; }
QTableWidget {
    border: 1px solid #DEE2E6;
    gridline-color: #E9ECEF;
    background: white;
    selection-background-color: #D0EBFF;
}
QTableWidget::item {
    padding: 3px 6px;
}
QHeaderView::section {
    background: #F1F3F5;
    border: none;
    border-bottom: 2px solid #DEE2E6;
    padding: 5px 8px;
    font-weight: bold;
    color: #495057;
}
QComboBox {
    padding: 4px 8px;
    border: 1px solid #CED4DA;
    border-radius: 4px;
    background: white;
}
QLineEdit {
    padding: 4px 8px;
    border: 1px solid #CED4DA;
    border-radius: 4px;
    background: white;
}
QStatusBar {
    background: #F1F3F5;
    border-top: 1px solid #DEE2E6;
}
"""


class MainWindow(QMainWindow):
    """퀀트 시스템 메인 윈도우"""

    def __init__(self) -> None:
        super().__init__()
        self.force_quit = False
        self._setup_ui()
        self._setup_tray()
        self._setup_auto_refresh()
        self._connect_signals()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Korean Quant System")
        self.setMinimumSize(QSize(1000, 700))
        self.resize(1200, 800)
        self.setStyleSheet(_STYLESHEET)

        # 중앙 위젯
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # ── 좌측 패널 (프리셋 + 스케줄러) ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self._preset_panel = PresetPanel()
        left_layout.addWidget(self._preset_panel)

        self._scheduler_panel = SchedulerPanel()
        # 버튼에 objectName 지정 (스타일시트 매칭)
        self._scheduler_panel._start_btn.setObjectName("startBtn")
        self._scheduler_panel._stop_btn.setObjectName("stopBtn")
        left_layout.addWidget(self._scheduler_panel)

        left_layout.addStretch()
        left_panel.setMaximumWidth(340)
        left_panel.setMinimumWidth(280)

        # ── 우측 패널 (상: 탭 / 하: 로그) ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 상하 스플리터 (탭 위, 로그 아래)
        v_splitter = QSplitter(Qt.Orientation.Vertical)

        # 상단: 탭 영역
        tabs_widget = QWidget()
        tabs_layout = QVBoxLayout(tabs_widget)
        tabs_layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()

        # 탭 1: 포트폴리오
        portfolio_tab = QWidget()
        portfolio_layout = QVBoxLayout(portfolio_tab)
        portfolio_layout.setContentsMargins(4, 4, 4, 4)
        self._portfolio_view = PortfolioView()
        portfolio_layout.addWidget(self._portfolio_view)

        auto_row = QHBoxLayout()
        self._auto_refresh_cb = QCheckBox("30초마다 자동 갱신")
        self._auto_refresh_cb.stateChanged.connect(self._toggle_auto_refresh)
        auto_row.addWidget(self._auto_refresh_cb)
        auto_row.addStretch()
        portfolio_layout.addLayout(auto_row)

        self._tabs.addTab(portfolio_tab, "포트폴리오")

        # 탭 2: 차트
        self._chart_view = ChartView()
        self._tabs.addTab(self._chart_view, "차트")

        # 탭 3: 백테스트
        self._backtest_runner = BacktestRunner()
        self._tabs.addTab(self._backtest_runner, "백테스트")

        # 탭 4: 설정
        self._emergency_panel = EmergencyPanel()
        self._tabs.addTab(self._emergency_panel, "설정")

        tabs_layout.addWidget(self._tabs)
        v_splitter.addWidget(tabs_widget)

        # 하단: 로그 (항상 표시)
        self._log_viewer = LogViewer()
        v_splitter.addWidget(self._log_viewer)

        # 상단 70%, 하단 30% 비율
        v_splitter.setSizes([500, 200])
        v_splitter.setCollapsible(0, False)
        v_splitter.setCollapsible(1, False)

        right_layout.addWidget(v_splitter)

        # ── 좌우 스플리터 ──
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.addWidget(left_panel)
        h_splitter.addWidget(right_panel)
        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)

        main_layout.addWidget(h_splitter)

        # 상태 바
        self._status_widget = StatusBarWidget()
        status_bar = QStatusBar()
        status_bar.addPermanentWidget(self._status_widget, 1)
        self.setStatusBar(status_bar)

    def _setup_tray(self) -> None:
        self._tray = TrayIcon(self)
        self._tray.show()

    def _setup_auto_refresh(self) -> None:
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._portfolio_view.refresh)

    def _toggle_auto_refresh(self, state: int) -> None:
        if state:
            self._auto_refresh_timer.start(30000)
            self._portfolio_view.refresh()
        else:
            self._auto_refresh_timer.stop()

    def _connect_signals(self) -> None:
        # 스케줄러 로그 → 하단 로그 뷰어
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
        if self.force_quit:
            self._auto_refresh_timer.stop()
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
