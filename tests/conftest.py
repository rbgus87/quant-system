"""pytest 공통 fixture.

매 테스트 종료 후 scheduler.main의 모듈 싱글턴(_storage / _api / _collector)을
None으로 리셋한다. 싱글턴이 이전 테스트의 mock을 캐시하면 다음 테스트의 patch가
무력화되므로 격리가 필요하다.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_scheduler_singletons():
    """scheduler.main 모듈 레벨 싱글턴 격리 (테스트 간 캐시 오염 방지)."""
    yield
    try:
        import scheduler.main as sm
    except ImportError:
        return
    sm._storage = None
    sm._api = None
    sm._collector = None
