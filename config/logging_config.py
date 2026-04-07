# config/logging_config.py
import logging
import logging.handlers
import os
from config.settings import settings

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    """프로젝트 전역 로깅 설정"""
    log_dir = os.path.dirname(settings.log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format=_LOG_FORMAT,
        datefmt=_LOG_DATEFMT,
        handlers=[
            # 콘솔 출력
            logging.StreamHandler(),
            # 파일 저장 (10MB × 5개 롤링)
            logging.handlers.RotatingFileHandler(
                settings.log_path,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            ),
        ],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pykrx").setLevel(logging.WARNING)

    # 거래 로그 전용 일별 파일 핸들러
    _setup_trading_file_handler(log_dir, numeric_level)


def _setup_trading_file_handler(log_dir: str, level: int) -> None:
    """거래 관련 로거에 일별 로테이션 파일 핸들러를 추가한다."""
    trading_dir = log_dir or "logs"
    os.makedirs(trading_dir, exist_ok=True)

    trading_path = os.path.join(trading_dir, "trading.log")
    retention = settings.logging.trading_log_retention_days
    handler = logging.handlers.TimedRotatingFileHandler(
        trading_path,
        when="midnight",
        backupCount=retention,
        encoding="utf-8",
    )
    handler.suffix = "%Y%m%d"
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))

    # 거래 관련 로거에만 핸들러 부착
    for name in ("trading", "strategy.rebalancer"):
        logging.getLogger(name).addHandler(handler)
