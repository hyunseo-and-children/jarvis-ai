"""회원 프로필 세션 activity와 inactivity claim 저장소 (이슈 #79).

정합성 시각은 PostgreSQL ``now()`` 이며, 세션당 한 행을 유지한다. 스케줄러는
``ACTIVE`` 또는 lease가 만료된 ``PROCESSING`` 행만 bounded batch로 원자 선점한다.
운영(jwks)은 PostgreSQL 장애를 전파하고, dev/test만 monotonic clock 기반 메모리 폴백을 쓴다.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from app.agents.profile import processed_events
from app.core.config import get_settings
from app.core.pg_resilience import hardened_pg_conninfo, run_with_query_timeout

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
_fallback_rows: dict[tuple[int, str], "_FallbackActivity"] | None = None
_fallback_warned = False
_init_lock = asyncio.Lock()
_pending_cleanup: list[AsyncConnectionPool] = []

# 테스트가 실제 sleep 없이 경계를 고정할 수 있는 dev/fallback 전용 clock.
_monotonic = time.monotonic


@dataclass(frozen=True)
class SessionActivity:
    user_id: int
    session_id: str
    last_activity_at: datetime | float
    status: str
    claim_token: str | None = None
    lease_expires_at: datetime | float | None = None


@dataclass(frozen=True)
class ActivityClaim:
    user_id: int
    session_id: str
    claim_token: str
    last_activity_at: datetime | float


@dataclass
class _FallbackActivity:
    last_activity_at: float
    status: str = "active"
    claim_token: str | None = None
    lease_expires_at: float | None = None


def set_pool(pool: AsyncConnectionPool | None) -> None:
    """테스트용 pool 교체. 기존 pool 정리는 다음 async 진입에서 수행한다."""
    global _pool, _fallback_rows
    old_pool = _pool
    _pool = pool
    _fallback_rows = None
    if old_pool is not None and old_pool is not pool:
        _pending_cleanup.append(old_pool)


def reset() -> None:
    """테스트 격리용 메모리 상태와 loop-bound 초기화 lock을 재생성한다."""
    global _pool, _fallback_rows, _init_lock
    old_pool = _pool
    _pool = None
    _fallback_rows = {}
    _init_lock = asyncio.Lock()
    if old_pool is not None:
        _pending_cleanup.append(old_pool)


async def _drain_pending_cleanup() -> None:
    while _pending_cleanup:
        pool = _pending_cleanup.pop()
        try:
            await pool.close()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
        except Exception:
            pass


async def ensure_schema_on_connection(conn) -> None:  # noqa: ANN001 - psycopg AsyncConnection
    """conversation_turn INSERT와 같은 pool에서도 재사용하는 idempotent schema DDL."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_session_activity (
            user_id bigint NOT NULL,
            session_id text NOT NULL,
            last_activity_at timestamptz NOT NULL DEFAULT now(),
            status text NOT NULL DEFAULT 'active',
            claim_token text,
            lease_expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, session_id),
            CONSTRAINT profile_session_activity_status_check
                CHECK (status IN ('active', 'processing', 'completed'))
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profile_session_activity_due "
        "ON profile_session_activity (status, last_activity_at, user_id, session_id)"
    )


async def _ensure_schema(pool: AsyncConnectionPool) -> None:
    settings = get_settings()

    async def _run() -> None:
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(max(1, int(settings.state_store_migration_timeout_s * 1000))),),
                )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("schema:profile_session_activity",),
                )
                await ensure_schema_on_connection(conn)

    await asyncio.wait_for(_run(), timeout=settings.state_store_migration_timeout_s)


async def _get_pool() -> AsyncConnectionPool | None:
    global _pool, _fallback_rows, _fallback_warned
    await _drain_pending_cleanup()
    async with _init_lock:
        if _pool is None and _fallback_rows is None:
            settings = get_settings()
            pool = None
            try:
                pool = AsyncConnectionPool(
                    hardened_pg_conninfo(settings.profile_db_url),
                    open=False,
                    min_size=settings.state_store_pool_min_size,
                    max_size=settings.state_store_pool_max_size,
                    timeout=settings.state_store_query_timeout_s,
                )
                await asyncio.wait_for(
                    pool.open(wait=True), timeout=settings.state_store_connect_timeout_s
                )
                await _ensure_schema(pool)
                _pool = pool
            except Exception as exc:
                if pool is not None:
                    try:
                        await pool.close()
                    except asyncio.CancelledError:
                        task = asyncio.current_task()
                        if task is not None and task.cancelling() > 0:
                            raise
                    except Exception:
                        pass
                if settings.auth_mode == "jwks":
                    raise
                if not _fallback_warned:
                    logger.warning(
                        "pg-profile session activity 연결 실패(%s) — InMemory 폴백 "
                        "(dev 전용: 재시작 시 timeout activity 증발)",
                        exc,
                    )
                    _fallback_warned = True
                _fallback_rows = {}
    return _pool


async def touch_on_connection(conn, user_id: int, session_id: str) -> bool:  # noqa: ANN001
    """현재 DB transaction에서 활동과 세션 종료 generation을 함께 갱신한다."""
    row = await (
        await conn.execute(
            """
            INSERT INTO profile_session_activity (
                user_id, session_id, last_activity_at, status, updated_at
            ) VALUES (%s, %s, now(), 'active', now())
            ON CONFLICT (user_id, session_id) DO UPDATE
            SET last_activity_at = now(), status = 'active',
                claim_token = NULL, lease_expires_at = NULL, updated_at = now()
            RETURNING user_id
            """,
            (user_id, session_id),
        )
    ).fetchone()
    await processed_events.invalidate_session_end_on_connection(conn, user_id, session_id)
    return row is not None


async def touch_session(user_id: int, session_id: str) -> bool:
    """회원 발화 저장 시 세션 활동을 DB 서버 시각으로 upsert한다."""
    pool = await _get_pool()
    if pool is None:
        assert _fallback_rows is not None
        key = (user_id, session_id)
        _fallback_rows[key] = _FallbackActivity(last_activity_at=_monotonic())
        await processed_events.unmark_event(
            processed_events.session_end_event_id(user_id, session_id)
        )
        return True

    async def _run() -> bool:
        async with pool.connection() as conn:
            return await touch_on_connection(conn, user_id, session_id)

    return await run_with_query_timeout(_run())


async def get_session(user_id: int, session_id: str) -> SessionActivity | None:
    pool = await _get_pool()
    if pool is None:
        assert _fallback_rows is not None
        row = _fallback_rows.get((user_id, session_id))
        if row is None:
            return None
        return SessionActivity(
            user_id=user_id,
            session_id=session_id,
            last_activity_at=row.last_activity_at,
            status=row.status,
            claim_token=row.claim_token,
            lease_expires_at=row.lease_expires_at,
        )

    async def _run() -> SessionActivity | None:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT user_id, session_id, last_activity_at, status, "
                    "claim_token, lease_expires_at "
                    "FROM profile_session_activity WHERE user_id = %s AND session_id = %s",
                    (user_id, session_id),
                )
            ).fetchone()
        return _row_to_activity(row) if row is not None else None

    return await run_with_query_timeout(_run())


async def claim_expired_sessions(
    *, idle_timeout_s: float, lease_s: float, batch_size: int
) -> list[ActivityClaim]:
    """기한이 지난 ACTIVE/lease-expired PROCESSING 세션을 한 SQL로 bounded claim한다."""
    if idle_timeout_s <= 0 or lease_s <= 0 or batch_size <= 0:
        raise ValueError("idle timeout, lease and batch size must be positive")
    token = uuid.uuid4().hex
    pool = await _get_pool()
    if pool is None:
        assert _fallback_rows is not None
        now = _monotonic()
        cutoff = now - idle_timeout_s
        eligible = [
            (key, row)
            for key, row in _fallback_rows.items()
            if row.last_activity_at <= cutoff
            and (
                row.status == "active"
                or (
                    row.status == "processing"
                    and (row.lease_expires_at is None or row.lease_expires_at <= now)
                )
            )
        ]
        eligible.sort(key=lambda item: (item[1].last_activity_at, item[0][0], item[0][1]))
        claims: list[ActivityClaim] = []
        for (user_id, session_id), row in eligible[:batch_size]:
            row.status = "processing"
            row.claim_token = token
            row.lease_expires_at = now + lease_s
            claims.append(
                ActivityClaim(
                    user_id=user_id,
                    session_id=session_id,
                    claim_token=token,
                    last_activity_at=row.last_activity_at,
                )
            )
        return claims

    async def _run() -> list[ActivityClaim]:
        async with pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    WITH candidates AS (
                        SELECT user_id, session_id
                        FROM profile_session_activity
                        WHERE last_activity_at <= now() - make_interval(secs => %s)
                          AND (
                              status = 'active'
                              OR (status = 'processing'
                                  AND (lease_expires_at IS NULL OR lease_expires_at <= now()))
                          )
                        ORDER BY last_activity_at, user_id, session_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE profile_session_activity AS activity
                    SET status = 'processing', claim_token = %s,
                        lease_expires_at = now() + make_interval(secs => %s),
                        updated_at = now()
                    FROM candidates
                    WHERE activity.user_id = candidates.user_id
                      AND activity.session_id = candidates.session_id
                    RETURNING activity.user_id, activity.session_id,
                              activity.claim_token, activity.last_activity_at
                    """,
                    (idle_timeout_s, batch_size, token, lease_s),
                )
            ).fetchall()
        claims = [
            ActivityClaim(
                user_id=int(row[0]),
                session_id=str(row[1]),
                claim_token=str(row[2]),
                last_activity_at=row[3],
            )
            for row in rows
        ]
        claims.sort(key=lambda claim: (claim.last_activity_at, claim.user_id, claim.session_id))
        return claims

    return await run_with_query_timeout(_run())


async def claim_is_current(claim: ActivityClaim, *, idle_timeout_s: float) -> bool:
    """finalizer 직전 claim 소유·lease·최신 activity 경계를 DB 시각으로 재검증한다."""
    if idle_timeout_s <= 0:
        raise ValueError("idle_timeout_s must be positive")
    pool = await _get_pool()
    if pool is None:
        assert _fallback_rows is not None
        row = _fallback_rows.get((claim.user_id, claim.session_id))
        now = _monotonic()
        return bool(
            row is not None
            and row.status == "processing"
            and row.claim_token == claim.claim_token
            and row.lease_expires_at is not None
            and row.lease_expires_at > now
            and row.last_activity_at <= now - idle_timeout_s
        )

    async def _run() -> bool:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT 1 FROM profile_session_activity
                    WHERE user_id = %s AND session_id = %s
                      AND status = 'processing' AND claim_token = %s
                      AND lease_expires_at > now()
                      AND last_activity_at <= now() - make_interval(secs => %s)
                    """,
                    (claim.user_id, claim.session_id, claim.claim_token, idle_timeout_s),
                )
            ).fetchone()
        return row is not None

    return await run_with_query_timeout(_run())


async def complete_session(user_id: int, session_id: str, *, token: str | None = None) -> bool:
    """activity를 terminal COMPLETED로 전환한다. token이 있으면 현재 claim 소유만 허용한다."""
    pool = await _get_pool()
    if pool is None:
        assert _fallback_rows is not None
        key = (user_id, session_id)
        row = _fallback_rows.get(key)
        if token is not None and (
            row is None or row.status != "processing" or row.claim_token != token
        ):
            return False
        if row is None:
            row = _FallbackActivity(last_activity_at=_monotonic())
            _fallback_rows[key] = row
        row.status = "completed"
        row.claim_token = None
        row.lease_expires_at = None
        return True

    async def _run() -> bool:
        async with pool.connection() as conn:
            if token is None:
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO profile_session_activity (
                            user_id, session_id, last_activity_at, status, updated_at
                        ) VALUES (%s, %s, now(), 'completed', now())
                        ON CONFLICT (user_id, session_id) DO UPDATE
                        SET status = 'completed', claim_token = NULL,
                            lease_expires_at = NULL, updated_at = now()
                        RETURNING user_id
                        """,
                        (user_id, session_id),
                    )
                ).fetchone()
            else:
                row = await (
                    await conn.execute(
                        """
                        UPDATE profile_session_activity
                        SET status = 'completed', claim_token = NULL,
                            lease_expires_at = NULL, updated_at = now()
                        WHERE user_id = %s AND session_id = %s
                          AND status = 'processing' AND claim_token = %s
                        RETURNING user_id
                        """,
                        (user_id, session_id, token),
                    )
                ).fetchone()
        return row is not None

    return await run_with_query_timeout(_run())


async def complete_terminal_session(
    user_id: int,
    session_id: str,
    *,
    observed: SessionActivity | None,
) -> bool:
    """관찰한 activity generation이 그대로일 때만 terminal COMPLETED로 전환한다.

    Spring 종료 처리 중 새 회원 발화가 저장되면 touch가 ``last_activity_at``을 바꾸고
    processed-event claim도 삭제한다. 이 조건부 완료는 뒤늦게 끝난 finalizer가 그 새
    generation을 다시 COMPLETED로 덮는 것을 막는다.
    """
    pool = await _get_pool()
    if pool is None:
        assert _fallback_rows is not None
        key = (user_id, session_id)
        current = _fallback_rows.get(key)
        if observed is None:
            if current is not None:
                return False
            _fallback_rows[key] = _FallbackActivity(
                last_activity_at=_monotonic(),
                status="completed",
            )
            return True
        if current is None or current.last_activity_at != observed.last_activity_at:
            return False
        current.status = "completed"
        current.claim_token = None
        current.lease_expires_at = None
        return True

    async def _run() -> bool:
        async with pool.connection() as conn:
            if observed is None:
                row = await (
                    await conn.execute(
                        """
                        INSERT INTO profile_session_activity (
                            user_id, session_id, last_activity_at, status, updated_at
                        ) VALUES (%s, %s, now(), 'completed', now())
                        ON CONFLICT (user_id, session_id) DO NOTHING
                        RETURNING user_id
                        """,
                        (user_id, session_id),
                    )
                ).fetchone()
            else:
                row = await (
                    await conn.execute(
                        """
                        UPDATE profile_session_activity
                        SET status = 'completed', claim_token = NULL,
                            lease_expires_at = NULL, updated_at = now()
                        WHERE user_id = %s AND session_id = %s
                          AND last_activity_at = %s
                        RETURNING user_id
                        """,
                        (user_id, session_id, observed.last_activity_at),
                    )
                ).fetchone()
        return row is not None

    return await run_with_query_timeout(_run())


async def release_claim(claim: ActivityClaim) -> bool:
    """현재 token 소유 PROCESSING activity만 ACTIVE로 돌려 즉시 재시도 가능하게 한다."""
    pool = await _get_pool()
    if pool is None:
        assert _fallback_rows is not None
        row = _fallback_rows.get((claim.user_id, claim.session_id))
        if row is None or row.status != "processing" or row.claim_token != claim.claim_token:
            return False
        row.status = "active"
        row.claim_token = None
        row.lease_expires_at = None
        return True

    async def _run() -> bool:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE profile_session_activity
                    SET status = 'active', claim_token = NULL,
                        lease_expires_at = NULL, updated_at = now()
                    WHERE user_id = %s AND session_id = %s
                      AND status = 'processing' AND claim_token = %s
                    RETURNING user_id
                    """,
                    (claim.user_id, claim.session_id, claim.claim_token),
                )
            ).fetchone()
        return row is not None

    return await run_with_query_timeout(_run())


def _row_to_activity(row: tuple) -> SessionActivity:
    return SessionActivity(
        user_id=int(row[0]),
        session_id=str(row[1]),
        last_activity_at=row[2],
        status=str(row[3]),
        claim_token=str(row[4]) if row[4] is not None else None,
        lease_expires_at=row[5],
    )
