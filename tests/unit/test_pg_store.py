"""app.core.pg_store 초기화 타임아웃 방어 회귀 테스트 (PR #46 후속 리뷰).

`_init_lock` 을 쥔 채 실행되는 초기화 블록은 CartStateStore·ThreadFilterStore·RevertStore
가 전부 공유한다 — 이 블록 안의 어느 한 await 라도 무제한 대기하면 전체 buyer 파이프라인이
함께 멈춘다. `ctx.__aenter__()`(커넥션 수립)뿐 아니라 `store.setup()`(DDL)도 동일 상한으로
감싸는지 검증한다.
"""

from __future__ import annotations

import asyncio

from app.core import pg_store
from app.core.config import get_settings


class _HangingStore:
    """setup() 이 영원히 끝나지 않는 fake — timeout 으로만 빠져나올 수 있어야 한다."""

    async def setup(self) -> None:
        await asyncio.sleep(10)


class _FakeAsyncPostgresStoreCtx:
    async def __aenter__(self) -> _HangingStore:
        return _HangingStore()

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


async def test_get_store_bounds_hanging_setup_by_timeout(monkeypatch) -> None:
    """setup() 이 멈춰도 state_store_connect_timeout_s 상한으로 InMemoryStore 폴백한다."""
    monkeypatch.setattr(get_settings(), "state_store_connect_timeout_s", 0.05)

    import langgraph.store.postgres.aio as pg_aio_module

    monkeypatch.setattr(
        pg_aio_module.AsyncPostgresStore,
        "from_conn_string",
        lambda *args, **kwargs: _FakeAsyncPostgresStoreCtx(),
    )

    pg_store.set_store(None)
    try:
        store = await asyncio.wait_for(pg_store.get_store(), timeout=2.0)
        assert store is not None
    finally:
        pg_store.set_store(None)
