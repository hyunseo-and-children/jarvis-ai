"""pg-profile 공통 리질리언스 경계 (이슈 #50).

연결 문자열 하드닝, 애플리케이션 쿼리 deadline, bounded LRU, 그리고 BaseStore의
read-modify-write를 여러 앱 인스턴스 사이에서 직렬화하는 PostgreSQL advisory lock을
한곳에 둔다. 저장 도메인 로직은 각 store가 소유하고 이 모듈은 I/O 경계만 제공한다.
"""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Generic, TypeVar

from langgraph.store.base import BaseStore
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings

_T = TypeVar("_T")
_K = TypeVar("_K")
_V = TypeVar("_V")

_advisory_pool: AsyncConnectionPool | None = None
_advisory_init_lock = asyncio.Lock()


def hardened_pg_conninfo(conninfo: str) -> str:
    """기존 DSN에 libpq/TCP와 서버 statement timeout을 병합한다.

    ``make_conninfo``를 사용해 URI·keyword DSN 양쪽을 안전하게 처리한다. 기존 ``options``는
    보존하고 마지막 ``-c statement_timeout``으로 이 서비스의 deadline을 우선한다.
    """
    settings = get_settings()
    current = conninfo_to_dict(conninfo)
    current_options = str(current.get("options") or "").strip()
    statement_timeout_ms = max(1, math.ceil(settings.state_store_query_timeout_s * 1000))
    options = f"{current_options} -c statement_timeout={statement_timeout_ms}".strip()
    return make_conninfo(
        conninfo,
        connect_timeout=max(2, math.ceil(settings.state_store_connect_timeout_s)),
        keepalives=1,
        keepalives_idle=settings.state_store_keepalives_idle_s,
        keepalives_interval=settings.state_store_keepalives_interval_s,
        keepalives_count=settings.state_store_keepalives_count,
        tcp_user_timeout=settings.state_store_tcp_user_timeout_ms,
        options=options,
    )


async def run_with_query_timeout(awaitable: Awaitable[_T]) -> _T:
    """pg-profile 단일 I/O를 공통 애플리케이션 deadline으로 제한한다."""
    return await asyncio.wait_for(
        awaitable,
        timeout=get_settings().state_store_query_timeout_s,
    )


def state_store_pool_config() -> dict[str, int | float]:
    """AsyncPostgresStore가 advisory lock과 실제 쿼리에 별도 연결을 쓰도록 풀을 구성한다."""
    settings = get_settings()
    return {
        "min_size": settings.state_store_pool_min_size,
        "max_size": settings.state_store_pool_max_size,
        "timeout": settings.state_store_query_timeout_s,
    }


def _postgres_pool(store: BaseStore) -> AsyncConnectionPool | None:
    """실 AsyncPostgresStore가 pool 기반인지 판별한다. InMemory/테스트 fake는 None."""
    conn = getattr(store, "conn", None)
    return conn if isinstance(conn, AsyncConnectionPool) else None


async def _get_advisory_pool() -> AsyncConnectionPool:
    """BaseStore pool 고갈을 피하기 위한 advisory-lock 전용 pool."""
    global _advisory_pool
    async with _advisory_init_lock:
        if _advisory_pool is None:
            settings = get_settings()
            pool = AsyncConnectionPool(
                hardened_pg_conninfo(settings.profile_db_url),
                open=False,
                min_size=0,
                max_size=settings.state_store_pool_max_size,
                timeout=settings.state_store_query_timeout_s,
            )
            try:
                await asyncio.wait_for(
                    pool.open(wait=True),
                    timeout=settings.state_store_connect_timeout_s,
                )
            except BaseException:
                await pool.close()
                raise
            _advisory_pool = pool
    return _advisory_pool


async def close_advisory_pool() -> None:
    """앱 종료·통합 테스트 종료 시 advisory-lock pool을 명시적으로 닫는다."""
    global _advisory_pool
    async with _advisory_init_lock:
        pool = _advisory_pool
        _advisory_pool = None
        if pool is not None:
            await pool.close()


def reset_advisory_pool() -> None:
    """테스트 이벤트 루프 격리용으로 닫힌 advisory pool의 초기화 lock을 교체한다.

    실행 중 lock 교체는 상호배제를 깨므로 허용하지 않는다. 중앙 테스트 fixture가 같은
    이벤트 루프에서 ``close_advisory_pool()``을 완료한 뒤에만 호출한다.
    """
    global _advisory_init_lock
    if _advisory_pool is not None or _advisory_init_lock.locked():
        raise RuntimeError("advisory pool must be closed and idle before reset")
    _advisory_init_lock = asyncio.Lock()


@asynccontextmanager
async def mutation_lock(
    store: BaseStore,
    key: str,
    local_lock: asyncio.Lock,
) -> AsyncIterator[None]:
    """RMW를 DB 인스턴스 간 직렬화하고, 비-PG 경로에서는 로컬 lock을 사용한다.

    transaction-scoped advisory lock이라 요청 태스크가 취소되거나 연결이 끊겨도 PostgreSQL이
    자동 해제한다. 실제 BaseStore 작업은 같은 pool의 다른 연결을 쓰므로 pool max_size는
    최소 2여야 한다(Settings 검증).
    """
    if _postgres_pool(store) is None:
        async with local_lock:
            yield
        return

    settings = get_settings()
    pool = await _get_advisory_pool()
    async with pool.connection(timeout=settings.state_store_query_timeout_s) as conn:
        async with conn.transaction():
            await run_with_query_timeout(
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (key,),
                )
            )
            yield


class BoundedLRUCache(Generic[_K, _V]):
    """의존성 없는 고정 용량 LRU. ``get``도 recency를 갱신한다."""

    def __init__(self, *, max_entries: int) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._data: OrderedDict[_K, _V] = OrderedDict()

    def __len__(self) -> int:
        return len(self._data)

    def __setitem__(self, key: _K, value: _V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def get(self, key: _K, default: _V | None = None) -> _V | None:
        try:
            value = self._data.pop(key)
        except KeyError:
            return default
        self._data[key] = value
        return value

    def clear(self) -> None:
        self._data.clear()
