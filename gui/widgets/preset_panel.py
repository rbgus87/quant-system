# gui/widgets/preset_panel.py
"""프리셋 선택 패널 — config.yaml의 preset/sizing을 GUI에서 변경"""

import logging
from pathlib import Path
from typing import Optional

import yaml
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

STRATEGY_PRESETS = {
    "A": "핵심 추천 (V70-M30, Vol70)",
    "B": "보수적 (V70-M30, Vol50)",
    "C": "공격적 (V100, Vol70)",
}

SIZING_PRESETS = ["소액", "중액", "대액", "거액"]


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
        self._sizing_combo.currentIndexChanged.connect(self._on_sizing_changed)
        row2.addWidget(self._sizing_combo, 1)
        group_layout.addLayout(row2)

        # 종목 수
        row_nstocks = QHBoxLayout()
        row_nstocks.addWidget(QLabel("종목 수:"))
        self._nstocks_slider = QSlider(Qt.Orientation.Horizontal)
        self._nstocks_slider.setRange(3, 50)
        self._nstocks_slider.setValue(15)
        self._nstocks_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._nstocks_slider.setTickInterval(5)
        row_nstocks.addWidget(self._nstocks_slider, 1)
        self._nstocks_spin = QSpinBox()
        self._nstocks_spin.setRange(3, 50)
        self._nstocks_spin.setValue(15)
        self._nstocks_spin.setSuffix("개")
        self._nstocks_spin.setFixedWidth(70)
        row_nstocks.addWidget(self._nstocks_spin)
        group_layout.addLayout(row_nstocks)

        # 슬라이더 ↔ 스핀박스 양방향 연동
        self._nstocks_slider.valueChanged.connect(self._nstocks_spin.setValue)
        self._nstocks_spin.valueChanged.connect(self._nstocks_slider.setValue)

        # 종목 수 힌트 (프리셋 기본값 / 커스텀 표시)
        self._nstocks_hint = QLabel("")
        self._nstocks_hint.setStyleSheet("color: gray; font-size: 10px; margin-left: 2px;")
        group_layout.addWidget(self._nstocks_hint)
        self._nstocks_spin.valueChanged.connect(self._update_nstocks_hint)

        # 적용 버튼
        row3 = QHBoxLayout()
        row3.addStretch()
        self._apply_btn = QPushButton("적용")
        self._apply_btn.setToolTip("변경된 전략 설정을 config.yaml에 저장")
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

            preset = data.get("preset", "A")
            sizing = data.get("sizing", "중액")

            idx = self._strategy_combo.findData(preset)
            if idx >= 0:
                self._strategy_combo.setCurrentIndex(idx)

            idx = self._sizing_combo.findData(sizing)
            if idx >= 0:
                self._sizing_combo.setCurrentIndex(idx)

            # 종목 수: 개별 오버라이드 → 금액 프리셋 → 기본값
            n_stocks = (
                data.get("portfolio", {}).get("n_stocks")
                or data.get("presets", {}).get(sizing, {}).get("portfolio", {}).get("n_stocks")
                or 15
            )
            self._nstocks_spin.setValue(n_stocks)
            self._update_nstocks_hint(n_stocks)

            self._status_label.setText(f"현재: {preset} / {sizing} / {n_stocks}종목")
        except Exception as e:
            logger.warning(f"config.yaml 로드 실패: {e}")
            self._status_label.setText("config.yaml 로드 실패")

    def _apply(self) -> None:
        """선택한 프리셋을 config.yaml에 저장"""
        import re

        preset = self._strategy_combo.currentData()
        sizing = self._sizing_combo.currentData()
        n_stocks = self._nstocks_spin.value()

        try:
            with open(self._config_path, encoding="utf-8") as f:
                content = f.read()

            # YAML 파싱 후 preset/sizing만 변경 (주석 보존을 위해 텍스트 치환)
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

            # n_stocks 개별 오버라이드 (sizing 프리셋의 기본값과 다를 때만)
            data = yaml.safe_load(content) or {}
            preset_n = (
                data.get("presets", {}).get(sizing, {}).get("portfolio", {}).get("n_stocks")
            )

            if n_stocks != preset_n:
                # 기존 portfolio 오버라이드가 있으면 n_stocks만 교체, 없으면 추가
                if re.search(r"^portfolio:\s*\n\s+n_stocks:", content, re.MULTILINE):
                    content = re.sub(
                        r"^(portfolio:\s*\n\s+n_stocks:\s*)\d+",
                        rf"\g<1>{n_stocks}",
                        content,
                        flags=re.MULTILINE,
                    )
                else:
                    # sizing 라인 바로 아래에 portfolio 오버라이드 삽입
                    content = re.sub(
                        r'^(sizing:\s*".*")',
                        rf'\1\nportfolio:\n  n_stocks: {n_stocks}',
                        content,
                        flags=re.MULTILINE,
                    )
            else:
                # 프리셋 기본값과 같으면 오버라이드 제거
                content = re.sub(
                    r"\nportfolio:\n\s+n_stocks:\s*\d+\n?",
                    "\n",
                    content,
                )

            with open(self._config_path, "w", encoding="utf-8") as f:
                f.write(content)

            self._status_label.setText(f"적용됨: {preset} / {sizing} / {n_stocks}종목")
            self._status_label.setStyleSheet("color: green; font-size: 11px;")
            self.preset_changed.emit(preset, sizing)
            logger.info(f"프리셋 변경: {preset} / {sizing} / n_stocks={n_stocks}")
        except Exception as e:
            self._status_label.setText(f"저장 실패: {e}")
            self._status_label.setStyleSheet("color: red; font-size: 11px;")
            logger.error(f"config.yaml 저장 실패: {e}")

    def _get_preset_nstocks(self, sizing: str) -> int | None:
        """금액 프리셋의 기본 종목 수를 반환"""
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("presets", {}).get(sizing, {}).get("portfolio", {}).get("n_stocks")
        except Exception:
            return None

    def _on_sizing_changed(self) -> None:
        """금액 프리셋 변경 시 해당 프리셋의 기본 종목 수로 업데이트"""
        sizing = self._sizing_combo.currentData()
        if not sizing:
            return
        n = self._get_preset_nstocks(sizing)
        if n:
            self._nstocks_spin.setValue(n)

    def _update_nstocks_hint(self, value: int) -> None:
        """종목 수 변경 시 프리셋 기본값 대비 힌트 표시"""
        sizing = self._sizing_combo.currentData()
        preset_n = self._get_preset_nstocks(sizing) if sizing else None
        if preset_n is None:
            self._nstocks_hint.setText("")
            return
        if value == preset_n:
            self._nstocks_hint.setText(f"{sizing} 기본값")
            self._nstocks_hint.setStyleSheet("color: gray; font-size: 10px;")
        else:
            self._nstocks_hint.setText(
                f"{sizing} 기본값: {preset_n}개 (커스텀)"
            )
            self._nstocks_hint.setStyleSheet("color: #4DABF7; font-size: 10px;")

    def current_preset(self) -> str:
        return self._strategy_combo.currentData()

    def current_sizing(self) -> str:
        return self._sizing_combo.currentData()
