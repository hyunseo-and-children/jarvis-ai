"""카탈로그 검색 서비스 — SearchBackend 심(seam) (확정 2026-07-15, 이슈 #2 배선).

MVP: 질의 시점 Spring 위임(GET /internal/products/search, I-1, §4.6). decompose 필터를 Spring 에
넘기고 후보를 받는다. **BE I-1 은 excludeProductIds·ratingMin·sort 파라미터가 없으므로**
(v0.15.5, C-15 해소) dedup 제외·평점 하한은 **응답 수신 후 AI 사후필터**로 적용한다.

[결정 2026-07-20, api-spec §4.8 말미] 임베딩 검색을 두 방식으로 구현해 골든셋 확정:
  방식2 EmbeddingRerankBackend — Spring 후보를 AI 임베딩으로 재정렬(라이브, BE 계약 변경 없음).
  방식1 VectorSearchBackend    — AI 벡터검색으로 후보 확보 → Spring hydrate(가용성·상세).
                                 라이브 hydrate 는 C-17(§4.6 id 제약 조회) 필요 — 미주입 시 미착수.
AI 생성물(임베딩)은 I-17 배치(§4.8, artifact_store)가 갱신하며 상품 원본 컬럼 미러는 영구 미채택.
"""

from __future__ import annotations

import asyncio
import functools
import math
from typing import Protocol

from app.core.config import get_settings
from app.pipelines import embedding as _embedding
from app.pipelines.artifact_store import ArtifactStore, get_catalog_store
from app.schemas.spring import ProductSearchFilters, ProductSearchResult
from app.services import spring_client


class SearchBackend(Protocol):
    """검색 백엔드 계약. 기본=Spring 위임, 임베딩 방식1/2로 교체 가능한 심."""

    async def search(self, filters: ProductSearchFilters) -> ProductSearchResult:
        """필터로 상품을 검색해 결과를 반환한다."""
        ...


def _cosine(a: list[float], b: list[float]) -> float:
    """코사인 유사도. 빈 벡터/0벡터/차원 불일치는 -1.0(최하위=제외)로 처리한다.

    차원이 다르면(모델 교체·마이그레이션) zip 절단으로 잘못된 값이 나오므로 조용히 계산하지 않고 제외한다.
    """
    if not a or not b or len(a) != len(b):
        return -1.0
    num = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return -1.0
    return num / (na * nb)


def vector_rank(query_vec: list[float], store: ArtifactStore, *, k: int) -> list[int]:
    """query 임베딩과 저장 임베딩의 코사인으로 상위 k productId 를 반환한다 (방식1 코어, 오프라인 안전).

    라이브 가용성(재고·활성) 확인은 별도(C-17 hydrate). 오프라인 골든셋 비교는 이 랭킹만 사용한다.
    """
    scored = [
        (_cosine(query_vec, art.embedding), art.product_id) for art in store.all() if art.embedding
    ]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [pid for _, pid in scored[:k]]


class SpringSearchBackend:
    """MVP 기본 백엔드 — Spring GET /internal/products/search 위임 (I-1)."""

    async def search(self, filters: ProductSearchFilters) -> ProductSearchResult:
        """Spring 위임 검색. 실패 시 spring_client 가 SpringUnavailableError 를 던진다."""
        return await spring_client.search_products(filters)


class EmbeddingRerankBackend:
    """방식2 — Spring 검색(I-1) 후보를 AI 임베딩으로 시맨틱 재정렬. 라이브(BE 계약 변경 없음).

    후보의 저장 임베딩(artifact_store)과 query 임베딩의 코사인으로 재정렬한다. 임베딩이 없는 후보는
    맨 뒤로(−1.0). keyword 없거나 후보 없으면 Spring 순서 그대로 반환.

    store 조회·정렬과 임베딩 호출 둘 다 블로킹 I/O 다 — store 는 pg-catalog 대상이면 psycopg
    동기 드라이버(이슈 #31), 임베딩은 Google API 동기 HTTP 호출(embedding.py). 둘 다
    asyncio.to_thread 로 별도 스레드에 넘겨 FastAPI 이벤트루프를 막지 않는다(PR #42 리뷰).
    후보마다 store.get() 을 순차 호출하는 N+1 형태는 남아있다 — SQL 배치 조회로 바꾸는 건
    방식1/2 승격(§4.8 말미) 때 함께 재설계할 별도 과제로 남겨둔다.
    """

    def __init__(self, *, store: ArtifactStore | None = None, embed=None) -> None:
        self._store = store or get_catalog_store()
        # 미주입 기본값은 질의(query) 임베딩 — 비대칭 임베딩 바인딩(이슈 #65)
        self._embed = embed or functools.partial(
            _embedding.embed_texts, task_type=get_settings().embedding_task_query
        )

    def _rerank(self, products: list, qvec: list[float]) -> list:
        def score(product) -> float:
            art = self._store.get(product.product_id)
            return _cosine(qvec, art.embedding) if art and art.embedding else -1.0

        return sorted(products, key=score, reverse=True)

    async def search(self, filters: ProductSearchFilters) -> ProductSearchResult:
        result = await spring_client.search_products(filters)
        if not filters.keyword or not result.products:
            return result
        embedded = await asyncio.to_thread(self._embed, [filters.keyword])
        qvec = embedded[0]
        reranked = await asyncio.to_thread(self._rerank, result.products, qvec)
        return ProductSearchResult(products=reranked, total_count=result.total_count)


class VectorSearchBackend:
    """방식1 — AI 벡터검색으로 상위 N productId 확보 → Spring hydrate(필터·가용성·상세).

    라이브 hydrate 는 C-17(§4.6 id 제약 조회) 필요 — 미주입(hydrate=None) 시 미착수 신호로
    SpringUnavailableError 를 던진다. hydrate 계약은 (ids, filters): 가격·카테고리·브랜드 등
    ProductSearchFilters 를 Spring 이 함께 적용하고 품절·비활성을 제거한다. hydrate 후 후보가 줄 수 있어
    벡터 후보는 limit 의 over_fetch 배로 여유 조회한다(config.catalog_vector_overfetch).
    오프라인 골든셋 비교는 vector_rank(랭킹)만 쓰고 hydrate 없이 한다.

    vector_rank 의 store.all() 은 pg-catalog 대상이면 카탈로그 전체를 블로킹으로 읽어오는
    비용이 크고, 임베딩 호출도 Google API 동기 HTTP 라 블로킹이다 — 둘 다 asyncio.to_thread 로
    이벤트루프 차단은 막았지만(이슈 #31, PR #42 리뷰), "SQL 에서 ORDER BY embedding <-> %s
    LIMIT k 로 직접 top-k 만 조회"하는 근본 최적화는 아니다. 방식1 승격(§4.8 말미, 골든셋
    확정 후) 시 함께 재설계할 과제로 남겨둔다.
    """

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        embed=None,
        hydrate=None,
        over_fetch: int | None = None,
    ) -> None:
        self._store = store or get_catalog_store()
        # 미주입 기본값은 질의(query) 임베딩 — 비대칭 임베딩 바인딩(이슈 #65)
        self._embed = embed or functools.partial(
            _embedding.embed_texts, task_type=get_settings().embedding_task_query
        )
        self._hydrate = (
            hydrate  # Callable[[list[int], ProductSearchFilters], Awaitable[...]] | None
        )
        self._over_fetch = (
            over_fetch if over_fetch is not None else get_settings().catalog_vector_overfetch
        )

    async def search(self, filters: ProductSearchFilters) -> ProductSearchResult:
        embedded = await asyncio.to_thread(self._embed, [filters.keyword or ""])
        qvec = embedded[0]
        k = max(
            filters.limit, filters.limit * self._over_fetch
        )  # hydrate 필터/품절 제거 대비 여유조회
        ids = await asyncio.to_thread(vector_rank, qvec, self._store, k=k)
        if self._hydrate is None:
            raise spring_client.SpringUnavailableError(
                "VectorSearchBackend(방식1) 라이브 hydrate 미착수 — C-17(§4.6 id 제약 조회) 필요"
            )
        return await self._hydrate(ids, filters)


# MVP 기본 백엔드 — Spring 위임(§4.6). 임베딩 방식 승격은 골든셋 확정 후(§4.8 말미).
default_backend: SearchBackend = SpringSearchBackend()


async def search_catalog(
    filters: ProductSearchFilters,
    exclude_product_ids: list[int] | None = None,
    backend: SearchBackend | None = None,
) -> ProductSearchResult:
    """활성 백엔드로 카탈로그를 검색하고 AI 사후필터(dedup 제외·평점 하한)를 적용한다.

    BE I-1 에 dedup·평점 파라미터가 없어(C-15), Spring 검색은 keyword/category/price/brand/size 만
    보내고 exclude_product_ids(최근 구매 dedup, §4.7 결정 14-F)·rating_min 은 여기서 사후 제외한다.
    정렬(sort)은 rerank 단계 소관 — 여기서는 검색순서를 보존한다.
    backend 미지정 시 default_backend(Spring 위임) 사용 — 테스트에서 주입 가능.
    """
    used = backend or default_backend
    result = await used.search(filters)
    products = result.products

    if exclude_product_ids:
        excluded = set(exclude_product_ids)
        products = [p for p in products if p.product_id not in excluded]

    if filters.rating_min is not None:
        threshold = filters.rating_min
        products = [p for p in products if (p.rating or 0.0) >= threshold]

    return ProductSearchResult(products=products, total_count=len(products))
