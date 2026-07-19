"""구매자 추천 그래프 (이슈 #2) — 파이프라인·degrade·fallback·멀티턴·경로 B 회귀.

run_buyer_turn 을 fake LLM/검색/push 로 직접 구동한다(라이브 Anthropic·Spring 불필요).
SSE 는 상품 카드를 싣지 않는다(경로 B) — products.ready 는 {sessionId, listId} 만.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agents.buyer.graph import get_thread_store, run_buyer_turn
from app.core.auth import Identity
from app.core.conversation import conversation_key
from app.schemas.spring import ProductSearchResult
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
        run_buyer_turn(_req(), _member(), llm=FakeLLM(), search=_make_search(DEFAULT_PRODUCTS), push_fn=push)
    )
    types = _types(events)
    assert types.count("conditions") == 1
    assert types.count("products.ready") == 1
    assert types.count("done") == 1
    assert types[-1] == "done"
    assert types.index("conditions") < types.index("products.ready") < types.index("done")

    # push 된 productIds = rerank 산출(101,102) — 경로 B(표시필드 없음).
    assert len(push.pushes) == 1
    assert push.pushes[0].product_ids == [101, 102]

    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["finishReason"] == "stop"


async def test_products_ready_carries_no_cards() -> None:
    """[HARD] 경로 B — products.ready 는 상관키만, 어떤 이벤트에도 카드 필드 없음."""
    events = await _collect(
        run_buyer_turn(_req(), _member(), llm=FakeLLM(), search=_make_search(DEFAULT_PRODUCTS), push_fn=_RecordingPush())
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
        run_buyer_turn(_req(), _member(), llm=FakeLLM(), search=_failing_search, push_fn=_RecordingPush())
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
            _req(), _member(), llm=FakeLLM(rerank_error=True), search=_make_search(DEFAULT_PRODUCTS), push_fn=push
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
        run_buyer_turn(_req(), _member(), llm=FakeLLM(), search=_make_search(DEFAULT_PRODUCTS), push_fn=_failing_push)
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
        run_buyer_turn(_req(message="오늘 날씨 어때?"), _member(), llm=llm, search=_make_search(DEFAULT_PRODUCTS), push_fn=_RecordingPush())
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
    ev1 = await _collect(run_buyer_turn(_req(), _member(), llm=FakeLLM(decompose_error=True), search=_make_search(DEFAULT_PRODUCTS), push_fn=_RecordingPush()))
    assert ev1[-1]["type"] == "error" and ev1[-1]["data"]["code"] == "LLM_UNAVAILABLE"

    ev2 = await _collect(run_buyer_turn(_req(), _member(), llm=FakeLLM(decompose_error=True, timeout=True), search=_make_search(DEFAULT_PRODUCTS), push_fn=_RecordingPush()))
    assert ev2[-1]["data"]["code"] == "LLM_TIMEOUT"


# ─────────── rerank 후보 부분집합 / 멀티턴 ───────────


async def test_rerank_ids_subset_of_candidates() -> None:
    """rerank 가 후보 외 id 를 내면 코드가 제거하고 유효 id 만 push (REQ-REC-081)."""
    push = _RecordingPush()
    llm = FakeLLM(rerank={"ranked": [{"productId": 999, "rationale": "환각"}, {"productId": 101, "rationale": "ok"}], "overallComment": "c"})
    await _collect(run_buyer_turn(_req(), _member(), llm=llm, search=_make_search(DEFAULT_PRODUCTS), push_fn=push))
    assert push.pushes[0].product_ids == [101]  # 999(후보 외) 제거


async def test_multiturn_filters_persisted_and_fed_back() -> None:
    """1턴 병합 필터가 스레드 스토어(신원 스코프)에 저장되고 2턴 decompose 로 다시 주입된다."""
    llm = FakeLLM()
    ident = _member()
    await _collect(run_buyer_turn(_req(), ident, llm=llm, search=_make_search(DEFAULT_PRODUCTS), push_fn=_RecordingPush()))

    key = conversation_key("u1", "t1")
    stored = get_thread_store().get(key)
    assert stored is not None and stored.category == "무선이어폰"

    # 2턴 — decompose user 프롬프트에 직전 필터(PRIOR_FILTERS)가 실렸는지 확인.
    llm.calls.clear()
    await _collect(run_buyer_turn(_req(message="그중에 5만원 이하"), ident, llm=llm, search=_make_search(DEFAULT_PRODUCTS), push_fn=_RecordingPush()))
    decompose_calls = [u for (m, u) in llm.calls if "haiku" in m]
    assert decompose_calls and "무선이어폰" in decompose_calls[0]


async def test_thread_store_scoped_by_identity() -> None:
    """서로 다른 신원이 같은 threadId 를 써도 필터가 섞이지 않는다(IDOR 방지)."""
    a = Identity(user_id="A", is_guest=False, seller_id=None, subject="A")
    await _collect(run_buyer_turn(_req(thread_id="shared"), a, llm=FakeLLM(), search=_make_search(DEFAULT_PRODUCTS), push_fn=_RecordingPush()))
    assert get_thread_store().get(conversation_key("A", "shared")) is not None
    assert get_thread_store().get(conversation_key("B", "shared")) is None


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
