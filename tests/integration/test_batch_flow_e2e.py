"""AI 생성물 배치 흐름 E2E 스모크 (이슈 #35) — I-17 pull → enrich → search_doc → 임베딩 → upsert.

api-spec §4.8(I-17 변경분 pull, hasMore 루프·커서 전진·DELISTED)이 실 HTTP 경계를 통해
동작하는지 확인한다. 임베딩은 주입형 fake(torch 미설치 CI 에서도 동작).
AI Postgres 에는 **AI 생성물만** 저장한다 — 상품 원본 컬럼 사본 금지(§4.8).
"""

from __future__ import annotations

import pytest

from app.pipelines.artifact_store import CatalogArtifactStore
from app.pipelines.artifacts_batch import run_artifacts_batch
from tests.integration._stubs import ScriptedLLM


def fake_embed(texts: list[str]) -> list[list[float]]:
    """결정적 임베딩 대역 — 문자 코드 기반 3차원 벡터(torch 불필요)."""
    return [[float(len(t)), float(sum(ord(c) for c in t) % 97), 1.0] for t in texts]


def _change(product_id: int, **overrides) -> dict:
    change = {
        "productId": product_id,
        "status": "ACTIVE",
        "updatedAt": "2026-07-20T00:00:00Z",
        "name": f"상품-{product_id}",
        "description": "여행용 방수 파우치",
        "category": "여행용품",
        "brand": "트래블러",
    }
    change.update(overrides)
    return change


@pytest.fixture
def batch_llm() -> ScriptedLLM:
    """배치 전용 LLM 대역 (enrichment 분기)."""
    return ScriptedLLM()


async def test_batch_pulls_and_upserts_artifacts(spring, batch_llm) -> None:
    """I-17 1페이지 pull → 생성물 upsert. 저장물은 AI 산출물(search_doc·임베딩·extras)뿐이다."""
    spring.changes_pages = [
        {"since": "0", "items": [_change(101), _change(102)], "nextCursor": "c1", "hasMore": False}
    ]
    store = CatalogArtifactStore()

    result = await run_artifacts_batch(llm=batch_llm, embed=fake_embed, store=store)

    assert result.processed == 2
    assert result.pages == 1
    artifact = store.get(101)
    assert artifact is not None
    assert artifact.embedding and artifact.extras["tags"]
    assert "여행" in artifact.search_doc
    # 실 HTTP 경계를 지났는가 — X-Internal-Token + since 커서(§2.3 레인 c·§4.8)
    req = spring.requests_to("/internal/products/changes")[0]
    assert req["headers"]["x-internal-token"] == "e2e-internal-token"
    assert req["query"]["since"] == "0"


async def test_batch_follows_has_more_pagination(spring, batch_llm) -> None:
    """hasMore=True 면 nextCursor 로 같은 주기 안에서 이어 받는다 (§4.8)."""
    spring.changes_pages = [
        {"since": "0", "items": [_change(101)], "nextCursor": "c1", "hasMore": True},
        {"since": "c1", "items": [_change(102)], "nextCursor": "c2", "hasMore": False},
    ]
    store = CatalogArtifactStore()

    result = await run_artifacts_batch(llm=batch_llm, embed=fake_embed, store=store)

    assert result.pages == 2
    assert result.processed == 2
    assert store.get(102) is not None
    assert [r["query"]["since"] for r in spring.requests_to("/internal/products/changes")] == [
        "0",
        "c1",
    ]


async def test_batch_cursor_advances_for_next_cycle(spring, batch_llm) -> None:
    """커서는 페이지 처리 성공 후 전진 — 다음 주기는 그 지점부터 pull 한다 (§4.8)."""
    spring.changes_pages = [
        {"since": "0", "items": [_change(101)], "nextCursor": "c1", "hasMore": False},
        {"since": "c1", "items": [_change(103)], "nextCursor": "c2", "hasMore": False},
    ]
    store = CatalogArtifactStore()

    await run_artifacts_batch(llm=batch_llm, embed=fake_embed, store=store)
    assert store.get_cursor() == "c1"

    await run_artifacts_batch(llm=batch_llm, embed=fake_embed, store=store)
    assert store.get(103) is not None, "2주기는 커서 이후 변경분을 받아야 한다"


async def test_batch_removes_delisted_artifacts(spring, batch_llm) -> None:
    """DELISTED 는 생성물을 삭제한다 — 판매 종료 상품이 추천 후보에 남지 않게(§4.8)."""
    spring.changes_pages = [
        {"since": "0", "items": [_change(101)], "nextCursor": "c1", "hasMore": False},
        {
            "since": "c1",
            "items": [_change(101, status="DELISTED")],
            "nextCursor": "c2",
            "hasMore": False,
        },
    ]
    store = CatalogArtifactStore()

    await run_artifacts_batch(llm=batch_llm, embed=fake_embed, store=store)
    assert store.get(101) is not None

    result = await run_artifacts_batch(llm=batch_llm, embed=fake_embed, store=store)
    assert result.delisted == 1
    assert store.get(101) is None


async def test_full_rebuild_replaces_atomically(spring, batch_llm) -> None:
    """full_rebuild 는 since=0 부터 임시 스토어에 쌓고 성공 시 원자 교체 — stale 제거(§4.8)."""
    spring.changes_pages = [
        {"since": "0", "items": [_change(102)], "nextCursor": "c9", "hasMore": False}
    ]
    store = CatalogArtifactStore()
    # 이전 주기의 잔재(더 이상 변경분에 없는 상품)
    await run_artifacts_batch(llm=batch_llm, embed=fake_embed, store=store)
    spring.changes_pages = [
        {"since": "0", "items": [_change(103)], "nextCursor": "c9", "hasMore": False}
    ]

    await run_artifacts_batch(llm=batch_llm, embed=fake_embed, store=store, full_rebuild=True)

    assert store.get(103) is not None
    assert store.get(102) is None, "재구축은 더 이상 없는 상품의 stale 생성물을 제거한다"


async def test_batch_degrades_without_advancing_cursor_on_failure(spring, batch_llm) -> None:
    """Spring 도달 불가면 커서를 전진시키지 않는다 — 다음 주기 자연 복구(§4.8)."""
    from app.services.spring_client import SpringUnavailableError

    spring.changes_pages = []  # 매칭 없음 → 빈 페이지. 실패는 아래에서 직접 주입.
    store = CatalogArtifactStore()

    async def failing_fetch(cursor, limit):
        raise SpringUnavailableError("spring down")

    with pytest.raises(SpringUnavailableError):
        await run_artifacts_batch(fetch=failing_fetch, llm=batch_llm, embed=fake_embed, store=store)

    assert store.get_cursor() is None, "실패 주기는 커서를 전진시키지 않는다"
