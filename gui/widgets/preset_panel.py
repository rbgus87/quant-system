# gui/widgets/preset_panel.py
"""프리셋 선택 패널 — config.yaml의 preset/sizing을 GUI에서 변경"""

import logging
from pathlib import Path
from typing import Optional

import yaml
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

STRATEGY_PRESETS = {
    "A": "균형 (40-40-20)",
    "B": "순수 밸류 (70-10-20)",
    "C": "모멘텀 중심 (15-70-15)",
    "D": "고배당 방어 (50-10-40)",
    "E": "소형 밸류 (60-30-10)",
    "F": "올시즌 방어 (50-20-30)",
    "G": "KOSDAQ 성장 (25-50-25)",
    "H": "최소 변동성 (40-20-40)",
    "I": "KOSDAQ 밸류 (60-15-25)",
}

SIZING_PRESETS = ["100만", "500만", "1000만", "3000만", "5000만", "1억", "5억"]


class PresetPanel(QWidget):
    """프리셋 선택 및 적용 위젯"""

    preset_changed = pyqtSignal(str, str)  # (preset, sizing)

    def __init__(self, config_path: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config_path = Path(config_path or "config/config.yaml")
        self._setup_ui()
        self._load_current()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("전략 설정")
        group_layout = QVBoxLayout(group)

        # 전략 프리셋
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("전략 프리셋:"))
        self._strategy_combo = QComboBox()
        for key, desc in STRATEGY_PRESETS.items():
            self._strategy_combo.addItem(f"{key}: {desc}", key)
        row1.addWidget(self._strategy_combo, 1)
        group_layout.addLayout(row1)

        # 금액 프리셋
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("투자 금액:"))
        self._sizing_combo = QComboBox()
        for s in SIZING_PRESETS:
            self._sizing_combo.addItem(s, s)
        row2.addWidget(self._sizing_combo, 1)
        group_layout.addLayout(row2)

        # 적용 버튼
        row3 = QHBoxLayout()
        row3.addStretch()
        self._apply_btn = QPushButton("적용")
        self._apply_btn.clicked.connect(self._apply)
        row3.addWidget(self._apply_btn)
        group_layout.addLayout(row3)

        # 현재 적용 상태
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: gray; font-size: 11px;")
        group_layout.addWidget(self._status_label)

        layout.addWidget(group)

    def _load_current(self) -> None:
        """config.yaml에서 현재 프리셋 읽기"""
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            preset = data.get("preset", "B")
            sizing = data.get("sizing", "3000만")

            idx = self._strategy_combo.findData(preset)
            if idx >= 0:
                self._strategy_combo.setCurrentIndex(idx)

            idx = self._sizing_combo.findData(sizing)
            if idx >= 0:
                self._sizing_combo.setCurrentIndex(idx)

            self._status_label.setText(f"현재: {preset} / {sizing}")
        except Exception as e:
            logger.warning(f"config.yaml 로드 실패: {e}")
            self._status_label.setText("config.yaml 로드 실패")

    def _apply(self) -> None:
        """선택한 프리셋을 config.yaml에 저장"""
        preset = self._strategy_combo.currentData()
        sizing = self._sizing_combo.currentData()

        try:
            with open(self._config_path, encoding="utf-8") as f:
                content = f.read()

            # YAML 파싱 후 preset/sizing만 변경 (주석 보존을 위해 텍스트 치환)
            import re

            content = re.sub(
                r'^preset:\s*".*"',
                f'preset: "{preset}"',
                content,
                flags=re.MULTILINE,
            )
            content = re.sub(
                r'^sizing:\s*".*"',
                f'sizing: "{sizing}"',
                content,
                flags=re.MULTILINE,
            )

            with open(self._config_path, "w", encoding="utf-8") as f:
                f.write(content)

            self._status_label.setText(f"적용됨: {preset} / {sizing}")
            self._status_label.setStyleSheet("color: green; font-size: 11px;")
            self.preset_changed.emit(preset, sizing)
            logger.info(f"프리셋 변경: {preset} / {sizing}")
        except Exception as e:
            self._status_label.setText(f"저장 실패: {e}")
            self._status_label.setStyleSheet("color: red; font-size: 11px;")
            logger.error(f"config.yaml 저장 실패: {e}")

    def current_preset(self) -> str:
        return self._strategy_combo.currentData()

    def current_sizing(self) -> str:
        return self._sizing_combo.currentData()
