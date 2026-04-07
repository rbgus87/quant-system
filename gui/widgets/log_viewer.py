# gui/widgets/log_viewer.py
"""3탭 로그 뷰어 — 거래 / 시스템 / 에러 분리"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.log_handler import QtLogSignalBridge

logger = logging.getLogger(__name__)

# 로그 레벨별 색상
_LEVEL_COLORS = {
    "ERROR": QColor("#FF4444"),
    "CRITICAL": QColor("#FF4444"),
    "WARNING": QColor("#FF8800"),
    "INFO": QColor("#00AA00"),
    "DEBUG": QColor("#888888"),
}

# 거래 탭 전용 배경색 (HTML rich-text용)
_TRADE_BG = {
    "매수": "#3D1F1F",   # 빨강 배경
    "BUY": "#3D1F1F",
    "매도": "#1F2D3D",   # 파랑 배경
    "SELL": "#1F2D3D",
    "실패": "#3D3D1F",   # 노랑 배경
    "FAIL": "#3D3D1F",
    "failed": "#3D3D1F",
    "reject": "#3D3D1F",
}

_TRADE_BG_LIGHT = {
    "매수": "#FFE0E0",
    "BUY": "#FFE0E0",
    "매도": "#E0E8FF",
    "SELL": "#E0E8FF",
    "실패": "#FFFDE0",
    "FAIL": "#FFFDE0",
    "failed": "#FFFDE0",
    "reject": "#FFFDE0",
}


class LogPanel(QWidget):
    """개별 로그 패널 (검색 + 레벨 필터 + 텍스트 영역)"""

    MAX_LINES = 2000

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        enable_trade_highlight: bool = False,
    ) -> None:
        super().__init__(parent)
        self._line_count = 0
        self._autoscroll = True
        self._is_dark = True
        self._enable_trade_highlight = enable_trade_highlight
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 검색 + 필터 행
        search_row = QHBoxLayout()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("로그 검색...")
        self._search_input.returnPressed.connect(self._search_next)
        search_row.addWidget(self._search_input, 1)

        search_prev_btn = QPushButton("<")
        search_prev_btn.setFixedWidth(30)
        search_prev_btn.setToolTip("이전 검색 결과")
        search_prev_btn.clicked.connect(self._search_prev)
        search_row.addWidget(search_prev_btn)

        search_next_btn = QPushButton(">")
        search_next_btn.setFixedWidth(30)
        search_next_btn.setToolTip("다음 검색 결과")
        search_next_btn.clicked.connect(self._search_next)
        search_row.addWidget(search_next_btn)

        self._level_filter = QComboBox()
        self._level_filter.addItems(["전체", "ERROR", "WARNING", "INFO"])
        self._level_filter.setToolTip("로그 레벨 필터")
        self._level_filter.setFixedWidth(90)
        search_row.addWidget(self._level_filter)

        layout.addLayout(search_row)

        # 로그 텍스트 영역
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 9))
        self._text.setStyleSheet(
            "QTextEdit { background-color: #1E1E1E; color: #CCCCCC; }"
        )
        layout.addWidget(self._text)

        # 하단 컨트롤
        btn_row = QHBoxLayout()

        self._line_count_label = QLabel("0 / 2,000")
        self._line_count_label.setStyleSheet("color: gray; font-size: 10px;")
        btn_row.addWidget(self._line_count_label)

        btn_row.addStretch()

        clear_btn = QPushButton("지우기")
        clear_btn.clicked.connect(self.clear)
        btn_row.addWidget(clear_btn)

        self._autoscroll_btn = QPushButton("자동 스크롤: ON")
        self._autoscroll_btn.clicked.connect(self._toggle_autoscroll)
        btn_row.addWidget(self._autoscroll_btn)

        layout.addLayout(btn_row)

    def set_dark_mode(self, is_dark: bool) -> None:
        """테마 변경"""
        self._is_dark = is_dark
        if is_dark:
            self._text.setStyleSheet(
                "QTextEdit { background-color: #1E1E1E; color: #CCCCCC; }"
            )
        else:
            self._text.setStyleSheet(
                "QTextEdit { background-color: #FAFAFA; color: #212529; }"
            )

    def append_log(self, line: str) -> None:
        """로그 한 줄 추가"""
        # 레벨 필터 체크
        level_filter = self._level_filter.currentText()
        if level_filter != "전체":
            level_order = ["DEBUG", "INFO", "WARNING", "ERROR"]
            filter_idx = (
                level_order.index(level_filter) if level_filter in level_order else 0
            )
            line_level = "DEBUG"
            for level in level_order:
                if f"[{level}]" in line or f" {level} " in line:
                    line_level = level
                    break
            line_idx = (
                level_order.index(line_level) if line_level in level_order else 0
            )
            if line_idx < filter_idx:
                return

        # 최대 라인 수 초과 시 상단 제거
        self._line_count += 1
        if self._line_count > self.MAX_LINES:
            cursor = self._text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                self._line_count - self.MAX_LINES,
            )
            cursor.removeSelectedText()
            self._line_count = self.MAX_LINES

        # 거래 탭 시각적 강조 (HTML)
        if self._enable_trade_highlight:
            self._append_trade_html(line)
        else:
            self._append_plain(line)

        # 라인 카운트
        self._line_count_label.setText(f"{self._line_count:,} / {self.MAX_LINES:,}")

        if self._autoscroll:
            scrollbar = self._text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _append_plain(self, line: str) -> None:
        """일반 텍스트 추가 (레벨 색상)"""
        color = QColor("#CCCCCC") if self._is_dark else QColor("#212529")
        for level, c in _LEVEL_COLORS.items():
            if f"[{level}]" in line or f" {level} " in line:
                color = c
                break

        fmt = QTextCharFormat()
        fmt.setForeground(color)

        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)

    def _append_trade_html(self, line: str) -> None:
        """거래 로그 HTML 강조 추가"""
        bg_map = _TRADE_BG if self._is_dark else _TRADE_BG_LIGHT
        bg_color = None
        line_lower = line.lower()
        for keyword, color in bg_map.items():
            if keyword.lower() in line_lower:
                bg_color = color
                break

        # 텍스트 색상
        text_color = "#CCCCCC" if self._is_dark else "#212529"
        for level, c in _LEVEL_COLORS.items():
            if f"[{level}]" in line:
                text_color = c.name()
                break

        import html as html_mod

        escaped = html_mod.escape(line)
        if bg_color:
            html_line = (
                f'<div style="background-color:{bg_color}; padding:2px 4px; '
                f'margin:1px 0; border-radius:2px;">'
                f'<span style="color:{text_color}; font-family:Consolas; font-size:9pt;">'
                f"{escaped}</span></div>"
            )
        else:
            html_line = (
                f'<span style="color:{text_color}; font-family:Consolas; font-size:9pt;">'
                f"{escaped}</span><br>"
            )

        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html_line)

    def clear(self) -> None:
        self._text.clear()
        self._line_count = 0
        self._line_count_label.setText(f"0 / {self.MAX_LINES:,}")

    def _toggle_autoscroll(self) -> None:
        self._autoscroll = not self._autoscroll
        state = "ON" if self._autoscroll else "OFF"
        self._autoscroll_btn.setText(f"자동 스크롤: {state}")

    def focus_search(self) -> None:
        """검색 입력창에 포커스"""
        self._search_input.setFocus()
        self._search_input.selectAll()

    def _search_next(self) -> None:
        text = self._search_input.text()
        if not text:
            return
        found = self._text.find(text)
        if not found:
            cursor = self._text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._text.setTextCursor(cursor)
            self._text.find(text)

    def _search_prev(self) -> None:
        from PyQt6.QtGui import QTextDocument

        text = self._search_input.text()
        if not text:
            return
        found = self._text.find(text, QTextDocument.FindFlag.FindBackward)
        if not found:
            cursor = self._text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._text.setTextCursor(cursor)
            self._text.find(text, QTextDocument.FindFlag.FindBackward)


class TabbedLogViewer(QWidget):
    """3탭 로그 뷰어 (거래 / 시스템 / 에러)"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bridge = QtLogSignalBridge(self)
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.South)

        self._trading_panel = LogPanel(enable_trade_highlight=True)
        self._system_panel = LogPanel()
        self._error_panel = LogPanel()

        self._tabs.addTab(self._trading_panel, "거래")
        self._tabs.addTab(self._system_panel, "시스템")
        self._tabs.addTab(self._error_panel, "에러")

        # 시스템 탭을 기본 선택
        self._tabs.setCurrentIndex(1)

        layout.addWidget(self._tabs)

    def _connect_signals(self) -> None:
        self._bridge.trading_log.connect(self._trading_panel.append_log)
        self._bridge.system_log.connect(self._system_panel.append_log)
        self._bridge.error_log.connect(self._on_error_log)
        self._tabs.currentChanged.connect(self._clear_error_badge)

    def _on_error_log(self, line: str) -> None:
        """에러 로그 수신 시 탭에 추가하고, 에러 탭이 아닌 곳이면 탭 제목 강조"""
        self._error_panel.append_log(line)
        if self._tabs.currentIndex() != 2:
            if "!" not in self._tabs.tabText(2):
                self._tabs.setTabText(2, "에러 !")

    def _clear_error_badge(self, index: int) -> None:
        """에러 탭 선택 시 뱃지 제거"""
        if index == 2:
            self._tabs.setTabText(2, "에러")

    @property
    def bridge(self) -> QtLogSignalBridge:
        """QtLogHandler에 전달할 브릿지 객체"""
        return self._bridge

    def append_log(self, line: str) -> None:
        """외부(QProcess stdout 등)에서 전달되는 로그 라인을 라우팅"""
        self._bridge.dispatch(line)

    def set_dark_mode(self, is_dark: bool) -> None:
        """테마 변경"""
        self._trading_panel.set_dark_mode(is_dark)
        self._system_panel.set_dark_mode(is_dark)
        self._error_panel.set_dark_mode(is_dark)

    def clear(self) -> None:
        """전체 탭 클리어"""
        self._trading_panel.clear()
        self._system_panel.clear()
        self._error_panel.clear()

    def focus_search(self) -> None:
        """현재 활성 탭의 검색창에 포커스"""
        current = self._tabs.currentWidget()
        if isinstance(current, LogPanel):
            current.focus_search()
