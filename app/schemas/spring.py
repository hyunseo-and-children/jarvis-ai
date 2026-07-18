"""Spring 역방향 호출 스키마 (api-spec v0.15.0 §4).

AI → Spring 역방향 계약 (모두 camelCase on the wire, Pydantic alias):
  1. search_products      — I-1, GET /internal/products/search (§4.6, 최우선 C-15; GET/POST 역제안 🔴)
  2. get_recent_purchases — I-19, GET /internal/members/{id}/orders (§4.7, C-6, dedup·프로필 소스)
  3. add_to_cart          — I-2, POST /internal/cart/items 단건 (§4.1, C-3)
  4. get_cart             — I-18, GET /internal/cart (§4.9, C-16)
  5. push_recommendations — I-21, POST /internal/recommendations, productIds (경로 B, §4.2, C-9)
  6. fetch_product_changes — I-17, GET /internal/products/changes (§4.8, C-4, AI 생성물 배치)
  (I-6/I-9 판매자 콜백 응답 스키마는 C-13/C-14 협의 후 확정 — dict 유지)

Python 속성은 snake_case, 직렬화는 by_alias=True 로 camelCase, 입력은 populate_by_name 으로 양쪽 허용.
[HARD] push 페이로드에는 표시 필드(price/image/reviewCount)를 넣지 않는다 — 추천 산출물만 (§4.2).

[변경 v0.6.0] 장바구니 구계약(CartItem items[] 다건 + reason 4종/GUEST_NOT_ALLOWED) 폐기
→ BE I-2 문서 채택: AddToCartRequest 단건(userId|guestId 본문 신원, optionId), 게스트 허용.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """camelCase 직렬화 공통 베이스 (schemas.chat.CamelModel 와 동일 규약)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ── 1. 상품 검색 (§4.6, C-15) ──


class ProductSearchFilters(CamelModel):
    """POST /products/search 요청 (decompose 산출, api-spec §4.6).

    필수는 limit 만 — 나머지 필터는 전부 선택. 구조화 필터 + 키워드(MVP 는 Spring DB 텍스트 검색).
    excludeProductIds 는 최근 구매 dedup (결정 14-F, 원천 = GET /orders/recent §4.7). 게스트는 빈 배열.
    """

    category: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    brand: list[str] | None = None
    rating_min: float | None = None
    keyword: str | None = None
    exclude_product_ids: list[str] = Field(default_factory=list)
    sort: str | None = None
    limit: int = 30


class SpringProduct(CamelModel):
    """Spring 검색 응답의 상품 1건. price/stock 은 질의 시점 최신값 (rerank·예산 계산용)."""

    product_id: str
    name: str
    price: int
    list_price: int | None = None
    stock: int | None = None
    category: str | None = None
    brand: str | None = None
    rating: float | None = None
    main_image: str | None = None


class ProductSearchResult(CamelModel):
    """POST /products/search 응답. totalCount 는 완화 칩 estCount(COUNT) 산정용 (§4.6)."""

    products: list[SpringProduct] = Field(default_factory=list)
    total_count: int = 0


# ── 2. 구매 이력 조회 (§4.7, C-6) — 구 order_seed 시드 대체 ──


class OrderHistoryItem(CamelModel):
    """I-19 주문 아이템 (api-spec §4.7). dedup 은 productId 기준 — category 없음(§4.7 갭)."""

    order_item_id: int
    product_id: int  # 숫자(BIGINT, §2.6 internal)
    product_name: str | None = None
    option_name: str | None = None
    quantity: int = 1
    price: int | None = None
    status: str | None = None


class OrderHistory(CamelModel):
    """I-19 주문 1건. shippingFee 는 MVP 항상 0, totalAmount = 아이템 스냅샷 합."""

    order_id: int
    ordered_at: str  # ISO-8601
    representative_status: str | None = None  # 대표 상태 enum 8종(표시 문구는 FE 매핑)
    items: list[OrderHistoryItem] = Field(default_factory=list)
    items_total: int | None = None
    shipping_fee: int = 0
    total_amount: int | None = None


class RecentPurchases(CamelModel):
    """I-19 GET /internal/members/{id}/orders 응답 data (api-spec §4.7, BE 본문 재작성 v0.15.0).

    [변경 v0.15.0] 구 3필드(productId/category/purchasedAt) 폐기 — BE 본문 재작성 반영.
    ⚠️ items 에 category 없음 → 소모품 카테고리 억제(결정 14-F) 불가, exact productId 제외만 가능.
    실패/타임아웃 시 dedup 없이 추천 진행(degrade, §4.7).
    """

    orders: list[OrderHistory] = Field(default_factory=list)


# ── 3. 장바구니 담기 (I-2, §4.1) — BE 문서 채택, 단건 ──


class AddToCartRequest(CamelModel):
    """I-2 POST /internal/cart/items 요청 본문 (api-spec §4.1, BE 문서 채택).

    userId/guestId 둘 중 하나 — AI-검증 JWT sub 유래(챗 요청의 메아리, FE 본문 값 불신).
    게스트 담기 허용(결정 8 개정). productId·guestId 타입은 §2.6 경계 정책(internal 숫자 vs SSE 문자열)·DDL(guest UUID) 미정 — 현행 유지, 🔴 C-5.
    quantity 1~99, 동일 상품·옵션 기존 존재 시 Spring 이 합산.
    """

    user_id: int | None = None
    guest_id: int | None = None
    product_id: str
    option_id: str | None = None
    quantity: int = Field(1, ge=1, le=99)


class AddToCartResult(CamelModel):
    """I-2 성공 응답 — {success, data:{cartItemId}} (api-spec §4.1).

    실패는 HTTP 오류로 옴: 400 CART_OPTION_REQUIRED(options 목록 → 되물음 멀티턴) /
    400 CART_OPTION_INVALID / 404 PRODUCT_NOT_FOUND / 401 INTERNAL_TOKEN_INVALID.
    SSE action.reason 매핑: PRODUCT_NOT_FOUND | CART_ERROR | OUT_OF_STOCK(🔴 협의).
    """

    success: bool
    cart_item_id: int | str | None = None


# ── 4. 장바구니 조회 (I-9, §4.9, C-16) ──


class CartViewItem(CamelModel):
    """I-9 GET /internal/cart 응답 항목 — productName/optionName 은 챗 답변 생성 필수(🔴)."""

    cart_item_id: int | str
    product_id: str
    product_name: str | None = None
    option_id: str | None = None
    option_name: str | None = None
    quantity: int = 1
    price: int | None = None  # 표시가(선택, 총액 안내용 — 표시 권위는 Spring)


class CartView(CamelModel):
    """I-9 응답. 빈 장바구니는 items=[] 정상 200 (오류 아님, §4.9)."""

    items: list[CartViewItem] = Field(default_factory=list)


# ── 5. 추천 목록 push (I-21, §4.2, 경로 B) ──


class RecommendationPush(CamelModel):
    """I-21 POST /internal/recommendations 요청 (경로 B, api-spec §4.2, v0.15.0).

    최종 랭크 상품 id(Top-N)만 전달한다 — listId 는 FastAPI 가 생성해 넘기고(Spring 이 Redis 에
    이 키로 TTL 저장), FE 는 CH-5 GET /api/chat/lists/{listId} 로 카드를 조회한다.
    [변경 v0.15.0] 구 groups/items 구조 폐기. [Q2 역제안 v0.15.2] reason 은 이 콜백에 포함
    (reasons[{productId, reason}]) → Spring 이 CH-5 카드에 echo. BE 확정 시 reasons 필드 추가(§4.2).
    productId 는 internal 계약이라 숫자(BIGINT, §2.6). 순서 유지 = 렌더 순서.
    노출 개수(Top-N)는 config(expose_min~expose_max)로 recommendation 그래프가 결정 —
    이 스키마는 전송 컨테이너라 하드 개수 대신 방어적 상한(max_length)만 둔다.
    상위는 이 콜백이 성공한 뒤에만 SSE products.ready 를 emit 한다(§3.3).
    """

    session_id: str
    list_id: str
    product_ids: list[int] = Field(default_factory=list, max_length=50)  # 방어적 상한(실제 개수는 config)


# ── 6. 상품 변경분 pull (I-17, §4.8, C-4) — AI 생성물 갱신 배치 ──


class ProductChange(CamelModel):
    """I-17 변경분 항목. status=DELISTED 필수 — AI 생성물 삭제/비활성 트리거 (§4.8).

    콘텐츠 필드는 enrichment·search_doc 조립 입력 — AI 는 저장하지 않고 산출물 생성에만 사용.
    """

    product_id: str
    status: str  # ACTIVE | DELISTED
    updated_at: str  # ISO-8601
    name: str | None = None
    description: str | None = None
    category: str | None = None
    attributes: dict | None = None


class ProductChangesPage(CamelModel):
    """I-17 응답 페이지. hasMore=True 면 nextCursor 로 같은 주기 안에서 즉시 재요청."""

    items: list[ProductChange] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
