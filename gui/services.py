"""GUI 전용 싱글턴 컨테이너.

위젯/워커 스레드가 매번 DataStorage()/KRXDataCollector()/KiwoomRestClient()/
DartDisclosureStorage()를 새로 생성하던 패턴을 제거한다. 매 인스턴스가
DB 마이그레이션을 다시 돌려 로그 노이즈를 만들고 토큰을 재발급해 키움 API
충돌을 유발하던 문제(2026-05-07 텔레그램 -100% 사고의 정황 증거)의 근본 차단.

scheduler/main.py의 get_storage / get_api / get_collector 와 동일한 패턴이지만
프로세스가 다르므로 별도 모듈로 둔다.

thread-safety:
- SQLAlchemy Engine은 connection pool 기반으로 thread-safe
- KiwoomRestClient는 토큰 발급 동시성을 막기 위해 lock으로 감싼 lazy init
- KRXDataCollector도 내부 DataStorage 보호용으로 동일 처리
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_storage: Optional[object] = None  # data.storage.DataStorage
_collector: Optional[object] = None  # data.collector.KRXDataCollector
_api: Optional[object] = None  # trading.kiwoom_api.KiwoomRestClient
_disc_storage: Optional[object] = None  # dart_notifier.storage.DartDisclosureStorage


def get_storage():
    """프로세스 단일 DataStorage 인스턴스 반환 (lazy init)."""
    global _storage
    with _lock:
        if _storage is None:
            from data.storage import DataStorage

            _storage = DataStorage()
        return _storage


def get_collector():
    """프로세스 단일 KRXDataCollector 인스턴스 반환 (lazy init)."""
    global _collector
    with _lock:
        if _collector is None:
            from data.collector import KRXDataCollector

            _collector = KRXDataCollector()
        return _collector


def get_api():
    """프로세스 단일 KiwoomRestClient 인스턴스 반환 (lazy init).

    동일 앱키로 새 KiwoomRestClient를 만들면 기존 토큰이 무효화될 수 있어
    GUI 위젯/워커가 이를 공유해야 한다.
    """
    global _api
    with _lock:
        if _api is None:
            from trading.kiwoom_api import KiwoomRestClient

            _api = KiwoomRestClient()
        return _api


def get_disclosure_storage():
    """프로세스 단일 DartDisclosureStorage 인스턴스 반환 (lazy init)."""
    global _disc_storage
    with _lock:
        if _disc_storage is None:
            from dart_notifier.storage import DartDisclosureStorage

            _disc_storage = DartDisclosureStorage()
        return _disc_storage


def shutdown() -> None:
    """앱 종료 시 호출 — DB 연결 풀 해제 등 자원 정리."""
    global _storage, _collector, _api, _disc_storage
    with _lock:
        for name, obj in (("storage", _storage), ("disc_storage", _disc_storage)):
            if obj is None:
                continue
            try:
                obj.engine.dispose()
                logger.debug("services.%s engine.dispose() 완료", name)
            except Exception as e:
                logger.debug("services.%s dispose 실패: %s", name, e)
        _storage = None
        _collector = None
        _api = None
        _disc_storage = None
