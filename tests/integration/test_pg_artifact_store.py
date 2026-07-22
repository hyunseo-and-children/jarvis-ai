"""PgCatalogArtifactStore 통합 테스트 (이슈 #31, api-spec §4.8) — 실 pg-catalog 필요.

`docker compose up -d pg-catalog` 로 컨테이너가 떠 있어야 통과한다. 기본 pytest 실행에서는
@pytest.mark.integration 으로 제외된다(pyproject.toml addopts) — 명시적으로
`uv run pytest tests/integration -m integration` 로 실행한다.

CatalogArtifactStore(인메모리)는 유닛 테스트가 계속 쓰므로 여기서 건드리지 않는다
(tests/conftest.py InMemory 격리 컨벤션, 커밋 5066ecf 와 동일 원칙 — 실 인프라 테스트는 분리).
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.pipelines.artifact_store import CatalogArtifact
from app.pipelines.pg_artifact_store import PgCatalogArtifactStore

pytestmark = pytest.mark.integration

_DIM = 1536


def _vec(*nonzero: float) -> list[float]:
    """products.embedding 은 vector(1536) 고정 — 앞자리만 채우고 나머지는 0으로 패딩."""
    out = [0.0] * _DIM
    for i, v in enumerate(nonzero):
        out[i] = v
    return out


@pytest.fixture
def store():
    s = PgCatalogArtifactStore(get_settings().catalog_db_url)
    s.clear()
    s.set_cursor(None)
    yield s
    s.clear()
    s.set_cursor(None)
    s.close()


def test_upsert_and_get_roundtrip(store):
    store.upsert(
        CatalogArtifact(
            product_id=1,
            search_doc="여행 방수 파우치",
            embedding=_vec(0.6, 0.8),
            extras={"tags": ["여행"]},
        )
    )
    art = store.get(1)
    assert art is not None
    assert art.product_id == 1
    assert art.search_doc == "여행 방수 파우치"
    assert art.embedding == pytest.approx(_vec(0.6, 0.8))
    assert art.extras == {"tags": ["여행"]}


def test_upsert_is_idempotent_update(store):
    store.upsert(CatalogArtifact(product_id=1, search_doc="old", embedding=_vec(1.0)))
    store.upsert(CatalogArtifact(product_id=1, search_doc="new", embedding=_vec(0.0, 1.0)))
    assert store.count() == 1
    assert store.get(1).search_doc == "new"


def test_delete_removes_artifact(store):
    store.upsert(CatalogArtifact(product_id=1, search_doc="x", embedding=_vec(1.0)))
    store.delete(1)
    assert store.get(1) is None


def test_get_missing_returns_none(store):
    assert store.get(999) is None


def test_all_returns_every_artifact(store):
    store.upsert(CatalogArtifact(product_id=1, search_doc="a", embedding=_vec(1.0)))
    store.upsert(CatalogArtifact(product_id=2, search_doc="b", embedding=_vec(0.0, 1.0)))
    ids = {a.product_id for a in store.all()}
    assert ids == {1, 2}


def test_replace_all_atomic_swap_removes_stale(store):
    store.upsert(CatalogArtifact(product_id=99, search_doc="stale", embedding=_vec(1.0)))
    store.replace_all([CatalogArtifact(product_id=1, search_doc="fresh", embedding=_vec(0.0, 1.0))])
    assert store.get(99) is None
    assert store.get(1) is not None
    assert store.count() == 1


def test_replace_all_and_cursor_commit_together(store):
    store.upsert(CatalogArtifact(product_id=99, search_doc="stale", embedding=_vec(1.0)))
    store.set_cursor("old")

    store.replace_all_and_set_cursor(
        [CatalogArtifact(product_id=1, search_doc="fresh", embedding=_vec(0.0, 1.0))],
        "fresh-cursor",
    )

    assert store.get(99) is None
    assert store.get(1) is not None
    assert store.get_cursor() == "fresh-cursor"


def test_cursor_persists_across_store_instances(store):
    store.set_cursor("c42")
    other = PgCatalogArtifactStore(get_settings().catalog_db_url)
    try:
        assert other.get_cursor() == "c42"
    finally:
        other.close()


def test_cursor_defaults_to_none(store):
    assert store.get_cursor() is None


def test_pg_store_satisfies_shared_protocol(store):
    from app.pipelines.artifact_store import ArtifactStore

    assert isinstance(store, ArtifactStore)
