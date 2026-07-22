"""장바구니 서브그래프 (이슈 #3) — 담기·옵션 되물음·조회·오류 매핑·라우팅·배선 회귀.

stream_cart_add/view 는 add_fn/get_cart_fn 주입으로, 라우팅은 run_buyer_turn + FakeLLM 으로,
spring_client 배선은 _client 몽키패치로 라이브 Spring 없이 구동한다.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agents.buyer.cart.graph import stream_cart_add, stream_cart_view
from app.agents.buyer.cart.state import CartStateStore, PendingAdd
from app.agents.buyer.graph import run_buyer_turn
from app.agents.buyer.recommendation.state import CartIntent
from app.core.auth import Identity
from app.core.config import get_settings
from app.schemas.spring import (
    AddToCartResult,
    CartOption,
    CartView,
    CartViewItem,
    ProductSearchResult,
)
from app.services.spring_client import (
    CartError,
    CartOptionInvalid,
    CartOptionRequired,
    CartProductNotFound,
    CartQuantityExceeded,
    CartStockInsufficient,
    SpringUnavailableError,
)


def _member() -> Identity:
    return Identity(user_id="123", is_guest=False, seller_id=None, subject="123")


def _guest() -> Identity:
    return Identity(user_id=None, is_guest=True, seller_id=None, subject="guest-uuid-1")


def _anon() -> Identity:
    return Identity(user_id=None, is_guest=True, seller_id=None, subject=None)


async def _collect(gen) -> list[dict]:
    events: list[dict] = []
    async for frame in gen:
        line = frame.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


def _types(events) -> list[str]:
    return [e["type"] for e in events]


def _empty_cart(**_):
    async def _get(*, user_id=None, guest_id=None):
        return CartView(items=[])

    return _get


# ─────────── 담기 성공 / 합산 ───────────


async def test_cart_add_success() -> None:
    store = CartStateStore()

    async def add_fn(req):
        return AddToCartResult(success=True, cart_item_id=55)

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADDED"
    assert action["cartItemId"] == 55
    assert _types(events)[-1] == "done"


async def test_cart_add_merge_notice_when_existing() -> None:
    store = CartStateStore()

    async def add_fn(req):
        return AddToCartResult(success=True, cart_item_id=56)

    async def get_cart_fn(*, user_id=None, guest_id=None):
        return CartView(
            items=[CartViewItem(cart_item_id=9, product_id=1, option_id=None, quantity=2)]
        )

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=get_cart_fn,
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADDED"
    assert "더했" in action["message"]  # 합산 안내


# ─────────── 옵션 되물음 멀티턴 ───────────


async def test_cart_add_option_required_reasks_and_sets_pending() -> None:
    store = CartStateStore()

    async def add_fn(req):
        raise CartOptionRequired(
            [CartOption(option_id=3, name="블루"), CartOption(option_id=4, name="레드")]
        )

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    types = _types(events)
    assert "action" not in types  # 되물음은 실패 action 이 아니다(§4.1)
    token = next(e for e in events if e["type"] == "token")["data"]["text"]
    assert "블루" in token and "레드" in token
    pending = await store.get_pending("m:t")
    assert pending is not None and pending.product_id == 1


async def test_cart_option_reask_strips_seller_text() -> None:
    """Spring 옵션명(판매자 입력 영향)은 token 조립 후 위험 문자가 제거된다."""
    store = CartStateStore()

    async def add_fn(req):
        raise CartOptionRequired(
            [
                CartOption(option_id=3, name="블\x1b[31m루\u200b\u202e"),
                CartOption(option_id=4, name="레\n드"),
            ]
        )

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="unsafe-option",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )

    token = next(e for e in events if e["type"] == "token")["data"]["text"]
    assert "블[31m루" in token and "레 드" in token
    assert all(ch not in token for ch in ("\x1b", "\u200b", "\u202e", "\n"))


async def test_cart_add_reask_then_success_clears_pending() -> None:
    store = CartStateStore()
    await store.set_pending(
        "m:t", PendingAdd(product_id=1, quantity=2, options=[CartOption(option_id=3, name="블루")])
    )

    async def add_fn(req):
        assert (
            req.product_id == 1 and req.option_id == 3 and req.quantity == 2
        )  # pending 상품/수량 + 이번 optionId
        return AddToCartResult(success=True, cart_item_id=77)

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, option_id=3, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADDED" and action["cartItemId"] == 77
    assert await store.get_pending("m:t") is None  # 성공 후 정리


async def test_cart_add_option_invalid_exhausts_to_cart_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "cart_option_reask_max", 1)
    store = CartStateStore()
    # 이미 1회 재질문한 상태(attempts=1) → 다음 INVALID 는 상한 초과 → CART_ERROR
    await store.set_pending(
        "m:t",
        PendingAdd(
            product_id=1, quantity=1, options=[CartOption(option_id=3, name="블루")], attempts=1
        ),
    )

    async def add_fn(req):
        raise CartOptionInvalid([CartOption(option_id=3, name="블루")])

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, option_id=9, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=settings,
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "CART_ERROR"
    assert await store.get_pending("m:t") is None


async def test_cart_add_option_invalid_reasks_within_limit() -> None:
    store = CartStateStore()
    await store.set_pending(
        "m:t",
        PendingAdd(
            product_id=1, quantity=1, options=[CartOption(option_id=3, name="블루")], attempts=0
        ),
    )

    async def add_fn(req):
        raise CartOptionInvalid([CartOption(option_id=3, name="블루")])

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, option_id=9, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert "action" not in _types(events)  # 아직 상한 내 → 재질문
    assert (await store.get_pending("m:t")).attempts == 1


# ─────────── 담기 오류 매핑 ───────────


async def test_cart_add_product_not_found() -> None:
    store = CartStateStore()

    async def add_fn(req):
        raise CartProductNotFound()

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=999, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "PRODUCT_NOT_FOUND"


async def test_cart_add_stock_insufficient_exposes_remaining() -> None:
    """재고 부족 → reason STOCK_INSUFFICIENT + 남은 재고 수 노출(2026-07-22)."""
    store = CartStateStore()

    async def add_fn(req):
        raise CartStockInsufficient(3)

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=5),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "STOCK_INSUFFICIENT"
    assert "3" in action["message"]


async def test_cart_add_stock_insufficient_without_count_falls_back() -> None:
    """남은 재고 수 미상(None) → 일반 재고부족 안내(reason 은 여전히 STOCK_INSUFFICIENT)."""
    store = CartStateStore()

    async def add_fn(req):
        raise CartStockInsufficient(None)

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=5),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "STOCK_INSUFFICIENT"


async def test_cart_add_stock_zero_says_soldout() -> None:
    """재고 0(품절, BE ON_SALE+stock 0) → "품절된 상품이에요"(reason 은 STOCK_INSUFFICIENT 유지)."""
    store = CartStateStore()

    async def add_fn(req):
        raise CartStockInsufficient(0)

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "STOCK_INSUFFICIENT"
    assert action["message"] == "품절된 상품이에요."


async def test_cart_add_quantity_exceeded_uses_be_message() -> None:
    """수량 상한 초과(합산 > 99, BE VALIDATION_ERROR) → CART_ERROR + BE 동일 문구."""
    store = CartStateStore()

    async def add_fn(req):
        raise CartQuantityExceeded("합산 초과")

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=5),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "CART_ERROR"
    assert action["message"] == "수량은 최대 99개까지 담을 수 있습니다."


async def test_cart_add_error_maps_to_cart_error() -> None:
    store = CartStateStore()

    async def add_fn(req):
        raise CartError("token invalid")

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "CART_ERROR"


async def test_cart_add_degrades_when_get_cart_fails() -> None:
    """조회 실패해도 담기는 진행한다(§4.9 degrade)."""
    store = CartStateStore()

    async def add_fn(req):
        return AddToCartResult(success=True, cart_item_id=1)

    async def get_cart_fn(*, user_id=None, guest_id=None):
        raise SpringUnavailableError("down")

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=get_cart_fn,
        )
    )
    assert next(e for e in events if e["type"] == "action")["data"]["type"] == "CART_ADDED"


async def test_cart_add_no_product_asks_clarify() -> None:
    store = CartStateStore()

    async def add_fn(req):
        raise AssertionError("productId 없으면 add 호출 금지")

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=None, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert "action" not in _types(events)
    assert "어떤 상품" in next(e for e in events if e["type"] == "token")["data"]["text"]


async def test_cart_add_anon_requires_login() -> None:
    store = CartStateStore()

    async def add_fn(req):
        raise AssertionError("익명은 add 호출 금지")

    events = await _collect(
        stream_cart_add(
            identity=_anon(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="a:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "CART_ERROR"


async def test_cart_add_guest_uses_guest_id() -> None:
    store = CartStateStore()
    captured = {}

    async def add_fn(req):
        captured["userId"] = req.user_id
        captured["guestId"] = req.guest_id
        return AddToCartResult(success=True, cart_item_id=1)

    await _collect(
        stream_cart_add(
            identity=_guest(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="g:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert captured["userId"] is None and captured["guestId"] == "guest-uuid-1"


# ─────────── 조회 ───────────


async def test_cart_view_lists_items() -> None:
    async def get_cart_fn(*, user_id=None, guest_id=None):
        return CartView(
            items=[
                CartViewItem(
                    cart_item_id=1,
                    product_id=1,
                    product_name="방수 파우치",
                    option_name="블루",
                    quantity=2,
                )
            ]
        )

    events = await _collect(stream_cart_view(identity=_member(), get_cart_fn=get_cart_fn))
    token = next(e for e in events if e["type"] == "token")["data"]["text"]
    assert "방수 파우치" in token and "블루" in token and "2개" in token


async def test_cart_view_strips_seller_text() -> None:
    """장바구니 상품명·옵션명은 사용자 token 경계에서 정제된다."""

    async def get_cart_fn(*, user_id=None, guest_id=None):
        return CartView(
            items=[
                CartViewItem(
                    cart_item_id=1,
                    product_id=1,
                    product_name="방수\x1b[31m 파우치\u200b\u202e",
                    option_name="블\n루",
                    quantity=2,
                ),
                CartViewItem(
                    cart_item_id=2,
                    product_id=2,
                    product_name="정상 상품",
                    quantity=1,
                ),
            ]
        )

    events = await _collect(stream_cart_view(identity=_member(), get_cart_fn=get_cart_fn))
    token = next(e for e in events if e["type"] == "token")["data"]["text"]
    assert token == ("장바구니에 담긴 상품이에요:\n방수[31m 파우치 (블 루) · 2개\n정상 상품 · 1개")
    assert all(ch not in token for ch in ("\x1b", "\u200b", "\u202e"))


async def test_cart_view_empty() -> None:
    events = await _collect(stream_cart_view(identity=_member(), get_cart_fn=_empty_cart()))
    assert "비어" in next(e for e in events if e["type"] == "token")["data"]["text"]


async def test_cart_view_unavailable() -> None:
    async def get_cart_fn(*, user_id=None, guest_id=None):
        raise SpringUnavailableError("down")

    events = await _collect(stream_cart_view(identity=_member(), get_cart_fn=get_cart_fn))
    assert "불러오지" in next(e for e in events if e["type"] == "token")["data"]["text"]


# ─────────── 라우팅 (run_buyer_turn + FakeLLM) ───────────


def _req(message="담아줘", thread_id="t1"):
    return SimpleNamespace(session_id="s1", thread_id=thread_id, message=message)


async def test_route_cart_add(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._fakes import FakeLLM
    import app.services.spring_client as sc

    async def fake_add(req):
        return AddToCartResult(success=True, cart_item_id=42)

    async def fake_get(*, user_id=None, guest_id=None):
        return CartView(items=[])

    monkeypatch.setattr(sc, "add_to_cart", fake_add)
    monkeypatch.setattr(sc, "get_cart", fake_get)
    # 직전 추천이 있어야 담기 가능(경로 B) — last_reco 시드.
    from app.agents.buyer.cart.state import get_cart_store
    from app.core.conversation import conversation_key

    seed_store = await get_cart_store()
    await seed_store.set_last_reco(conversation_key("123", "t1"), [(101, "이어폰")])
    llm = FakeLLM(decompose={"intent": "cart_add", "cart": {"productId": 101, "quantity": 1}})
    events = await _collect(run_buyer_turn(_req(), _member(), llm=llm))
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADDED" and action["cartItemId"] == 42


async def test_route_cart_view(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._fakes import FakeLLM
    import app.services.spring_client as sc

    async def fake_get(*, user_id=None, guest_id=None):
        return CartView(
            items=[CartViewItem(cart_item_id=1, product_id=1, product_name="키보드", quantity=1)]
        )

    monkeypatch.setattr(sc, "get_cart", fake_get)
    llm = FakeLLM(decompose={"intent": "cart_view", "cart": {}})
    events = await _collect(run_buyer_turn(_req(message="장바구니 뭐 있어?"), _member(), llm=llm))
    assert "키보드" in next(e for e in events if e["type"] == "token")["data"]["text"]
    assert "action" not in _types(events)


async def test_last_reco_stored_after_recommendation() -> None:
    """추천 턴이 후보를 last_reco 로 저장해 이후 담기의 productId 해소 소스가 된다."""
    from app.agents.buyer.cart.state import get_cart_store
    from app.core.conversation import conversation_key
    from tests._fakes import DEFAULT_PRODUCTS, FakeLLM

    async def search(filters, exclude_product_ids=None):
        return ProductSearchResult(products=DEFAULT_PRODUCTS, total_count=len(DEFAULT_PRODUCTS))

    async def push(p):
        return True

    await _collect(
        run_buyer_turn(
            _req(message="무선 이어폰 추천", thread_id="t9"),
            _member(),
            llm=FakeLLM(),
            search=search,
            push_fn=push,
        )
    )
    cart_store = await get_cart_store()
    reco = await cart_store.get_last_reco(conversation_key("123", "t9"))
    assert [pid for pid, _ in reco] == [101, 102, 103]


# ─────────── spring_client 배선 (I-2 담기 · I-18 조회) ───────────


class _CartResp:
    def __init__(self, status_code, data) -> None:
        self.status_code = status_code
        self._data = data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._data


class _CartClient:
    def __init__(self, resp) -> None:
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url, json=None):
        return self._resp

    async def get(self, url, params=None):
        return self._resp


async def test_add_to_cart_success_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    monkeypatch.setattr(
        sc,
        "_client",
        lambda: _CartClient(_CartResp(200, {"success": True, "data": {"cartItemId": 55}})),
    )
    res = await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=1))
    assert res.success and res.cart_item_id == 55


async def test_add_to_cart_option_required_raises_with_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[BE 확정 2026-07-18] error.detail.options = [{optionId, name, extraPrice}] 를 파싱한다."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {
        "error": {
            "code": "CART_OPTION_REQUIRED",
            "detail": {
                "options": [
                    {"optionId": 3, "name": "블루", "extraPrice": 0},
                    {"optionId": 4, "name": "레드", "extraPrice": 1000},
                ]
            },
        }
    }
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartOptionRequired) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=1))
    opts = ei.value.options
    assert [o.option_id for o in opts] == [3, 4]
    assert [o.name for o in opts] == ["블루", "레드"]
    assert opts[1].extra_price == 1000


async def test_add_to_cart_option_required_legacy_location(monkeypatch: pytest.MonkeyPatch) -> None:
    """구버전 위치(error.options, optionName)도 방어적으로 파싱한다(하위호환)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {
        "error": {
            "code": "CART_OPTION_REQUIRED",
            "options": [{"optionId": 9, "optionName": "그린"}],
        }
    }
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartOptionRequired) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=1))
    assert ei.value.options[0].option_id == 9 and ei.value.options[0].name == "그린"


async def test_add_to_cart_product_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    monkeypatch.setattr(
        sc, "_client", lambda: _CartClient(_CartResp(404, {"error": {"code": "PRODUCT_NOT_FOUND"}}))
    )
    with pytest.raises(sc.CartProductNotFound):
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=999, quantity=1))


async def test_add_to_cart_stock_insufficient_parses_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 CART_STOCK_INSUFFICIENT + error.detail.availableStock → CartStockInsufficient(3)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {"error": {"code": "CART_STOCK_INSUFFICIENT", "detail": {"availableStock": 3}}}
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartStockInsufficient) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=5))
    assert ei.value.available_stock == 3


async def test_add_to_cart_stock_available_float_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """availableStock 이 BE Double 직렬화로 4.9999998 처럼 오면 round → 5(절삭 아님, 리뷰 #75)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {"error": {"code": "CART_STOCK_INSUFFICIENT", "detail": {"availableStock": 4.9999998}}}
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartStockInsufficient) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=5))
    assert ei.value.available_stock == 5


async def test_add_to_cart_stock_insufficient_missing_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """availableStock 누락 시에도 CartStockInsufficient(None) 로 전파(방어)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {"error": {"code": "CART_STOCK_INSUFFICIENT"}}
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartStockInsufficient) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=5))
    assert ei.value.available_stock is None


async def test_add_to_cart_validation_error_raises_quantity_exceeded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """400 VALIDATION_ERROR(합산 > 99) → CartQuantityExceeded(CartError 하위) + 드리프트 관측 로그."""
    import logging

    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {"error": {"code": "VALIDATION_ERROR", "message": "수량은 최대 99개까지 담을 수 있습니다."}}
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with caplog.at_level(logging.WARNING, logger="app.services.spring_client"):
        with pytest.raises(sc.CartQuantityExceeded):
            # 각 요청 수량은 <=99(클라 검증), 합산 초과는 BE가 VALIDATION_ERROR 로 낸다
            await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=5))
    # CartError 하위라 일반 캐치로도 낙성(전용 핸들러 누락 시 CART_ERROR degrade)
    assert issubclass(sc.CartQuantityExceeded, sc.CartError)
    # 드리프트 관측: BE message 를 WARN 으로 남긴다(코드가 다른 사유로 재사용될 때 감지용)
    assert "VALIDATION_ERROR" in caplog.text and "수량은 최대 99개까지" in caplog.text


async def test_get_cart_parses_items(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.spring_client as sc

    body = {
        "success": True,
        "data": {
            "items": [
                {
                    "cartItemId": 55,
                    "productId": 1,
                    "productName": "파우치",
                    "optionId": 3,
                    "optionName": "블루",
                    "quantity": 2,
                    "price": 12900,
                }
            ]
        },
    }
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(200, body)))
    view = await sc.get_cart(user_id=1)
    assert len(view.items) == 1
    assert (
        view.items[0].product_name == "파우치"
        and view.items[0].option_name == "블루"
        and view.items[0].quantity == 2
    )


# ─────────── 리뷰 수정 회귀 (Fix 1~4) ───────────


def test_parse_cart_clamps_quantity() -> None:
    """수량 상한(99) 초과 발화가 파싱 시점에 클램프된다(Fix1 — ValidationError 스트림 중단 방지)."""
    from app.agents.buyer.recommendation.decompose import _parse_cart

    assert _parse_cart({"productId": 1, "quantity": 1000}).quantity == 99
    assert _parse_cart({"productId": 1, "quantity": 0}).quantity == 1
    assert _parse_cart({"productId": 1, "quantity": 3}).quantity == 3


async def test_cart_add_rejects_out_of_context_product() -> None:
    """last_reco 밖 productId(LLM 오추출)는 담지 않고 안내 token(Fix4)."""
    store = CartStateStore()

    async def add_fn(req):
        raise AssertionError("문맥 밖 상품은 add 호출 금지")

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=777, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            allowed_product_ids={101, 102},
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert "action" not in _types(events)
    assert "어떤 상품" in next(e for e in events if e["type"] == "token")["data"]["text"]


async def test_cart_add_allows_in_context_product() -> None:
    """last_reco 안 productId 는 정상 담기(Fix4 — pending 아닌 신규)."""
    store = CartStateStore()

    async def add_fn(req):
        return AddToCartResult(success=True, cart_item_id=5)

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=101, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            allowed_product_ids={101, 102},
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert next(e for e in events if e["type"] == "action")["data"]["type"] == "CART_ADDED"


async def test_cart_add_invalid_quantity_maps_cart_error() -> None:
    """req 생성이 try 안이라 quantity 스펙 위반도 CART_ERROR 로 degrade(Fix2)."""
    store = CartStateStore()

    async def add_fn(req):
        raise AssertionError("검증 실패 시 add 미도달")

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1000),  # 클램프 우회(직접 주입)
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "CART_ERROR"


async def test_last_reco_stored_in_ranked_display_order() -> None:
    """last_reco 는 검색순서가 아니라 노출(rerank) 순서로 저장된다(Codex P1, Fix3)."""
    from app.agents.buyer.cart.state import get_cart_store
    from app.core.conversation import conversation_key
    from tests._fakes import DEFAULT_PRODUCTS, FakeLLM

    async def search(filters, exclude_product_ids=None):
        return ProductSearchResult(products=DEFAULT_PRODUCTS, total_count=len(DEFAULT_PRODUCTS))

    async def push(p):
        return True

    # rerank 가 검색순서(101,102,103)와 다르게 재정렬(103 먼저).
    llm = FakeLLM(
        rerank={
            "ranked": [{"productId": 103, "rationale": "a"}, {"productId": 101, "rationale": "b"}],
            "overallComment": "c",
        }
    )
    await _collect(
        run_buyer_turn(
            _req(message="추천", thread_id="tR"), _member(), llm=llm, search=search, push_fn=push
        )
    )
    cart_store = await get_cart_store()
    reco = await cart_store.get_last_reco(conversation_key("123", "tR"))
    # 노출 순서: rerank [103,101] + expose_min 보충 102 → [103,101,102] (검색순서 아님)
    assert [pid for pid, _ in reco][:2] == [103, 101]


# ─────────── 리뷰 라운드 2 회귀 (R1·R2) ───────────


async def test_last_reco_not_stored_when_push_fails() -> None:
    """push 실패로 카드가 노출되지 않으면 last_reco 를 저장하지 않는다(R1 — 경로 B 불변식)."""
    from app.agents.buyer.cart.state import get_cart_store
    from app.core.conversation import conversation_key
    from tests._fakes import DEFAULT_PRODUCTS, FakeLLM

    async def search(filters, exclude_product_ids=None):
        return ProductSearchResult(products=DEFAULT_PRODUCTS, total_count=len(DEFAULT_PRODUCTS))

    async def failing_push(p):
        from app.services.spring_client import SpringUnavailableError

        raise SpringUnavailableError("push down")

    await _collect(
        run_buyer_turn(
            _req(message="추천", thread_id="tNo"),
            _member(),
            llm=FakeLLM(),
            search=search,
            push_fn=failing_push,
        )
    )
    cart_store = await get_cart_store()
    reco = await cart_store.get_last_reco(conversation_key("123", "tNo"))
    assert reco == []  # 저장 안 됨 → 다음 턴 "그거 담아줘"가 미노출 상품을 담지 못함


async def test_cart_add_option_required_is_uncapped() -> None:
    """api-spec §4.1 — REQUIRED 는 상한 없는 되물음 멀티턴(INVALID 상한과 분리). 반복돼도 재질문."""
    store = CartStateStore()
    await store.set_pending(
        "m:t",
        PendingAdd(
            product_id=1, quantity=1, options=[CartOption(option_id=3, name="블루")], attempts=1
        ),
    )

    async def add_fn(req):
        raise CartOptionRequired([CartOption(option_id=3, name="블루")])

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, option_id=None, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert "action" not in _types(events)  # CART_ERROR 아님 — 계속 재질문(§4.1)
    pending = await store.get_pending("m:t")
    assert pending is not None and pending.attempts == 1  # INVALID 카운터 보존(리셋 안 함)


async def test_cart_add_reask_prefers_new_quantity() -> None:
    """옵션 답변과 함께 수량을 다시 말하면("레드로 5개") 새 수량을 우선한다(라운드5)."""
    store = CartStateStore()
    await store.set_pending(
        "m:t", PendingAdd(product_id=1, quantity=1, options=[CartOption(option_id=4, name="레드")])
    )
    captured = {}

    async def add_fn(req):
        captured["quantity"] = req.quantity
        return AddToCartResult(success=True, cart_item_id=1)

    await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, option_id=4, quantity=5),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert captured["quantity"] == 5  # pending 의 1 이 아니라 이번 턴 5


async def test_cart_add_reask_ignores_quantity_for_other_target() -> None:
    """전환이 성립 안 한(미추천 상품 언급) 턴의 수량은 옛 pending 상품에 적용하지 않는다(라운드6)."""
    store = CartStateStore()
    await store.set_pending(
        "m:t", PendingAdd(product_id=1, quantity=2, options=[CartOption(option_id=4, name="레드")])
    )
    captured = {}

    async def add_fn(req):
        captured["productId"] = req.product_id
        captured["quantity"] = req.quantity
        return AddToCartResult(success=True, cart_item_id=1)

    # cart.product_id=99(미추천 → allowed 밖, 전환 미성립), quantity=5 는 옛 상품(1)에 적용 금지.
    await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=99, option_id=4, quantity=5),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            allowed_product_ids={1, 2},
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert captured["productId"] == 1  # 옛 pending 상품
    assert captured["quantity"] == 2  # 이번 턴 5 가 아니라 pending 의 2


# ─────────── 리뷰 라운드 3 회귀 ───────────


def test_parse_cart_coerces_float_and_string() -> None:
    """LLM JSON 변형(float·숫자문자열)도 조용한 폴백 없이 int 로 해석한다."""
    from app.agents.buyer.recommendation.decompose import _parse_cart

    assert _parse_cart({"productId": 101.0, "quantity": 2.0}).product_id == 101
    assert _parse_cart({"productId": 101.0, "quantity": 2.0}).quantity == 2
    assert _parse_cart({"productId": "101", "quantity": "3"}).product_id == 101
    assert _parse_cart({"productId": "101", "quantity": "3"}).quantity == 3
    # bool 은 제외(수량 True 오해석 방지)
    assert _parse_cart({"quantity": True}).quantity == 1


async def test_cart_add_switches_product_during_pending() -> None:
    """되물음 중 다른 추천 상품으로 전환하면 pending 을 버리고 새 상품을 담는다(라운드3)."""
    store = CartStateStore()
    await store.set_pending(
        "m:t", PendingAdd(product_id=1, quantity=1, options=[CartOption(option_id=3, name="블루")])
    )
    captured = {}

    async def add_fn(req):
        captured["productId"] = req.product_id
        return AddToCartResult(success=True, cart_item_id=8)

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=2, quantity=1),  # 다른 상품으로 전환
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            allowed_product_ids={1, 2},
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    assert captured["productId"] == 2  # 옛 상품(1) 아닌 새 상품(2)
    assert next(e for e in events if e["type"] == "action")["data"]["type"] == "CART_ADDED"
    assert await store.get_pending("m:t") is None


# ─────────── 리뷰 라운드 4 회귀 ───────────


def test_cart_identity_non_numeric_sub_is_anon() -> None:
    """비숫자 sub(dev 미검증 토큰)는 익명 취급 — int 변환 실패로 죽지 않는다."""
    from app.agents.buyer.cart.graph import cart_identity

    assert cart_identity(_member()) == (123, None)
    assert cart_identity(_guest()) == (None, "guest-uuid-1")
    bad = Identity(user_id="abc", is_guest=False, seller_id=None, subject="abc")
    assert cart_identity(bad) == (None, None)


async def test_cart_add_non_numeric_member_maps_cart_error() -> None:
    """비숫자 user_id 회원은 예외로 죽지 않고 CART_ERROR 로 낙성한다."""
    store = CartStateStore()

    async def add_fn(req):
        raise AssertionError("익명 취급 → add 미도달")

    bad = Identity(user_id="abc", is_guest=False, seller_id=None, subject="abc")
    events = await _collect(
        stream_cart_add(
            identity=bad,
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="b:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    action = next(e for e in events if e["type"] == "action")["data"]
    assert action["type"] == "CART_ADD_FAILED" and action["reason"] == "CART_ERROR"


async def test_general_intent_clears_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """되물음 중 취소(general 전환)하면 stale pending 이 정리된다(라운드4)."""
    from app.agents.buyer.cart.state import PendingAdd, get_cart_store
    from app.core.conversation import conversation_key
    from tests._fakes import FakeLLM

    key = conversation_key("123", "t1")
    cart_store = await get_cart_store()
    await cart_store.set_pending(
        key, PendingAdd(product_id=1, quantity=1, options=[CartOption(option_id=3, name="블루")])
    )
    llm = FakeLLM(decompose={"intent": "general", "reply": "네, 취소할게요."})
    await _collect(run_buyer_turn(_req(message="그만할래"), _member(), llm=llm))
    assert await cart_store.get_pending(key) is None  # 정리됨


# ─────────── #18 리뷰 수정 회귀 ───────────


async def test_cart_add_reask_shows_option_surcharge() -> None:
    """되물음 문구에 옵션 추가금(extraPrice)을 표시한다(Codex #18)."""
    store = CartStateStore()

    async def add_fn(req):
        raise CartOptionRequired(
            [
                CartOption(option_id=3, name="블루", extra_price=0),
                CartOption(option_id=4, name="레드", extra_price=1000),
            ]
        )

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    token = next(e for e in events if e["type"] == "token")["data"]["text"]
    assert "레드(+1,000원)" in token and "블루" in token


async def test_add_to_cart_empty_detail_options_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detail.options 가 빈 배열이면 구버전 위치의 잔재 options 로 폴백하지 않는다(Claude #18)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {
        "error": {
            "code": "CART_OPTION_REQUIRED",
            "detail": {"options": []},
            "options": [{"optionId": 99, "name": "stale"}],
        }
    }
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartOptionRequired) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=1))
    assert ei.value.options == []  # 빈 배열 신뢰 — 99(잔재) 안 고름


async def test_add_to_cart_malformed_option_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """형식 이상 옵션 항목은 건너뛰고 정상 항목만 파싱한다(되물음 흐름 보호, Claude #18)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {
        "error": {
            "code": "CART_OPTION_REQUIRED",
            "detail": {
                "options": [
                    {"optionId": "not-int", "name": "깨짐"},  # optionId 변환 불가 → 건너뜀
                    {"optionId": 3, "name": "블루", "extraPrice": 0},  # 정상
                ]
            },
        }
    }
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartOptionRequired) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=1))
    assert [o.option_id for o in ei.value.options] == [3]  # 깨진 항목 제외, 정상만


async def test_cart_add_reask_formats_surcharge_by_sign() -> None:
    """추가금은 부호별로: 양수=+, 음수=할인(-), 0/None=미표시('(+-)' 깨짐 없이, Claude #18)."""
    store = CartStateStore()

    async def add_fn(req):
        raise CartOptionRequired(
            [
                CartOption(option_id=4, name="레드", extra_price=-1000),  # 할인
                CartOption(option_id=5, name="블랙", extra_price=0),  # 추가금 없음
                CartOption(option_id=6, name="화이트", extra_price=2000),  # 추가금
            ]
        )

    events = await _collect(
        stream_cart_add(
            identity=_member(),
            cart=CartIntent(product_id=1, quantity=1),
            cart_store=store,
            thread_key="m:t",
            settings=get_settings(),
            add_fn=add_fn,
            get_cart_fn=_empty_cart(),
        )
    )
    token = next(e for e in events if e["type"] == "token")["data"]["text"]
    assert "화이트(+2,000원)" in token  # 양수 추가금만 표시
    assert "레드" in token and "레드(" not in token  # 음수(계약 미정의) 미표시
    assert "블랙" in token and "블랙(" not in token  # 0 미표시
    assert "+-" not in token and "-1,000" not in token


def test_parse_cart_error_logs_when_all_options_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """옵션이 전부 파싱 실패하면 계약 위반 신호로 경고 로그를 남긴다(Claude #18)."""
    import logging
    import app.services.spring_client as sc

    class _R:
        status_code = 400

        def json(self):
            return {
                "error": {
                    "code": "CART_OPTION_REQUIRED",
                    "detail": {"options": [{"optionId": "bad"}]},
                }
            }

    with caplog.at_level(logging.WARNING, logger="app.services.spring_client"):
        code, options, _stock = sc._parse_cart_error(_R())
    assert options == [] and code == "CART_OPTION_REQUIRED"
    assert any("전부 파싱 실패" in r.getMessage() for r in caplog.records)


async def test_add_to_cart_bad_extra_price_keeps_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """extraPrice(표시용)가 이상해도 옵션 자체는 버리지 않는다(extra_price=None, Claude #18)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {
        "error": {
            "code": "CART_OPTION_REQUIRED",
            "detail": {
                "options": [
                    {
                        "optionId": 3,
                        "name": "블루",
                        "extraPrice": "weird",
                    },  # extraPrice 이상 → None 으로, 옵션 유지
                ]
            },
        }
    }
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartOptionRequired) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=1))
    assert [o.option_id for o in ei.value.options] == [3]
    assert ei.value.options[0].extra_price is None


async def test_add_to_cart_float_extra_price_coerced(monkeypatch: pytest.MonkeyPatch) -> None:
    """BE 가 정수 금액을 float(1500.0)로 내려도 int 로 수용한다(Claude #18)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {
        "error": {
            "code": "CART_OPTION_REQUIRED",
            "detail": {
                "options": [
                    {"optionId": 7, "name": "골드", "extraPrice": 1500.0},
                    {
                        "optionId": 8,
                        "name": "실버",
                        "extraPrice": 999.9999999998,
                    },  # BigDecimal.doubleValue 오차
                ]
            },
        }
    }
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartOptionRequired) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=1))
    assert ei.value.options[0].extra_price == 1500
    assert ei.value.options[1].extra_price == 1000  # 반올림


async def test_add_to_cart_naninf_extra_price_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    """NaN/Infinity extraPrice 여도 스트림이 죽지 않고 옵션은 유지된다(extra_price None, Claude #18)."""
    import app.services.spring_client as sc
    from app.schemas.spring import AddToCartRequest

    body = {
        "error": {
            "code": "CART_OPTION_REQUIRED",
            "detail": {
                "options": [
                    {"optionId": 9, "name": "네온", "extraPrice": float("nan")},
                    {"optionId": 10, "name": "무한", "extraPrice": float("inf")},
                    {
                        "optionId": 11,
                        "name": "초대형",
                        "extraPrice": 10**400,
                    },  # float 변환 OverflowError
                ]
            },
        }
    }
    monkeypatch.setattr(sc, "_client", lambda: _CartClient(_CartResp(400, body)))
    with pytest.raises(sc.CartOptionRequired) as ei:
        await sc.add_to_cart(AddToCartRequest(user_id=1, product_id=1, quantity=1))
    assert [o.option_id for o in ei.value.options] == [9, 10, 11]
    assert all(o.extra_price is None for o in ei.value.options)


async def test_cart_state_store_all_operations_have_query_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """buyer cart BaseStore I/O 전 구간에 pg-profile query deadline을 적용한다."""

    class _HangStore:
        async def aget(self, *args, **kwargs):
            await asyncio.sleep(10)

        async def aput(self, *args, **kwargs):
            await asyncio.sleep(10)

        async def adelete(self, *args, **kwargs):
            await asyncio.sleep(10)

    monkeypatch.setattr(get_settings(), "state_store_query_timeout_s", 0.01)
    store = CartStateStore(_HangStore())
    pending = PendingAdd(product_id=1, quantity=1)
    operations = [
        lambda: store.set_last_reco("k", [(1, "상품")]),
        lambda: store.get_last_reco("k"),
        lambda: store.set_pending("k", pending),
        lambda: store.get_pending("k"),
        lambda: store.clear_pending("k"),
    ]
    for operation in operations:
        with pytest.raises(TimeoutError):
            await operation()


async def test_last_reco_name_cache_is_bounded_lru(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.buyer.cart import state as cart_state

    monkeypatch.setattr(get_settings(), "state_store_local_cache_max_entries", 2)
    cart_state.reset_cart_store()
    store = CartStateStore()

    await store.set_last_reco("a", [(1, "A")])
    await store.set_last_reco("b", [(2, "B")])
    assert await store.get_last_reco("a") == [(1, "A")]  # a를 MRU로 승격
    await store.set_last_reco("c", [(3, "C")])

    assert await store.get_last_reco("b") == [(2, "")]  # id는 영속, 이름만 LRU miss degrade
    assert len(cart_state._last_reco_names) == 2
