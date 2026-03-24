# gui/themes.py
"""다크/라이트 테마 스타일시트"""


def _common_styles(bg: str, fg: str, bg2: str, border: str, accent: str,
                   header_bg: str, select_bg: str, input_bg: str,
                   alt_bg: str = "") -> str:
    """공통 스타일 템플릿"""
    return f"""
QMainWindow {{
    background-color: {bg};
    color: {fg};
}}
QWidget {{
    color: {fg};
}}
QGroupBox {{
    border: 1px solid {border};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 14px;
    font-weight: bold;
    color: {fg};
    background-color: {bg2};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {fg};
}}
QLabel {{
    color: {fg};
}}
QCheckBox {{
    color: {fg};
}}
QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: 4px;
    background: {bg2};
}}
QTabBar::tab {{
    padding: 6px 16px;
    margin-right: 2px;
    border: 1px solid {border};
    border-bottom: none;
    border-radius: 4px 4px 0 0;
    background: {header_bg};
    color: {fg};
}}
QTabBar::tab:selected {{
    background: {bg2};
    color: {fg};
    font-weight: bold;
}}
QPushButton {{
    padding: 5px 14px;
    border: 1px solid {border};
    border-radius: 4px;
    background: {input_bg};
    color: {fg};
}}
QPushButton:hover {{
    background: {header_bg};
}}
QPushButton:pressed {{
    background: {border};
}}
QPushButton:disabled {{
    color: {border};
    background: {bg};
}}
QPushButton#startBtn {{
    background: {input_bg};
    color: #40C057;
    border: 2px solid #40C057;
    font-weight: bold;
}}
QPushButton#startBtn:hover {{ background: #40C057; color: white; }}
QPushButton#startBtn:disabled {{ color: {border}; border-color: {border}; }}
QPushButton#stopBtn {{
    background: {input_bg};
    color: #FA5252;
    border: 2px solid #FA5252;
    font-weight: bold;
}}
QPushButton#stopBtn:hover {{ background: #FA5252; color: white; }}
QPushButton#stopBtn:disabled {{ color: {border}; border-color: {border}; }}
QTableWidget {{
    border: 1px solid {border};
    gridline-color: {border};
    background: {bg2};
    color: {fg};
    selection-background-color: {select_bg};
    alternate-background-color: {alt_bg};
}}
QTableWidget::item {{
    padding: 3px 6px;
}}
QHeaderView::section {{
    background: {header_bg};
    border: none;
    border-bottom: 2px solid {border};
    padding: 5px 8px;
    font-weight: bold;
    color: {fg};
}}
QComboBox {{
    padding: 4px 8px;
    border: 1px solid {border};
    border-radius: 4px;
    background: {input_bg};
    color: {fg};
}}
QComboBox QAbstractItemView {{
    background: {input_bg};
    color: {fg};
    selection-background-color: {select_bg};
}}
QSpinBox {{
    padding: 4px 8px;
    border: 1px solid {border};
    border-radius: 4px;
    background: {input_bg};
    color: {fg};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    border: none;
    background: {header_bg};
    width: 16px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {border};
}}
QSlider::groove:horizontal {{
    border: 1px solid {border};
    height: 6px;
    background: {input_bg};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {accent};
    border: none;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    background: #74C0FC;
}}
QSlider::sub-page:horizontal {{
    background: {accent};
    border-radius: 3px;
}}
QLineEdit {{
    padding: 4px 8px;
    border: 1px solid {border};
    border-radius: 4px;
    background: {input_bg};
    color: {fg};
}}
QStatusBar {{
    background: {header_bg};
    border-top: 1px solid {border};
    color: {fg};
}}
QProgressBar {{
    border: 1px solid {border};
    border-radius: 3px;
    background: {bg};
    color: {fg};
    text-align: center;
}}
QProgressBar::chunk {{
    background: {accent};
    border-radius: 3px;
}}
QSplitter::handle {{
    background: {border};
    height: 3px;
}}
QScrollBar:vertical {{
    background: {bg};
    width: 10px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {border};
    border-radius: 5px;
    min-height: 20px;
}}
"""


def light_theme() -> str:
    return _common_styles(
        bg="#F8F9FA",
        fg="#212529",
        bg2="#FFFFFF",
        border="#DEE2E6",
        accent="#4DABF7",
        header_bg="#F1F3F5",
        select_bg="#D0EBFF",
        input_bg="#FFFFFF",
        alt_bg="#F1F3F5",
    )


def dark_theme() -> str:
    return _common_styles(
        bg="#1A1B1E",
        fg="#C1C2C5",
        bg2="#25262B",
        border="#373A40",
        accent="#4DABF7",
        header_bg="#2C2E33",
        select_bg="#1C3A5C",
        input_bg="#2C2E33",
        alt_bg="#2C2E33",
    )
