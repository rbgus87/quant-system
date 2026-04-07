# gui/app.py
"""PyQt6 GUI 엔트리포인트

실행: python -m gui.app
"""

import os
import sys

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from config.font import setup_matplotlib_korean_font
from config.logging_config import setup_logging
from gui.main_window import MainWindow


def main() -> None:
    # PyInstaller --onefile에서 자식 프로세스가 exe 재실행하는 것 방지
    import multiprocessing
    multiprocessing.freeze_support()

    import logging
    setup_logging()
    setup_matplotlib_korean_font()

    app = QApplication(sys.argv)
    app.setApplicationName("Korean Quant System")
    app.setStyle("Fusion")

    window = MainWindow()

    # GUI 프로세스 내부 로그를 탭별 로그 뷰어로 라우팅
    from config.logging_config import _LOG_FORMAT, _LOG_DATEFMT
    from gui.widgets.log_handler import QtLogHandler

    qt_handler = QtLogHandler(window._log_viewer.bridge)
    qt_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    logging.getLogger().addHandler(qt_handler)

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
