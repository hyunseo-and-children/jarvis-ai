"""PgConversationStore 통합 테스트 (이슈 #33, api-spec §6.3 a) — 실 pg-profile 필요.

`docker compose up -d pg-profile` 로 컨테이너가 떠 있어야 통과한다. 기본 pytest 실행에서는
@pytest.mark.integration 으로 제외된다(pyproject.toml addopts) — 명시적으로
`uv run pytest tests/integration -m integration` 로 실행한다.

ConversationStore(인메모리)는 유닛 테스트가 계속 쓰므로 여기서 건드리지 않는다
(tests/conftest.py InMemory 격리 컨벤션과 동일 원칙). 키는 매 테스트 uuid 로 발급해
재실행 간 충돌을 피한다.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from psycopg_pool import AsyncConnectionPool

from app.core import conversation as conversation_module
from app.core.conversation import PgConversationStore, TurnStatus
from app.core.config import get_settings

pytestmark = pytest.mark.integration


def _conversation_id() -> str:
    return f"it-conv-{uuid.uuid4().hex}"


@pytest.fixture
async def pool():
    p = AsyncConnectionPool(get_settings().profile_db_url, open=False)
    await p.open(wait=True)
    try:
        await PgConversationStore(p).setup()
        yield p
    finally:
        await p.close()


async def test_save_and_get_turn_roundtrip(pool) -> None:
    store = PgConversationStore(pool)
    conversation_id = _conversation_id()
    turn_id = await store.save_user_message(conversation_id, "u1", "member", "안녕하세요")
    turn = await store.get_turn(turn_id)
    assert turn is not None
    assert turn.conversation_id == conversation_id
    assert turn.user_id == "u1"
    assert turn.role == "member"
    assert turn.user_text == "안녕하세요"
    assert turn.assistant_text == ""
    assert turn.status == TurnStatus.PENDING


async def test_finalize_assistant_updates_status_and_text(pool) -> None:
    store = PgConversationStore(pool)
    conversation_id = _conversation_id()
    turn_id = await store.save_user_message(conversation_id, "u1", "member", "질문")
    await store.finalize_assistant(turn_id, "부분 응답", TurnStatus.CANCELLED)
    turn = await store.get_turn(turn_id)
    assert turn is not None
    assert turn.assistant_text == "부분 응답"
    assert turn.status == TurnStatus.CANCELLED


async def test_turns_for_returns_ordered_by_creation(pool) -> None:
    store = PgConversationStore(pool)
    conversation_id = _conversation_id()
    ids = [
        await store.save_user_message(conversation_id, "u1", "member", f"m{i}") for i in range(3)
    ]
    for tid in ids:
        await store.finalize_assistant(tid, "ok", TurnStatus.COMPLETED)
    turns = await store.turns_for(conversation_id)
    assert [t.user_text for t in turns] == ["m0", "m1", "m2"]


async def test_setup_backfills_legacy_rows_in_previous_logical_order(pool) -> None:
    store = PgConversationStore(pool)
    conversation_id = _conversation_id()
    suffix = uuid.uuid4().hex
    first_id, second_id = f"z-{suffix}", f"a-{suffix}"
    created_at = "2026-07-20T00:00:00+00:00"
    async with pool.connection() as conn:
        for turn_id, text in ((first_id, "physical-first"), (second_id, "physical-second")):
            await conn.execute(
                "INSERT INTO conversation_turns "
                "(turn_id, conversation_id, role, user_text, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (turn_id, conversation_id, "member", text, created_at),
            )
        await conn.execute("ALTER TABLE conversation_turns DROP COLUMN sequence_id")

    await store.setup()

    turns = await store.turns_for(conversation_id)
    assert [turn.turn_id for turn in turns] == [second_id, first_id]


async def test_turns_for_uses_insert_order_when_timestamps_match(pool) -> None:
    store = PgConversationStore(pool)
    conversation_id = _conversation_id()
    suffix = uuid.uuid4().hex
    first_id, second_id = f"z-{suffix}", f"a-{suffix}"
    created_at = "2026-07-21T00:00:00+00:00"
    async with pool.connection() as conn:
        for turn_id, text in ((first_id, "first"), (second_id, "second")):
            await conn.execute(
                "INSERT INTO conversation_turns "
                "(turn_id, conversation_id, role, user_text, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (turn_id, conversation_id, "member", text, created_at),
            )

    turns = await store.turns_for(conversation_id)
    assert [turn.turn_id for turn in turns] == [first_id, second_id]


async def test_get_turn_missing_returns_none(pool) -> None:
    store = PgConversationStore(pool)
    assert await store.get_turn(f"missing-{uuid.uuid4().hex}") is None


async def test_scoped_by_conversation_id(pool) -> None:
    store = PgConversationStore(pool)
    conv_a, conv_b = _conversation_id(), _conversation_id()
    await store.save_user_message(conv_a, "A", "member", "a")
    await store.save_user_message(conv_b, "B", "member", "b")
    turns_a = await store.turns_for(conv_a)
    turns_b = await store.turns_for(conv_b)
    assert len(turns_a) == 1 and turns_a[0].user_text == "a"
    assert len(turns_b) == 1 and turns_b[0].user_text == "b"


async def test_state_persists_across_store_instances() -> None:
    """재시작·다중 인스턴스 스모크 — 새 연결로도 이전에 쓴 값이 보인다."""
    conversation_id = _conversation_id()
    pool_a = AsyncConnectionPool(get_settings().profile_db_url, open=False)
    await pool_a.open(wait=True)
    try:
        store_a = PgConversationStore(pool_a)
        await store_a.setup()
        turn_id = await store_a.save_user_message(conversation_id, "u1", "member", "영속성 확인")
    finally:
        await pool_a.close()

    pool_b = AsyncConnectionPool(get_settings().profile_db_url, open=False)
    await pool_b.open(wait=True)
    try:
        turn = await PgConversationStore(pool_b).get_turn(turn_id)
    finally:
        await pool_b.close()
    assert turn is not None and turn.user_text == "영속성 확인"


async def test_get_conversation_store_connects_to_real_postgres() -> None:
    """app.core.conversation.get_conversation_store() 가 실제로 pg-profile 에 연결된다."""
    conversation_module.set_store(None)
    try:
        store = await conversation_module.get_conversation_store()
        assert isinstance(store, PgConversationStore)
        conversation_id = _conversation_id()
        turn_id = await store.save_user_message(conversation_id, "u1", "member", "연결 확인")
        turn = await store.get_turn(turn_id)
        assert turn is not None and turn.user_text == "연결 확인"
    finally:
        conversation_module.set_store(None)


async def test_get_conversation_store_concurrent_calls_single_connection() -> None:
    """동시 get_conversation_store() 호출이 커넥션 풀을 중복 생성하지 않는다(PR #48 리뷰)."""
    conversation_module.set_store(None)
    try:
        stores = await asyncio.gather(
            *(conversation_module.get_conversation_store() for _ in range(10))
        )
        assert len({id(s) for s in stores}) == 1
    finally:
        conversation_module.set_store(None)
