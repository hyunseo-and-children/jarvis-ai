"""카탈로그 검색 서비스 — SearchBackend 심(seam) (확정 2026-07-15, 이슈 #2 배선).

MVP: 질의 시점 Spring 위임(GET /internal/products/search, I-1, §4.6). decompose 필터를
Spring 에 넘기고 후보를 받는다. **BE I-1 은 excludeProductIds·ratingMin·sort 파라미터가 없으므로**
(v0.15.5, C-15 해소) dedup 제외·평점 하한은 **응답 수신 후 AI 사후필터**로 적용한다.

[OPEN — 질의 시점 후보 흐름, api-spec §4.8 말미] 방식1(AI 벡터 검색 → Spring id 제약 조회)
vs 방식2(Spring 검색 → 임베딩 재정렬 보조) 병행 검토 — SearchBackend 인터페이스로 양쪽
교체 가능하게 유지한다. AI 생성물(임베딩)은 I-17 배치(§4.8)가 갱신하며 상품 원본 컬럼 미러는
영구 미채택.
"""

from __future__ import annotations

from typing import Protocol

from app.schemas.spring import ProductSearchFilters, ProductSearchResult
from app.services import spring_client


class SearchBackend(Protocol):
    """검색 백엔드 계약. 기본=Spring 위임(방식2 계열), 방식1(AI 벡터 우선)로 교체 가능한 심."""

    async def search(self, filters: ProductSearchFilters) -> ProductSearchResult:
        """필터로 상품을 검색해 결과를 반환한다."""
        ...


class SpringSearchBackend:
    """MVP 백엔드 — Spring GET /internal/products/search 위임 (I-1)."""

    async def search(self, filters: ProductSearchFilters) -> ProductSearchResult:
        """Spring 위임 검색. 실패 시 spring_client 가 SpringUnavailableError 를 던진다."""
        return await spring_client.search_products(filters)


# MVP 기본 백엔드. 후보 흐름 OPEN 확정(방식1) 시 벡터 우선 백엔드로 교체 가능(§4.8).
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
