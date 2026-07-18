"""SSE·Spring 스키마 camelCase 직렬화 계약 테스트 (api-spec v0.4.0 §2.2/§3.1/§4).

와이어 포맷이 camelCase 인지(별칭 배선)와 입력 시 snake/camel 양쪽 허용을 고정한다.
"""

from __future__ import annotations

from app.schemas.chat import ChatRequest, DoneData, ProductsReadyData
from app.schemas.spring import (
    ProductSearchFilters,
    RecommendationPush,
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
    req = ChatRequest.model_validate(
        {"sessionId": "s", "threadId": "t", "message": "m"}
    )
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
    """I-21 추천 push 는 sessionId/listId/productIds(숫자 id만) 로 직렬화된다 (§4.2 v0.15.0)."""
    push = RecommendationPush(session_id="s-1", list_id="l-1", product_ids=[101, 205, 552])
    d = push.model_dump(by_alias=True)
    assert d == {"sessionId": "s-1", "listId": "l-1", "productIds": [101, 205, 552]}
    # 표시 필드·groups 구조 부재 확인 (경로 B — id 만 전달).
    assert "groups" not in d
    assert "price" not in d
