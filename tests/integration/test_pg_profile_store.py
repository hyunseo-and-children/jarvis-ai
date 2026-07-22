"""ProfileStore(pgvector) + processed_events 멱등성 통합 테스트 (이슈 #33).

`docker compose up -d pg-profile` 로 컨테이너가 떠 있어야 통과한다. 기본 pytest 실행에서는
@pytest.mark.integration 으로 제외된다(pyproject.toml addopts) — 명시적으로
`uv run pytest tests/integration -m integration` 로 실행한다.

임베딩은 Google API(embed_texts) 대신 결정론적 fake 를 주입해 라이브 API 키 없이도
pgvector 인덱스·store_vectors 테이블 배선 자체를 검증한다(정밀 유사도 랭킹은 검증 범위 밖 —
그건 catalog 쪽 embed_texts 자체 단위테스트 소관). InMemoryStore 는 유닛 테스트가 계속 쓰므로
여기서 건드리지 않는다. 키는 매 테스트 uuid 로 발급해 재실행 간 충돌을 피한다.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from langgraph.store.postgres.aio import AsyncPostgresStore

from app.agents.profile import processed_events
from app.agents.profile import store as profile_store_module
from app.agents.profile.store import ProfileStore
from app.core.config import get_settings
from app.core.pg_resilience import hardened_pg_conninfo, state_store_pool_config

pytestmark = pytest.mark.integration

_DIM = 8


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """결정론적 fake 임베딩 — 텍스트 길이 기반 8차원 벡터(실제 유사도 의미 없음, 배선 검증용)."""
    return [[float(len(t) % (i + 1)) for i in range(_DIM)] for t in texts]


def _user() -> str:
    return f"it-user-{uuid.uuid4().hex}"


@pytest.fixture
async def pg_store():
    index_config = {"dims": _DIM, "embed": _fake_embed, "fields": ["fact"]}
    async with AsyncPostgresStore.from_conn_string(
        get_settings().profile_db_url, index=index_config
    ) as store:
        await store.setup()
        yield store


async def test_summary_roundtrip(pg_store) -> None:
    wrapper = ProfileStore(pg_store)
    user_id = _user()
    await wrapper.set_summary(user_id, "# 취향\n- 이어폰", "2026-07-20T00:00:00+00:00")
    summary = await wrapper.get_summary(user_id)
    assert summary is not None
    assert summary.markdown == "# 취향\n- 이어폰"
    assert summary.generated_at == "2026-07-20T00:00:00+00:00"


async def test_summary_missing_user_returns_none(pg_store) -> None:
    wrapper = ProfileStore(pg_store)
    assert await wrapper.get_summary(_user()) is None


async def test_facts_stored_as_individual_items_and_ordered(pg_store) -> None:
    """fact 1개 = item 1개 — 여러 fact 를 추가해도 순서대로(생성 시각 기준) 조회된다."""
    wrapper = ProfileStore(pg_store)
    user_id = _user()
    await wrapper.add_fact(user_id, "3만원대 무선이어폰 선호")
    await wrapper.add_fact(user_id, "블루투스 노이즈캔슬링 선호")
    facts = await wrapper.get_facts(user_id)
    assert facts == ["3만원대 무선이어폰 선호", "블루투스 노이즈캔슬링 선호"]


async def test_add_fact_cap_evicts_oldest(pg_store) -> None:
    wrapper = ProfileStore(pg_store)
    user_id = _user()
    for i in range(5):
        await wrapper.add_fact(user_id, f"fact-{i}", cap=3)
    facts = await wrapper.get_facts(user_id)
    assert facts == ["fact-2", "fact-3", "fact-4"]


async def test_session_ctx_roundtrip_and_clear_upto(pg_store) -> None:
    wrapper = ProfileStore(pg_store)
    key = _user()
    await wrapper.append_session_ctx(key, "A")
    await wrapper.append_session_ctx(key, "B")
    buffer, watermark = await wrapper.get_session_ctx_snapshot(key)
    assert buffer == ["A", "B"] and watermark == 2

    await wrapper.append_session_ctx(key, "C")  # 스냅샷 이후 도착
    await wrapper.clear_session_ctx_upto(key, watermark)
    assert await wrapper.get_session_ctx(key) == ["C"]  # A/B 만 지워짐


async def test_facts_semantic_index_populated(pg_store) -> None:
    """pgvector store_vectors 테이블에 fact 임베딩이 실제로 채워진다(REQ-PROF-073/074 배선 검증)."""
    wrapper = ProfileStore(pg_store)
    user_id = _user()
    await wrapper.add_fact(user_id, "임베딩 배선 확인용 fact")
    rows = await pg_store.asearch(("facts", user_id), query="임베딩 배선 확인용")
    assert len(rows) == 1
    assert rows[0].value["fact"] == "임베딩 배선 확인용 fact"


async def test_append_session_ctx_concurrent_calls_no_lost_update(pg_store) -> None:
    """동시 append_session_ctx 호출이 서로의 발화를 잃지 않는다(lost update 방지, PR #47 리뷰)."""
    wrapper = ProfileStore(pg_store)
    key = _user()
    await asyncio.gather(
        wrapper.append_session_ctx(key, "A"),
        wrapper.append_session_ctx(key, "B"),
        wrapper.append_session_ctx(key, "C"),
    )
    assert set(await wrapper.get_session_ctx(key)) == {"A", "B", "C"}


async def test_get_profile_store_module_concurrent_calls_single_connection() -> None:
    """동시 _get_store() 호출이 커넥션을 중복 생성하지 않는다(PR #47 리뷰)."""
    profile_store_module.set_store(None)
    try:
        stores = await asyncio.gather(*(profile_store_module._get_store() for _ in range(10)))
        assert len({id(s) for s in stores}) == 1
    finally:
        profile_store_module.set_store(None)


async def test_processed_events_get_pool_concurrent_calls_single_connection() -> None:
    """동시 _get_pool() 호출이 커넥션 풀을 중복 생성하지 않는다(PR #47 리뷰)."""
    processed_events.set_pool(None)
    try:
        pools = await asyncio.gather(*(processed_events._get_pool() for _ in range(10)))
        assert len({id(p) for p in pools}) == 1
    finally:
        processed_events.set_pool(None)


async def test_processed_events_set_pool_none_defers_cleanup_to_next_get_pool_call() -> None:
    """set_pool(None) 이 sync 컨텍스트에서 놓친 정리를 다음 _get_pool() 이 확실히 처리한다.

    fire-and-forget(asyncio.get_running_loop().create_task) 방식은 실행 중인 이벤트
    루프가 없으면(예: conftest 의 sync autouse fixture) 조용히 스킵돼 실제로 한 번도
    정리가 안 됐었다(app/core/pg_store.py 와 동일 버그, PR #47 후속 리뷰) — 지연 정리
    큐로 교체 후, 다음 _get_pool() 호출 시 확실히 close() 가 실행되는지 검증한다.
    """
    processed_events.set_pool(None)
    pool = await processed_events._get_pool()
    assert pool is not None
    assert not pool.closed

    processed_events.set_pool(None)  # sync 호출 — 정리는 아직 큐에만 쌓인 상태
    assert not pool.closed  # 아직 정리 안 됨(다음 _get_pool() 전까지)

    await processed_events._get_pool()  # 이 호출 진입 시 _drain_pending_cleanup() 이 실행됨
    assert pool.closed

    processed_events.set_pool(None)


async def test_processed_events_reset_defers_cleanup_to_next_get_pool_call() -> None:
    """reset() 도 set_pool(None) 과 동일하게 기존 풀을 정리 대기열에 넣고 다음 호출에서 닫는다."""
    processed_events.set_pool(None)
    pool = await processed_events._get_pool()
    assert pool is not None
    assert not pool.closed

    processed_events.reset()  # sync — 정리는 아직 큐에만 쌓인 상태
    assert not pool.closed

    await processed_events._get_pool()
    assert pool.closed

    processed_events.set_pool(None)


async def test_state_persists_across_store_instances() -> None:
    """재시작·다중 인스턴스 스모크 — 새 연결로도 이전에 쓴 값이 보인다."""
    user_id = _user()
    index_config = {"dims": _DIM, "embed": _fake_embed, "fields": ["fact"]}
    async with AsyncPostgresStore.from_conn_string(
        get_settings().profile_db_url, index=index_config
    ) as store_a:
        await store_a.setup()
        await ProfileStore(store_a).set_summary(user_id, "# 영속성", "2026-07-20T00:00:00+00:00")

    async with AsyncPostgresStore.from_conn_string(
        get_settings().profile_db_url, index=index_config
    ) as store_b:
        await store_b.setup()
        summary = await ProfileStore(store_b).get_summary(user_id)
    assert summary is not None and summary.markdown == "# 영속성"


async def test_profile_rmw_is_safe_across_postgres_pools() -> None:
    """서로 다른 앱 인스턴스의 fact/session RMW가 중복·lost update 없이 직렬화된다."""
    conninfo = hardened_pg_conninfo(get_settings().profile_db_url)
    pool_config = state_store_pool_config()
    index_config = {"dims": _DIM, "embed": _fake_embed, "fields": ["fact"]}
    async with (
        AsyncPostgresStore.from_conn_string(
            conninfo, pool_config=pool_config, index=index_config
        ) as store_a,
        AsyncPostgresStore.from_conn_string(
            conninfo, pool_config=pool_config, index=index_config
        ) as store_b,
    ):
        await store_a.setup()
        await store_b.setup()
        profile_a, profile_b = ProfileStore(store_a), ProfileStore(store_b)

        session_key = _user()
        messages = {f"message-{i}" for i in range(20)}
        await asyncio.gather(
            *(
                (profile_a if i % 2 == 0 else profile_b).append_session_ctx(session_key, message)
                for i, message in enumerate(messages)
            )
        )
        assert set(await profile_a.get_session_ctx(session_key)) == messages

        user_id = _user()
        await asyncio.gather(
            *(
                profile.add_fact(user_id, "same-fact")
                for profile in (profile_a, profile_b)
                for _ in range(5)
            )
        )
        assert (await profile_a.get_facts(user_id)).count("same-fact") == 1

        clear_key = _user()
        await profile_a.append_session_ctx(clear_key, "old")
        _, watermark = await profile_a.get_session_ctx_snapshot(clear_key)
        await asyncio.gather(
            profile_a.clear_session_ctx_upto(clear_key, watermark),
            profile_b.append_session_ctx(clear_key, "new"),
        )
        assert await profile_a.get_session_ctx(clear_key) == ["new"]


# ─────────── processed_events (session-end 멱등 원자성) ───────────


async def test_processed_events_mark_if_new_atomic_under_concurrency() -> None:
    """동시 mark_if_new 호출 중 정확히 1건만 True(신규) — UNIQUE 제약 기반 원자성(이슈 #33 핵심)."""
    processed_events.set_pool(None)
    event_id = f"it-evt-{uuid.uuid4().hex}"
    try:
        results = await asyncio.gather(*(processed_events.mark_if_new(event_id) for _ in range(10)))
        assert sum(results) == 1
        assert await processed_events.seen_event(event_id) is True
    finally:
        await processed_events.unmark_event(event_id)
        processed_events.set_pool(None)


async def test_processed_events_unmark_allows_reprocessing() -> None:
    processed_events.set_pool(None)
    event_id = f"it-evt-{uuid.uuid4().hex}"
    try:
        assert await processed_events.mark_if_new(event_id) is True
        assert await processed_events.mark_if_new(event_id) is False
        await processed_events.unmark_event(event_id)
        assert await processed_events.seen_event(event_id) is False
        assert await processed_events.mark_if_new(event_id) is True
    finally:
        await processed_events.unmark_event(event_id)
        processed_events.set_pool(None)


async def test_processed_events_stale_claim_recovery_and_completion() -> None:
    """PROCESSING lease는 crash 후 재선점되고 COMPLETED row는 다시 선점되지 않는다."""
    processed_events.set_pool(None)
    event_id = f"it-claim-{uuid.uuid4().hex}"
    try:
        first = await processed_events.claim_event(event_id, lease_s=0.01)
        assert first is not None
        await asyncio.sleep(0.02)

        second = await processed_events.claim_event(event_id, lease_s=1)
        assert second is not None and second != first
        assert not await processed_events.release_claim(event_id, first)
        assert await processed_events.complete_claim(event_id, second)
        assert await processed_events.claim_event(event_id, lease_s=0.01) is None
    finally:
        await processed_events.unmark_event(event_id)
        processed_events.set_pool(None)
