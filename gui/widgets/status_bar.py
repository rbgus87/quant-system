# gui/widgets/status_bar.py
"""하단 상태 바 — 연결 상태, 시간 표시"""

import logging
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QStatusBar, QWidget

logger = logging.getLogger(__name__)


class StatusBarWidget(QWidget):
    """커스텀 상태 바 위젯"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._setup_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_time)
        self._timer.start(1000)

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)

        self._scheduler_status = QLabel("스케줄러: 중지")
        layout.addWidget(self._scheduler_status)

        layout.addStretch()

        self._time_label = QLabel("")
        layout.addWidget(self._time_label)

    def set_scheduler_status(self, running: bool) -> None:
        if running:
            self._scheduler_status.setText("스케줄러: 실행 중")
            self._scheduler_status.setStyleSheet("color: green; font-weight: bold;")
        else:
            self._scheduler_status.setText("스케줄러: 중지")
            self._scheduler_status.setStyleSheet("color: gray;")

    def _update_time(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._time_label.setText(now)
