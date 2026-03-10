# config/logging_config.py
import logging
import logging.handlers
import os
from config.settings import settings


def setup_logging():
    """프로젝트 전역 로깅 설정"""
    log_dir = os.path.dirname(settings.log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
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
