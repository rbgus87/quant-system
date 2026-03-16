# gui/widgets/log_viewer.py
"""실시간 로그 뷰어 — 스케줄러 출력 + 로그 파일 표시"""

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextCharFormat
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
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

        if self._autoscroll:
            scrollbar = self._text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def clear(self) -> None:
        self._text.clear()
        self._line_count = 0

    def _toggle_autoscroll(self) -> None:
        self._autoscroll = not self._autoscroll
        state = "ON" if self._autoscroll else "OFF"
        self._autoscroll_btn.setText(f"자동 스크롤: {state}")
