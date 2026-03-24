# gui/widgets/status_bar.py
"""하단 상태 바 — 연결 상태, 장 운영시간, 모의/실전 표시"""

import logging
import os
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
        self._update_time()  # 즉시 업데이트

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)

        self._scheduler_status = QLabel("스케줄러: 중지")
        layout.addWidget(self._scheduler_status)

        self._market_label = QLabel("")
        self._market_label.setStyleSheet("color: gray;")
        layout.addWidget(self._market_label)

        layout.addStretch()

        self._mode_label = QLabel("")
        layout.addWidget(self._mode_label)
        self._update_trading_mode()

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
        now = datetime.now()
        self._time_label.setText(now.strftime("%Y-%m-%d %H:%M:%S"))
        self._update_market_status(now)

    def _update_market_status(self, now: datetime) -> None:
        """장 운영시간 표시 (평일 09:00~15:30)"""
        if now.weekday() >= 5:  # 토, 일
            self._market_label.setText("휴장")
            self._market_label.setStyleSheet("color: gray;")
            return

        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

        if market_open <= now <= market_close:
            self._market_label.setText("장 운영 중")
            self._market_label.setStyleSheet("color: #40C057; font-weight: bold;")
        elif now < market_open:
            mins_left = int((market_open - now).total_seconds() / 60)
            self._market_label.setText(f"개장까지 {mins_left}분")
            self._market_label.setStyleSheet("color: #FFA500;")
        else:
            self._market_label.setText("장 마감")
            self._market_label.setStyleSheet("color: gray;")

    def _update_trading_mode(self) -> None:
        """모의/실전 투자 모드 표시"""
        is_paper = os.getenv("IS_PAPER_TRADING", "true").lower() == "true"
        if is_paper:
            self._mode_label.setText("[모의투자]")
            self._mode_label.setStyleSheet("color: #4DABF7; font-weight: bold;")
        else:
            self._mode_label.setText("[실전투자]")
            self._mode_label.setStyleSheet("color: #FF6B6B; font-weight: bold;")
