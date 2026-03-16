# gui/tray_icon.py
"""시스템 트레이 아이콘 — 최소화 시 트레이로 이동"""

import logging
from typing import Optional

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

logger = logging.getLogger(__name__)


class TrayIcon(QSystemTrayIcon):
    """시스템 트레이 아이콘"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._parent = parent
        self._setup()

    def _setup(self) -> None:
        # 아이콘 (기본 정보 아이콘 사용)
        self.setIcon(QIcon.fromTheme("applications-system", QIcon()))
        self.setToolTip("Korean Quant System")

        # 컨텍스트 메뉴
        menu = QMenu()

        show_action = QAction("열기", self)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        menu.addSeparator()

        quit_action = QAction("종료", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        if self._parent:
            self._parent.show()
            self._parent.activateWindow()

    def _quit(self) -> None:
        if self._parent:
            self._parent.force_quit = True
            self._parent.close()
