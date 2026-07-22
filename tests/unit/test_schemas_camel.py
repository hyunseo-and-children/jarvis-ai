"""SSE·Spring 스키마 camelCase 직렬화 계약 테스트 (api-spec v0.4.0 §2.2/§3.1/§4).

와이어 포맷이 camelCase 인지(별칭 배선)와 입력 시 snake/camel 양쪽 허용을 고정한다.
"""

from __future__ import annotations

from app.schemas.chat import ChatRequest, DoneData, ProductsReadyData
from app.schemas.spring import (
    ProductCreate,
    ProductSearchFilters,
    RecoReason,
    RecommendationPush,
    SellerProductRow,
)


def test_products_ready_serializes_camel() -> None:
    """products.ready 는 sessionId/listId 로 직렬화된다 (snake 속성 → camel 별칭)."""
    d = ProductsReadyData(session_id="s-1", list_id="l-1").model_dump(by_alias=True)
    assert d == {"sessionId": "s-1", "listId": "l-1"}


def test_done_serializes_camel() -> None:
    """done 은 finishReason 으로 직렬화된다."""
    assert DoneData(finish_reason="stop").model_dump(by_alias=True) == {"finishReason": "stop"}


def test_chat_request_accepts_camel_input() -> None:
    """요청 본문(camelCase)이 snake 속성으로 파싱된다 (populate_by_name)."""
    req = ChatRequest.model_validate({"sessionId": "s", "threadId": "t", "message": "m"})
    assert req.session_id == "s"
    assert req.thread_id == "t"


def test_search_filters_serialize_camel() -> None:
    """검색 필터는 excludeProductIds/priceMax 등 camelCase 로 나간다 (§4.2 와이어)."""
    f = ProductSearchFilters(price_max=50000, exclude_product_ids=[1], limit=30)
    d = f.model_dump(by_alias=True)
    assert d["priceMax"] == 50000
    assert d["excludeProductIds"] == [1]
    assert d["limit"] == 30


def test_recommendation_push_i21_serializes_camel() -> None:
    """I-21 추천 push 는 sessionId/listId/productIds(숫자 id만) 로 직렬화된다 (§4.2 v0.15.0).

    reasons 미지정 시 빈 배열 — Spring 미수용 상태여도 무해한 하위호환 형태(이슈 #61).
    """
    push = RecommendationPush(session_id="s-1", list_id="l-1", product_ids=[101, 205, 552])
    d = push.model_dump(by_alias=True)
    assert d == {"sessionId": "s-1", "listId": "l-1", "productIds": [101, 205, 552], "reasons": []}
    # 표시 필드·groups 구조 부재 확인 (경로 B — id 만 전달).
    assert "groups" not in d
    assert "price" not in d


def test_recommendation_push_reasons_serializes_camel() -> None:
    """I-21 reasons 는 {productId, reason} camelCase 항목으로 직렬화된다 (§4.2 v0.15.2, 이슈 #61)."""
    push = RecommendationPush(
        session_id="s-1",
        list_id="l-1",
        product_ids=[101, 205],
        reasons=[
            RecoReason(product_id=101, reason="방수 등급이 높아 우천 시에도 안전합니다."),
            RecoReason(product_id=205, reason="가벼워 휴대가 편합니다."),
        ],
    )
    d = push.model_dump(by_alias=True)
    assert d["reasons"] == [
        {"productId": 101, "reason": "방수 등급이 높아 우천 시에도 안전합니다."},
        {"productId": 205, "reason": "가벼워 휴대가 편합니다."},
    ]
    # 순서 권위는 productIds — reasons 는 부분집합/순서무관 허용(계약 §4.2).
    assert d["productIds"] == [101, 205]


def test_seller_product_row_serializes_camel() -> None:
    """SellerProductRow(I-9)는 originalPrice/stockQuantity/displayedSalesCount 로 직렬화된다."""
    row = SellerProductRow(
        product_id=101,
        name="여행용 파우치",
        price=10000,
        original_price=12000,
        stock_quantity=5,
        status="ON_SALE",
        displayed_sales_count=42,
    )
    d = row.model_dump(by_alias=True)
    assert d["originalPrice"] == 12000
    assert d["stockQuantity"] == 5
    assert d["displayedSalesCount"] == 42


def test_product_create_by_alias() -> None:
    """ProductCreate(I-10) 요청 바디는 camelCase 로 직렬화된다."""
    payload = ProductCreate(name="여행용 파우치", price=10000, stock_quantity=5)
    d = payload.model_dump(by_alias=True)
    assert d == {
        "name": "여행용 파우치",
        "price": 10000,
        "originalPrice": None,
        "stockQuantity": 5,
        "category": None,
        "description": None,
        "imageUrl": None,
    }
