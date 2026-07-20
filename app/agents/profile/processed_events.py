"""session-end 이벤트 멱등성 — pg-profile processed_events 테이블 (이슈 #33, api-spec §2.7/§3.5).

ProfileStore._processed(인메모리 set)를 대체한다. LangGraph BaseStore 의 get→put 두 단계는
진짜 동시성(다중 인스턴스·동시 재전송) 하에서 원자적이지 않다 — mark_if_new 는 "check-and-set
이 원자적이어야" 의미가 있으므로, UNIQUE 제약을 건 전용 테이블에 `INSERT ... ON CONFLICT
DO NOTHING RETURNING` 으로 구현한다(db/profile/init/00_processed_events.sql).

app/core/pg_store.py(BaseStore 공유 연결)와 별개 연결을 쓴다 — BaseStore 는 이 용도의
원자적 INSERT 를 제공하지 않기 때문. dev 폴백은 InMemory set + 경고 1회(다른 스토어와
동일 규약), 운영(auth_mode=jwks)은 폴백 금지.
"""

from __future__ import annotations

import asyncio
import logging

from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
_fallback_pool: set[str] | None = None  # dev 폴백 — InMemory set
_fallback_warned = False


def set_pool(pool: AsyncConnectionPool | None) -> None:
    """풀 교체(테스트용) — None 이면 다음 사용 시 재초기화한다."""
    global _pool, _fallback_pool
    _pool = pool
    _fallback_pool = None


def reset() -> None:
    """테스트 격리용 — InMemory 폴백으로 초기화(실제 연결 시도 없이 즉시 blank)."""
    global _pool, _fallback_pool
    _pool = None
    _fallback_pool = set()


async def _get_pool() -> AsyncConnectionPool | None:
    """AsyncConnectionPool(pg-profile) 지연 초기화 — 실패 시 dev 한정 InMemory set 폴백(None 반환)."""
    global _pool, _fallback_pool, _fallback_warned
    if _pool is None and _fallback_pool is None:
        settings = get_settings()
        try:
            pool = AsyncConnectionPool(settings.profile_db_url, open=False)
            await asyncio.wait_for(
                pool.open(wait=True), timeout=settings.state_store_connect_timeout_s
            )
            _pool = pool
        except Exception as exc:
            if settings.auth_mode == "jwks":
                raise  # 운영 — 폴백 금지(멱등이 조용히 깨지면 안 된다)
            if not _fallback_warned:
                logger.warning(
                    "pg-profile processed_events 연결 실패(%s) — InMemory 폴백 "
                    "(dev 전용: 프로세스 재시작 시 멱등 상태 증발)",
                    exc,
                )
                _fallback_warned = True
            _fallback_pool = set()
    return _pool


async def seen_event(event_id: str) -> bool:
    pool = await _get_pool()
    if pool is None:
        assert _fallback_pool is not None
        return event_id in _fallback_pool
    async with pool.connection() as conn:
        row = await (
            await conn.execute("SELECT 1 FROM processed_events WHERE event_id = %s", (event_id,))
        ).fetchone()
    return row is not None


async def mark_if_new(event_id: str) -> bool:
    """미처리면 원자적으로 마킹하고 True, 이미 처리됐으면 False (동시 재전송 레이스 차단)."""
    pool = await _get_pool()
    if pool is None:
        assert _fallback_pool is not None
        if event_id in _fallback_pool:
            return False
        _fallback_pool.add(event_id)
        return True
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "INSERT INTO processed_events (event_id) VALUES (%s) "
                "ON CONFLICT DO NOTHING RETURNING event_id",
                (event_id,),
            )
        ).fetchone()
    return row is not None


async def mark_event(event_id: str) -> None:
    pool = await _get_pool()
    if pool is None:
        assert _fallback_pool is not None
        _fallback_pool.add(event_id)
        return
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO processed_events (event_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (event_id,),
        )


async def unmark_event(event_id: str) -> None:
    """마킹 해제 — 처리 실패 시 재전송이 재처리 가능하게(멱등은 성공에만 적용)."""
    pool = await _get_pool()
    if pool is None:
        assert _fallback_pool is not None
        _fallback_pool.discard(event_id)
        return
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM processed_events WHERE event_id = %s", (event_id,))
