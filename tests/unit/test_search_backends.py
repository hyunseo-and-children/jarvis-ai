"""임베딩 검색 백엔드 (이슈 #7, §4.8 결정 2026-07-20) — 방식1/방식2 + 골든셋 비교.

embed·store 주입형 fake(어휘 기반 결정적 임베딩). 방식1 라이브 hydrate 는 C-17 미착수(에러) 검증.
"""

from __future__ import annotations

import pytest

from app.pipelines import embedding as _embedding
from app.pipelines.artifact_store import CatalogArtifact, CatalogArtifactStore
from app.pipelines.compare import GoldenCase, compare_backends, recall_at_k
from app.schemas.spring import ProductSearchFilters, ProductSearchResult, SpringProduct
from app.services import search_service, spring_client
from app.services.search_service import (
    EmbeddingRerankBackend,
    VectorSearchBackend,
    _cosine,
    vector_rank,
)

_VOCAB = ["여행", "방수", "이어폰", "무선", "린넨", "셔츠"]


def _embed(texts):
    return [[1.0 if w in t else 0.0 for w in _VOCAB] for t in texts]


def _seed_store():
    store = CatalogArtifactStore()
    store.upsert(
        CatalogArtifact(
            product_id=1,
            search_doc="여행 방수 파우치",
            embedding=_embed(["여행 방수"])[0],
        )
    )
    store.upsert(
        CatalogArtifact(
            product_id=2,
            search_doc="무선 이어폰",
            embedding=_embed(["무선 이어폰"])[0],
        )
    )
    store.upsert(
        CatalogArtifact(
            product_id=3,
            search_doc="린넨 셔츠",
            embedding=_embed(["린넨 셔츠"])[0],
        )
    )
    return store


def test_cosine_basic():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([], [1.0]) == -1.0


def test_vector_rank_orders_by_similarity():
    store = _seed_store()
    ranked = vector_rank(_embed(["여행 방수"])[0], store, k=3)
    assert ranked[0] == 1


async def test_embedding_rerank_backend_reorders(monkeypatch):
    store = _seed_store()

    async def fake_search(filters):
        return ProductSearchResult(
            products=[
                SpringProduct(product_id=3, name="셔츠", price=10),
                SpringProduct(product_id=2, name="이어폰", price=20),
                SpringProduct(product_id=1, name="파우치", price=30),
            ],
            total_count=3,
        )

    monkeypatch.setattr(spring_client, "search_products", fake_search)
    backend = EmbeddingRerankBackend(store=store, embed=_embed)
    result = await backend.search(ProductSearchFilters(keyword="여행 방수", limit=10))
    assert [p.product_id for p in result.products][0] == 1  # 재정렬로 여행 방수가 최상위


async def test_embedding_rerank_offloads_scoring_to_thread(monkeypatch):
    store = _seed_store()
    calls = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(search_service.asyncio, "to_thread", fake_to_thread)

    async def fake_search(filters):
        return ProductSearchResult(
            products=[SpringProduct(product_id=1, name="파우치", price=30)], total_count=1
        )

    monkeypatch.setattr(spring_client, "search_products", fake_search)
    backend = EmbeddingRerankBackend(store=store, embed=_embed)
    result = await backend.search(ProductSearchFilters(keyword="여행 방수", limit=10))

    # 임베딩 호출(_embed)·store 조회+정렬(_rerank) 둘 다 스레드로 오프로드됨(PR #42 리뷰)
    assert calls == [_embed, backend._rerank]
    assert result.products[0].product_id == 1


async def test_embedding_rerank_passthrough_without_keyword(monkeypatch):
    store = _seed_store()

    async def fake_search(filters):
        return ProductSearchResult(
            products=[SpringProduct(product_id=9, name="x", price=1)], total_count=1
        )

    monkeypatch.setattr(spring_client, "search_products", fake_search)
    backend = EmbeddingRerankBackend(store=store, embed=_embed)
    result = await backend.search(ProductSearchFilters(limit=10))  # keyword 없음
    assert [p.product_id for p in result.products] == [9]


async def test_vector_backend_without_hydrate_signals_c17():
    backend = VectorSearchBackend(store=_seed_store(), embed=_embed)  # hydrate 미주입
    with pytest.raises(spring_client.SpringUnavailableError):
        await backend.search(ProductSearchFilters(keyword="여행", limit=5))


async def test_vector_backend_with_hydrate_returns_ranked_and_receives_filters():
    store = _seed_store()
    seen = {}

    async def hydrate(ids, filters):
        seen["ids"] = ids
        seen["filters"] = filters
        # Spring 이 필터·가용성 적용했다고 가정, 벡터 순서 보존해 상위 limit 반환
        return ProductSearchResult(
            products=[SpringProduct(product_id=i, name="n", price=1) for i in ids[: filters.limit]],
            total_count=len(ids),
        )

    backend = VectorSearchBackend(store=store, embed=_embed, hydrate=hydrate, over_fetch=4)
    result = await backend.search(
        ProductSearchFilters(keyword="무선 이어폰", limit=3, category="이어폰")
    )
    assert result.products[0].product_id == 2
    assert seen["filters"].category == "이어폰"  # 필터가 hydrate 로 전달됨(리뷰 반영, finding 2)


async def test_vector_backend_offloads_ranking_to_thread(monkeypatch):
    store = _seed_store()
    calls = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(search_service.asyncio, "to_thread", fake_to_thread)

    async def hydrate(ids, filters):
        return ProductSearchResult(
            products=[SpringProduct(product_id=i, name="n", price=1) for i in ids[: filters.limit]],
            total_count=len(ids),
        )

    backend = VectorSearchBackend(store=store, embed=_embed, hydrate=hydrate)
    result = await backend.search(ProductSearchFilters(keyword="무선 이어폰", limit=3))

    # 임베딩 호출(_embed)·store.all() 스캔(vector_rank) 둘 다 스레드로 오프로드됨(PR #42 리뷰)
    assert calls == [_embed, vector_rank]
    assert result.products


def test_cosine_dim_mismatch_excluded():
    assert _cosine([1.0, 0.0, 0.0], [1.0, 0.0]) == -1.0  # 차원 불일치 → 제외(finding 3)


def test_recall_at_k():
    assert recall_at_k([1, 2, 3], {1, 4}, 3) == pytest.approx(0.5)
    assert recall_at_k([], {1}, 3) == 0.0
    assert recall_at_k([1], set(), 3) == 0.0


def test_compare_backends_reports_both_methods():
    store = _seed_store()
    cases = [
        GoldenCase(query="여행 방수", relevant_ids={1}),
        GoldenCase(query="무선 이어폰", relevant_ids={2}),
    ]

    def candidates(_q):
        return [1, 2, 3]

    report = compare_backends(cases, store=store, embed=_embed, candidates=candidates, k=3)
    assert report.method1.mean_recall_at_k == pytest.approx(1.0)
    assert report.method2.mean_recall_at_k == pytest.approx(1.0)
    assert 0.0 <= report.mean_overlap <= 1.0


# ── 이슈 #65: 비대칭 임베딩 바인딩 — 미주입 기본값이 질의(QUERY) task_type 을 바인딩하는지 ──


def test_rerank_backend_default_embed_binds_query_task(monkeypatch):
    seen = {}

    def spy(texts, *, task_type=None):
        seen["task_type"] = task_type
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(_embedding, "embed_texts", spy)
    backend = EmbeddingRerankBackend()
    backend._embed(["질의"])
    assert seen["task_type"] == "RETRIEVAL_QUERY"


def test_vector_backend_default_embed_binds_query_task(monkeypatch):
    seen = {}

    def spy(texts, *, task_type=None):
        seen["task_type"] = task_type
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(_embedding, "embed_texts", spy)
    backend = VectorSearchBackend()
    backend._embed(["질의"])
    assert seen["task_type"] == "RETRIEVAL_QUERY"
