"""구매자 추천 그래프 (이슈 #2) — 파이프라인·degrade·fallback·멀티턴·경로 B 회귀.

run_buyer_turn 을 fake LLM/검색/push 로 직접 구동한다(라이브 Anthropic·Spring 불필요).
SSE 는 상품 카드를 싣지 않는다(경로 B) — products.ready 는 {sessionId, listId} 만.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.agents.buyer.graph import get_thread_store, run_buyer_turn
from app.core.auth import Identity
from app.core.config import get_settings
from app.core.conversation import conversation_key
from app.schemas.spring import ProductSearchResult, SpringProduct
from app.services.spring_client import SpringUnavailableError
from tests._fakes import DEFAULT_PRODUCTS, FakeLLM


def _req(message: str = "무선 이어폰 추천해줘", session_id: str = "s1", thread_id: str = "t1"):
    return SimpleNamespace(session_id=session_id, thread_id=thread_id, message=message)


def _member() -> Identity:
    return Identity(user_id="u1", is_guest=False, seller_id=None, subject="u1")


def _guest() -> Identity:
    return Identity(user_id=None, is_guest=True, seller_id=None, subject=None)


def _make_search(products):
    async def _search(filters, exclude_product_ids=None):
        return ProductSearchResult(products=list(products), total_count=len(products))

    return _search


async def _failing_search(filters, exclude_product_ids=None):
    raise SpringUnavailableError("spring down")


class _RecordingPush:
    def __init__(self) -> None:
        self.pushes: list = []

    async def __call__(self, push) -> bool:
        self.pushes.append(push)
        return True


async def _failing_push(push) -> bool:
    raise SpringUnavailableError("push down")


async def _collect(gen) -> list[dict]:
    events: list[dict] = []
    async for frame in gen:
        line = frame.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


def _types(events) -> list[str]:
    return [e["type"] for e in events]


# ─────────── 해피패스 파이프라인 ───────────


async def test_happy_path_pipeline() -> None:
    """decompose→search→rerank→push→products.ready→done, rerank 순서 id 를 push 한다."""
    push = _RecordingPush()
    events = await _collect(
        run_buyer_turn(
            _req(), _member(), llm=FakeLLM(), search=_make_search(DEFAULT_PRODUCTS), push_fn=push
        )
    )
    types = _types(events)
    assert types.count("conditions") == 1
    assert types.count("products.ready") == 1
    assert types.count("done") == 1
    assert types[-1] == "done"
    assert types.index("conditions") < types.index("products.ready") < types.index("done")

    # push 된 productIds — rerank 순서(101,102)가 앞, expose_min 보충으로 검색순서 103 추가.
    assert len(push.pushes) == 1
    assert push.pushes[0].product_ids[:2] == [101, 102]
    assert set(push.pushes[0].product_ids) <= {101, 102, 103}

    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["finishReason"] == "stop"


async def test_products_ready_carries_no_cards() -> None:
    """[HARD] 경로 B — products.ready 는 상관키만, 어떤 이벤트에도 카드 필드 없음."""
    events = await _collect(
        run_buyer_turn(
            _req(),
            _member(),
            llm=FakeLLM(),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_RecordingPush(),
        )
    )
    ready = next(e for e in events if e["type"] == "products.ready")["data"]
    assert set(ready.keys()) == {"sessionId", "listId"}
    assert ready["listId"]
    for ev in events:
        for banned in ("price", "rationale", "items", "productId", "name"):
            assert banned not in ev["data"]


# ─────────── degrade 3종 ───────────


async def test_search_failed_emits_error() -> None:
    """검색 실패 → error SEARCH_FAILED 로 종결(products.ready·done 없음)."""
    events = await _collect(
        run_buyer_turn(
            _req(), _member(), llm=FakeLLM(), search=_failing_search, push_fn=_RecordingPush()
        )
    )
    types = _types(events)
    assert types[-1] == "error"
    assert "products.ready" not in types
    assert "done" not in types
    err = events[-1]["data"]
    assert err["code"] == "SEARCH_FAILED"


async def test_rerank_failure_degrades_to_search_order() -> None:
    """rerank 실패 시 검색순서 상위 N 으로 degrade — products.ready 유지, done stop."""
    push = _RecordingPush()
    events = await _collect(
        run_buyer_turn(
            _req(),
            _member(),
            llm=FakeLLM(rerank_error=True),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=push,
        )
    )
    types = _types(events)
    assert "error" not in types
    assert "products.ready" in types
    assert types[-1] == "done"
    # 검색 순서(101,102,103) 상위 노출 — rerank 없이도 하드 제약(검색 반영) 유지.
    assert push.pushes[0].product_ids == [101, 102, 103]


async def test_push_failure_skips_products_ready() -> None:
    """push 실패 시 products.ready 를 emit 하지 않고 done 으로 종료(§3.3)."""
    events = await _collect(
        run_buyer_turn(
            _req(),
            _member(),
            llm=FakeLLM(),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_failing_push,
        )
    )
    types = _types(events)
    assert "products.ready" not in types
    assert types[-1] == "done"
    assert "error" not in types


# ─────────── zero-result / fallback ───────────


async def test_zero_result_done() -> None:
    """검색 0건 → zero_result done(오류 아님), products.ready 없음."""
    push = _RecordingPush()
    events = await _collect(
        run_buyer_turn(_req(), _member(), llm=FakeLLM(), search=_make_search([]), push_fn=push)
    )
    types = _types(events)
    assert "products.ready" not in types
    assert "error" not in types
    assert types[-1] == "done"
    done = events[-1]["data"]
    assert done["finishReason"] == "zero_result"
    assert push.pushes == []  # push 미호출


async def test_general_intent_uses_fallback() -> None:
    """intent=general → fallback token + done, conditions/products.ready 없음."""
    llm = FakeLLM(decompose={"intent": "general", "reply": "안녕하세요! 무엇을 도와드릴까요?"})
    events = await _collect(
        run_buyer_turn(
            _req(message="오늘 날씨 어때?"),
            _member(),
            llm=llm,
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_RecordingPush(),
        )
    )
    types = _types(events)
    assert "conditions" not in types
    assert "products.ready" not in types
    assert types[-1] == "done"
    token = next(e for e in events if e["type"] == "token")["data"]
    assert "안녕하세요" in token["text"]


# ─────────── LLM 미구성 / decompose 실패 ───────────


async def test_llm_unavailable_when_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 미구성(키 없음)이면 네트워크 없이 즉시 LLM_UNAVAILABLE error."""
    import app.agents.buyer.graph as bg

    monkeypatch.setattr(bg, "get_llm", lambda: None)
    events = await _collect(run_buyer_turn(_req(), _member()))
    assert _types(events) == ["error"]
    assert events[0]["data"]["code"] == "LLM_UNAVAILABLE"


async def test_decompose_error_maps_to_llm_code() -> None:
    """decompose 실패는 LLM_UNAVAILABLE, 타임아웃 메시지는 LLM_TIMEOUT 로 매핑."""
    ev1 = await _collect(
        run_buyer_turn(
            _req(),
            _member(),
            llm=FakeLLM(decompose_error=True),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_RecordingPush(),
        )
    )
    assert ev1[-1]["type"] == "error" and ev1[-1]["data"]["code"] == "LLM_UNAVAILABLE"

    ev2 = await _collect(
        run_buyer_turn(
            _req(),
            _member(),
            llm=FakeLLM(decompose_error=True, timeout=True),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_RecordingPush(),
        )
    )
    assert ev2[-1]["data"]["code"] == "LLM_TIMEOUT"


# ─────────── rerank 후보 부분집합 / 멀티턴 ───────────


async def test_rerank_ids_subset_of_candidates() -> None:
    """rerank 가 후보 외 id 를 내면 코드가 제거하고 유효 id 만 push (REQ-REC-081)."""
    push = _RecordingPush()
    llm = FakeLLM(
        rerank={
            "ranked": [
                {"productId": 999, "rationale": "환각"},
                {"productId": 101, "rationale": "ok"},
            ],
            "overallComment": "c",
        }
    )
    await _collect(
        run_buyer_turn(
            _req(), _member(), llm=llm, search=_make_search(DEFAULT_PRODUCTS), push_fn=push
        )
    )
    ids = push.pushes[0].product_ids
    assert 999 not in ids  # 후보 외 id 제거(REQ-REC-081)
    assert ids[0] == 101  # rerank 유효 산출이 선두, 나머지는 expose_min 보충


async def test_multiturn_filters_persisted_and_fed_back() -> None:
    """1턴 병합 필터가 스레드 스토어(신원 스코프)에 저장되고 2턴 decompose 로 다시 주입된다."""
    llm = FakeLLM()
    ident = _member()
    await _collect(
        run_buyer_turn(
            _req(), ident, llm=llm, search=_make_search(DEFAULT_PRODUCTS), push_fn=_RecordingPush()
        )
    )

    key = conversation_key("u1", "t1")
    thread_store = await get_thread_store()
    stored = await thread_store.get(key)
    assert stored is not None and stored.category == "무선이어폰"

    # 2턴 — decompose user 프롬프트에 직전 필터(PRIOR_FILTERS)가 실렸는지 확인.
    llm.calls.clear()
    await _collect(
        run_buyer_turn(
            _req(message="그중에 5만원 이하"),
            ident,
            llm=llm,
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_RecordingPush(),
        )
    )
    decompose_calls = [u for (m, u) in llm.calls if "haiku" in m]
    assert decompose_calls and "무선이어폰" in decompose_calls[0]


async def test_thread_store_scoped_by_identity() -> None:
    """서로 다른 신원이 같은 threadId 를 써도 필터가 섞이지 않는다(IDOR 방지)."""
    a = Identity(user_id="A", is_guest=False, seller_id=None, subject="A")
    await _collect(
        run_buyer_turn(
            _req(thread_id="shared"),
            a,
            llm=FakeLLM(),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_RecordingPush(),
        )
    )
    thread_store = await get_thread_store()
    assert await thread_store.get(conversation_key("A", "shared")) is not None
    assert await thread_store.get(conversation_key("B", "shared")) is None


# ─────────── 검색 사후필터 (search_service) ───────────


async def test_search_catalog_post_filters_exclude_and_rating() -> None:
    """BE I-1 엔 dedup·평점 파라미터 없음 → search_catalog 가 사후 제외한다(C-15)."""
    from app.schemas.spring import ProductSearchFilters
    from app.services.search_service import search_catalog
    from tests._fakes import FakeBackend

    # 101(4.5)·102(4.2)·103(3.9) 중 exclude 101 + rating_min 4.0 → 102 만.
    res = await search_catalog(
        ProductSearchFilters(rating_min=4.0), exclude_product_ids=[101], backend=FakeBackend()
    )
    assert [p.product_id for p in res.products] == [102]


# ─────────── 리뷰 수정 회귀 (Fix A~E) ───────────


class _FakeResp:
    def __init__(self, data) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, data) -> None:
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url, params=None):
        return _FakeResp(self._data)


def test_spring_product_maps_i1_wire_fields() -> None:
    """SpringProduct 가 BE I-1 응답 필드명(categoryName/brandName/originalPrice/imageUrl)을 매핑한다."""
    from app.schemas.spring import SpringProduct

    p = SpringProduct.model_validate(
        {
            "productId": 1,
            "name": "린넨 셔츠",
            "price": 29900,
            "originalPrice": 39900,
            "categoryName": "여성의류",
            "brandName": "더센트",
            "imageUrl": "https://x/1.jpg",
            "rating": 4.8,
        }
    )
    assert p.product_id == 1
    assert p.category == "여성의류"  # categoryName → category (None 유실 방지)
    assert p.brand == "더센트"
    assert p.list_price == 39900
    assert p.main_image == "https://x/1.jpg"


async def test_search_products_parses_i1_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_products 가 {success,data:{items}} 응답을 SpringProduct 로 파싱한다(§4.6)."""
    import app.services.spring_client as sc
    from app.schemas.spring import ProductSearchFilters

    payload = {
        "success": True,
        "data": {
            "items": [
                {
                    "productId": 1,
                    "name": "셔츠",
                    "price": 29900,
                    "categoryName": "의류",
                    "brandName": "B",
                    "rating": 4.8,
                }
            ]
        },
    }
    monkeypatch.setattr(sc, "_client", lambda: _FakeClient(payload))
    res = await sc.search_products(ProductSearchFilters())
    assert len(res.products) == 1
    assert res.products[0].category == "의류" and res.products[0].brand == "B"


async def test_search_products_malformed_maps_to_search_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 이지만 스키마 불일치(필수 price 결측) 응답은 SpringUnavailableError 로 degrade(§7)."""
    import app.services.spring_client as sc
    from app.schemas.spring import ProductSearchFilters

    payload = {"success": True, "data": {"items": [{"productId": 1, "name": "x"}]}}  # price 없음
    monkeypatch.setattr(sc, "_client", lambda: _FakeClient(payload))
    with pytest.raises(SpringUnavailableError):
        await sc.search_products(ProductSearchFilters())


async def test_expose_min_fill_from_search_order() -> None:
    """rerank 가 expose_min 미만을 내면 검색순서로 보충한다(REQ-REC-021 5~8개)."""
    products = [
        SpringProduct(
            product_id=pid, name=f"P{pid}", price=1000 * pid, rating=4.0, category="c", brand="b"
        )
        for pid in range(201, 207)  # 6개 후보
    ]
    push = _RecordingPush()
    llm = FakeLLM(
        rerank={"ranked": [{"productId": 201, "rationale": "top"}], "overallComment": "c"}
    )
    await _collect(
        run_buyer_turn(_req(), _member(), llm=llm, search=_make_search(products), push_fn=push)
    )
    ids = push.pushes[0].product_ids
    assert ids[0] == 201  # rerank 선두 유지
    assert len(ids) == 5  # expose_min 까지 검색순서로 보충


async def test_push_failure_emits_notice_token() -> None:
    """push 실패 시 목록 지연 안내 token 을 낸다(경로 B 실패 계약, error 아님)."""
    events = await _collect(
        run_buyer_turn(
            _req(),
            _member(),
            llm=FakeLLM(),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_failing_push,
        )
    )
    texts = " ".join(e["data"].get("text", "") for e in events if e["type"] == "token")
    assert "잠시 후" in texts or "문제" in texts
    assert _types(events)[-1] == "done"


# ─────────── 구매 이력 dedup (#4, §4.7 결정 14-F) ───────────

import app.services.spring_client as _sc_mod  # noqa: E402
from app.schemas.spring import OrderHistory, OrderHistoryItem, RecentPurchases  # noqa: E402

_REAL_GET_RECENT = _sc_mod.get_recent_purchases  # autouse 패치 전에 캡처(배선 테스트용)


def _guest() -> Identity:
    return Identity(user_id=None, is_guest=True, seller_id=None, subject="guest-1")


def _member_num() -> Identity:
    """숫자 sub 회원(실제 JWT sub 는 숫자 BIGINT, §2.6) — dedup 경로 검증용."""
    return Identity(user_id="123", is_guest=False, seller_id=None, subject="123")


def _recording_search(products, sink):
    async def _s(filters, exclude_product_ids=None):
        sink["exclude"] = exclude_product_ids
        return ProductSearchResult(products=list(products), total_count=len(products))

    return _s


def _purchases(*product_ids):
    async def _fn(user_id, status=None):
        return RecentPurchases(
            orders=[
                OrderHistory(
                    order_id=1,
                    ordered_at="2026-07-10T00:00:00",
                    items=[
                        OrderHistoryItem(order_item_id=i, product_id=pid)
                        for i, pid in enumerate(product_ids, 1)
                    ],
                )
            ]
        )

    return _fn


def _fix_now(monkeypatch, when=datetime(2026, 7, 19)):
    monkeypatch.setattr("app.agents.buyer.recommendation.graph._now", lambda: when)


async def test_recommendation_dedups_recent_purchases(monkeypatch: pytest.MonkeyPatch) -> None:
    """회원 최근 구매 productId 는 그래프 사후필터로 후보에서 제외된다(exact 제외, 결정 14-F).

    병렬화로 검색엔 exclude 를 넘기지 않고(그래프에서 제외), 최종 push 에 101 이 빠진다.
    """
    _fix_now(monkeypatch)
    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _purchases(101))
    push = _RecordingPush()
    sink: dict = {}
    await _collect(
        run_buyer_turn(
            _req(),
            _member_num(),
            llm=FakeLLM(),
            search=_recording_search(DEFAULT_PRODUCTS, sink),
            push_fn=push,
        )
    )
    assert sink["exclude"] is None  # 검색엔 exclude 미전달(병렬 — 제외는 그래프 사후필터)
    assert 101 not in push.pushes[0].product_ids  # 최근 구매 101 제외
    assert 102 in push.pushes[0].product_ids


async def test_recommendation_skips_dedup_for_guest(monkeypatch: pytest.MonkeyPatch) -> None:
    """게스트는 이력 조회를 스킵하고 제외 없이 추천한다(결정 8)."""
    called = {"n": 0}

    async def _spy(user_id, status=None):
        called["n"] += 1
        return RecentPurchases()

    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _spy)
    push = _RecordingPush()
    await _collect(
        run_buyer_turn(
            _req(), _guest(), llm=FakeLLM(), search=_make_search(DEFAULT_PRODUCTS), push_fn=push
        )
    )
    assert called["n"] == 0  # 조회 스킵
    assert 101 in push.pushes[0].product_ids  # 제외 안 됨


async def test_recommendation_degrades_when_purchases_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """이력 조회 실패 시 dedup 없이 추천을 정상 진행한다(degrade, §4.7)."""

    async def _boom(user_id, status=None):
        raise SpringUnavailableError("orders down")

    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _boom)
    push = _RecordingPush()
    events = await _collect(
        run_buyer_turn(
            _req(),
            _member_num(),
            llm=FakeLLM(),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=push,
        )
    )
    assert 101 in push.pushes[0].product_ids  # dedup 없이 진행
    assert _types(events)[-1] == "done"


async def test_recommendation_degrades_on_non_numeric_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """비숫자 sub 회원은 dedup 없이 진행(int 변환 실패로 죽지 않음)."""
    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _purchases(101))
    bad = Identity(user_id="abc", is_guest=False, seller_id=None, subject="abc")
    push = _RecordingPush()
    await _collect(
        run_buyer_turn(
            _req(), bad, llm=FakeLLM(), search=_make_search(DEFAULT_PRODUCTS), push_fn=push
        )
    )
    assert 101 in push.pushes[0].product_ids  # dedup 스킵


async def test_recommendation_search_and_purchases_run_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """검색과 이력조회를 병렬 실행한다 — 검색 호출에 exclude 를 넘기지 않는다(§4.7 지연 가드)."""
    _fix_now(monkeypatch)
    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _purchases(101))
    sink: dict = {}
    await _collect(
        run_buyer_turn(
            _req(),
            _member_num(),
            llm=FakeLLM(),
            search=_recording_search(DEFAULT_PRODUCTS, sink),
            push_fn=_RecordingPush(),
        )
    )
    assert sink["exclude"] is None


def test_purchased_product_ids_excludes_canceled_returned() -> None:
    """취소/반품 아이템은 보유분이 아니라 제외 대상에서 뺀다(Claude #19)."""
    rp = RecentPurchases(
        orders=[
            OrderHistory(
                order_id=1,
                ordered_at="2026-07-10T00:00:00",
                items=[
                    OrderHistoryItem(order_item_id=1, product_id=101, status="DELIVERED"),
                    OrderHistoryItem(order_item_id=2, product_id=102, status="CANCELED"),
                    OrderHistoryItem(order_item_id=3, product_id=103, status="CANCELLED"),
                    OrderHistoryItem(order_item_id=4, product_id=104, status="RETURNED"),
                ],
            )
        ]
    )
    # 철자 양쪽(CANCELED/CANCELLED) 모두 제외
    assert rp.purchased_product_ids(exclude_statuses={"CANCELED", "CANCELLED", "RETURNED"}) == {101}


def test_purchased_product_ids_window_excludes_old() -> None:
    """윈도우(since)보다 오래된 구매는 제외 목록에서 뺀다 — 영구 제외 방지(Codex #19)."""
    rp = RecentPurchases(
        orders=[
            OrderHistory(
                order_id=1,
                ordered_at="2026-07-15T00:00:00",
                items=[OrderHistoryItem(order_item_id=1, product_id=101)],
            ),
            OrderHistory(
                order_id=2,
                ordered_at="2025-01-01T00:00:00",
                items=[OrderHistoryItem(order_item_id=2, product_id=102)],
            ),
            OrderHistory(
                order_id=3,
                ordered_at="bad-date",
                items=[OrderHistoryItem(order_item_id=3, product_id=103)],
            ),
        ]
    )
    assert rp.purchased_product_ids(since=datetime(2026, 7, 1)) == {101}  # 오래된 102·불명 103 제외
    assert rp.purchased_product_ids() == {101, 102, 103}  # since 없으면 전체(불명 포함)


async def test_get_recent_purchases_parses_and_collects_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-19 응답을 파싱하고 productId 집합을 모은다(§4.7)."""
    body = {
        "success": True,
        "data": {
            "orders": [
                {
                    "orderId": 1023,
                    "orderedAt": "2026-07-10T14:23:00",
                    "status": "DELIVERED",
                    "items": [
                        {
                            "orderItemId": 2001,
                            "productId": 552,
                            "productName": "무선 키보드",
                            "quantity": 1,
                            "price": 29000,
                            "status": "DELIVERED",
                        }
                    ],
                },
                {
                    "orderId": 1024,
                    "orderedAt": "2026-07-11T09:00:00",
                    "status": "SHIPPING",
                    "items": [
                        {"orderItemId": 2002, "productId": 88},
                        {"orderItemId": 2003, "productId": 552},
                    ],
                },
            ]
        },
    }
    monkeypatch.setattr(_sc_mod, "_client", lambda: _FakeClient(body))
    res = await _REAL_GET_RECENT(123)
    assert res.purchased_product_ids() == {552, 88}


async def test_get_recent_purchases_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """스키마 불일치(필수 productId 결측)는 SpringUnavailableError 로(호출측 degrade)."""
    body = {
        "success": True,
        "data": {"orders": [{"orderId": 1, "orderedAt": "x", "items": [{"orderItemId": 1}]}]},
    }
    monkeypatch.setattr(_sc_mod, "_client", lambda: _FakeClient(body))
    with pytest.raises(SpringUnavailableError):
        await _REAL_GET_RECENT(1)


# ─────────── #19 리뷰 2차 회귀 ───────────


def test_parse_ordered_at_normalizes_tz() -> None:
    """aware ordered_at 은 UTC 로 변환 후 naive 화(offset 만 버리지 않음, Claude #19)."""
    from app.schemas.spring import _parse_ordered_at

    # 09:00+09:00 == 00:00 UTC
    assert _parse_ordered_at("2026-07-10T09:00:00+09:00") == datetime(2026, 7, 10, 0, 0, 0)
    assert _parse_ordered_at("2026-07-10T00:00:00") == datetime(
        2026, 7, 10, 0, 0, 0
    )  # naive 그대로
    assert _parse_ordered_at("bad") is None


async def test_recommendation_dedup_empty_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """dedup 로 후보가 전부 제외되면 '조건 바꿔라'가 아니라 원인을 바르게 안내한다(Claude #19)."""
    _fix_now(monkeypatch)
    monkeypatch.setattr(
        _sc_mod, "get_recent_purchases", _purchases(101, 102, 103)
    )  # DEFAULT 전부 제외
    events = await _collect(
        run_buyer_turn(
            _req(),
            _member_num(),
            llm=FakeLLM(),
            search=_make_search(DEFAULT_PRODUCTS),
            push_fn=_RecordingPush(),
        )
    )
    token = next(e for e in events if e["type"] == "token")["data"]["text"]
    assert "최근에 구매" in token
    assert "products.ready" not in _types(events)
    assert events[-1]["data"]["finishReason"] == "zero_result"


async def test_recommendation_skips_dedup_for_seller(monkeypatch: pytest.MonkeyPatch) -> None:
    """판매자 토큰(user_id=sub·seller_id=sub)은 sub 를 memberId 로 쓰지 않는다(IDOR 방지, Claude #19)."""
    called = {"n": 0}

    async def _spy(user_id, status=None):
        called["n"] += 1
        return RecentPurchases()

    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _spy)
    seller = Identity(user_id="500", is_guest=False, seller_id="500", subject="500")
    push = _RecordingPush()
    await _collect(
        run_buyer_turn(
            _req(), seller, llm=FakeLLM(), search=_make_search(DEFAULT_PRODUCTS), push_fn=push
        )
    )
    assert called["n"] == 0  # 판매자 sub 로 I-19 조회 안 함
    assert 101 in push.pushes[0].product_ids  # dedup 미적용


# ─────────── 소모품 카테고리 억제 + 되돌리기 (#4, 결정 14-F) ───────────


def _purchases_cat(*items):
    """items = (productId, category, name) — 카테고리 포함 최근 구매."""

    async def _fn(user_id, status=None):
        return RecentPurchases(
            orders=[
                OrderHistory(
                    order_id=1,
                    ordered_at="2026-07-15T00:00:00",
                    items=[
                        OrderHistoryItem(
                            order_item_id=idx, product_id=pid, category=cat, product_name=name
                        )
                        for idx, (pid, cat, name) in enumerate(items, 1)
                    ],
                )
            ]
        )

    return _fn


def _prod(pid, cat, name="상품"):
    return SpringProduct(
        product_id=pid, name=name, price=10000, rating=4.0, category=cat, brand="b"
    )


async def test_recommendation_suppresses_consumable_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """최근 구매한 소모품 카테고리는 후보에서 억제되고 되돌리기 칩이 나온다(결정 14-F)."""
    _fix_now(monkeypatch)
    monkeypatch.setattr(get_settings(), "consumable_categories", ["조미료"])
    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _purchases_cat((900, "조미료", "소금")))
    products = [_prod(201, "조미료", "후추"), _prod(202, "무선이어폰", "이어폰")]
    push = _RecordingPush()
    events = await _collect(
        run_buyer_turn(
            _req(), _member_num(), llm=FakeLLM(), search=_make_search(products), push_fn=push
        )
    )
    assert 201 not in push.pushes[0].product_ids  # 조미료 억제
    assert 202 in push.pushes[0].product_ids
    sug = next(e for e in events if e["type"] == "suggestions")["data"]
    assert sug["chips"][0]["revert"]["category"] == "조미료"
    assert sug["chips"][0]["estCount"] == 1
    assert "소금" in sug["chips"][0]["label"]


async def test_recommendation_nonconsumable_not_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    """비소모품 카테고리는 억제하지 않는다(exact 제외만) — 되돌리기 칩 없음."""
    _fix_now(monkeypatch)
    monkeypatch.setattr(get_settings(), "consumable_categories", ["조미료"])
    monkeypatch.setattr(
        _sc_mod, "get_recent_purchases", _purchases_cat((202, "무선이어폰", "이어폰"))
    )
    products = [_prod(201, "조미료", "후추"), _prod(202, "무선이어폰", "이어폰")]
    push = _RecordingPush()
    events = await _collect(
        run_buyer_turn(
            _req(), _member_num(), llm=FakeLLM(), search=_make_search(products), push_fn=push
        )
    )
    assert 202 not in push.pushes[0].product_ids  # exact 제외(구매한 productId)
    assert 201 in push.pushes[0].product_ids  # 조미료지만 구매 안 함 → 유지
    assert "suggestions" not in _types(events)  # 억제 카테고리 없음 → 칩 없음


async def test_recommendation_no_consumable_config_no_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """consumable_categories 미설정(기본 [])이면 카테고리 억제·칩 없음."""
    _fix_now(monkeypatch)
    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _purchases_cat((900, "조미료", "소금")))
    products = [_prod(201, "조미료", "후추")]
    push = _RecordingPush()
    events = await _collect(
        run_buyer_turn(
            _req(), _member_num(), llm=FakeLLM(), search=_make_search(products), push_fn=push
        )
    )
    assert 201 in push.pushes[0].product_ids
    assert "suggestions" not in _types(events)


async def test_recommendation_revert_unsuppresses_category(monkeypatch: pytest.MonkeyPatch) -> None:
    """되돌리기(revertCategories)하면 다음 턴부터 그 카테고리를 억제하지 않는다(멀티턴 지속)."""
    _fix_now(monkeypatch)
    monkeypatch.setattr(get_settings(), "consumable_categories", ["조미료"])
    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _purchases_cat((900, "조미료", "소금")))
    products = [_prod(201, "조미료", "후추"), _prod(202, "무선이어폰", "이어폰")]
    # 턴 1: 조미료 억제
    push1 = _RecordingPush()
    await _collect(
        run_buyer_turn(
            _req(thread_id="tR"),
            _member_num(),
            llm=FakeLLM(),
            search=_make_search(products),
            push_fn=push1,
        )
    )
    assert 201 not in push1.pushes[0].product_ids
    # 턴 2: 사용자 되돌리기
    push2 = _RecordingPush()
    llm2 = FakeLLM(
        decompose={"intent": "recommend", "revertCategories": ["조미료"], "filters": {}, "case": 2}
    )
    events2 = await _collect(
        run_buyer_turn(
            _req(thread_id="tR"),
            _member_num(),
            llm=llm2,
            search=_make_search(products),
            push_fn=push2,
        )
    )
    assert 201 in push2.pushes[0].product_ids  # 조미료 복원
    assert "suggestions" not in _types(events2)  # 더는 억제 안 함 → 칩 없음


async def test_recommendation_all_suppressed_offers_revert(monkeypatch: pytest.MonkeyPatch) -> None:
    """후보가 전부 소모품 억제로 비어도 되돌리기 칩은 제공한다(복원 가능)."""
    _fix_now(monkeypatch)
    monkeypatch.setattr(get_settings(), "consumable_categories", ["조미료"])
    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _purchases_cat((900, "조미료", "소금")))
    products = [_prod(201, "조미료", "후추")]
    events = await _collect(
        run_buyer_turn(
            _req(),
            _member_num(),
            llm=FakeLLM(),
            search=_make_search(products),
            push_fn=_RecordingPush(),
        )
    )
    assert "products.ready" not in _types(events)
    assert events[-1]["data"]["finishReason"] == "zero_result"
    token = next(e for e in events if e["type"] == "token")["data"]["text"]
    assert "가렸" in token and "구매하신 것들" not in token  # 카테고리 억제 문구(exact 문구 아님)
    sug = next(e for e in events if e["type"] == "suggestions")["data"]
    assert sug["chips"][0]["revert"]["category"] == "조미료"


async def test_recommendation_guest_no_suppression(monkeypatch: pytest.MonkeyPatch) -> None:
    """게스트는 이력 조회 스킵 → 카테고리 억제·칩 없음."""
    monkeypatch.setattr(get_settings(), "consumable_categories", ["조미료"])
    products = [_prod(201, "조미료", "후추")]
    push = _RecordingPush()
    events = await _collect(
        run_buyer_turn(_req(), _guest(), llm=FakeLLM(), search=_make_search(products), push_fn=push)
    )
    assert 201 in push.pushes[0].product_ids
    assert "suggestions" not in _types(events)


def test_order_item_category_and_recent_items() -> None:
    """I-19 categoryName 파싱 + recent_items 윈도우/상태 필터."""
    from app.schemas.spring import RecentPurchases

    rp = RecentPurchases.model_validate(
        {
            "orders": [
                {
                    "orderId": 1,
                    "orderedAt": "2026-07-15T00:00:00",
                    "items": [
                        {
                            "orderItemId": 1,
                            "productId": 5,
                            "categoryName": "조미료",
                            "status": "DELIVERED",
                        },
                        {
                            "orderItemId": 2,
                            "productId": 6,
                            "categoryName": "조미료",
                            "status": "CANCELED",
                        },
                    ],
                },
            ]
        }
    )
    items = rp.recent_items(exclude_statuses={"CANCELED"})
    assert [i.product_id for i in items] == [5]
    assert items[0].category == "조미료"


async def test_recommendation_revert_ignores_non_consumable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """소모품 화이트리스트 밖 revert 문자열은 무시(무한 누적·임의 문자열 방지, Claude)."""
    from app.agents.buyer.recommendation.state import get_revert_store
    from app.core.conversation import conversation_key

    _fix_now(monkeypatch)
    monkeypatch.setattr(get_settings(), "consumable_categories", ["조미료"])
    monkeypatch.setattr(_sc_mod, "get_recent_purchases", _purchases_cat((900, "조미료", "소금")))
    products = [_prod(201, "조미료", "후추")]
    llm = FakeLLM(
        decompose={
            "intent": "recommend",
            "revertCategories": ["해킹", "무선이어폰"],
            "filters": {},
            "case": 2,
        }
    )
    await _collect(
        run_buyer_turn(
            _req(thread_id="tN"),
            _member_num(),
            llm=llm,
            search=_make_search(products),
            push_fn=_RecordingPush(),
        )
    )
    # 화이트리스트 밖이라 저장 안 됨 → 조미료 억제 유지(되돌려지지 않음)
    revert_store = await get_revert_store()
    assert await revert_store.get(conversation_key("123", "tN")) == set()


def test_suggestion_chip_requires_exactly_one_kind() -> None:
    """SuggestionChip 은 revert/relaxation 중 정확히 하나여야 한다(§3.1)."""
    import pytest as _pytest
    from app.schemas.chat import RelaxationRef, RevertRef, SuggestionChip

    SuggestionChip(label="ok", revert=RevertRef(category="조미료"), est_count=1)  # 유효
    SuggestionChip(
        label="ok", relaxation=RelaxationRef(field="priceMax", value=1), est_count=1
    )  # 유효
    with _pytest.raises(ValueError):
        SuggestionChip(label="none", est_count=1)  # 둘 다 없음
    with _pytest.raises(ValueError):
        SuggestionChip(
            label="both",
            revert=RevertRef(category="x"),
            relaxation=RelaxationRef(field="f", value=1),
            est_count=1,
        )
