"""buyer 스레드 상태(ThreadFilter/Cart/Revert) AsyncPostgresStore 통합 테스트 (이슈 #33).

`docker compose up -d pg-profile` 로 컨테이너가 떠 있어야 통과한다. 기본 pytest 실행에서는
@pytest.mark.integration 으로 제외된다(pyproject.toml addopts) — 명시적으로
`uv run pytest tests/integration -m integration` 로 실행한다.

InMemoryStore 는 유닛 테스트가 계속 쓰므로 여기서 건드리지 않는다(tests/conftest.py InMemory
격리 컨벤션, test_pg_artifact_store.py 와 동일 원칙 — 실 인프라 테스트는 분리). 키는 매 테스트
uuid 로 발급해 재실행 간 충돌·잔여 데이터 간섭을 피한다(로컬 dev 볼륨은 소모성).
"""

from __future__ import annotations

import uuid

import pytest
from langgraph.store.postgres.aio import AsyncPostgresStore

from app.agents.buyer.cart.state import CartStateStore, PendingAdd
from app.agents.buyer.graph import ThreadFilterStore
from app.agents.buyer.recommendation.state import RevertStore
from app.core import pg_store as pg_store_module
from app.core.config import get_settings
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


async def test_state_persists_across_store_instances() -> None:
    """재시작·다중 인스턴스 스모크(이슈 #33 범위) — 새 연결로도 이전에 쓴 값이 보인다."""
    key = _key()
    async with AsyncPostgresStore.from_conn_string(get_settings().profile_db_url) as store_a:
        await store_a.setup()
        await CartStateStore(store_a).set_last_reco(key, [(999, "영속성 테스트")])

    async with AsyncPostgresStore.from_conn_string(get_settings().profile_db_url) as store_b:
        await store_b.setup()
        reco = await CartStateStore(store_b).get_last_reco(key)
    assert reco == [(999, "영속성 테스트")]


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
