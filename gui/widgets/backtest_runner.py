# gui/widgets/backtest_runner.py
"""백테스트 실행기 — 기간/프리셋 선택 후 백그라운드 실행 + 진행률"""

import logging
import os
import sys
from typing import Optional

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class BacktestRunner(QWidget):
    """백테스트 실행 + 결과 표시"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._output_lines: list[str] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("백테스트")
        group_layout = QVBoxLayout(group)

        # 설정 + 버튼 (한 행에 배치)
        from PyQt6.QtWidgets import QFormLayout

        settings_row = QHBoxLayout()

        # 왼쪽: 입력 필드
        form = QFormLayout()
        form.setSpacing(6)
        self._start_edit = QLineEdit("2020-01-01")
        self._start_edit.setFixedWidth(110)
        form.addRow("시작일:", self._start_edit)

        self._end_edit = QLineEdit("2025-12-31")
        self._end_edit.setFixedWidth(110)
        form.addRow("종료일:", self._end_edit)

        self._cash_edit = QLineEdit("10,000,000")
        self._cash_edit.setFixedWidth(110)
        form.addRow("초기자본:", self._cash_edit)

        settings_row.addLayout(form)

        # 오른쪽: 버튼 + 상태
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)

        self._run_btn = QPushButton("백테스트 실행")
        self._run_btn.setObjectName("startBtn")
        self._run_btn.setMinimumHeight(36)
        self._run_btn.clicked.connect(self._run_backtest)
        btn_col.addWidget(self._run_btn)

        self._stop_btn = QPushButton("중지")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_backtest)
        btn_col.addWidget(self._stop_btn)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-weight: bold;")
        btn_col.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setFixedHeight(6)
        btn_col.addWidget(self._progress)

        btn_col.addStretch()
        settings_row.addLayout(btn_col)
        settings_row.addStretch()
        group_layout.addLayout(settings_row)

        # 출력 영역
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        from PyQt6.QtGui import QFont
        self._output.setFont(QFont("Consolas", 9))
        self._output.setStyleSheet(
            "QTextEdit { background-color: #1E1E1E; color: #CCCCCC; }"
        )
        group_layout.addWidget(self._output)

        layout.addWidget(group)

    def _run_backtest(self) -> None:
        """백테스트 프로세스 시작"""
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            return

        start = self._start_edit.text().strip()
        end = self._end_edit.text().strip()
        cash = self._cash_edit.text().strip().replace(",", "")

        self._output.clear()
        self._output_lines.clear()
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_label.setText("실행 중...")

        self._process = QProcess(self)
        self._process.setWorkingDirectory(os.getcwd())

        env = self._process.processEnvironment()
        if env.isEmpty():
            from PyQt6.QtCore import QProcessEnvironment
            env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self._process.setProcessEnvironment(env)

        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.readyReadStandardError.connect(self._read_error)
        self._process.finished.connect(self._on_finished)

        args = [
            "-m", "backtest.engine",
            "--start", start,
            "--end", end,
            "--cash", cash,
        ]
        self._process.start(sys.executable, args)

    def _stop_backtest(self) -> None:
        if self._process:
            self._process.terminate()
            if not self._process.waitForFinished(3000):
                self._process.kill()

    def _read_output(self) -> None:
        data = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        for line in data.strip().splitlines():
            self._output.append(line)
            self._output_lines.append(line)

    def _read_error(self) -> None:
        data = self._process.readAllStandardError().data().decode("utf-8", errors="replace")
        for line in data.strip().splitlines():
            self._output.append(f"<span style='color: #FF6666;'>{line}</span>")
            self._output_lines.append(line)

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

        if exit_code == 0:
            self._status_label.setText("완료")
            self._status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self._status_label.setText(f"실패 (code={exit_code})")
            self._status_label.setStyleSheet("color: red; font-weight: bold;")
