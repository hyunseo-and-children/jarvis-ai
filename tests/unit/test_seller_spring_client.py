"""SpringClient(판매자 조회/쓰기) 단위 테스트 — httpx.MockTransport (respx 미설치, §0).

DESIGN-SELLER-TOOLS-STAGE1 §6 테스트 목록. 실 네트워크 없이 요청 URL·헤더·쿼리·바디와
응답 파싱, 오류 매핑(SpringUnavailableError)을 검증한다.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import get_settings
from app.schemas.spring import ProductCreate, ProductUpdate
from app.services.spring_client import SpringClient, SpringUnavailableError

BASE_URL = "http://spring.internal.test"
TOKEN = "svc-token-123"


def _client(
    handler, *, internal_token: str | None = TOKEN, timeout: float | None = None
) -> SpringClient:
    transport = httpx.MockTransport(handler)
    return SpringClient(BASE_URL, internal_token, transport=transport, timeout=timeout)


async def test_get_sales_hits_brand_path_with_internal_token() -> None:
    """요청 URL이 /internal/seller/{brandId}/sales, X-Internal-Token 헤더, from/to/granularity 쿼리."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        return httpx.Response(200, json={"series": []})

    client = _client(handler)
    await client.get_sales("brand-1", "2026-07-01", "2026-07-14", "daily")

    assert "/internal/seller/brand-1/sales" in captured["url"]
    assert captured["headers"]["X-Internal-Token"] == TOKEN
    assert "from=2026-07-01" in captured["url"]
    assert "to=2026-07-14" in captured["url"]
    assert "granularity=daily" in captured["url"]


async def test_get_sales_parses_camel_response() -> None:
    """camelCase 응답(series[{date,sales,orderCount,isAnomaly,deviationPct}]) → SalesResult 파싱."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "series": [
                    {
                        "date": "2026-07-01",
                        "sales": 10000,
                        "orderCount": 5,
                        "isAnomaly": False,
                        "deviationPct": 1.2,
                    }
                ]
            },
        )

    client = _client(handler)
    result = await client.get_sales("brand-1", "2026-07-01", "2026-07-01")

    assert len(result.series) == 1
    point = result.series[0]
    assert point.date == "2026-07-01"
    assert point.sales == 10000
    assert point.order_count == 5
    assert point.is_anomaly is False
    assert point.deviation_pct == 1.2


async def test_account_events_has_no_brand_in_path() -> None:
    """I-8은 /internal/account-events (brandId 없음)."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"events": []})

    client = _client(handler)
    await client.get_account_events(event_type="LOGIN_FAIL")

    assert captured["url"].startswith(f"{BASE_URL}/internal/account-events")
    assert "brand" not in captured["url"].lower().split("?")[0]


async def test_create_product_posts_body_by_alias() -> None:
    """POST 바디가 camelCase(stockQuantity 등), 201 파싱."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"productId": 101, "status": "ON_SALE"})

    client = _client(handler)
    payload = ProductCreate(name="여행용 파우치", price=10000, stock_quantity=5)
    result = await client.create_product("brand-1", payload)

    assert captured["method"] == "POST"
    assert captured["body"]["stockQuantity"] == 5
    assert captured["body"]["name"] == "여행용 파우치"
    assert result.product_id == 101
    assert result.status == "ON_SALE"


async def test_create_product_excludes_unset_optional_fields() -> None:
    """미설정 옵션 필드(originalPrice/category/description/imageUrl)는 바디에서 아예 빠진다.

    (opus 리뷰 m4 — exclude_none 누락 시 null 로 전송되던 문제)
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"productId": 101, "status": "ON_SALE"})

    client = _client(handler)
    payload = ProductCreate(name="여행용 파우치", price=10000, stock_quantity=5)
    await client.create_product("brand-1", payload)

    assert captured["body"] == {"name": "여행용 파우치", "price": 10000, "stockQuantity": 5}
    for absent_key in ("originalPrice", "category", "description", "imageUrl"):
        assert absent_key not in captured["body"]


async def test_update_product_uses_patch_and_product_path() -> None:
    """PATCH /…/products/{productId}, 바꿀 필드만 전송."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"productId": 101})

    client = _client(handler)
    patch = ProductUpdate(price=9000)
    await client.update_product("brand-1", 101, patch)

    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/internal/seller/brand-1/products/101")
    # 바꿀 필드(price)만 전송 — 미지정 필드(name 등)는 바디에 없어야 한다.
    assert captured["body"] == {"price": 9000}


async def test_delete_product_uses_delete_and_returns_hidden() -> None:
    """DELETE, 응답 status=HIDDEN."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        return httpx.Response(200, json={"productId": 101, "status": "HIDDEN"})

    client = _client(handler)
    result = await client.delete_product("brand-1", 101)

    assert captured["method"] == "DELETE"
    assert result.status == "HIDDEN"
    assert result.product_id == 101


async def test_timeout_maps_to_spring_unavailable() -> None:
    """httpx.TimeoutException → SpringUnavailableError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    client = _client(handler)
    with pytest.raises(SpringUnavailableError):
        await client.get_sales("brand-1", "2026-07-01", "2026-07-14")


async def test_4xx_maps_to_spring_unavailable() -> None:
    """404/500 등 오류 응답 → SpringUnavailableError."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "NOT_FOUND"})

    client = _client(handler)
    with pytest.raises(SpringUnavailableError):
        await client.get_sales("brand-1", "2026-07-01", "2026-07-14")


async def test_client_uses_configured_timeout_3s() -> None:
    """timeout 미지정 시 settings.spring_timeout_s(기본 3.0) 를 사용한다."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"series": []})

    client = _client(handler, timeout=None)
    assert client._timeout == get_settings().spring_timeout_s
    assert client._timeout == 3.0


async def test_invalid_response_schema_maps_to_spring_unavailable() -> None:
    """필수 필드 누락 응답(pydantic ValidationError) → SpringUnavailableError (opus 리뷰 M1).

    스키마가 전부 초안(🔴 C-13/C-14)이라 실측 응답과 어긋날 수 있다 — model_validate 실패도
    타임아웃/4xx 와 동일하게 단일 지점(_validate)에서 SpringUnavailableError 로 변환되어야
    도구 계층이 raise 없이 "Error: ..." 문자열로 degrade 할 수 있다.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        # SalesSeriesPoint 는 sales/orderCount 가 필수 — date 만 있는 응답은 스키마 위반.
        return httpx.Response(200, json={"series": [{"date": "2026-07-01"}]})

    client = _client(handler)
    with pytest.raises(SpringUnavailableError):
        await client.get_sales("brand-1", "2026-07-01", "2026-07-01")


async def test_get_order_events_passes_stats_param() -> None:
    """stats 플래그가 쿼리 파라미터로 전달된다(opus 리뷰 m6, api-spec §4.4 stats)."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"events": []})

    client = _client(handler)
    await client.get_order_events("brand-1", "2026-07-01", "2026-07-14", stats=True)

    assert "stats=true" in captured["url"]


# ── I-13 행동 이벤트 (REALIGN ②-3 — 07/17 확정 명세) ──


async def test_get_events_serializes_filters_as_query() -> None:
    """eventType 복수 반복 쿼리·productId 숫자·groupBy 가 URL 에 실린다."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200, json={"success": True, "data": {"groupBy": "date", "series": []}}
        )

    client = _client(handler)
    await client.get_events(
        "brand-1",
        "2026-07-01",
        "2026-07-14",
        event_type=["product_view", "add_to_cart"],
        product_id=101,
        group_by="date",
    )

    url = captured["url"]
    assert "/internal/seller/brand-1/events" in url
    assert "eventType=product_view" in url and "eventType=add_to_cart" in url
    assert "productId=101" in url
    assert "groupBy=date" in url


async def test_get_events_unwraps_success_envelope_and_parses_rows() -> None:
    """{success,data} 봉투를 벗기고 groupBy=product rows 를 파싱한다(숫자 productId)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "groupBy": "product",
                    "rows": [
                        {
                            "productId": 101,
                            "productName": "에어 러너 2",
                            "counts": {
                                "productView": 1820,
                                "addToCart": 240,
                                "checkoutStart": 96,
                                "purchaseComplete": 61,
                            },
                            "viewToCartRate": 0.132,
                            "uniqueVisitors": 1503,
                        }
                    ],
                    "total": 17,
                },
            },
        )

    client = _client(handler)
    result = await client.get_events("brand-1", "2026-07-01", "2026-07-14")

    assert result.group_by == "product"
    assert result.total == 17
    row = result.rows[0]
    assert row.product_id == 101
    assert row.counts["productView"] == 1820
    assert row.view_to_cart_rate == 0.132
    assert row.unique_visitors == 1503


async def test_get_events_null_rate_and_event_type_shape() -> None:
    """viewToCartRate null 수용 + groupBy=eventType 은 counts 만 채워진다."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"groupBy": "eventType", "counts": {"productView": 8120}},
            },
        )

    client = _client(handler)
    result = await client.get_events("brand-1", "2026-07-01", "2026-07-14", group_by="eventType")

    assert result.counts == {"productView": 8120}
    assert result.rows == [] and result.series == []


async def test_non_envelope_response_passes_through() -> None:
    """봉투 없는 응답(과도기·타 API)은 그대로 파싱된다 — 하위 호환."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"groupBy": "product", "rows": [], "total": 0})

    client = _client(handler)
    result = await client.get_events("brand-1", "2026-07-01", "2026-07-14")

    assert result.total == 0
