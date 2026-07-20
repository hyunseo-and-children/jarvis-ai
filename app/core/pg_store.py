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
import contextlib
import logging

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_store: BaseStore | None = None
_store_ctx: object | None = None  # AsyncPostgresStore cm — 앱 수명 동안 GC 방지
_fallback_warned = False
_init_lock = asyncio.Lock()


def set_store(store: BaseStore | None) -> None:
    """store 교체(테스트용) — None 이면 다음 사용 시 재초기화한다.

    기존 `_store_ctx`(실제 연결된 AsyncPostgresStore)가 있으면 백그라운드 태스크로
    close 를 시도한다 — 이 함수는 sync 라 여기서 직접 await 할 수 없다(PR #46 리뷰).
    """
    global _store, _store_ctx
    old_ctx = _store_ctx
    _store = store
    _store_ctx = None
    if old_ctx is not None:
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(_close_ctx(old_ctx))


async def _close_ctx(ctx) -> None:  # noqa: ANN001 - AsyncPostgresStore 의 async context manager
    with contextlib.suppress(Exception):
        await ctx.__aexit__(None, None, None)


def reset_store() -> None:
    """테스트 격리용 — InMemoryStore 로 초기화(실제 연결 시도 없이 즉시 blank).

    `_init_lock` 도 새로 만든다 — pytest-asyncio 는 테스트 함수마다 새 이벤트 루프를
    쓰는데, 모듈 전역 asyncio.Lock 을 여러 루프에 걸쳐 재사용하면 이전 루프에 묶인
    내부 상태로 다음 테스트에서 락 획득이 영원히 안 풀리는 hang 이 발생할 수 있다.
    """
    global _init_lock
    set_store(InMemoryStore())
    _init_lock = asyncio.Lock()


async def get_store() -> BaseStore:
    """AsyncPostgresStore(pg-profile) 지연 초기화 — 실패 시 dev 한정 InMemoryStore 폴백.

    락 없는 지연 초기화는 콜드 스타트 시 동시 요청이 각자 커넥션을 중복 생성해
    앞선 연결(들)이 정리 없이 버려지는 레이스가 있다 — `_init_lock` 으로 초기화
    블록 전체를 직렬화한다(PR #46 리뷰).
    """
    global _store, _store_ctx, _fallback_warned
    async with _init_lock:
        if _store is None:
            settings = get_settings()
            entered_ctx = None
            try:
                from langgraph.store.postgres.aio import AsyncPostgresStore  # noqa: PLC0415

                ctx = AsyncPostgresStore.from_conn_string(settings.profile_db_url)
                store = await asyncio.wait_for(
                    ctx.__aenter__(), timeout=settings.state_store_connect_timeout_s
                )
                entered_ctx = ctx  # __aenter__ 성공 후에만 __aexit__ 대상(부분 실패 정리용)
                await store.setup()
                _store_ctx = ctx
                _store = store
            except Exception as exc:
                if entered_ctx is not None:
                    # setup() 실패 등 부분 실패 — 이미 연 연결을 닫아 커넥션 누수를 막는다.
                    with contextlib.suppress(Exception):
                        await entered_ctx.__aexit__(type(exc), exc, exc.__traceback__)
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
