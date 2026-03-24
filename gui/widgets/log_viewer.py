# gui/widgets/log_viewer.py
"""실시간 로그 뷰어 — 스케줄러 출력 + 로그 파일 표시"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextCharFormat
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# 로그 레벨별 색상
_LEVEL_COLORS = {
    "ERROR": QColor("#FF4444"),
    "WARNING": QColor("#FF8800"),
    "INFO": QColor("#00AA00"),
    "DEBUG": QColor("#888888"),
}


class LogViewer(QWidget):
    """로그 출력 뷰어"""

    MAX_LINES = 2000

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._line_count = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("로그")
        group_layout = QVBoxLayout(group)

        # 검색 + 필터 행
        search_row = QHBoxLayout()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("로그 검색... (Ctrl+F)")
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

        group_layout.addLayout(search_row)

        # 로그 텍스트 영역
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 9))
        self._text.setStyleSheet(
            "QTextEdit { background-color: #1E1E1E; color: #CCCCCC; }"
        )
        group_layout.addWidget(self._text)

        # 버튼 행
        btn_row = QHBoxLayout()

        self._line_count_label = QLabel("0 / 2,000")
        self._line_count_label.setStyleSheet("color: gray; font-size: 10px;")
        btn_row.addWidget(self._line_count_label)

        btn_row.addStretch()

        clear_btn = QPushButton("지우기")
        clear_btn.clicked.connect(self.clear)
        btn_row.addWidget(clear_btn)

        self._autoscroll_btn = QPushButton("자동 스크롤: ON")
        self._autoscroll = True
        self._autoscroll_btn.clicked.connect(self._toggle_autoscroll)
        btn_row.addWidget(self._autoscroll_btn)

        group_layout.addLayout(btn_row)
        layout.addWidget(group)

    def append_log(self, line: str) -> None:
        """로그 한 줄 추가"""
        # 레벨 필터 체크
        level_filter = self._level_filter.currentText()
        if level_filter != "전체":
            # 필터링된 레벨 이상만 표시
            level_order = ["DEBUG", "INFO", "WARNING", "ERROR"]
            filter_idx = level_order.index(level_filter) if level_filter in level_order else 0
            line_level = "DEBUG"
            for level in level_order:
                if f"[{level}]" in line or f" {level} " in line:
                    line_level = level
                    break
            line_idx = level_order.index(line_level) if line_level in level_order else 0
            if line_idx < filter_idx:
                return

        # 최대 라인 수 초과 시 상단 제거
        self._line_count += 1
        if self._line_count > self.MAX_LINES:
            cursor = self._text.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(
                cursor.MoveOperation.Down,
                cursor.MoveMode.KeepAnchor,
                self._line_count - self.MAX_LINES,
            )
            cursor.removeSelectedText()
            self._line_count = self.MAX_LINES

        # 색상 결정
        color = QColor("#CCCCCC")
        for level, c in _LEVEL_COLORS.items():
            if f"[{level}]" in line or f" {level} " in line:
                color = c
                break

        fmt = QTextCharFormat()
        fmt.setForeground(color)

        cursor = self._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)

        # 라인 카운트 표시
        self._line_count_label.setText(f"{self._line_count:,} / {self.MAX_LINES:,}")

        if self._autoscroll:
            scrollbar = self._text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def clear(self) -> None:
        self._text.clear()
        self._line_count = 0
        self._line_count_label.setText(f"0 / {self.MAX_LINES:,}")

    def _toggle_autoscroll(self) -> None:
        self._autoscroll = not self._autoscroll
        state = "ON" if self._autoscroll else "OFF"
        self._autoscroll_btn.setText(f"자동 스크롤: {state}")

    def focus_search(self) -> None:
        """검색 입력창에 포커스 (Ctrl+F에서 호출)"""
        self._search_input.setFocus()
        self._search_input.selectAll()

    def _search_next(self) -> None:
        """다음 검색 결과로 이동"""
        text = self._search_input.text()
        if not text:
            return
        found = self._text.find(text)
        if not found:
            # 처음부터 재검색
            cursor = self._text.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self._text.setTextCursor(cursor)
            self._text.find(text)

    def _search_prev(self) -> None:
        """이전 검색 결과로 이동"""
        from PyQt6.QtGui import QTextDocument

        text = self._search_input.text()
        if not text:
            return
        found = self._text.find(text, QTextDocument.FindFlag.FindBackward)
        if not found:
            # 끝에서부터 재검색
            cursor = self._text.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self._text.setTextCursor(cursor)
            self._text.find(text, QTextDocument.FindFlag.FindBackward)
