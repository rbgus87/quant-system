# gui/widgets/backtest_runner.py
"""백테스트 실행기 — 기간/프리셋 선택 후 백그라운드 실행 + 진행률"""

import logging
import os
import sys
from typing import Optional

from PyQt6.QtCore import QDate, QProcess
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
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
        self._report_path: Optional[str] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        group = QGroupBox("백테스트")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(6)

        # 기간 설정
        period_row = QHBoxLayout()
        period_row.addWidget(QLabel("시작:"))
        self._start_edit = QDateEdit()
        self._start_edit.setCalendarPopup(True)
        self._start_edit.setDate(QDate(2020, 1, 1))
        self._start_edit.setDisplayFormat("yyyy-MM-dd")
        self._start_edit.setMaximumWidth(140)
        period_row.addWidget(self._start_edit)

        period_row.addWidget(QLabel("종료:"))
        self._end_edit = QDateEdit()
        self._end_edit.setCalendarPopup(True)
        self._end_edit.setDate(QDate.currentDate())
        self._end_edit.setDisplayFormat("yyyy-MM-dd")
        self._end_edit.setMaximumWidth(140)
        period_row.addWidget(self._end_edit)

        period_row.addWidget(QLabel("초기자본:"))
        self._cash_edit = QLineEdit("10,000,000")
        self._cash_edit.setMaximumWidth(120)
        self._cash_edit.textChanged.connect(self._format_cash)
        period_row.addWidget(self._cash_edit)

        period_row.addStretch()

        self._run_btn = QPushButton("실행")
        self._run_btn.clicked.connect(self._run_backtest)
        period_row.addWidget(self._run_btn)

        self._stop_btn = QPushButton("중지")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_backtest)
        period_row.addWidget(self._stop_btn)

        self._open_report_btn = QPushButton("리포트 열기")
        self._open_report_btn.setEnabled(False)
        self._open_report_btn.setToolTip("백테스트 완료 후 HTML 리포트를 브라우저에서 엽니다")
        self._open_report_btn.clicked.connect(self._open_report)
        period_row.addWidget(self._open_report_btn)

        self._status_label = QLabel("")
        period_row.addWidget(self._status_label)

        group_layout.addLayout(period_row)

        # 진행률
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        group_layout.addWidget(self._progress)

        # 출력 영역 (백테스트 로그)
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("백테스트 실행 결과가 여기에 표시됩니다")
        from PyQt6.QtGui import QFont
        self._output.setFont(QFont("Consolas", 9))
        group_layout.addWidget(self._output, 1)  # stretch=1로 나머지 공간 채움

        layout.addWidget(group)

    def _run_backtest(self) -> None:
        """백테스트 프로세스 시작"""
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            return

        start = self._start_edit.date().toString("yyyy-MM-dd")
        end = self._end_edit.date().toString("yyyy-MM-dd")
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

        # PyInstaller exe에서는 sys.executable이 exe이므로 Python 탐색
        python = sys.executable
        if getattr(sys, "frozen", False):
            import shutil
            python = shutil.which("python") or shutil.which("python3") or python

        script = os.path.join(os.getcwd(), "run_backtest.py")
        args = [
            script,
            "--mode", "custom",
            "--start", start,
            "--end", end,
            "--cash", cash,
            "--auto-lead",
        ]
        self._process.start(python, args)

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
            # 리포트 경로 탐색
            self._report_path = self._find_report_path()
            if self._report_path:
                self._open_report_btn.setEnabled(True)
        else:
            self._status_label.setText(f"실패 (code={exit_code})")
            self._status_label.setStyleSheet("color: red; font-weight: bold;")

    def _find_report_path(self) -> Optional[str]:
        """출력 라인에서 HTML 리포트 경로 추출"""
        import re
        for line in reversed(self._output_lines):
            # reports/ 디렉토리의 .html 파일 매칭
            match = re.search(r'((?:reports[/\\]|[A-Za-z]:[/\\])[\w\-/\\.]+\.html)', line)
            if match:
                path = match.group(1)
                full_path = os.path.join(os.getcwd(), path) if not os.path.isabs(path) else path
                if os.path.exists(full_path):
                    return full_path
        # 폴백: reports/ 디렉토리에서 최신 HTML 찾기
        reports_dir = os.path.join(os.getcwd(), "reports")
        if os.path.isdir(reports_dir):
            html_files = [
                os.path.join(reports_dir, f)
                for f in os.listdir(reports_dir)
                if f.endswith(".html")
            ]
            if html_files:
                return max(html_files, key=os.path.getmtime)
        return None

    def _open_report(self) -> None:
        """HTML 리포트를 브라우저에서 열기"""
        if self._report_path and os.path.exists(self._report_path):
            import webbrowser
            webbrowser.open(f"file:///{self._report_path}")

    def _format_cash(self) -> None:
        """초기자본 천 단위 콤마 포맷"""
        text = self._cash_edit.text().replace(",", "")
        if text.isdigit() and text:
            formatted = f"{int(text):,}"
            if formatted != self._cash_edit.text():
                self._cash_edit.blockSignals(True)
                cursor_pos = self._cash_edit.cursorPosition()
                old_len = len(self._cash_edit.text())
                self._cash_edit.setText(formatted)
                # 커서 위치 보정
                new_len = len(formatted)
                self._cash_edit.setCursorPosition(cursor_pos + (new_len - old_len))
                self._cash_edit.blockSignals(False)
