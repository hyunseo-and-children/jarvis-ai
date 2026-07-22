"""AI 생성물 갱신 배치 — I-17 pull 러너 (api-spec §4.8, C-4, 이슈 #7).

fetch_product_changes 로 변경분을 커서 기반 pull(hasMore 루프) → HIDDEN 은 생성물 삭제 →
ON_SALE 은 enrich(Haiku) → search_doc 조립 → 임베딩 → artifact_store upsert. 커서는 페이지 처리
성공 후에만 전진(자연 복구, §4.8).

증분(기본): 대상 스토어에 직접 upsert 하고 페이지마다 커서 전진.
전체 재구축(full_rebuild): since="0" 부터 **임시 스토어**에 쌓은 뒤, 성공 시 원자 교체(replace_all)한다
  — 재구축 중 실패해도 기존 정상 데이터가 보존되고, 더 이상 존재하지 않는 상품의 stale artifact 가 제거된다.

fetch·llm·embed·store 는 주입형(테스트·오프라인 대체) — torch 미설치 환경에서도 embed 주입으로 동작.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.llm import LLMClient, get_llm
from app.pipelines import embedding as _embedding
from app.pipelines.artifact_store import (
    ArtifactStore,
    CatalogArtifact,
    CatalogArtifactStore,
    get_catalog_store,
)
from app.pipelines.enrichment import enrich_product
from app.schemas.spring import ProductChange, ProductChangesPage
from app.services import spring_client

_log = logging.getLogger(__name__)
_HIDDEN = "HIDDEN"

Fetch = Callable[[str | None, int], Awaitable[ProductChangesPage]]
Embed = Callable[[list[str]], list[list[float]]]


@dataclass
class BatchResult:
    processed: int
    hidden: int
    pages: int
    cursor: str | None


async def _process_change(
    change: ProductChange,
    *,
    llm: LLMClient,
    embed: Embed,
    store: ArtifactStore,
    settings: Settings,
) -> None:
    product = {
        "name": change.name,
        "description": change.description,
        "category": change.category,
        "brand": change.brand,
        "attributes": change.attributes,
    }
    extras = await enrich_product(product, llm=llm, settings=settings)
    doc = _embedding.build_search_doc({**product, "extras": extras})
    vec = embed([doc])[0]
    store.upsert(
        CatalogArtifact(
            product_id=change.product_id,
            search_doc=doc,
            embedding=vec,
            extras=extras,
            # 임베딩 프로비넌스(이슈 #65, embedding_meta_complete CHECK 대응).
            # embed_dim 은 실제 반환 벡터 길이에서 도출 — embed 주입 교체 시에도 기록값이
            # 실제 벡터와 어긋나지 않는다(PR 리뷰). model·task 는 벡터에 없어 settings 소관.
            embed_model=settings.embedding_model_id,
            embed_dim=len(vec),
            embed_task=settings.embedding_task_document,
            normalized=settings.embedding_normalized,
        )
    )


async def _drain(
    fetch: Fetch,
    start_cursor: str,
    target: ArtifactStore,
    *,
    llm: LLMClient,
    embed: Embed,
    settings: Settings,
    persist_cursor: bool,
) -> BatchResult:
    """start_cursor 부터 hasMore 소진까지 target 에 반영한다. persist_cursor=True 면 페이지마다 커서 전진.

    페이지 처리 중 예외는 그대로 전파 — 해당 페이지 커서 미전진으로 다음 주기 재개(자연 복구).
    이미 성공한 앞 페이지는 artifact와 커서가 함께 저장된 유효 체크포인트이므로 롤백하지 않는다.
    """
    cursor = start_cursor
    processed = hidden = pages = 0
    while True:
        page = await fetch(cursor, settings.catalog_batch_page_size)
        for change in page.items:
            if change.status == _HIDDEN:
                target.delete(change.product_id)
                hidden += 1
                continue
            await _process_change(change, llm=llm, embed=embed, store=target, settings=settings)
            processed += 1
        pages += 1
        if page.next_cursor:
            cursor = page.next_cursor
        if persist_cursor:
            target.set_cursor(cursor)  # 페이지 처리 성공 후에만 전진(자연 복구)
        if not page.has_more:
            break
        if not page.next_cursor:
            _log.warning("hasMore=True 이나 nextCursor 없음 — 배치 중단(무한루프 방지)")
            break
    return BatchResult(processed=processed, hidden=hidden, pages=pages, cursor=cursor)


async def run_artifacts_batch(
    *,
    fetch: Fetch | None = None,
    llm: LLMClient | None = None,
    embed: Embed | None = None,
    store: ArtifactStore | None = None,
    settings: Settings | None = None,
    full_rebuild: bool = False,
) -> BatchResult:
    """I-17 배치 1회 실행. full_rebuild=True 면 since="0" 초기 전체 구축(원자 교체)."""
    settings = settings or get_settings()
    fetch = fetch or spring_client.fetch_product_changes
    llm = llm or get_llm()
    # 미주입 기본값은 문서(document) 임베딩 — 비대칭 임베딩 바인딩(이슈 #65)
    embed = embed or functools.partial(
        _embedding.embed_texts, task_type=settings.embedding_task_document
    )
    store = store or get_catalog_store()
    if llm is None:
        raise RuntimeError(
            "run_artifacts_batch: LLM 미구성 — enrichment 불가(config anthropic_api_key)"
        )

    async def rebuild() -> BatchResult:
        # 임시 스토어에 전체 구축 후 성공 시 원자 교체 — 중간 실패해도 기존 데이터 보존 + stale 제거.
        work = CatalogArtifactStore()
        rebuilt = await _drain(
            fetch, "0", work, llm=llm, embed=embed, settings=settings, persist_cursor=False
        )
        store.replace_all_and_set_cursor(work.all(), rebuilt.cursor)
        return rebuilt

    did_rebuild = full_rebuild
    if full_rebuild:
        result = await rebuild()
    else:
        start = store.get_cursor() or "0"
        try:
            result = await _drain(
                fetch, start, store, llm=llm, embed=embed, settings=settings, persist_cursor=True
            )
        except spring_client.InvalidCursorError:
            checkpoint = store.get_cursor()
            if start == "0" and checkpoint in (None, "0"):
                raise
            # 앞서 성공한 페이지가 있으면 그 artifact·cursor 체크포인트는 유지한다. rebuild는 별도
            # 임시 스토어에서 수행하므로 실패해도 이 마지막 성공 체크포인트를 덮어쓰지 않는다.
            # 최초 실행도 실제 커서가 0에서 전진했다면 이후 INVALID_CURSOR를 즉시 복구한다.
            _log.warning("I-17 커서 무효 — since=0 원자적 전체 재구축으로 복구")
            result = await rebuild()
            did_rebuild = True

    _log.info(
        "artifacts batch: processed=%d hidden=%d pages=%d rebuild=%s",
        result.processed,
        result.hidden,
        result.pages,
        did_rebuild,
    )
    return result
