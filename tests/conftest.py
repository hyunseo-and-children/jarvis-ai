"""테스트 공통 — 인프라 전역 상태(레이트 리밋·활성 스트림 레지스트리)를 테스트마다 격리."""

from __future__ import annotations

import pytest

from app.core.conversation import reset_store
from app.core.ratelimit import reset_limiter
from app.core.stream import get_registry


@pytest.fixture(autouse=True)
def _reset_infra_state():
    """각 테스트 전후로 인메모리 카운터·레지스트리를 비워 테스트 간 누수를 막는다."""
    reset_limiter()
    reset_store()
    get_registry()._active.clear()
    yield
    reset_limiter()
    reset_store()
    get_registry()._active.clear()
