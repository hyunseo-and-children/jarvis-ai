"""pg-profile 공유 AsyncPostgresStore(BaseStore) 연결 (이슈 #33).

buyer 스레드 상태(ThreadFilterStore·CartStateStore·RevertStore)가 공유하는 단일 pg-profile
연결을 지연 초기화한다 — checkpointer 물리 배치를 프로필 인스턴스에 동거시키는 기본안
(SPEC-PROFILE-001 OPEN-P9)을 그대로 따르되, 실제 메커니즘은 checkpointer 가 아니라
BaseStore(app/agents/seller/history.py 와 동일 패턴 — 실행 모델이 실제 LangGraph StateGraph
가 아니라 단순 스레드 키 조회이므로 checkpointer 는 과설계). dev 폴백은 InMemoryStore +
경고 1회(seller 선례), 운영(auth_mode=jwks)은 폴백 금지 — 재시작 시 멀티턴 상태 증발은
운영에서 허용 불가.
"""

from __future__ import annotations

import asyncio
import logging

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_store: BaseStore | None = None
_store_ctx: object | None = None  # AsyncPostgresStore cm — 앱 수명 동안 GC 방지
_fallback_warned = False


def set_store(store: BaseStore | None) -> None:
    """store 교체(테스트용) — None 이면 다음 사용 시 재초기화한다."""
    global _store, _store_ctx
    _store = store
    _store_ctx = None


def reset_store() -> None:
    """테스트 격리용 — InMemoryStore 로 초기화(실제 연결 시도 없이 즉시 blank)."""
    set_store(InMemoryStore())


async def get_store() -> BaseStore:
    """AsyncPostgresStore(pg-profile) 지연 초기화 — 실패 시 dev 한정 InMemoryStore 폴백."""
    global _store, _store_ctx, _fallback_warned
    if _store is None:
        settings = get_settings()
        try:
            from langgraph.store.postgres.aio import AsyncPostgresStore  # noqa: PLC0415

            ctx = AsyncPostgresStore.from_conn_string(settings.profile_db_url)
            store = await asyncio.wait_for(
                ctx.__aenter__(), timeout=settings.state_store_connect_timeout_s
            )
            await store.setup()
            _store_ctx = ctx
            _store = store
        except Exception as exc:
            if settings.auth_mode == "jwks":
                raise  # 운영 — 폴백 금지(멀티턴 상태가 조용히 증발하면 안 된다)
            if not _fallback_warned:
                logger.warning(
                    "pg-profile store 연결 실패(%s) — InMemoryStore 폴백 "
                    "(dev 전용: 프로세스 재시작 시 스레드 상태 증발)",
                    exc,
                )
                _fallback_warned = True
            _store = InMemoryStore()
    return _store
