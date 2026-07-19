"""Spring 역방향 호출 클라이언트 (api-spec v0.15.x §4).

AI → Spring 질의 시점 역방향 + 배치:
  - search_products        : 후보 확보 (I-1, GET /internal/products/search, §4.6, C-15) — [배선 완료]
  - get_recent_purchases   : 구매 이력 조회 (I-19, GET /internal/members/{id}/orders, §4.7, C-6) — dedup·프로필 소스
  - add_to_cart            : 장바구니 담기 (I-2, POST /internal/cart/items, 단건, §4.1)
  - get_cart               : 장바구니 조회 (I-18, GET /internal/cart, §4.9, C-16)
  - push_recommendations   : 최종 랭크 id push (I-21, POST /internal/recommendations, 경로 B, §4.2) — [배선 완료]
  - get_seller_aggregates  : 판매자 집계 조회 (I-6 등 5종, §4.4, C-13)
  - get_product_detail     : draft before-source 읽기 (I-9, §4.5, C-14)
  - fetch_product_changes  : AI 생성물 갱신 배치 pull (I-17, §4.8, C-4)
AI 는 커머스 DB 에 직접 write 하지 않는다. 와이어 포맷은 camelCase (스키마 alias).

인증 레인 (api-spec §2.3, v0.13.0 통일): AI→Spring 역호출은 전 구간 X-Internal-Token 서비스 토큰
  + 본문/쿼리 신원(AI-검증 JWT sub 유래, IDOR 방지). 판매자는 brandId(JWT 클레임)를 {brandId} path 에.
타임아웃: AI→Spring 전 구간 3s 통일 (api-spec §2.9 c — BE I-2 문서 기준).

[배선 v이슈#2] search_products = **GET**(사용자 확정 "그냥 GET으로") — BE I-1 파라미터
  keyword/categoryName/minPrice/maxPrice/brandName/size. dedup·평점·정렬은 요청 파라미터가 아니라
  AI 사후필터(§4.6 v0.15.5, C-15). push_recommendations = POST I-21(productIds 만, 경로 B).
"""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.spring import (
    AddToCartRequest,
    CartOption,
    AddToCartResult,
    CartView,
    ProductChangesPage,
    ProductSearchFilters,
    ProductSearchResult,
    RecentPurchases,
    RecommendationPush,
    SpringProduct,
)


_log = logging.getLogger(__name__)


class SpringUnavailableError(Exception):
    """Spring 서버 도달 불가/오류 응답. 상위에서 SEARCH_FAILED 등으로 매핑한다."""


class CartOptionRequired(Exception):
    """I-2 400 CART_OPTION_REQUIRED — 옵션 필수인데 optionId 없음(§4.1). options 목록 동반."""

    def __init__(self, options: list[CartOption]) -> None:
        super().__init__("cart option required")
        self.options = options


class CartOptionInvalid(Exception):
    """I-2 400 CART_OPTION_INVALID — 옵션이 상품 소속 아님(§4.1). options 재확인용 동반."""

    def __init__(self, options: list[CartOption]) -> None:
        super().__init__("cart option invalid")
        self.options = options


class CartProductNotFound(Exception):
    """I-2 404 PRODUCT_NOT_FOUND — 없는 상품(§4.1)."""


class CartError(Exception):
    """I-2 담기 운영 오류(401 INTERNAL_TOKEN_INVALID·도달 불가·미상 코드) → action CART_ERROR."""


def _client() -> httpx.AsyncClient:
    """공용 httpx.AsyncClient 팩토리. base_url·서비스 토큰은 설정에서 주입한다.

    타임아웃 3s — api-spec §2.9 c (AI→Spring 콜백 통일 기준). 초과 시 각 계약의
    degrade 규칙 적용(조회 생략·담기 CART_ERROR·dedup 생략 등).
    """
    settings = get_settings()
    headers = {"X-Internal-Token": settings.internal_api_token} if settings.internal_api_token else {}
    return httpx.AsyncClient(
        base_url=settings.spring_base_url,
        timeout=settings.spring_timeout_s,
        headers=headers,
    )


def _search_query_params(filters: ProductSearchFilters) -> dict:
    """decompose 필터 → BE I-1 GET 쿼리 파라미터 (§4.6, C-15).

    BE I-1 파라미터는 keyword/categoryName/minPrice/maxPrice/brandName/size(≤30) 뿐이다.
    brandName 은 단수 — MVP 는 첫 브랜드만 보내고(복수 브랜드는 후속) 나머지 필터
    (excludeProductIds·ratingMin·sort)는 여기 싣지 않고 AI 사후필터(search_service)로 처리한다.
    """
    params: dict[str, object] = {}
    if filters.keyword:
        params["keyword"] = filters.keyword
    if filters.category:
        params["categoryName"] = filters.category
    if filters.price_min is not None:
        params["minPrice"] = filters.price_min
    if filters.price_max is not None:
        params["maxPrice"] = filters.price_max
    if filters.brand:
        params["brandName"] = filters.brand[0]
    params["size"] = min(filters.limit, 30)
    return params


def _parse_search_response(data: object) -> ProductSearchResult:
    """BE I-1 응답 {success, data:{items:[...]}} → ProductSearchResult (§4.6, v0.15.5).

    BE 응답엔 totalCount 가 없어 total_count 는 수신 items 수로 둔다.
    """
    items: list = []
    if isinstance(data, dict):
        payload = data.get("data")
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            items = payload["items"]
        elif isinstance(data.get("items"), list):
            items = data["items"]
    products = [SpringProduct.model_validate(it) for it in items if isinstance(it, dict)]
    return ProductSearchResult(products=products, total_count=len(products))


def _parse_cart_error(resp: httpx.Response) -> tuple[str | None, list[CartOption]]:
    """I-2 실패 응답에서 code·options 를 방어적으로 파싱한다(§4.1, BE 스키마 🔴).

    code 는 error.code | code. options 는 [BE 확정 2026-07-18] **error.detail.options**
    ([{optionId, name, extraPrice}]) 를 우선하고, 구버전 위치(error.options·options·data.options)도
    방어적으로 본다. name 은 name|optionName, extraPrice(추가금)까지 읽는다.
    """
    try:
        body = resp.json()
    except ValueError:
        return None, []
    if not isinstance(body, dict):
        return None, []
    err = body.get("error") if isinstance(body.get("error"), dict) else None
    code = (err or {}).get("code") or body.get("code")
    detail = (err or {}).get("detail") if isinstance((err or {}).get("detail"), dict) else None
    # BE 확정 위치(error.detail.options)는 '키 존재'로 우선한다 — 빈 배열이어도 그 값을 신뢰하고
    # 구버전 위치로 조용히 폴백하지 않는다(잔재 options 오선택 방지).
    if detail is not None and "options" in detail:
        raw = detail.get("options") or []
    else:
        raw = (err or {}).get("options") or body.get("options") or (body.get("data") or {}).get("options") or []
    options: list[CartOption] = []
    for opt in raw if isinstance(raw, list) else []:
        if isinstance(opt, dict) and opt.get("optionId") is not None:
            # extraPrice 는 표시용 부가 필드 — 형식이 이상해도 옵션 자체를 버리지 않게 방어적으로 분리.
            raw_extra = opt.get("extraPrice")
            if isinstance(raw_extra, bool):
                extra = None
            elif isinstance(raw_extra, int):
                extra = raw_extra
            elif isinstance(raw_extra, float) and raw_extra.is_integer():
                extra = int(raw_extra)  # BE(Java) BigDecimal/Double → 정수 금액이 1000.0 로 올 수 있음
            else:
                extra = None
            try:
                options.append(
                    CartOption(
                        option_id=opt["optionId"],
                        name=opt.get("name") or opt.get("optionName") or "",
                        extra_price=extra,
                    )
                )
            except (ValidationError, ValueError, TypeError):
                _log.warning("cart 옵션 항목 파싱 실패 — 건너뜀: %r", opt)
                continue  # 형식 이상 옵션은 건너뜀 — 되물음 흐름 전체가 죽지 않게(방어적)
    if raw and not options:
        # error.detail.options 는 BE 확정 계약(§4.1 v0.15.8) — 전멸은 계약 위반 신호라 로그 남김.
        _log.warning("CART_OPTION_REQUIRED options 전부 파싱 실패(계약 위반 가능): %r", raw)
    return code, options


async def search_products(filters: ProductSearchFilters) -> ProductSearchResult:
    """Spring 상품 검색 위임 — I-1 GET /internal/products/search (api-spec §4.6 / C-15).

    유일·영구 후보 확보 경로. 사용자 확정에 따라 **GET** 으로 호출한다. dedup·평점·정렬은
    응답 후 AI 사후필터(search_service.search_catalog)에서 적용한다. 도달 불가/오류 응답은
    SpringUnavailableError 로 전파 — 상위(추천 그래프)가 SEARCH_FAILED 로 낸다.
    """
    params = _search_query_params(filters)
    try:
        async with _client() as client:
            resp = await client.get("/internal/products/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        # 응답 파싱·검증도 같은 경계 안 — 200 이지만 스키마 불일치인 malformed 응답도
        # SEARCH_FAILED degrade(§7)로 흐르게 한다(ValidationError 가 그대로 새어 500 되지 않게).
        return _parse_search_response(data)
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        raise SpringUnavailableError(f"search_products 실패: {exc}") from exc


async def get_recent_purchases(user_id: int, status: str | None = None) -> RecentPurchases:
    """구매 이력 질의 시점 조회 — I-19 (스텁, api-spec §4.7 / C-6).

    GET {spring_base_url}/internal/members/{user_id}/orders + X-Internal-Token.
    userId 는 AI-검증 JWT sub 유래(신원 도출, 요청 본문 불신). 응답은 주문 상세 배열(OrderHistory) —
    ⚠️ items 에 category 없음: dedup 은 exact productId 제외만, 카테고리 억제 불가(§4.7 갭).
    소비처: 추천 dedup(결정 14-F) + 프로필 sleep-time 구매 소스(결정 16).
    게스트는 호출 스킵(이력 없음). 실패/타임아웃 시 dedup 없이 추천 진행(degrade, §4.7).
    """
    raise SpringUnavailableError(
        "get_recent_purchases not wired to live Spring yet (api-spec §4.7, I-19, C-6)"
    )


async def add_to_cart(request: AddToCartRequest) -> AddToCartResult:
    """장바구니 담기 — BE I-2 문서 채택 (api-spec §4.1). [배선 완료]

    POST {spring_base_url}/internal/cart/items + X-Internal-Token. 본문 신원(userId|guestId)은
    AI-검증 JWT sub 유래(요청 본문 불신, §2.3). 담기 시 재고검증 없음(C-3 해소 v0.15.5 — OUT_OF_STOCK 폐기).
    실패 코드는 typed 예외로 전파: CART_OPTION_REQUIRED→CartOptionRequired(options),
    CART_OPTION_INVALID→CartOptionInvalid(options), 404→CartProductNotFound, 그 외→CartError.
    """
    try:
        async with _client() as client:
            resp = await client.post("/internal/cart/items", json=request.model_dump(by_alias=True))
    except httpx.HTTPError as exc:
        raise CartError(f"add_to_cart 도달 실패: {exc}") from exc

    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError as exc:
            raise CartError(f"add_to_cart 응답 파싱 실패: {exc}") from exc
        payload = data.get("data") if isinstance(data, dict) else None
        cart_item_id = payload.get("cartItemId") if isinstance(payload, dict) else None
        return AddToCartResult(success=True, cart_item_id=cart_item_id)

    code, options = _parse_cart_error(resp)
    if resp.status_code == 400 and code == "CART_OPTION_REQUIRED":
        raise CartOptionRequired(options)
    if resp.status_code == 400 and code == "CART_OPTION_INVALID":
        raise CartOptionInvalid(options)
    if resp.status_code == 404:
        raise CartProductNotFound()
    # 401 INTERNAL_TOKEN_INVALID·미상 코드 → 운영 오류
    raise CartError(f"add_to_cart 실패: {resp.status_code} {code}")


async def get_cart(user_id: int | None = None, guest_id: str | None = None) -> CartView:
    """장바구니 조회 — I-18 (api-spec §4.9 / C-16). [배선 완료]

    GET {spring_base_url}/internal/cart?userId=|guestId= + X-Internal-Token.
    빈 장바구니는 items=[] 정상 200. 도달 불가/오류/스키마 불일치는 SpringUnavailableError —
    조회는 안내용이라 상위(담기)는 조회 실패 시에도 진행한다(degrade, §4.9).
    """
    params: dict[str, object] = {}
    if user_id is not None:
        params["userId"] = user_id
    if guest_id is not None:
        params["guestId"] = guest_id
    try:
        async with _client() as client:
            resp = await client.get("/internal/cart", params=params)
            resp.raise_for_status()
            data = resp.json()
        payload = data.get("data") if isinstance(data, dict) else None
        items = payload.get("items") if isinstance(payload, dict) else None
        return CartView.model_validate({"items": items or []})
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        raise SpringUnavailableError(f"get_cart 실패: {exc}") from exc


async def push_recommendations(push: RecommendationPush) -> bool:
    """최종 랭크 id 를 Spring 에 push — I-21 (경로 B, api-spec §4.2 / C-9). [배선 완료]

    POST {spring_base_url}/internal/recommendations 로 {sessionId, listId, productIds[숫자]} 를
    보낸다(표시 필드 없음 — Spring enrich·CH-5 조회, §4.3). listId 는 FastAPI 가 생성.
    콜백 성공 시에만 상위가 SSE products.ready 를 emit 한다 — 실패 시 미emit·done 종료(§3.3).
    도달 불가/오류 응답은 SpringUnavailableError 로 전파(상위가 products.ready 스킵).
    """
    try:
        async with _client() as client:
            resp = await client.post(
                "/internal/recommendations",
                json=push.model_dump(by_alias=True),
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SpringUnavailableError(f"push_recommendations 실패: {exc}") from exc
    return True


async def get_seller_aggregates(brand_id: str, metric: str, group_by: str | None = None) -> dict:
    """판매자 집계 조회 — I-6 등 5종 (스텁, api-spec §4.4 / C-13 최우선). 이슈 #9 소관."""
    raise SpringUnavailableError(
        "get_seller_aggregates not wired to live Spring yet (api-spec §4.4, I-6, C-13)"
    )


async def get_product_detail(brand_id: str, product_id: str) -> dict:
    """draft before-source 읽기 — I-9 자사 상품 목록 (스텁, api-spec §4.5 / C-14). 이슈 #9 소관."""
    raise SpringUnavailableError(
        "get_product_detail not wired to live Spring yet (api-spec §4.5, I-9, C-14)"
    )


async def fetch_product_changes(cursor: str | None, limit: int = 500) -> ProductChangesPage:
    """상품 변경분 pull — I-17, AI 생성물 갱신 배치 (스텁, api-spec §4.8 / C-4). 이슈 #7 소관."""
    raise SpringUnavailableError(
        "fetch_product_changes not wired to live Spring yet (api-spec §4.8, I-17, C-4)"
    )
