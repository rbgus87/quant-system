# gui/widgets/scheduler_panel.py
"""스케줄러 제어 패널 — 시작/중지/즉시 실행"""

import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtCore import QProcess, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class SchedulerPanel(QWidget):
    """스케줄러 프로세스 관리 위젯"""

    status_changed = pyqtSignal(bool)  # True=running, False=stopped
    log_output = pyqtSignal(str)  # 스케줄러 stdout/stderr

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._setup_ui()
        self._update_buttons()

        # 상태 폴링 타이머
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_status)
        self._timer.start(2000)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("스케줄러")
        group_layout = QVBoxLayout(group)

        # 상태 표시
        status_row = QHBoxLayout()
        self._status_icon = QLabel()
        self._status_label = QLabel("중지됨")
        self._status_label.setStyleSheet("font-weight: bold;")
        status_row.addWidget(self._status_icon)
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        group_layout.addLayout(status_row)

        # 다음 실행 시간
        self._next_run_label = QLabel("")
        self._next_run_label.setStyleSheet("color: gray; font-size: 11px;")
        group_layout.addWidget(self._next_run_label)

        # 버튼 행
        btn_row = QHBoxLayout()

        self._start_btn = QPushButton("시작")
        self._start_btn.setToolTip("스케줄러 상주 프로세스 시작 (Ctrl+R)")
        self._start_btn.clicked.connect(self.start_scheduler)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("중지")
        self._stop_btn.setToolTip("스케줄러 프로세스 중지 (Ctrl+Q)")
        self._stop_btn.clicked.connect(self.stop_scheduler)
        btn_row.addWidget(self._stop_btn)

        self._now_btn = QPushButton("즉시 실행")
        self._now_btn.setToolTip("월말 체크 무시하고 즉시 리밸런싱 1회 실행")
        self._now_btn.clicked.connect(self._run_now)
        btn_row.addWidget(self._now_btn)

        group_layout.addLayout(btn_row)

        # 추가 버튼 행
        btn_row2 = QHBoxLayout()

        self._dryrun_btn = QPushButton("연결 테스트")
        self._dryrun_btn.setToolTip("API 연결 + 토큰 + 텔레그램 확인")
        self._dryrun_btn.clicked.connect(self._run_dryrun)
        btn_row2.addWidget(self._dryrun_btn)

        self._screen_btn = QPushButton("스크리닝만")
        self._screen_btn.setToolTip("종목 선정만 실행 (매매 없음)")
        self._screen_btn.clicked.connect(self._run_screen_only)
        btn_row2.addWidget(self._screen_btn)

        group_layout.addLayout(btn_row2)

        layout.addWidget(group)

    def _python_path(self) -> str:
        """Python 인터프리터 경로 (PyInstaller exe에서는 sys.executable이 exe 자체이므로 탐색)"""
        exe = sys.executable
        # PyInstaller로 빌드된 경우 sys.executable이 .exe를 가리킴
        if getattr(sys, "frozen", False):
            import shutil
            python = shutil.which("python") or shutil.which("python3")
            if python:
                return python
            # venv 또는 시스템 Python 탐색
            for candidate in ["python.exe", "python3.exe"]:
                path = os.path.join(os.path.dirname(exe), candidate)
                if os.path.exists(path):
                    return path
        return exe

    def _scheduler_script(self) -> str:
        """scheduler/main.py 절대 경로"""
        return os.path.join(os.getcwd(), "scheduler", "main.py")

    def _create_process(self) -> QProcess:
        """QProcess 생성 및 시그널 연결"""
        proc = QProcess(self)
        proc.setWorkingDirectory(os.getcwd())

        # Windows에서 자식 프로세스 UTF-8 출력 강제
        env = proc.processEnvironment()
        if env.isEmpty():
            from PyQt6.QtCore import QProcessEnvironment
            env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONLEGACYWINDOWSSTDIO", "0")
        proc.setProcessEnvironment(env)

        proc.readyReadStandardOutput.connect(lambda: self._read_output(proc))
        proc.readyReadStandardError.connect(lambda: self._read_error(proc))
        proc.finished.connect(self._on_process_finished)
        return proc

    def _read_output(self, proc: QProcess) -> None:
        data = proc.readAllStandardOutput().data().decode("utf-8", errors="replace")
        for line in data.strip().splitlines():
            self.log_output.emit(line)

    def _read_error(self, proc: QProcess) -> None:
        data = proc.readAllStandardError().data().decode("utf-8", errors="replace")
        for line in data.strip().splitlines():
            self.log_output.emit(line)

    def _on_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if self._process and self._process.property("mode") == "scheduler":
            self._process = None
            self._update_buttons()
            self.status_changed.emit(False)
            logger.info(f"스케줄러 종료 (code={exit_code})")

    def is_running(self) -> bool:
        return self._process is not None and self._process.state() == QProcess.ProcessState.Running

    def start_scheduler(self) -> None:
        """스케줄러 시작 (상주 모드)"""
        if self.is_running():
            return

        self._process = self._create_process()
        self._process.setProperty("mode", "scheduler")
        self._process.start(self._python_path(), [self._scheduler_script()])
        self._update_buttons()
        self.status_changed.emit(True)
        self.log_output.emit("[GUI] 스케줄러 시작")
        logger.info("스케줄러 프로세스 시작")

    def stop_scheduler(self) -> None:
        """스케줄러 중지"""
        if not self.is_running():
            return

        self._process.terminate()
        if not self._process.waitForFinished(5000):
            self._process.kill()
        self._process = None
        self._update_buttons()
        self.status_changed.emit(False)
        self.log_output.emit("[GUI] 스케줄러 중지")
        logger.info("스케줄러 프로세스 중지")

        # Windows에서 terminate()는 cleanup 없이 즉시 종료되므로
        # GUI에서 직접 텔레그램 종료 알림 발송
        try:
            from notify.telegram import TelegramNotifier
            TelegramNotifier().send("퀀트 스케줄러가 종료되었습니다.")
        except Exception as e:
            logger.debug(f"종료 알림 발송 실패: {e}")

    def _run_now(self) -> None:
        """즉시 리밸런싱 (1회 실행)"""
        self._run_oneshot(self._now_btn, "즉시 실행", "--now", "[GUI] 즉시 리밸런싱 실행")

    def _run_dryrun(self) -> None:
        """연결 테스트"""
        self._run_oneshot(self._dryrun_btn, "연결 테스트", "--dry-run", "[GUI] 연결 테스트 실행")

    def _run_screen_only(self) -> None:
        """스크리닝만 실행"""
        self._run_oneshot(self._screen_btn, "스크리닝만", "--screen-only", "[GUI] 스크리닝만 실행")

    def _run_oneshot(self, btn: QPushButton, label: str, flag: str, log_msg: str) -> None:
        """원샷 프로세스 실행 (버튼 상태 관리 포함)"""
        original_text = btn.text()
        btn.setEnabled(False)
        btn.setText(f"{label} 중...")

        proc = self._create_process()
        proc.setProperty("mode", "oneshot")
        proc.finished.connect(lambda: self._reset_oneshot_btn(btn, original_text))
        proc.start(self._python_path(), [self._scheduler_script(), flag])
        self.log_output.emit(log_msg)

    def _reset_oneshot_btn(self, btn: QPushButton, text: str) -> None:
        """원샷 프로세스 완료 시 버튼 복원"""
        btn.setEnabled(True)
        btn.setText(text)

    def _update_buttons(self) -> None:
        running = self.is_running()
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._now_btn.setEnabled(not running)
        self._dryrun_btn.setEnabled(not running)
        self._screen_btn.setEnabled(not running)

        if running:
            self._status_label.setText("실행 중")
            self._status_label.setStyleSheet("font-weight: bold; color: green;")
            self._next_run_label.setText(self._build_schedule_info())
        else:
            self._status_label.setText("중지됨")
            self._status_label.setStyleSheet("font-weight: bold; color: gray;")
            # 중지 상태에서도 다음 리밸런싱 정보 표시
            self._next_run_label.setText(self._build_schedule_info())

    def _check_status(self) -> None:
        """프로세스 상태 주기적 확인"""
        self._update_buttons()

    def _build_schedule_info(self) -> str:
        """리밸런싱 빈도 + 다음 예정일 + 모드 정보 문자열"""
        try:
            from config.settings import settings

            freq = settings.portfolio.rebalance_frequency
            is_paper = os.getenv("IS_PAPER_TRADING", "true").lower() == "true"
            mode = "모의투자" if is_paper else "실전투자"

            if freq == "quarterly":
                freq_desc = "분기 리밸런싱 (3/6/9/12월)"
            else:
                freq_desc = "월간 리밸런싱 (매월)"

            next_date = self._calc_next_rebalance_date(freq)
            next_str = next_date if next_date else "계산 불가"
            return f"[{mode}] {freq_desc} | 다음: {next_str}"
        except Exception:
            return ""

    @staticmethod
    def _calc_next_rebalance_date(freq: str) -> str:
        """다음 리밸런싱 예정일 계산"""
        try:
            from config.calendar import get_krx_month_end_sessions
            from config.settings import settings

            now = datetime.now()
            # 앞으로 6개월 범위에서 월말 영업일 탐색
            end = now + timedelta(days=200)
            month_ends = get_krx_month_end_sessions(
                now.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
            )
            for dt in month_ends:
                if dt.date() < now.date():
                    continue
                if freq == "quarterly" and dt.month not in (3, 6, 9, 12):
                    continue
                weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][dt.weekday()]
                reb_time = settings.portfolio.rebalance_time
                return f"{dt.strftime('%Y-%m-%d')} ({weekday_kr}) {reb_time}"
        except Exception:
            pass
        return ""

    def cleanup(self) -> None:
        """앱 종료 시 프로세스 정리"""
        if self.is_running():
            self._process.terminate()
            self._process.waitForFinished(3000)
