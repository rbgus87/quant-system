# gui/app.py
"""PyQt6 GUI 엔트리포인트

실행: python -m gui.app
"""

import os
import sys

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from config.logging_config import setup_logging
from gui.main_window import MainWindow


def main() -> None:
    # PyInstaller --onefile에서 자식 프로세스가 exe 재실행하는 것 방지
    import multiprocessing
    multiprocessing.freeze_support()

    setup_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("Korean Quant System")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
