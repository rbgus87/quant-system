# gui/main_window.py
"""메인 윈도우 — 전체 레이아웃 조합"""

import logging
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.themes import dark_theme, light_theme

# 에러 팝업 대상 키워드 (매매/API 관련 심각한 에러만)
_CRITICAL_ERROR_KEYWORDS = ["주문", "매수", "매도", "API", "토큰", "인증"]
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


class MainWindow(QMainWindow):
    """퀀트 시스템 메인 윈도우"""

    def __init__(self) -> None:
        super().__init__()
        self.force_quit = False
        self._is_dark = True  # 기본 다크 모드
        self._setup_ui()
        self._setup_tray()
        self._setup_auto_refresh()
        self._connect_signals()
        self._apply_theme()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Korean Quant System")
        self.setMinimumSize(QSize(1000, 700))
        self.resize(1200, 800)

        # 중앙 위젯
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # ── 좌측 패널 ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self._preset_panel = PresetPanel()
        left_layout.addWidget(self._preset_panel)

        self._scheduler_panel = SchedulerPanel()
        self._scheduler_panel._start_btn.setObjectName("startBtn")
        self._scheduler_panel._stop_btn.setObjectName("stopBtn")
        left_layout.addWidget(self._scheduler_panel)

        left_layout.addStretch()

        # 테마 전환 버튼 (좌측 하단)
        self._theme_btn = QPushButton("Light")
        self._theme_btn.clicked.connect(self._toggle_theme)
        left_layout.addWidget(self._theme_btn)

        left_panel.setMaximumWidth(340)
        left_panel.setMinimumWidth(280)

        # ── 우측 패널 (상: 탭 / 하: 로그) ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        v_splitter = QSplitter(Qt.Orientation.Vertical)

        # 상단: 탭
        tabs_widget = QWidget()
        tabs_layout = QVBoxLayout(tabs_widget)
        tabs_layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()

        # 포트폴리오 탭
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

        # 차트 탭
        self._chart_view = ChartView()
        self._tabs.addTab(self._chart_view, "차트")

        # 백테스트 탭
        self._backtest_runner = BacktestRunner()
        self._tabs.addTab(self._backtest_runner, "백테스트")

        # 설정 탭
        self._emergency_panel = EmergencyPanel()
        self._tabs.addTab(self._emergency_panel, "설정")

        tabs_layout.addWidget(self._tabs)
        v_splitter.addWidget(tabs_widget)

        # 하단: 로그 (항상 표시)
        self._log_viewer = LogViewer()
        v_splitter.addWidget(self._log_viewer)

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

    def _toggle_theme(self) -> None:
        self._is_dark = not self._is_dark
        self._apply_theme()

    def _apply_theme(self) -> None:
        if self._is_dark:
            self.setStyleSheet(dark_theme())
            self._theme_btn.setText("Light")
            self._log_viewer._text.setStyleSheet(
                "QTextEdit { background-color: #1E1E1E; color: #CCCCCC; }"
            )
        else:
            self.setStyleSheet(light_theme())
            self._theme_btn.setText("Dark")
            self._log_viewer._text.setStyleSheet(
                "QTextEdit { background-color: #FAFAFA; color: #212529; }"
            )
        # 차트 테마 동기화
        self._chart_view.set_dark_mode(self._is_dark)

    def _connect_signals(self) -> None:
        self._scheduler_panel.log_output.connect(self._log_viewer.append_log)
        self._scheduler_panel.log_output.connect(self._check_critical_error)
        self._scheduler_panel.status_changed.connect(
            self._status_widget.set_scheduler_status
        )
        self._scheduler_panel.status_changed.connect(self._update_tray_tooltip)

        # 키보드 단축키
        QShortcut(QKeySequence("Ctrl+R"), self, self._scheduler_panel.start_scheduler)
        QShortcut(QKeySequence("Ctrl+T"), self, self._toggle_theme)
        QShortcut(QKeySequence("Ctrl+L"), self, self._log_viewer.clear)
        QShortcut(QKeySequence("Ctrl+F"), self, self._log_viewer.focus_search)
        QShortcut(QKeySequence("F5"), self, self._portfolio_view.refresh)

    def _check_critical_error(self, line: str) -> None:
        """매매/API 관련 심각한 에러 발생 시 팝업 알림"""
        if "[ERROR]" not in line and " ERROR " not in line:
            return
        if any(kw in line for kw in _CRITICAL_ERROR_KEYWORDS):
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "오류 발생", line[:300])

    def _update_tray_tooltip(self, running: bool) -> None:
        status = "실행 중" if running else "중지"
        self._tray.setToolTip(f"Korean Quant System - 스케줄러: {status}")

    def closeEvent(self, event: QCloseEvent) -> None:
        # 스케줄러 실행 중이면 트레이로 최소화 (force_quit이 아닌 경우)
        if not self.force_quit and self._scheduler_panel.is_running():
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "Korean Quant System",
                "스케줄러 실행 중 — 트레이에서 실행 중입니다.",
                TrayIcon.MessageIcon.Information,
                2000,
            )
            return

        # 완전 종료
        self._auto_refresh_timer.stop()
        self._scheduler_panel.cleanup()
        self._tray.hide()
        event.accept()
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()
        # PyInstaller exe에서 QApplication.quit()만으로 프로세스가 안 죽는 경우 대비
        import os
        os._exit(0)
