"""buyer 스레드 상태(ThreadFilter/Cart/Revert) AsyncPostgresStore 통합 테스트 (이슈 #33).

`docker compose up -d pg-profile` 로 컨테이너가 떠 있어야 통과한다. 기본 pytest 실행에서는
@pytest.mark.integration 으로 제외된다(pyproject.toml addopts) — 명시적으로
`uv run pytest tests/integration -m integration` 로 실행한다.

InMemoryStore 는 유닛 테스트가 계속 쓰므로 여기서 건드리지 않는다(tests/conftest.py InMemory
격리 컨벤션, test_pg_artifact_store.py 와 동일 원칙 — 실 인프라 테스트는 분리). 키는 매 테스트
uuid 로 발급해 재실행 간 충돌·잔여 데이터 간섭을 피한다(로컬 dev 볼륨은 소모성).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from langgraph.store.postgres.aio import AsyncPostgresStore

from app.agents.buyer.cart import state as cart_state
from app.agents.buyer.cart.state import CartStateStore, PendingAdd
from app.agents.buyer.graph import ThreadFilterStore
from app.agents.buyer.recommendation.state import RevertStore
from app.core import pg_store as pg_store_module
from app.core.config import get_settings
from app.core.pg_resilience import hardened_pg_conninfo, state_store_pool_config
from app.schemas.spring import CartOption, ProductSearchFilters

pytestmark = pytest.mark.integration


def _key() -> str:
    return f"it:{uuid.uuid4().hex}"


@pytest.fixture
async def pg_store():
    async with AsyncPostgresStore.from_conn_string(get_settings().profile_db_url) as store:
        await store.setup()
        yield store


async def test_thread_filter_store_roundtrip(pg_store) -> None:
    wrapper = ThreadFilterStore(pg_store)
    key = _key()
    await wrapper.put(key, ProductSearchFilters(category="이어폰", price_max=50000))
    fetched = await wrapper.get(key)
    assert fetched is not None
    assert fetched.category == "이어폰"
    assert fetched.price_max == 50000


async def test_thread_filter_store_missing_key_returns_none(pg_store) -> None:
    wrapper = ThreadFilterStore(pg_store)
    assert await wrapper.get(_key()) is None


async def test_cart_state_store_last_reco_roundtrip(pg_store) -> None:
    wrapper = CartStateStore(pg_store)
    key = _key()
    await wrapper.set_last_reco(key, [(101, "이어폰"), (102, "케이스")])
    reco = await wrapper.get_last_reco(key)
    assert reco == [(101, "이어폰"), (102, "케이스")]


async def test_cart_state_store_pending_roundtrip(pg_store) -> None:
    wrapper = CartStateStore(pg_store)
    key = _key()
    pending = PendingAdd(
        product_id=101,
        quantity=2,
        options=[CartOption(option_id=3, name="블루", extra_price=1000)],
        attempts=1,
    )
    await wrapper.set_pending(key, pending)
    fetched = await wrapper.get_pending(key)
    assert fetched is not None
    assert fetched.product_id == 101 and fetched.quantity == 2 and fetched.attempts == 1
    assert fetched.options[0].name == "블루" and fetched.options[0].extra_price == 1000

    await wrapper.clear_pending(key)
    assert await wrapper.get_pending(key) is None


async def test_revert_store_accumulates_categories(pg_store) -> None:
    wrapper = RevertStore(pg_store)
    key = _key()
    await wrapper.add(key, ["조미료"])
    await wrapper.add(key, ["세제"])
    assert await wrapper.get(key) == {"조미료", "세제"}


async def test_revert_store_add_concurrent_calls_no_lost_update(pg_store) -> None:
    """동시 add() 호출이 서로의 갱신을 잃지 않는다 — get→put 락 없으면 lost update(PR #46 리뷰)."""
    wrapper = RevertStore(pg_store)
    key = _key()
    await asyncio.gather(
        wrapper.add(key, ["A"]),
        wrapper.add(key, ["B"]),
        wrapper.add(key, ["C"]),
    )
    assert await wrapper.get(key) == {"A", "B", "C"}


async def test_state_persists_across_store_instances() -> None:
    """재시작·다중 인스턴스 스모크(이슈 #33 범위) — 새 연결로도 이전에 쓴 productId 가 보인다.

    상품명(product.name 사본)은 규칙상 pg-profile 에 저장하지 않고 프로세스 로컬 캐시에만
    두므로(PR #46 후속 리뷰), 재시작(=캐시 소실) 후에는 pid 만 복원되고 이름은 "" 로 degrade
    한다("그거 담아줘" pid 해소는 계속 작동)."""
    key = _key()
    async with AsyncPostgresStore.from_conn_string(get_settings().profile_db_url) as store_a:
        await store_a.setup()
        await CartStateStore(store_a).set_last_reco(key, [(999, "영속성 테스트")])

    cart_state._last_reco_names.clear()  # 프로세스 재시작 시 휘발성 이름 캐시 소실 재현

    async with AsyncPostgresStore.from_conn_string(get_settings().profile_db_url) as store_b:
        await store_b.setup()
        reco = await CartStateStore(store_b).get_last_reco(key)
    assert reco == [(999, "")]  # pid 는 영속, 이름은 재시작 후 소실(graceful degrade)


async def test_revert_store_concurrent_writes_across_postgres_pools() -> None:
    """서로 다른 앱 인스턴스(pool)의 RMW도 advisory lock으로 lost update가 없다."""
    key = _key()
    conninfo = hardened_pg_conninfo(get_settings().profile_db_url)
    pool_config = state_store_pool_config()
    async with AsyncPostgresStore.from_conn_string(
        conninfo, pool_config=pool_config
    ) as store_a, AsyncPostgresStore.from_conn_string(
        conninfo, pool_config=pool_config
    ) as store_b:
        await store_a.setup()
        await store_b.setup()
        wrappers = [RevertStore(store_a), RevertStore(store_b)]
        expected = {f"category-{i}" for i in range(20)}
        await asyncio.gather(
            *(wrappers[i % 2].add(key, [category]) for i, category in enumerate(expected))
        )
        assert await wrappers[0].get(key) == expected


async def test_hardened_store_connection_parameters_are_active() -> None:
    """실 pg-profile 세션에 statement/TCP timeout 설정이 적용된다."""
    settings = get_settings()
    async with AsyncPostgresStore.from_conn_string(
        hardened_pg_conninfo(settings.profile_db_url),
        pool_config=state_store_pool_config(),
    ) as store:
        async with store.conn.connection() as conn:
            row = await (await conn.execute("SHOW statement_timeout")).fetchone()
            statement_timeout = row["statement_timeout"]
            params = conn.info.get_parameters()

    assert statement_timeout in {
        f"{int(settings.state_store_query_timeout_s * 1000)}ms",
        f"{settings.state_store_query_timeout_s:g}s",
    }
    assert params["tcp_user_timeout"] == str(settings.state_store_tcp_user_timeout_ms)


async def test_pg_store_module_connects_to_real_postgres() -> None:
    """app.core.pg_store.get_store() 가 실제로 AsyncPostgresStore(pg-profile)에 연결된다.

    conftest 의 reset_*_store() 는 매 테스트 InMemoryStore 로 되돌리므로, 여기서는
    set_store(None) 으로 재초기화를 강제해 지연 연결 경로 자체를 검증한다.
    """
    pg_store_module.set_store(None)
    try:
        store = await pg_store_module.get_store()
        assert isinstance(store, AsyncPostgresStore)
        key = _key()
        await CartStateStore(store).set_last_reco(key, [(1, "a")])
        assert await CartStateStore(store).get_last_reco(key) == [(1, "a")]
    finally:
        pg_store_module.set_store(None)


async def test_pg_store_get_store_concurrent_calls_single_connection() -> None:
    """동시 get_store() 호출이 커넥션을 중복 생성하지 않는다(락 없으면 콜드 스타트 레이스, PR #46 리뷰)."""
    pg_store_module.set_store(None)
    try:
        stores = await asyncio.gather(*(pg_store_module.get_store() for _ in range(10)))
        assert len({id(s) for s in stores}) == 1
    finally:
        pg_store_module.set_store(None)


async def test_set_store_none_defers_cleanup_to_next_get_store_call() -> None:
    """set_store(None) 이 sync 컨텍스트에서 놓친 정리를 다음 get_store() 가 확실히 처리한다.

    fire-and-forget(asyncio.get_running_loop().create_task) 방식은 실행 중인
    이벤트 루프가 없으면(예: conftest 의 sync autouse fixture) 조용히 스킵돼
    실제로 한 번도 정리가 안 됐었다(PR #46 후속 리뷰) — 지연 정리 큐로 교체 후,
    다음 get_store() 호출 시 확실히 __aexit__ 가 실행되는지 conn.closed 로 검증한다.
    """
    pg_store_module.set_store(None)
    store = await pg_store_module.get_store()
    assert isinstance(store, AsyncPostgresStore)
    conn = store.conn
    assert not conn.closed

    pg_store_module.set_store(None)  # sync 호출 — 정리는 아직 큐에만 쌓인 상태
    assert not conn.closed  # 아직 정리 안 됨(다음 get_store() 전까지)

    await pg_store_module.get_store()  # 이 호출 진입 시 _drain_pending_cleanup() 이 실행됨
    assert conn.closed

    pg_store_module.set_store(None)
