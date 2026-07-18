"""Spring 역방향 호출 클라이언트 (api-spec v0.15.0 §4).

AI → Spring 질의 시점 역방향 + 배치 (api-spec v0.15.0):
  - search_products        : 후보 확보 (I-1, GET /internal/products/search, §4.6, C-15; GET/POST 역제안 🔴)
  - get_recent_purchases   : 구매 이력 조회 (I-19, GET /internal/members/{id}/orders, §4.7, C-6) — dedup·프로필 소스
  - add_to_cart            : 장바구니 담기 (I-2, POST /internal/cart/items, 단건, §4.1)
  - get_cart               : 장바구니 조회 (I-18, GET /internal/cart, §4.9, C-16)
  - push_recommendations   : 최종 랭크 id push (I-21, POST /internal/recommendations, 경로 B, §4.2)
  - get_seller_aggregates  : 판매자 집계 조회 (I-6 등 5종, GET /internal/seller/{brandId}/…, §4.4, C-13)
  - get_product_detail     : draft before-source 읽기 (I-9 자사 상품 목록, §4.5, C-14)
  - fetch_product_changes  : AI 생성물 갱신 배치 pull (I-17, GET /internal/products/changes, §4.8, C-4)
AI 는 커머스 DB 에 직접 write 하지 않는다. 와이어 포맷은 camelCase (스키마 alias).

인증 레인 (api-spec §2.3, v0.13.0 통일): AI→Spring 역호출은 전 구간 X-Internal-Token 서비스 토큰
  + 본문/쿼리 신원(AI-검증 JWT sub 유래, IDOR 방지). 판매자는 brandId(JWT 클레임)를 {brandId} path 에.
  구 "사용자/판매자 JWT 포워딩" 제안은 폐기.
타임아웃: AI→Spring 전 구간 3s 통일 (api-spec §2.9 c — BE I-2 문서 기준).

[변경 v0.6.0] add_to_cart 구계약(JWT 포워딩 + items[] 다건) 폐기 → BE I-2 문서 채택(단건,
게스트 담기 허용, optionId 되물음). get_cart(I-9) 신설.
[변경 v0.5.x] point_lookup 폐기(경로 B). 주문 시드(order_seed) → get_recent_purchases
질의 시점 조회로 대체. AI 생성물 갱신은 I-8 pull 배치(§4.8).
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.schemas.spring import (
    AddToCartRequest,
    AddToCartResult,
    CartView,
    ProductChangesPage,
    ProductSearchFilters,
    ProductSearchResult,
    RecentPurchases,
    RecommendationPush,
)


class SpringUnavailableError(Exception):
    """Spring 서버 도달 불가/오류 응답. 상위에서 SEARCH_FAILED 등으로 매핑한다."""


def _client() -> httpx.AsyncClient:
    """공용 httpx.AsyncClient 팩토리. base_url 은 설정에서 주입한다.

    타임아웃 3s — api-spec §2.9 c (AI→Spring 콜백 통일 기준). 초과 시 각 계약의
    degrade 규칙 적용(조회 생략·담기 CART_ERROR·dedup 생략 등).
    """
    settings = get_settings()
    return httpx.AsyncClient(base_url=settings.spring_base_url, timeout=3.0)


async def search_products(filters: ProductSearchFilters) -> ProductSearchResult:
    """Spring 상품 검색 위임 (스텁, api-spec §4.6 / C-15 최우선).

    후보 검색(I-1). BE 는 GET /internal/products/search 로 정의했으나 복잡 필터(배열·중첩)라
    POST 바디 역제안 중(🔴 C-15) — 확정 전까지 POST 유지. camelCase 필터 → price/stock + totalCount.
    유일·영구 후보 확보 경로 — AI 임베딩과의 결합(방식1/방식2)은 OPEN(§4.8 말미).

    TODO(SPEC-RECOMMEND-001): httpx POST(json=filters.model_dump(by_alias=True)) + 응답 파싱.
    """
    raise SpringUnavailableError(
        "search_products not wired to live Spring yet (SPEC-RECOMMEND-001, §4.6, C-15)"
    )


async def get_recent_purchases(user_id: int, status: str | None = None) -> RecentPurchases:
    """구매 이력 질의 시점 조회 — I-19 (스텁, api-spec §4.7 / C-6).

    GET {spring_base_url}/internal/members/{user_id}/orders + X-Internal-Token.
    userId 는 AI-검증 JWT sub 유래(신원 도출, 요청 본문 불신). 응답은 주문 상세 배열(OrderHistory) —
    ⚠️ items 에 category 없음: dedup 은 exact productId 제외만, 카테고리 억제 불가(§4.7 갭).
    소비처: 추천 dedup(결정 14-F) + 프로필 sleep-time 구매 소스(결정 16).
    게스트는 호출 스킵(이력 없음). 실패/타임아웃 시 dedup 없이 추천 진행(degrade, §4.7).

    [변경 v0.15.0] 구 GET /orders/recent(JWT 포워딩·3필드) 폐기 → BE I-19 실측(서비스 토큰).
    """
    raise SpringUnavailableError(
        "get_recent_purchases not wired to live Spring yet (api-spec §4.7, I-19, C-6)"
    )


async def add_to_cart(request: AddToCartRequest) -> AddToCartResult:
    """장바구니 담기 — BE I-2 문서 채택 (스텁, api-spec §4.1 / C-3 잔여).

    POST {spring_base_url}/internal/cart/items + X-Internal-Token (서비스 토큰).
    본문: {userId|guestId(둘 중 하나, AI-검증 JWT sub 유래), productId, optionId, quantity(1~99)}.
    단건 계약 — Case 3 묶음은 상품별 반복 호출(항목별 성공/실패 자연 분리).

    - 게스트 담기 허용 (BE 02 D30, 결정 8 개정 — 구 GUEST_NOT_ALLOWED 선차단 폐기).
    - 동일 상품·옵션 기존 존재 시 Spring 이 quantity 합산 (합산 권위 = Spring).
    - 400 CART_OPTION_REQUIRED(options 목록 포함) → 실패 action 없이 token 되물음 멀티턴.
    - 400 CART_OPTION_INVALID → options 재확인 후 1회 재시도, 반복 실패 시 CART_ERROR.
    - 404 PRODUCT_NOT_FOUND / 401 INTERNAL_TOKEN_INVALID(운영 오류 → CART_ERROR 노출).

    TODO: httpx POST + 오류 코드 매핑(§4.1 표). 재고(OUT_OF_STOCK) 코드는 🔴 협의(C-3).
    """
    raise SpringUnavailableError(
        "add_to_cart not wired to live Spring yet (api-spec §4.1, I-2, C-3)"
    )


async def get_cart(user_id: int | None = None, guest_id: int | None = None) -> CartView:
    """장바구니 조회 — I-18 (스텁, api-spec §4.9 / C-16). guest_id 타입은 §2.6/DDL 미정 — 현행 유지.

    GET {spring_base_url}/internal/cart?userId=|guestId= + X-Internal-Token.
    용도: (1) "장바구니에 뭐 있어?" 질의 → token 텍스트 답변(별도 SSE 이벤트 없음),
          (2) 담기 전 기존 보유 확인 → "이미 담겨 있어 N개로 늘렸어요" 안내.
    조회 실패/타임아웃 시에도 담기는 진행한다(degrade). 빈 장바구니는 items=[] 정상 200.
    productName/optionName 은 챗 답변 문장 생성에 필수(🔴 C-16).

    TODO: httpx GET + 파라미터 검증(userId/guestId 둘 중 하나).
    """
    raise SpringUnavailableError(
        "get_cart not wired to live Spring yet (api-spec §4.9, I-18, C-16)"
    )


async def push_recommendations(push: RecommendationPush) -> bool:
    """최종 랭크 id 를 Spring 에 push — I-21 (스텁, 경로 B, api-spec §4.2 / C-9).

    POST {spring_base_url}/internal/recommendations 로 {sessionId, listId, productIds[Top5 숫자]}
    를 보낸다(표시 필드 없음 — Spring enrich·CH-5 조회, §4.3). listId 는 FastAPI 가 생성.
    콜백 성공 시에만 상위가 SSE products.ready 를 emit 한다 — 실패 시 미emit·done 종료(§3.3).

    [변경 v0.15.0] 구 POST /recommendations(groups 구조 + listId 응답) 폐기 → I-21 실측.
    TODO(SPEC-RECOMMEND-001): httpx POST(json=push.model_dump(by_alias=True)) → 성공 여부 반환.
    """
    raise SpringUnavailableError(
        "push_recommendations not wired to live Spring yet (api-spec §4.2, I-21, C-9)"
    )


async def get_seller_aggregates(brand_id: str, metric: str, group_by: str | None = None) -> dict:
    """판매자 집계 조회 — I-6 등 5종 (스텁, api-spec §4.4 / C-13 최우선).

    GET {spring_base_url}/internal/seller/{brand_id}/sales(등) + X-Internal-Token.
    brandId 는 검증된 판매자 JWT 클레임에서 도출해 {brandId} path 에 쓴다(요청 본문/발화 불신,
    IDOR 방지, §2.6). 집계값만 반환(원시 로그 미제공). 통계 Q&A(§3.2)의 데이터 원천.

    [변경 v0.15.0] 구 /seller/aggregates(JWT 포워딩) 폐기 → BE 집계 5종(I-6/7/13/14/16) +
    전역 I-8(account-events). 구 order_seed 시드 집계 폐기(C-7 해소).
    TODO(seller graph SPEC): 5종별 경로·응답 스키마·metric/groupBy 값 집합은 🔴 협의(C-13).
    """
    raise SpringUnavailableError(
        "get_seller_aggregates not wired to live Spring yet (api-spec §4.4, I-6, C-13)"
    )


async def get_product_detail(brand_id: str, product_id: str) -> dict:
    """draft before-source 읽기 — I-9 자사 상품 목록 (스텁, api-spec §4.5 / C-14).

    GET {spring_base_url}/internal/seller/{brand_id}/products + X-Internal-Token.
    brandId 는 판매자 JWT 클레임 유래(§2.6). LLM 이 현재 값(before)을 읽고 개정안을 만들어
    SSE draft{op, productId, changes:[{field,before,after}]} 로 내보낸다.
    [변경 v0.11.0+] 반영은 AI 가 HITL 승인 후 I-11/I-10/I-12 직접 호출 — 구 "FE S-3 PATCH" 폐기.
    채팅 발화는 동의가 아니다(HITL 명시 승인만, §3.2).
    """
    raise SpringUnavailableError(
        "get_product_detail not wired to live Spring yet (api-spec §4.5, I-9, C-14)"
    )


async def fetch_product_changes(cursor: str | None, limit: int = 500) -> ProductChangesPage:
    """상품 변경분 pull — I-17, AI 생성물 갱신 배치 (스텁, api-spec §4.8 / C-4).

    GET {spring_base_url}/internal/products/changes?since={cursor}&limit={n} + X-Internal-Token.
    응답: items[{productId, status(ACTIVE|DELISTED), 콘텐츠 필드}], nextCursor, hasMore.
    hasMore=True 면 같은 주기 안에서 nextCursor 로 즉시 재요청(따라잡기).
    DELISTED 는 AI 생성물 삭제/비활성 — 없으면 유령 상품이 추천 후보로 남는다.

    배치 흐름: 변경분 조회 → enrichment(pipelines/enrichment) → search_doc 조립 →
    임베딩(pipelines/embedding) → AI Postgres upsert. 상품 원본 컬럼은 저장하지 않는다
    (AI 생성물 extras/search_doc/임베딩만, v0.5.1 확정).

    TODO(SPEC-CATALOG-DATA-001 재범위): 커서 영속화 + 주기 잡 + 파이프라인 연결.
    """
    raise SpringUnavailableError(
        "fetch_product_changes not wired to live Spring yet (api-spec §4.8, I-17, C-4)"
    )
