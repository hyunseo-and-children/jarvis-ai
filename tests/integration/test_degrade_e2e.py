"""Degrade 경로 E2E 스모크 (이슈 #35) — 상류 장애가 사용자 흐름을 어떻게 끊/잇는지.

SPEC-RECOMMEND-001 §7 + api-spec §3.3 의 degrade 규약을 실 HTTP 경계로 재현한다:
  - 검색 실패        → in-stream `error` SEARCH_FAILED 후 종료
  - LLM 미구성/오류  → `error` LLM_UNAVAILABLE / LLM_TIMEOUT
  - rerank 실패      → 검색 순서 폴백(스트림은 정상 완료)
  - push 실패        → `products.ready` 미emit + `done` 종료 (error 아님)
  - 이력(I-19) 실패  → dedup 없이 추천 진행
  - 담기 옵션 필요   → 되물음(멀티턴), 상품 없음 → action 실패 사유
"""

from __future__ import annotations

from tests.integration.conftest import auth_header, event_types, first_of, parse_sse

MESSAGE = "여행용 파우치 추천해줘"


def _chat(client, message: str = MESSAGE, *, thread: str = "th-deg", headers=None):
    return client.post(
        "/chat",
        json={"sessionId": "sess-deg", "threadId": thread, "message": message},
        headers=headers if headers is not None else auth_header(),
    )


def test_search_failure_emits_search_failed(client, spring, llm) -> None:
    """I-1 검색 5xx → in-stream error SEARCH_FAILED 로 종료한다 (§7)."""
    spring.fail_search = True

    events = parse_sse(_chat(client).text)
    error = first_of(events, "error")
    assert error is not None and error["code"] == "SEARCH_FAILED"
    # 후보가 없으므로 목록 push 는 하지 않는다
    assert spring.requests_to("/internal/recommendations") == []


def test_llm_unavailable_emits_error(client, spring, monkeypatch) -> None:
    """LLM 미구성(키 없음) → 네트워크 호출 없이 즉시 LLM_UNAVAILABLE (개발·CI 안전판)."""
    import app.agents.buyer.graph as buyer_graph

    monkeypatch.setattr(buyer_graph, "get_llm", lambda: None)

    error = first_of(parse_sse(_chat(client).text), "error")
    assert error is not None and error["code"] == "LLM_UNAVAILABLE"
    assert spring.requests_to("/internal/products/search") == []


def test_decompose_timeout_maps_to_llm_timeout(client, spring, monkeypatch) -> None:
    """decompose 타임아웃 → LLM_TIMEOUT (일반 실패와 구분, §2.9 c)."""
    import app.agents.buyer.graph as buyer_graph

    from tests.integration._stubs import ScriptedLLM

    monkeypatch.setattr(
        buyer_graph, "get_llm", lambda: ScriptedLLM(decompose_error=True, timeout=True)
    )

    error = first_of(parse_sse(_chat(client).text), "error")
    assert error is not None and error["code"] == "LLM_TIMEOUT"


def test_rerank_failure_falls_back_to_search_order(client, spring, monkeypatch) -> None:
    """rerank 실패해도 스트림은 완료된다 — 검색 순서 폴백(사용자 흐름 유지, §7)."""
    import app.agents.buyer.graph as buyer_graph

    from tests.integration._stubs import ScriptedLLM

    monkeypatch.setattr(buyer_graph, "get_llm", lambda: ScriptedLLM(rerank_error=True))

    events = parse_sse(_chat(client).text)
    types = event_types(events)
    assert types[-1] == "done"
    assert "error" not in types
    # 폴백이어도 경로 B 는 성립 — 검색 순서대로 push 된다
    ready = first_of(events, "products.ready")
    assert ready is not None
    assert spring.pushed_lists[ready["listId"]], "폴백 순서로라도 목록은 push 된다"


def test_push_failure_skips_products_ready_but_completes(client, spring, llm) -> None:
    """I-21 push 실패 → products.ready 미emit, 스트림은 error 가 아니라 done 으로 종료(§3.3)."""
    spring.fail_push = True

    events = parse_sse(_chat(client).text)
    types = event_types(events)
    assert "products.ready" not in types, "push 실패 시 상관키를 내보내면 FE 가 빈 목록을 조회한다"
    assert types[-1] == "done"
    assert "error" not in types


def test_purchase_history_failure_still_recommends(client, spring, llm) -> None:
    """I-19 이력 조회 실패 → dedup 없이 추천을 계속한다 (degrade, §4.7)."""
    spring.fail_purchases = True

    events = parse_sse(_chat(client, headers=auth_header("42")).text)
    types = event_types(events)
    assert types[-1] == "done"
    assert "error" not in types
    assert first_of(events, "products.ready") is not None


def test_cart_option_required_triggers_reask(client, spring, llm) -> None:
    """담기 CART_OPTION_REQUIRED → 옵션 되물음(멀티턴) — 실패 종료가 아니다 (§4.1)."""
    _chat(client)  # 추천 턴 (last_reco 적재)

    spring.fail_cart_add_code = "CART_OPTION_REQUIRED"
    spring.cart_option_payload = [
        {"optionId": 5001, "name": "L 사이즈", "extraPrice": 0},
        {"optionId": 5002, "name": "XL 사이즈", "extraPrice": 2000},
    ]
    llm._decompose = {
        "intent": "cart_add",
        "reply": "",
        "case": 2,
        "semanticQuery": "",
        "filters": {},
        "cart": {"productId": 102, "quantity": 1},
    }

    events = parse_sse(_chat(client, "그거 담아줘").text)
    text = "".join(e["data"].get("text", "") for e in events if e["type"] == "token")
    assert "사이즈" in text, "되물음 문구에 옵션이 제시돼야 한다"
    assert event_types(events)[-1] == "done"


def test_cart_product_not_found_reports_action_failure(client, spring, llm) -> None:
    """담기 404 PRODUCT_NOT_FOUND → action 으로 실패 사유를 알린다 (§4.1)."""
    _chat(client)

    spring.fail_cart_add_code = "PRODUCT_NOT_FOUND"
    llm._decompose = {
        "intent": "cart_add",
        "reply": "",
        "case": 2,
        "semanticQuery": "",
        "filters": {},
        "cart": {"productId": 102, "quantity": 1},
    }

    events = parse_sse(_chat(client, "그거 담아줘").text)
    action = first_of(events, "action")
    assert action is not None and action["type"] != "CART_ADDED"
    assert event_types(events)[-1] == "done"


def test_cart_view_degrades_when_spring_unreachable(client, spring, llm, monkeypatch) -> None:
    """장바구니 조회 실패 → 스트림은 안내 후 정상 종료(조회는 안내용, §4.9)."""
    import app.services.spring_client as sc

    from app.services.spring_client import SpringUnavailableError

    async def failing_get_cart(user_id=None, guest_id=None):
        raise SpringUnavailableError("cart down")

    monkeypatch.setattr(sc, "get_cart", failing_get_cart)
    llm._decompose = {
        "intent": "cart_view",
        "reply": "",
        "case": 2,
        "semanticQuery": "",
        "filters": {},
    }

    events = parse_sse(_chat(client, "장바구니 보여줘").text)
    assert event_types(events)[-1] == "done"
