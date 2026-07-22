"""Spring 역방향 호출 클라이언트 (api-spec v0.15.x §4 — 판매자 SpringClient 는 §4.4/§4.5).

AI → Spring 질의 시점 역방향 + 배치 (구매자, 모듈 레벨 함수):
  - search_products        : 후보 확보 (I-1, GET /internal/products/search, §4.6, C-15) — [배선 완료]
  - get_recent_purchases   : 구매 이력 조회 (I-19, GET /internal/members/{id}/orders, §4.7, C-6) — dedup·프로필 소스
  - add_to_cart            : 장바구니 담기 (I-2, POST /internal/cart/items, 단건, §4.1)
  - get_cart               : 장바구니 조회 (I-18, GET /internal/cart, §4.9, C-16)
  - push_recommendations   : 최종 랭크 id push (I-21, POST /internal/recommendations, 경로 B, §4.2) — [배선 완료]
  - fetch_product_changes  : AI 생성물 갱신 배치 pull (I-17, §4.8, C-4)
판매자 조회 8종 + 쓰기 3종(I-6~I-16 집계, I-9~I-12 CRUD)은 아래 `SpringClient` 클래스 소관 —
구스텁 get_seller_aggregates·get_product_detail 은 대체·삭제(DESIGN-SELLER-TOOLS-STAGE1).
AI 는 커머스 DB 에 직접 write 하지 않는다. 와이어 포맷은 camelCase (스키마 alias).

인증 레인 (api-spec §2.3, v0.13.0 통일): AI→Spring 역호출은 전 구간 X-Internal-Token 서비스 토큰
  + 본문/쿼리 신원(AI-검증 JWT sub 유래, IDOR 방지). 판매자는 brandId(JWT 클레임)를 {brandId} path 에.
타임아웃: AI→Spring 전 구간 3s 통일 (api-spec §2.9 c — BE I-2 문서 기준).

[배선 v이슈#2] search_products = **GET**(사용자 확정 "그냥 GET으로") — BE I-1 파라미터
  keyword/categoryName/minPrice/maxPrice/brandName/size. dedup·평점·정렬은 요청 파라미터가 아니라
  AI 사후필터(§4.6 v0.15.5, C-15). push_recommendations = POST I-21(productIds 만, 경로 B).

[변경 DESIGN-SELLER-TOOLS-STAGE1] 판매자 조회 8종 + 쓰기 3종은 아래 `SpringClient` 클래스로
신설한다(구스텁 `get_seller_aggregates`·`get_product_detail`는 대체·삭제) — 구매자 함수는
불변이다. `SpringClient`는 X-Internal-Token 서비스 토큰 + `{brandId}` path + 3s 타임아웃
(api-spec §2.3 v0.13.0·§2.9 c)을 한 곳에 바인딩하고, `transport` 인자로 테스트용
`httpx.MockTransport`를 주입할 수 있다(respx 미설치, §0 의존성 실측).
"""

from __future__ import annotations

import logging
import math
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.schemas.spring import (
    AccountEventsResult,
    AddToCartRequest,
    CartOption,
    AddToCartResult,
    BehaviorEventsResult,
    CartView,
    ChurnResult,
    FunnelResult,
    OrderEventsResult,
    ProductChangeLogResult,
    ProductChangesPage,
    ProductCreate,
    ProductCreateResult,
    ProductDeleteResult,
    ProductSearchFilters,
    ProductSearchResult,
    ProductUpdate,
    ProductUpdateResult,
    RecentPurchases,
    RecommendationPush,
    SalesResult,
    SellerProductList,
    SpringProduct,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


_log = logging.getLogger(__name__)


class SpringUnavailableError(Exception):
    """Spring 서버 도달 불가/오류 응답. 상위에서 SEARCH_FAILED 등으로 매핑한다."""


class InvalidCursorError(SpringUnavailableError):
    """I-17 400 INVALID_CURSOR — 안전한 전체 재구축이 필요한 신호."""


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


class CartStockInsufficient(Exception):
    """I-2 담기 재고 부족(400 CART_STOCK_INSUFFICIENT) → action reason STOCK_INSUFFICIENT.

    합산 수량 > 재고(재고는 상품 단위, 옵션별 재고 없음, 2026-07-22 신설). available_stock 은
    BE error.detail.availableStock(남은 재고) — LLM "재고가 N개뿐이에요" 안내용. 없으면 None.
    """

    def __init__(self, available_stock: int | None) -> None:
        self.available_stock = available_stock
        super().__init__(f"CART_STOCK_INSUFFICIENT (available={available_stock})")


class CartQuantityExceeded(CartError):
    """I-2 담기 수량 상한 초과(400 VALIDATION_ERROR, 합산 > 99) → action reason CART_ERROR.

    BE는 합산 수량 > CartItem.MAX_QUANTITY(99)일 때 VALIDATION_ERROR로 차단(CartService.addItem,
    재고검사보다 먼저). CartError 하위라 전용 핸들러가 없어도 CART_ERROR로 낙성한다.
    """


def _client() -> httpx.AsyncClient:
    """공용 httpx.AsyncClient 팩토리. base_url·서비스 토큰은 설정에서 주입한다.

    타임아웃 3s — api-spec §2.9 c (AI→Spring 콜백 통일 기준). 초과 시 각 계약의
    degrade 규칙 적용(조회 생략·담기 CART_ERROR·dedup 생략 등).
    """
    settings = get_settings()
    headers = (
        {"X-Internal-Token": settings.internal_api_token} if settings.internal_api_token else {}
    )
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
    """BE I-1 응답 → ProductSearchResult (§4.6, v0.15.5).

    현재 Spring ``ApiResponse<List<...>>`` 는 data 자체가 배열({success, data:[...]})이다.
    구 계약의 data:{items:[...]} 형태도 브랜치 간 호환을 위해 함께 수용한다. BE 응답엔
    totalCount 가 없어 total_count 는 수신 items 수로 둔다.
    """
    items: list = []
    if isinstance(data, list):
        items = data  # 래퍼 없이 바디가 곧 배열인 경우도 수용
    elif isinstance(data, dict):
        payload = data.get("data")
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
            items = payload["items"]
        elif isinstance(data.get("items"), list):
            items = data["items"]
        elif payload is not None:
            # data 키는 있으나 알려진 형태(list · {items})와 안 맞음 — silent 0 오인 방지 경고(§7).
            _log.warning(
                "검색 응답 data 형태 미인식(silent 0 아님) — data 타입=%s", type(payload).__name__
            )
        elif "data" not in data:
            # data 키 자체가 없음(= data:null 과 구분) — 더 의심스러운 drift.
            _log.warning("검색 응답에 data 키가 없음(silent 0 아님) — envelope drift 의심")
    else:
        _log.warning("검색 응답 최상위 형태 미인식(silent 0 아님) — type=%s", type(data).__name__)
    products = [SpringProduct.model_validate(it) for it in items if isinstance(it, dict)]
    return ProductSearchResult(products=products, total_count=len(products))


def _parse_cart_error(resp: httpx.Response) -> tuple[str | None, list[CartOption], int | None]:
    """I-2 실패 응답에서 code·options 를 방어적으로 파싱한다(§4.1, BE 스키마 🔴).

    code 는 error.code | code. options 는 [BE 확정 2026-07-18] **error.detail.options**
    ([{optionId, name, extraPrice}]) 를 우선하고, 구버전 위치(error.options·options·data.options)도
    방어적으로 본다. name 은 name|optionName, extraPrice(추가금)까지 읽는다.
    """
    try:
        body = resp.json()
    except ValueError:
        return None, [], None
    if not isinstance(body, dict):
        return None, [], None
    err = body.get("error") if isinstance(body.get("error"), dict) else None
    code = (err or {}).get("code") or body.get("code")
    detail = (err or {}).get("detail") if isinstance((err or {}).get("detail"), dict) else None
    # BE 확정 위치(error.detail.options)는 '키 존재'로 우선한다 — 빈 배열이어도 그 값을 신뢰하고
    # 구버전 위치로 조용히 폴백하지 않는다(잔재 options 오선택 방지).
    if detail is not None and "options" in detail:
        raw = detail.get("options") or []
    else:
        raw = (
            (err or {}).get("options")
            or body.get("options")
            or (body.get("data") or {}).get("options")
            or []
        )
    options: list[CartOption] = []
    for opt in raw if isinstance(raw, list) else []:
        if isinstance(opt, dict) and opt.get("optionId") is not None:
            # extraPrice 는 표시용 부가 필드 — 어떤 병적 입력(NaN/Inf/초대형 정수/이상 타입)에도
            # 옵션 자체를 버리거나 스트림을 죽이지 않게 통째로 방어(실패 시 None 강등).
            raw_extra = opt.get("extraPrice")
            try:
                if (
                    isinstance(raw_extra, bool)
                    or not isinstance(raw_extra, (int, float))
                    or not math.isfinite(raw_extra)
                ):
                    extra = None
                else:
                    # BE(Java) BigDecimal/Double 직렬화가 1000.0·999.9999998 처럼 올 수 있어 반올림 수용.
                    extra = round(raw_extra)
            except (ValueError, OverflowError, TypeError):
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
                continue  # 형식 이상 옵션은 건너뜀 — 되물음 흐름 전체가 죽지 않게(방어적)
    # 파싱 실패를 집계해 한 번에 로그(부분 실패도 포함). error.detail.options 는 BE 확정 계약이라
    # 실패는 계약 위반 신호 — REQUIRED/INVALID 공통 파서이므로 실제 code 를 함께 찍어 진단 오도 방지.
    if isinstance(raw, list) and raw:
        dropped = len(raw) - len(options)
        if not options:
            _log.warning(
                "cart 옵션 응답(code=%r) options 전부 파싱 실패(계약 위반 가능): %r", code, raw
            )
        elif dropped:
            _log.warning("cart 옵션 %d/%d개 파싱 실패(부분, code=%r)", dropped, len(raw), code)
    # 재고 부족(CART_STOCK_INSUFFICIENT)일 때 error.detail.availableStock(남은 재고). 병적 입력 방어.
    available_stock: int | None = None
    if detail is not None:
        raw_stock = detail.get("availableStock")
        if isinstance(raw_stock, bool):
            raw_stock = None
        if isinstance(raw_stock, int) and raw_stock >= 0:
            available_stock = raw_stock
        elif isinstance(raw_stock, float) and math.isfinite(raw_stock) and raw_stock >= 0:
            # BE(Java) Double 직렬화가 4.9999998·5.0000002 처럼 올 수 있어 반올림(extraPrice 파싱과 동일).
            # int() 절삭은 실제보다 1 적게 안내할 수 있음(재고는 정수 count).
            available_stock = round(raw_stock)
    return code, options, available_stock


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
    게스트는 호출 스킵(이력 없음, 호출 측 책임). 실패/타임아웃 시 dedup 없이 추천 진행(degrade, §4.7).
    [배선 완료]
    """
    params: dict[str, object] = {}
    if status is not None:
        params["status"] = status
    try:
        async with _client() as client:
            resp = await client.get(f"/internal/members/{user_id}/orders", params=params)
            resp.raise_for_status()
            data = resp.json()
        payload = data.get("data") if isinstance(data, dict) else None
        orders = payload.get("orders") if isinstance(payload, dict) else None
        return RecentPurchases.model_validate({"orders": orders or []})
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        raise SpringUnavailableError(f"get_recent_purchases 실패: {exc}") from exc


async def add_to_cart(request: AddToCartRequest) -> AddToCartResult:
    """장바구니 담기 — BE I-2 문서 채택 (api-spec §4.1). [배선 완료]

    POST {spring_base_url}/internal/cart/items + X-Internal-Token. 본문 신원(userId|guestId)은
    AI-검증 JWT sub 유래(요청 본문 불신, §2.3). 담기 시 BE 재고검증 있음(합산 수량 > 재고 →
    CART_STOCK_INSUFFICIENT + availableStock, 2026-07-22). 실패 코드는 typed 예외로 전파:
    CART_OPTION_REQUIRED→CartOptionRequired(options), CART_OPTION_INVALID→CartOptionInvalid(options),
    CART_STOCK_INSUFFICIENT→CartStockInsufficient(availableStock), VALIDATION_ERROR(합산>99)→
    CartQuantityExceeded, 404→CartProductNotFound, 그 외→CartError.
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

    code, options, available_stock = _parse_cart_error(resp)
    if resp.status_code == 400 and code == "CART_OPTION_REQUIRED":
        raise CartOptionRequired(options)
    if resp.status_code == 400 and code == "CART_OPTION_INVALID":
        raise CartOptionInvalid(options)
    if resp.status_code == 400 and code == "CART_STOCK_INSUFFICIENT":
        raise CartStockInsufficient(available_stock)
    if resp.status_code == 400 and code == "VALIDATION_ERROR":
        # 현 계약(api-spec §4.1)상 이 엔드포인트의 VALIDATION_ERROR 는 "합산 수량 > 99"뿐 → 수량 상한으로 매핑.
        # BE 가 다른 검증 실패를 같은 코드로 재사용하면 계약 드리프트이므로 message 를 남겨 관측 가능하게 한다.
        try:
            body = resp.json()
        except ValueError:
            body = None
        err = body.get("error") if isinstance(body, dict) else None
        be_message = err.get("message") if isinstance(err, dict) else None
        _log.warning("cart VALIDATION_ERROR → 수량초과로 매핑(드리프트 관측): message=%r", be_message)
        raise CartQuantityExceeded(f"add_to_cart 수량 상한 초과: {code}")
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


async def fetch_product_changes(cursor: str | None, limit: int = 500) -> ProductChangesPage:
    """상품 변경분 pull — I-17, AI 생성물 갱신 배치 (api-spec §4.8 / C-4). [배선 완료]

    GET {spring_base_url}/internal/products/changes?since={cursor}&limit={limit} + X-Internal-Token.
    응답 공통 envelope {success, data:{items, nextCursor, hasMore}}(BE 2026-07-18 확정). 도달 불가/오류/
    스키마 불일치는 SpringUnavailableError. INVALID_CURSOR만 전용 예외로 분류해 배치가
    since="0" 전체 재구축으로 복구하고, 나머지는 커서 미전진 상태로 다음 주기에 재개한다(§4.8).
    """
    params: dict[str, object] = {"since": cursor or "0", "limit": limit}
    try:
        async with _client() as client:
            resp = await client.get("/internal/products/changes", params=params)
            if resp.status_code == 400:
                try:
                    error_body = resp.json()
                except ValueError:
                    error_body = None
                if isinstance(error_body, dict):
                    error = error_body.get("error")
                    code = error.get("code") if isinstance(error, dict) else error_body.get("code")
                    if code == "INVALID_CURSOR":
                        raise InvalidCursorError("fetch_product_changes: INVALID_CURSOR")
            resp.raise_for_status()
            data = resp.json()
        # 200 이어도 success=false / data=null 은 실패 envelope — 빈 페이지로 오인해 배치가
        # 조기 종료(정합성 손상)되지 않게 명시 검증한다(리뷰 반영).
        if (
            not isinstance(data, dict)
            or data.get("success") is not True
            or not isinstance(data.get("data"), dict)
        ):
            raise SpringUnavailableError(
                f"fetch_product_changes 비정상 envelope: {repr(data)[:200]}"
            )
        return ProductChangesPage.model_validate(data["data"])
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        raise SpringUnavailableError(f"fetch_product_changes 실패: {exc}") from exc


class SpringClient:
    """판매자 internal API 콜백 클라이언트 (api-spec §4.4/§4.5, DESIGN-SELLER-TOOLS-STAGE1 §2).

    X-Internal-Token 서비스 토큰 + `{brandId}` path + 3s 타임아웃(§2.3 v0.13.0·§2.9 c).
    brand_id 는 메서드 인자로 명시 전달한다 — 호출자(app/agents/seller/tools.py 클로저)가
    검증된 Identity.brand_id 를 넣는다. 이 클래스는 신원을 스스로 판단하지 않는다(IDOR 방지).

    조회 실패는 SpringUnavailableError 로 변환해 raise 한다(타임아웃·4xx/5xx·연결 실패 공통).
    "Error: ..." 문자열 degrade 로의 변환은 상위 도구 계층(app/agents/seller/tools.py)의
    책임이다 — 이 클래스는 관심사 분리를 위해 예외만 던진다.

    테스트는 transport 인자로 httpx.MockTransport 를 주입한다(respx 미설치, §0).
    """

    def __init__(
        self,
        base_url: str,
        internal_token: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = base_url
        self._internal_token = internal_token
        self._transport = transport
        # timeout 미지정 시 Settings 기본값 사용 — 하드코딩 금지(§5), AI→Spring 전 구간 3s.
        self._timeout = timeout if timeout is not None else get_settings().spring_timeout_s

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """공용 요청 헬퍼. X-Internal-Token 헤더 부착 + raise_for_status +
        예외를 SpringUnavailableError 로 통일 변환한다."""
        headers = {"X-Internal-Token": self._internal_token} if self._internal_token else {}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.request(
                    method, path, params=params, json=json_body, headers=headers
                )
                response.raise_for_status()
                payload = response.json()
                # [변경 2026-07-19, REALIGN ②-3] BE 확정 명세(I-13 실측)가
                # {success, data:{...}} 봉투를 쓴다 — data 만 벗겨 모델에 넘긴다.
                # 봉투 없는 응답(과도기·타 API)은 그대로 통과(하위 호환).
                if (
                    isinstance(payload, dict)
                    and "success" in payload
                    and isinstance(payload.get("data"), dict)
                ):
                    return payload["data"]
                return payload
        except httpx.TimeoutException as exc:
            raise SpringUnavailableError(
                f"Spring 콜백 타임아웃({self._timeout}s): {method} {path}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SpringUnavailableError(
                f"Spring 콜백 오류 응답({exc.response.status_code}): {method} {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SpringUnavailableError(f"Spring 콜백 실패: {method} {path} ({exc})") from exc

    def _validate(self, model: type[_ModelT], data: dict) -> _ModelT:
        """Spring 응답을 스키마로 검증하는 단일 지점(opus 리뷰 M1).

        판매자 스키마는 전부 초안(🔴 C-13/C-14)이라 실측 필드 불일치 가능성이 높다.
        pydantic.ValidationError 를 여기서 한 번에 SpringUnavailableError 로 변환해,
        도구 계층(app/agents/seller/tools.py)이 `except SpringUnavailableError` 하나만으로
        degrade 문자열("Error: ...")을 반환할 수 있게 한다(개별 도구에서 ValidationError 를
        따로 잡을 필요가 없다).
        """
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise SpringUnavailableError(
                f"Spring 응답 형식이 예상과 다릅니다({model.__name__}): {exc}"
            ) from exc

    # ── 조회 8종 (전부 GET, api-spec §4.4/§4.5) ──

    async def get_sales(
        self, brand_id: str, from_: str, to: str, granularity: str = "daily"
    ) -> SalesResult:
        """I-6 매출 시계열 조회 (§4.4). granularity: daily/weekly/monthly/summary."""
        data = await self._request(
            "GET",
            f"/internal/seller/{brand_id}/sales",
            params={"from": from_, "to": to, "granularity": granularity},
        )
        return self._validate(SalesResult, data)

    async def get_funnel(self, brand_id: str, from_: str, to: str) -> FunnelResult:
        """I-7 구매전환 퍼널 조회 (§4.4). view→cart→checkout→purchase 4단."""
        data = await self._request(
            "GET", f"/internal/seller/{brand_id}/funnel", params={"from": from_, "to": to}
        )
        return self._validate(FunnelResult, data)

    async def get_events(
        self,
        brand_id: str,
        from_: str,
        to: str,
        event_type: list[str] | None = None,
        product_id: int | None = None,
        group_by: str | None = None,
    ) -> BehaviorEventsResult:
        """I-13 행동 이벤트 집계 조회 (§4.4 — 07/17 BE 확정, REALIGN ②-3).

        eventType 은 상품 연계 4종 복수 선택(미지정 = 4종 전체), groupBy 는
        product(기본)/eventType/date. 실패 코드: INVALID_PERIOD/INVALID_GROUP_BY(400).
        """
        params: dict = {"from": from_, "to": to}
        if event_type:
            params["eventType"] = event_type  # httpx 가 리스트를 반복 쿼리로 직렬화
        if product_id is not None:
            params["productId"] = product_id
        if group_by:
            params["groupBy"] = group_by
        data = await self._request("GET", f"/internal/seller/{brand_id}/events", params=params)
        return self._validate(BehaviorEventsResult, data)

    async def get_order_events(
        self,
        brand_id: str,
        from_: str,
        to: str,
        to_status: list[str] | None = None,
        actor_type: str | None = None,
        group_by: str | None = None,
        stats: bool | None = None,
    ) -> OrderEventsResult:
        """I-14 주문 상태 전이/조회 (§4.4). toStatus 는 복수 허용, stats 는 집계 모드 플래그.

        [혼동 금지] 구매자 get_recent_purchases(GET /orders/recent, §4.7)와 다른 계약이다.
        """
        params: dict = {"from": from_, "to": to}
        if to_status:
            params["toStatus"] = to_status
        if actor_type:
            params["actorType"] = actor_type
        if group_by:
            params["groupBy"] = group_by
        if stats is not None:
            params["stats"] = stats
        data = await self._request(
            "GET", f"/internal/seller/{brand_id}/order-events", params=params
        )
        return self._validate(OrderEventsResult, data)

    async def get_product_changes(
        self,
        brand_id: str,
        from_: str,
        to: str,
        change_type: str | None = None,
        product_id: int | None = None,
    ) -> ProductChangeLogResult:
        """I-15 상품 변경 이력(판매자 감사 로그) 조회 (§4.4). changeType: PRICE/STOCK/STATUS.

        [혼동 금지] 구매자 fetch_product_changes(I-8, §4.8 AI 생성물 배치)와 다른 계약이다.
        """
        params: dict = {"from": from_, "to": to}
        if change_type:
            params["changeType"] = change_type
        if product_id:
            params["productId"] = product_id
        data = await self._request(
            "GET", f"/internal/seller/{brand_id}/product-changes", params=params
        )
        return self._validate(ProductChangeLogResult, data)

    async def get_churn(self, brand_id: str, inactive_days: int) -> ChurnResult:
        """I-16 이탈 코호트 조회 (§4.4). inactiveDays 무활동 기준일."""
        data = await self._request(
            "GET",
            f"/internal/seller/{brand_id}/churn",
            params={"inactiveDays": inactive_days},
        )
        return self._validate(ChurnResult, data)

    async def get_account_events(
        self,
        event_type: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        group_by: str | None = None,
    ) -> AccountEventsResult:
        """I-8 계정/보안 이벤트 집계 조회 (§4.4). ⚠️ brandId path 없음 — 전역·admin 소유 🔴."""
        params: dict = {}
        if event_type:
            params["eventType"] = event_type
        if from_:
            params["from"] = from_
        if to:
            params["to"] = to
        if group_by:
            params["groupBy"] = group_by
        data = await self._request("GET", "/internal/account-events", params=params)
        return self._validate(AccountEventsResult, data)

    async def list_products(
        self,
        brand_id: str,
        status: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> SellerProductList:
        """I-9 자사 상품 목록 조회 (§4.5). status: ON_SALE/HIDDEN. draft/product 도구의 before 소스."""
        params: dict = {}
        if status:
            params["status"] = status
        if q:
            params["q"] = q
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        data = await self._request("GET", f"/internal/seller/{brand_id}/products", params=params)
        return self._validate(SellerProductList, data)

    # ── 쓰기 3종 (product_agent 전용, HITL 승인 후에만 호출, api-spec §4.5) ──

    async def create_product(self, brand_id: str, payload: ProductCreate) -> ProductCreateResult:
        """I-10 상품 등록 (§4.5). name/price/stockQuantity 필수(price ≤ originalPrice).

        미설정 옵션 필드(originalPrice/category/description/imageUrl 등)는 exclude_none 으로
        본문에서 제외한다(opus 리뷰 m4) — null 전송 대신 필드 자체를 생략한다.
        """
        data = await self._request(
            "POST",
            f"/internal/seller/{brand_id}/products",
            json_body=payload.model_dump(by_alias=True, exclude_none=True),
        )
        return self._validate(ProductCreateResult, data)

    async def update_product(
        self, brand_id: str, product_id: int, patch: ProductUpdate
    ) -> ProductUpdateResult:
        """I-11 상품 수정 (§4.5). 바꿀 필드만 전송 — 재고도 이 API로 통합(별도 재고 API 없음)."""
        data = await self._request(
            "PATCH",
            f"/internal/seller/{brand_id}/products/{product_id}",
            json_body=patch.model_dump(by_alias=True, exclude_none=True),
        )
        return self._validate(ProductUpdateResult, data)

    async def delete_product(self, brand_id: str, product_id: int) -> ProductDeleteResult:
        """I-12 상품 삭제(soft) (§4.5). 물리 삭제 없음 — status=HIDDEN 전환."""
        data = await self._request("DELETE", f"/internal/seller/{brand_id}/products/{product_id}")
        return self._validate(ProductDeleteResult, data)


# ── 싱글턴 (2026-07-18 ToolRuntime 전환 — 신원은 SellerContext, 클라이언트는 앱 소유) ──

_default_client: SpringClient | None = None


def get_spring_client() -> SpringClient:
    """앱 수명주기 공유 SpringClient 를 반환한다(커넥션 풀 재사용).

    도구(app/agents/seller/tools.py)는 이 싱글턴을 쓰고, 신원(brand_id)은
    ToolRuntime[SellerContext] 로 요청마다 주입받는다. 테스트는 set_spring_client()
    로 이중(double)을 끼운다.
    """
    global _default_client
    if _default_client is None:
        # [수정 2026-07-20 rebase 합류] 빈 생성자 호출(잠재 TypeError — 테스트는 전부
        # 주입식이라 미노출) 정정 + 토큰 키를 팀 규약 internal_api_token 으로 통일.
        settings = get_settings()
        _default_client = SpringClient(
            settings.spring_base_url, settings.internal_api_token or None
        )
    return _default_client


def set_spring_client(client: SpringClient | None) -> None:
    """싱글턴 교체(테스트 주입)·해제(None). 앱 종료 훅에서도 사용한다."""
    global _default_client
    _default_client = client
