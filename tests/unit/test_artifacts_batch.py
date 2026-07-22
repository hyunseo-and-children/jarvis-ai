"""AI 생성물 갱신 배치 (이슈 #7, api-spec §4.8 / C-4) — 배치 루프·enrich·search_doc·fetch 배선.

LLM·embed·fetch 주입형 fake 로 구동(라이브 Anthropic/torch/Spring 불필요). 스토어는 주입(격리).
"""

from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.pipelines import embedding as _embedding
from app.pipelines.artifact_store import ArtifactStore, CatalogArtifact, CatalogArtifactStore
from app.pipelines.artifacts_batch import run_artifacts_batch
from app.pipelines.enrichment import enrich_product
from app.schemas.spring import ProductChange, ProductChangesPage


def test_catalog_artifact_store_satisfies_shared_protocol():
    """CatalogArtifactStore(인메모리)·PgCatalogArtifactStore(pg-catalog) 공유 계약 정합 (이슈 #31)."""
    assert isinstance(CatalogArtifactStore(), ArtifactStore)


class _EnrichLLM:
    async def complete(self, *, system, user, tier, max_tokens=1024, json_output=True):
        return json.dumps(
            {"tags": ["여행", "방수"], "attributes": {"소재": "나일론"}}, ensure_ascii=False
        )


def _embed(texts):
    return [[float(len(t)), 1.0] for t in texts]  # 결정적 2차원(값 자체는 미검증)


def _change(pid, status="ACTIVE", name="여행 방수 파우치"):
    return ProductChange(
        product_id=pid,
        status=status,
        updated_at="2026-07-20T00:00:00Z",
        name=name,
        description="설명",
        category="여행용품",
        brand="트래블",
        attributes={"방수": True},
    )


# ── HTTP fake (fetch_product_changes 배선 검증) ──
class _Resp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._data


class _Client:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return self._resp


async def test_enrich_product_returns_extras():
    extras = await enrich_product(
        {"name": "파우치", "category": "여행용품"}, llm=_EnrichLLM(), settings=get_settings()
    )
    assert extras["tags"] == ["여행", "방수"]
    assert extras["attributes"] == {"소재": "나일론"}


def test_build_search_doc_includes_fields_and_tags():
    doc = _embedding.build_search_doc(
        {
            "name": "여행 파우치",
            "category": "여행용품",
            "brand": "트래블",
            "attributes": {"방수": True},
            "extras": {"tags": ["기내반입"], "attributes": {"소재": "나일론"}},
        }
    )
    for token in ("여행 파우치", "여행용품", "트래블", "기내반입", "나일론"):
        assert token in doc


async def test_batch_processes_and_upserts():
    store = CatalogArtifactStore()

    async def fetch(cursor, limit):
        return ProductChangesPage(items=[_change(1), _change(2)], next_cursor="c1", has_more=False)

    result = await run_artifacts_batch(
        fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
    )
    assert result.processed == 2
    assert result.delisted == 0
    assert store.count() == 2
    art = store.get(1)
    assert art is not None and art.embedding and art.search_doc
    assert art.extras["tags"] == ["여행", "방수"]
    assert store.get_cursor() == "c1"


async def test_batch_uses_raw_product_fields_without_retaining_separate_copies():
    store = CatalogArtifactStore()

    async def fetch(cursor, limit):
        return ProductChangesPage(
            items=[_change(1, name="저장하면 안 되는 원본명")],
            next_cursor="c1",
            has_more=False,
        )

    await run_artifacts_batch(
        fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
    )

    artifact = store.get(1)
    assert artifact is not None
    assert "저장하면 안 되는 원본명" in artifact.search_doc
    assert set(vars(artifact)) == {"product_id", "search_doc", "embedding", "extras"}


async def test_batch_delisted_removes_artifact():
    store = CatalogArtifactStore()
    store.upsert(CatalogArtifact(product_id=1, search_doc="x", embedding=[1.0, 0.0]))

    async def fetch(cursor, limit):
        return ProductChangesPage(
            items=[_change(1, status="DELISTED")], next_cursor="c1", has_more=False
        )

    result = await run_artifacts_batch(
        fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
    )
    assert result.delisted == 1
    assert result.processed == 0
    assert store.get(1) is None


async def test_batch_hasmore_loops_and_persists_final_cursor():
    store = CatalogArtifactStore()
    pages = [
        ProductChangesPage(items=[_change(1)], next_cursor="c1", has_more=True),
        ProductChangesPage(items=[_change(2)], next_cursor="c2", has_more=False),
    ]
    seen = []

    async def fetch(cursor, limit):
        seen.append(cursor)
        return pages.pop(0)

    result = await run_artifacts_batch(
        fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
    )
    assert result.pages == 2
    assert result.processed == 2
    assert store.count() == 2
    assert store.get_cursor() == "c2"
    assert seen[1] == "c1"  # 2번째 fetch 는 1페이지 nextCursor 로 이어감


async def test_batch_full_rebuild_starts_from_zero():
    store = CatalogArtifactStore()
    store.set_cursor("old-cursor")
    seen = []

    async def fetch(cursor, limit):
        seen.append(cursor)
        return ProductChangesPage(items=[], next_cursor=None, has_more=False)

    await run_artifacts_batch(
        fetch=fetch,
        llm=_EnrichLLM(),
        embed=_embed,
        store=store,
        settings=get_settings(),
        full_rebuild=True,
    )
    assert seen[0] == "0"


async def test_batch_requires_llm(monkeypatch):
    import app.pipelines.artifacts_batch as ab

    monkeypatch.setattr(ab, "get_llm", lambda: None)
    with pytest.raises(RuntimeError):
        await run_artifacts_batch(
            llm=None, embed=_embed, store=CatalogArtifactStore(), settings=get_settings()
        )


async def test_fetch_product_changes_parses_envelope(monkeypatch):
    import app.services.spring_client as sc

    body = {
        "success": True,
        "data": {
            "items": [{"productId": 7, "status": "ACTIVE", "updatedAt": "t", "name": "n"}],
            "nextCursor": "c9",
            "hasMore": True,
        },
    }
    monkeypatch.setattr(sc, "_client", lambda: _Client(_Resp(200, body)))
    page = await sc.fetch_product_changes("0", 500)
    assert page.has_more is True
    assert page.next_cursor == "c9"
    assert page.items[0].product_id == 7


async def test_fetch_product_changes_error_raises(monkeypatch):
    import app.services.spring_client as sc

    monkeypatch.setattr(sc, "_client", lambda: _Client(_Resp(500, {})))
    with pytest.raises(sc.SpringUnavailableError):
        await sc.fetch_product_changes("0", 500)


async def test_fetch_product_changes_classifies_invalid_cursor(monkeypatch):
    import app.services.spring_client as sc

    body = {
        "success": False,
        "error": {"code": "INVALID_CURSOR", "message": "expired cursor"},
    }
    monkeypatch.setattr(sc, "_client", lambda: _Client(_Resp(400, body)))

    with pytest.raises(sc.InvalidCursorError):
        await sc.fetch_product_changes("expired", 500)


async def test_batch_invalid_cursor_rebuilds_from_zero_atomically():
    import app.services.spring_client as sc

    store = CatalogArtifactStore()
    store.upsert(CatalogArtifact(product_id=99, search_doc="stale", embedding=[0.0, 1.0]))
    store.set_cursor("expired")
    seen = []

    async def fetch(cursor, limit):
        seen.append(cursor)
        if cursor == "expired":
            raise sc.InvalidCursorError("expired cursor")
        return ProductChangesPage(items=[_change(1)], next_cursor="fresh", has_more=False)

    result = await run_artifacts_batch(
        fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
    )

    assert seen == ["expired", "0"]
    assert store.get(99) is None
    assert store.get(1) is not None
    assert store.get_cursor() == "fresh"
    assert result.cursor == "fresh"


async def test_batch_invalid_cursor_rebuild_failure_preserves_rebuild_start_checkpoint():
    import app.services.spring_client as sc

    store = CatalogArtifactStore()
    stale = CatalogArtifact(product_id=99, search_doc="stale", embedding=[0.0, 1.0])
    store.upsert(stale)
    store.set_cursor("expired")

    async def fetch(cursor, limit):
        if cursor == "expired":
            raise sc.InvalidCursorError("expired cursor")
        raise RuntimeError("rebuild failed")

    with pytest.raises(RuntimeError, match="rebuild failed"):
        await run_artifacts_batch(
            fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
        )

    assert store.get(99) == stale
    assert store.get_cursor() == "expired"


async def test_batch_invalid_cursor_after_committed_page_keeps_that_checkpoint_on_rebuild_failure():
    import app.services.spring_client as sc

    store = CatalogArtifactStore()
    stale = CatalogArtifact(product_id=99, search_doc="stale", embedding=[0.0, 1.0])
    store.upsert(stale)
    store.set_cursor("old")
    seen = []

    async def fetch(cursor, limit):
        seen.append(cursor)
        if cursor == "old":
            return ProductChangesPage(items=[_change(1)], next_cursor="page-1", has_more=True)
        if cursor == "page-1":
            raise sc.InvalidCursorError("expired cursor")
        raise RuntimeError("rebuild failed")

    with pytest.raises(RuntimeError, match="rebuild failed"):
        await run_artifacts_batch(
            fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
        )

    assert seen == ["old", "page-1", "0"]
    assert store.get(99) == stale
    assert store.get(1) is not None
    assert store.get_cursor() == "page-1"


async def test_batch_invalid_cursor_after_initial_page_rebuilds_from_zero():
    import app.services.spring_client as sc

    store = CatalogArtifactStore()
    seen = []

    async def fetch(cursor, limit):
        seen.append(cursor)
        if seen == ["0"]:
            return ProductChangesPage(items=[_change(1)], next_cursor="page-1", has_more=True)
        if seen == ["0", "page-1"]:
            raise sc.InvalidCursorError("expired cursor")
        return ProductChangesPage(items=[_change(2)], next_cursor="fresh", has_more=False)

    result = await run_artifacts_batch(
        fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
    )

    assert seen == ["0", "page-1", "0"]
    assert store.get(1) is None
    assert store.get(2) is not None
    assert store.get_cursor() == "fresh"
    assert result.cursor == "fresh"


async def test_batch_invalid_cursor_at_zero_raises_without_repeating_same_request():
    import app.services.spring_client as sc

    store = CatalogArtifactStore()
    seen = []

    async def fetch(cursor, limit):
        seen.append(cursor)
        raise sc.InvalidCursorError("zero cursor rejected")

    with pytest.raises(sc.InvalidCursorError, match="zero cursor rejected"):
        await run_artifacts_batch(
            fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
        )

    assert seen == ["0"]
    assert store.get_cursor() is None


async def test_batch_invalid_cursor_commit_failure_preserves_rebuild_start_checkpoint():
    import app.services.spring_client as sc

    class FailingCommitStore(CatalogArtifactStore):
        fail_commit = False

        def set_cursor(self, cursor):
            if self.fail_commit:
                raise RuntimeError("cursor commit failed")
            super().set_cursor(cursor)

        def replace_all_and_set_cursor(self, artifacts, cursor):
            if self.fail_commit:
                raise RuntimeError("atomic commit failed")
            super().replace_all_and_set_cursor(artifacts, cursor)

    store = FailingCommitStore()
    stale = CatalogArtifact(product_id=99, search_doc="stale", embedding=[0.0, 1.0])
    store.upsert(stale)
    store.set_cursor("expired")
    store.fail_commit = True

    async def fetch(cursor, limit):
        if cursor == "expired":
            raise sc.InvalidCursorError("expired cursor")
        return ProductChangesPage(items=[_change(1)], next_cursor="fresh", has_more=False)

    with pytest.raises(RuntimeError, match="commit failed"):
        await run_artifacts_batch(
            fetch=fetch, llm=_EnrichLLM(), embed=_embed, store=store, settings=get_settings()
        )

    assert store.get(99) == stale
    assert store.get(1) is None
    assert store.get_cursor() == "expired"


async def test_batch_full_rebuild_replaces_stale():
    store = CatalogArtifactStore()
    store.upsert(CatalogArtifact(product_id=99, search_doc="old", embedding=[0.0, 1.0]))  # stale
    store.set_cursor("old")

    async def fetch(cursor, limit):
        return ProductChangesPage(items=[_change(1)], next_cursor="c1", has_more=False)

    result = await run_artifacts_batch(
        fetch=fetch,
        llm=_EnrichLLM(),
        embed=_embed,
        store=store,
        settings=get_settings(),
        full_rebuild=True,
    )
    assert store.get(99) is None  # stale 원자 교체로 제거(finding 1)
    assert store.get(1) is not None
    assert store.count() == 1
    assert store.get_cursor() == "c1"
    assert result.processed == 1


async def test_batch_full_rebuild_preserves_on_failure():
    store = CatalogArtifactStore()
    store.upsert(CatalogArtifact(product_id=99, search_doc="old", embedding=[0.0, 1.0]))

    async def fetch(cursor, limit):
        raise RuntimeError("rebuild boom")

    with pytest.raises(RuntimeError):
        await run_artifacts_batch(
            fetch=fetch,
            llm=_EnrichLLM(),
            embed=_embed,
            store=store,
            settings=get_settings(),
            full_rebuild=True,
        )
    assert store.get(99) is not None  # 재구축 실패 시 기존 데이터 보존(원자 교체)


async def test_fetch_product_changes_failure_envelope_raises(monkeypatch):
    import app.services.spring_client as sc

    monkeypatch.setattr(
        sc, "_client", lambda: _Client(_Resp(200, {"success": False, "data": None}))
    )
    with pytest.raises(sc.SpringUnavailableError):
        await sc.fetch_product_changes("0", 500)


async def test_fetch_product_changes_data_null_raises(monkeypatch):
    import app.services.spring_client as sc

    monkeypatch.setattr(sc, "_client", lambda: _Client(_Resp(200, {"success": True, "data": None})))
    with pytest.raises(sc.SpringUnavailableError):
        await sc.fetch_product_changes("0", 500)
