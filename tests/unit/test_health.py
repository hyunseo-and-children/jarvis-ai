"""헬스 체크 + /chat SSE 스텁 스모크 테스트 (dev 인증 모드).

스캐폴드가 부팅하고 CH-2 명명 계약대로 스트리밍하는지 검증한다:
  - GET /health == 200
  - POST /chat 가 text/event-stream 을 스트리밍하고 done 이벤트로 종료
  - SSE 이벤트명·필드가 api-spec v0.4.0 §3.1 과 일치 (camelCase, 6-event 세트)
  - [HARD] SSE 는 상품 카드를 싣지 않는다 (경로 B): products.ready 는 {sessionId, listId} 상관키만
  - MVP 표면: /profile/me, /events/* 는 404
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    """GET /health 는 200 과 status=ok 를 반환한다."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def _parse_sse(body: str) -> list[dict]:
    """SSE 본문에서 `data:` 라인의 JSON 이벤트를 순서대로 파싱한다."""
    events: list[dict] = []
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def test_chat_streams_sse_ending_with_done(buyer_fakes) -> None:
    """POST /chat 는 실 buyer 그래프를 SSE 로 스트리밍하고 done 으로 끝난다 (fake LLM/검색/push)."""
    resp = client.post(
        "/chat",
        json={"sessionId": "sess-1", "threadId": "thread-1", "message": "무선 이어폰 추천해줘"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]

    # 순서·유일성 계약 (api-spec §3.1, 경로 B): conditions·products.ready 각 1회, done 마지막.
    assert types.count("conditions") == 1
    assert types.count("products.ready") == 1
    assert types.count("done") == 1
    assert types[-1] == "done"
    assert types.index("conditions") < types.index("products.ready") < types.index("done")

    # [HARD] 스트림 어디에도 상품 카드가 없다 (경로 B) — 카드 필드 부재.
    assert "products" not in types  # 구 카드 이벤트명 폐기
    for ev in events:
        data = ev["data"]
        assert "price" not in data
        assert "rationale" not in data
        assert "items" not in data  # 카드 목록 없음

    # products.ready 는 상관관계 키만 (camelCase): sessionId + 비어있지 않은 listId.
    ready = next(e for e in events if e["type"] == "products.ready")["data"]
    assert set(ready.keys()) == {"sessionId", "listId"}
    assert ready["sessionId"] == "sess-1"
    assert ready["listId"]

    # conditions 는 chips 배열, 카테고리 칩이 먼저.
    conditions = next(e for e in events if e["type"] == "conditions")["data"]
    assert isinstance(conditions["chips"], list)
    assert conditions["chips"][0]["field"] == "category"

    # done.finishReason == "stop" (camelCase).
    done = next(e for e in events if e["type"] == "done")["data"]
    assert done["finishReason"] == "stop"


def test_seller_chat_requires_seller_scope() -> None:
    """판매자 스코프 없는 토큰(dev 게스트)의 /seller/chat 은 403 이다 (api-spec §3.2)."""
    resp = client.post(
        "/seller/chat",
        json={"sessionId": "sess-1", "threadId": "thread-1", "message": "이번 주 매출 어때?"},
    )
    assert resp.status_code == 403


def test_profile_me_is_post_mvp_404() -> None:
    """GET /profile/me 는 고도화(post-MVP)로 미등록 → 404 (MVP 표면 축소)."""
    resp = client.get("/profile/me")
    assert resp.status_code == 404


def test_events_catalog_is_post_mvp_404() -> None:
    """POST /events/catalog 는 고도화(post-MVP)로 미등록 → 404 (MVP 표면 축소)."""
    resp = client.post(
        "/events/catalog",
        json={"eventId": "evt-1", "changeType": "priceStock", "productId": "P-1"},
    )
    assert resp.status_code == 404


def test_openapi_surface_is_exactly_mvp() -> None:
    """OpenAPI 표면이 정확히 /chat, /seller/chat, /health 인지 확인."""
    paths = set(app.openapi()["paths"].keys())
    assert paths == {"/chat", "/seller/chat", "/health"}
