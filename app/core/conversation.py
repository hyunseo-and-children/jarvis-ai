"""대화 저장 (api-spec §6.3 a) — user/assistant 턴 + 상태 + 부분 텍스트 보존 (이슈 #33).

pg-profile 의 conversation_turns 일반 테이블로 이관 — checkpointer 가 **아니다**(재개용이
아니라 감사·구조화 로그 상관관계 조회용, db/profile/init/01_conversation_turns.sql). 유닛
테스트는 계속 인메모리(ConversationStore)를 주입해 실 인프라 없이 빠르게 돈다
(app/pipelines/artifact_store.py 와 동일 원칙 — 실 스토어 자체 검증은 tests/integration/).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import math
import uuid
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from app.agents.profile import session_activity
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.pg_resilience import hardened_pg_conninfo, run_with_query_timeout

logger = get_logger(__name__)


class TurnStatus(str, Enum):
    """어시스턴트 응답 저장 상태 (api-spec §6.3 a). PENDING 은 user 저장 직후 초기값."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class Turn:
    """한 턴 = user 메시지 원문 + assistant 응답(부분 포함) + 상태."""

    turn_id: str
    conversation_id: str
    user_id: str | None
    role: str
    user_text: str
    assistant_text: str = ""
    status: TurnStatus = TurnStatus.PENDING


class ConversationStoreProtocol(Protocol):
    """대화 저장소 공유 계약 — ConversationStore(인메모리)·PgConversationStore(pg-profile)."""

    async def save_user_message(
        self,
        conversation_id: str,
        user_id: str | None,
        role: str,
        text: str,
        *,
        session_id: str | None = None,
    ) -> str: ...

    async def finalize_assistant(
        self, turn_id: str, assistant_text: str, status: TurnStatus
    ) -> None: ...

    async def get_turn(self, turn_id: str) -> Turn | None: ...

    async def turns_for(self, conversation_id: str) -> list[Turn]: ...


class ConversationStore:
    """인메모리 대화 저장소(테스트 전용). conversationId(=sessionId) 별로 턴을 순서대로 보관한다."""

    # 인메모리 안전 상한(테스트/dev 폴백 전용) — pg-profile(디스크 기반)엔 적용하지 않는다.
    # [한계] 전역 FIFO라 한 사용자가 상한을 채우면 무관한 타 사용자의 확정 턴도 축출될 수 있다
    # (cross-tenant). 프로덕션 경로(PgConversationStore)는 이 한계가 없다.
    _MAX_TURNS = 5000

    def __init__(self) -> None:
        self._turns: dict[str, Turn] = {}
        self._by_conversation: dict[str, list[str]] = {}
        self._order: deque[str] = deque()
        self._seq = itertools.count(1)

    async def save_user_message(
        self,
        conversation_id: str,
        user_id: str | None,
        role: str,
        text: str,
        *,
        session_id: str | None = None,
    ) -> str:
        """사용자 메시지 수신 즉시 저장(§6.3 a). turn_id 를 반환한다(assistant 마감에 사용)."""
        turn_id = f"turn-{next(self._seq)}"
        self._turns[turn_id] = Turn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            user_text=text,
        )
        self._by_conversation.setdefault(conversation_id, []).append(turn_id)
        self._order.append(turn_id)
        self._evict_if_needed()
        member_id = _profile_member_id(user_id, role)
        if member_id is not None and session_id is not None:
            await session_activity.touch_session(member_id, session_id)
        return turn_id

    def _evict_if_needed(self) -> None:
        """상한 초과 시 **확정된** 턴부터 축출(무제한 메모리 증가 방지).

        진행 중(PENDING) 턴은 건너뛴다 — 응답 중 축출되면 finalize 가 유실되기 때문.
        모두 PENDING인 병리적 경우엔 축출을 보류(상한 일시 초과, 곧 확정되며 해소)."""
        attempts = len(self._order)
        while len(self._turns) > self._MAX_TURNS and attempts > 0:
            attempts -= 1
            old_id = self._order.popleft()
            turn = self._turns.get(old_id)
            if turn is None:
                continue  # 이미 제거된 참조
            if turn.status is TurnStatus.PENDING:
                self._order.append(old_id)  # 진행 중 — 축출 보류, 뒤로 미룸
                continue
            self._turns.pop(old_id, None)
            ids = self._by_conversation.get(turn.conversation_id)
            if ids and old_id in ids:
                ids.remove(old_id)
                if not ids:
                    del self._by_conversation[turn.conversation_id]

    async def finalize_assistant(
        self, turn_id: str, assistant_text: str, status: TurnStatus
    ) -> None:
        """어시스턴트 응답을 상태와 함께 마감한다. FAILED/CANCELLED 도 부분 텍스트를 보존한다."""
        turn = self._turns.get(turn_id)
        if turn is None:
            # 축출됐거나 미지의 turn — 응답이 저장소에서 유실됨(관측 가능하게 경고).
            logger.warning("finalize on evicted/unknown turn_id=%s (assistant 응답 유실)", turn_id)
            return
        turn.assistant_text = assistant_text
        turn.status = status

    async def get_turn(self, turn_id: str) -> Turn | None:
        return self._turns.get(turn_id)

    async def turns_for(self, conversation_id: str) -> list[Turn]:
        return [self._turns[t] for t in self._by_conversation.get(conversation_id, [])]


class PgConversationStore:
    """pg-profile conversation_turns 테이블 기반 스토어. ConversationStore 와 동일 인터페이스."""

    def __init__(self, pool) -> None:  # noqa: ANN001 - psycopg_pool.AsyncConnectionPool(지연 임포트)
        self._pool = pool

    async def setup(self) -> None:
        """기존 볼륨은 이전 논리 순서로 백필하고 신규 turn은 DB sequence로 정렬한다."""
        settings = get_settings()
        migration_timeout_ms = max(1, math.ceil(settings.state_store_migration_timeout_s * 1000))

        async def _run() -> None:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(migration_timeout_ms),),
                    )
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        ("schema:conversation_turns:sequence_id",),
                    )
                    await conn.execute(
                        """
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM information_schema.columns
                                WHERE table_schema = current_schema()
                                  AND table_name = 'conversation_turns'
                                  AND column_name = 'sequence_id'
                            ) THEN
                                ALTER TABLE conversation_turns ADD COLUMN sequence_id bigint;
                                CREATE SEQUENCE IF NOT EXISTS conversation_turns_sequence_id_seq;
                                ALTER SEQUENCE conversation_turns_sequence_id_seq
                                    OWNED BY conversation_turns.sequence_id;
                                WITH ordered AS (
                                    SELECT turn_id,
                                           row_number() OVER (ORDER BY created_at, turn_id) AS seq
                                    FROM conversation_turns
                                )
                                UPDATE conversation_turns AS turns
                                SET sequence_id = ordered.seq
                                FROM ordered
                                WHERE turns.turn_id = ordered.turn_id;
                                PERFORM setval(
                                    'conversation_turns_sequence_id_seq',
                                    GREATEST(
                                        COALESCE((SELECT MAX(sequence_id) FROM conversation_turns), 0)
                                            + 1,
                                        1
                                    ),
                                    false
                                );
                                ALTER TABLE conversation_turns
                                    ALTER COLUMN sequence_id
                                    SET DEFAULT nextval('conversation_turns_sequence_id_seq');
                                ALTER TABLE conversation_turns
                                    ALTER COLUMN sequence_id SET NOT NULL;
                            END IF;
                        END $$
                        """
                    )
                    await conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_conversation_turns_sequence "
                        "ON conversation_turns (conversation_id, sequence_id)"
                    )
                    # session_activity 자체 migration pool과 같은 lock을 사용해야 두 connection이
                    # profile_session_activity CREATE TABLE/INDEX를 동시에 시작하지 않는다.
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (session_activity.SCHEMA_LOCK_KEY,),
                    )
                    await session_activity.ensure_schema_on_connection(conn)

        await asyncio.wait_for(_run(), timeout=settings.state_store_migration_timeout_s)

    async def _execute(self, sql: str, params: tuple) -> int:
        """쓰기 쿼리(연결 획득+실행)를 실행 상한으로 감싼다.

        pg 가 응답 없이 멈추면 이 await 가 영영 안 끝나 commit_user_message() 가 반환하지
        못하고 해당 session_id 의 동시 스트림 슬롯이 영구히 잠긴다(§2.9 a, PR #48 후속 리뷰).
        """

        async def _run() -> int:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(sql, params)
                return cursor.rowcount

        return await run_with_query_timeout(_run())

    async def save_user_message(
        self,
        conversation_id: str,
        user_id: str | None,
        role: str,
        text: str,
        *,
        session_id: str | None = None,
    ) -> str:
        turn_id = uuid.uuid4().hex
        member_id = _profile_member_id(user_id, role)

        if member_id is None or session_id is None:
            await self._execute(
                "INSERT INTO conversation_turns "
                "(turn_id, conversation_id, user_id, role, user_text) "
                "VALUES (%s, %s, %s, %s, %s)",
                (turn_id, conversation_id, user_id, role, text),
            )
            return turn_id

        async def _run() -> None:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO conversation_turns "
                        "(turn_id, conversation_id, user_id, role, user_text) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (turn_id, conversation_id, user_id, role, text),
                    )
                    await session_activity.touch_on_connection(conn, member_id, session_id)

        await run_with_query_timeout(_run())
        return turn_id

    async def finalize_assistant(
        self, turn_id: str, assistant_text: str, status: TurnStatus
    ) -> None:
        rowcount = await self._execute(
            "UPDATE conversation_turns SET assistant_text = %s, status = %s WHERE turn_id = %s",
            (assistant_text, status.value, turn_id),
        )
        if rowcount == 0:
            logger.warning("conversation turn finalize 대상 없음: turn_id=%s", turn_id)

    async def get_turn(self, turn_id: str) -> Turn | None:
        # 읽기도 쓰기(_execute)와 동일 실행 상한 — 타임아웃이 없으면 pg 가 멈출 때 이 await 가
        # 영영 안 끝날 뿐 아니라 연결이 풀에 물린 채 반환되지 않아, 풀 고갈로 타임아웃이 걸린
        # 쓰기 경로(슬롯 확보/마감)까지 연쇄로 막힌다(PR #48 후속 리뷰).
        async def _run() -> Turn | None:
            async with self._pool.connection() as conn:
                row = await (
                    await conn.execute(
                        "SELECT turn_id, conversation_id, user_id, role, user_text, assistant_text, status "
                        "FROM conversation_turns WHERE turn_id = %s",
                        (turn_id,),
                    )
                ).fetchone()
            return _row_to_turn(row) if row else None

        return await run_with_query_timeout(_run())

    async def turns_for(self, conversation_id: str) -> list[Turn]:
        async def _run() -> list[Turn]:
            async with self._pool.connection() as conn:
                rows = await (
                    await conn.execute(
                        "SELECT turn_id, conversation_id, user_id, role, user_text, assistant_text, status "
                        "FROM conversation_turns WHERE conversation_id = %s "
                        "ORDER BY sequence_id",
                        (conversation_id,),
                    )
                ).fetchall()
            return [_row_to_turn(row) for row in rows]

        return await run_with_query_timeout(_run())


def _row_to_turn(row: tuple) -> Turn:
    turn_id, conversation_id, user_id, role, user_text, assistant_text, status = row
    return Turn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        user_text=user_text,
        assistant_text=assistant_text,
        status=TurnStatus(status),
    )


def _profile_member_id(user_id: str | None, role: str) -> int | None:
    """프로필 대상 회원 BIGINT만 activity touch 대상으로 정규화한다."""
    if role != "member" or user_id is None:
        return None
    try:
        value = int(user_id)
    except (TypeError, ValueError):
        return None
    return value if 0 < value < 2**63 else None


def conversation_key(subject: str | None, session_id: str) -> str:
    """대화 저장 키를 **신원에 스코프**한다(registry_key와 동일 IDOR 방지).

    session_id(요청 본문 유래)만으로 키잉하면 다른 신원이 같은 session_id 를 실어 한 대화에
    턴을 혼입시킬 수 있다(프로필 스캔 오염·히스토리 노출). subject(검증된 sub)를 접두어로 묶어
    사용자 간 대화 혼입을 막는다. 신원 없음(dev 무토큰)은 "anon".
    """
    return f"{subject or 'anon'}:{session_id}"


_store: ConversationStoreProtocol | None = None
_pool_ctx: object | None = None  # AsyncConnectionPool cm — 앱 수명 동안 GC 방지
_fallback_warned = False
_init_lock = asyncio.Lock()
_pending_cleanup: list[
    object
] = []  # set_store() 가 못 닫은 이전 풀 — get_conversation_store() 진입 시 정리


def set_store(store: ConversationStoreProtocol | None) -> None:
    """store 교체(테스트용) — None 이면 다음 사용 시 재초기화한다.

    기존 `_pool_ctx`(실제 연결된 풀)가 있으면 정리 대기열에 넣는다. 이 함수는 sync 라
    여기서 직접 await 할 수 없고, `asyncio.get_running_loop()` fire-and-forget 태스크
    방식은 **실행 중인 루프가 없으면 조용히 스킵**된다 — `tests/conftest.py` 의 sync
    autouse fixture 가 정확히 그 상황이라(이벤트 루프 시작 전) 풀이 정리 없이 영구
    누수된다(app/core/pg_store.py·app/agents/profile/processed_events.py 에서 이미
    고친 것과 동일 버그가 이 모듈에 재도입돼 있었다, PR #48 후속 리뷰). 대신 다음
    `get_conversation_store()` 호출(반드시 async 컨텍스트) 시점에 확실히 정리한다.
    """
    global _store, _pool_ctx
    old_pool = _pool_ctx
    _store = store
    _pool_ctx = None
    if old_pool is not None:
        _pending_cleanup.append(old_pool)


async def _drain_pending_cleanup() -> None:
    """대기열의 이전 풀들을 닫는다 — 다른(이미 소멸한) 이벤트 루프에서 만들어진 풀일 수 있다.

    `AsyncConnectionPool` 은 백그라운드 워커 태스크를 그 풀을 만든 이벤트 루프에 묶어
    두므로 cross-loop close 가 `CancelledError` 를 낼 수 있다. `BaseException` 째로
    삼키면 이 await 지점에서 **현재 태스크 자체**가 실제로 취소되는 경우까지 함께
    삼켜져 취소가 무시되는 안티패턴이 된다(processed_events.py 와 동일 근거·수정,
    PR #47 후속 리뷰) — `task.cancelling()` 으로 구분해 실제 취소 요청만 다시 던진다.
    """
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


async def get_conversation_store() -> ConversationStoreProtocol:
    """대화 저장소 — pg-profile(conversation_turns) 지연 초기화, 실패 시 dev 한정 인메모리 폴백.

    락 없는 지연 초기화는 콜드 스타트 시 동시 요청이 커넥션 풀을 중복 생성해
    앞선 풀(들)이 close 없이 버려지는 레이스가 있다 — `_init_lock` 으로 초기화
    블록 전체를 직렬화한다(app/core/pg_store.py 와 동일 패턴, PR #48 리뷰).
    """
    global _store, _pool_ctx, _fallback_warned
    await _drain_pending_cleanup()
    settings = get_settings()
    await asyncio.wait_for(
        _init_lock.acquire(),
        timeout=settings.state_store_query_timeout_s,
    )
    try:
        if _store is None:
            pool = None
            try:
                from psycopg_pool import AsyncConnectionPool  # noqa: PLC0415

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
                store = PgConversationStore(pool)
                await store.setup()
                _pool_ctx = pool
                _store = store
            except asyncio.CancelledError:
                # disconnect 등으로 pool.open() 도중 요청 태스크가 취소되면 CancelledError
                # (BaseException)라 아래 except Exception 이 못 잡아, 방금 만든 풀(+백그라운드
                # 워커)이 close 없이 샜다 — get_conversation_store() 는 open_stream 진입 전
                # chat/seller 핸들러에서 호출되므로 이 취소가 실제로 도달한다(store.py·
                # processed_events.py 와 동일 클래스, PR #48 후속 리뷰). 여기 풀은 이 루프에서
                # 방금 만든 것이라 취소는 항상 실제 취소 — best-effort 로 닫고 그대로 전파한다.
                if pool is not None:
                    with contextlib.suppress(Exception):
                        await pool.close()
                raise
            except Exception as exc:
                if pool is not None:
                    # open() 부분 실패(타임아웃 등) — 이미 생성된 풀을 닫아 커넥션 누수 방지.
                    with contextlib.suppress(Exception):
                        await pool.close()
                if settings.auth_mode == "jwks":
                    raise  # 운영 — 폴백 금지(대화 저장·감사 로그가 조용히 증발하면 안 된다)
                if not _fallback_warned:
                    logger.warning(
                        "pg-profile conversation_turns 연결 실패(%s) — 인메모리 폴백 "
                        "(dev 전용: 프로세스 재시작 시 대화 이력 증발)",
                        exc,
                    )
                    _fallback_warned = True
                _store = ConversationStore()
    finally:
        _init_lock.release()
    return _store


def reset_store() -> None:
    """테스트용 — 저장소 초기화(인메모리로 되돌림).

    `_init_lock` 도 새로 만든다 — pytest-asyncio 는 테스트 함수마다 새 이벤트 루프를
    쓰는데, 모듈 전역 asyncio.Lock 을 여러 루프에 걸쳐 재사용하면 이전 루프에 묶인
    내부 상태로 다음 테스트에서 락 획득이 영원히 안 풀리는 hang 이 발생할 수 있다
    (app/core/pg_store.py 와 동일 문제, 실제 재현·수정 이력은 docs/lessons.md).
    """
    global _init_lock
    set_store(ConversationStore())
    _init_lock = asyncio.Lock()
