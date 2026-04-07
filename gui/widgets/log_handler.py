# gui/widgets/log_handler.py
"""Thread-safe logging handler → Qt signal 라우팅"""

import logging
import re
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

# 로그 라인에서 레벨과 로거 이름을 추출하는 패턴
# 형식: 2024-01-15 14:30:45 [INFO] trading.order: message
_LOG_LINE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+"
    r"\[(?P<level>\w+)\]\s+"
    r"(?P<name>[\w.]+):\s+"
)

# 거래 탭에 라우팅할 로거 이름 접두사
_TRADING_PREFIXES = (
    "trading.",
    "strategy.rebalancer",
)

# 시스템 탭에 라우팅할 로거 이름 접두사 (거래 제외 나머지 전부)
_SYSTEM_PREFIXES = (
    "scheduler.",
    "data.",
    "config.",
    "monitor.",
    "strategy.",
    "factors.",
    "notify.",
    "backtest.",
    "gui.",
)

# WARNING 이상 레벨
_ERROR_LEVELS = {"WARNING", "ERROR", "CRITICAL"}


def classify_log_line(line: str) -> tuple[str, bool]:
    """로그 라인을 분류한다.

    Args:
        line: 포맷된 로그 라인

    Returns:
        (카테고리, is_error) 튜플.
        카테고리: "trading" | "system"
        is_error: WARNING 이상이면 True
    """
    m = _LOG_LINE_RE.match(line)
    if not m:
        # 파싱 불가 라인 (멀티라인 traceback, [GUI] 태그 등)
        # 거래 관련 키워드가 있으면 trading, 아니면 system
        trading_keywords = ("주문", "매수", "매도", "체결", "리밸런싱", "order", "trade", "rebalanc")
        if any(kw in line.lower() for kw in trading_keywords):
            is_err = "[ERROR]" in line or "[WARNING]" in line or "[CRITICAL]" in line
            return "trading", is_err
        is_err = "[ERROR]" in line or "[WARNING]" in line or "[CRITICAL]" in line
        return "system", is_err

    level = m.group("level")
    name = m.group("name")
    is_error = level in _ERROR_LEVELS

    for prefix in _TRADING_PREFIXES:
        if name.startswith(prefix) or name == prefix.rstrip("."):
            return "trading", is_error

    return "system", is_error


class QtLogSignalBridge(QObject):
    """로그 라인을 카테고리별 pyqtSignal로 발행하는 브릿지"""

    trading_log = pyqtSignal(str)   # 거래 탭
    system_log = pyqtSignal(str)    # 시스템 탭
    error_log = pyqtSignal(str)     # 에러 탭 (WARNING+)

    def dispatch(self, line: str) -> None:
        """로그 라인을 분류 후 적절한 시그널로 emit"""
        category, is_error = classify_log_line(line)

        if category == "trading":
            self.trading_log.emit(line)
        else:
            self.system_log.emit(line)

        if is_error:
            self.error_log.emit(line)


class QtLogHandler(logging.Handler):
    """Python logging.Handler → QtLogSignalBridge.

    GUI 프로세스 내부에서 발생하는 로그(백테스트 스레드 등)를
    thread-safe하게 Qt 시그널로 전달한다.
    """

    def __init__(
        self, bridge: QtLogSignalBridge, level: int = logging.NOTSET
    ) -> None:
        super().__init__(level)
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            self._bridge.dispatch(line)
        except Exception:
            self.handleError(record)
