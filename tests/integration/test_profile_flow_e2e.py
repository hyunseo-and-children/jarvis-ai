"""프로필 흐름 E2E 스모크 (이슈 #35) — 발화 누적 → session-end → 델타·consolidation → /profile/me.

api-spec §3.4(프로필 조회)·§3.5(I-20 세션 종료 통지, 멱등) + SPEC-PROFILE-001 의 2단 비동기 쓰기가
실제로 맞물리는지 확인한다. 턴 중에는 write 하지 않고(transient 격리) 세션 종료에 승격된다.
"""

from __future__ import annotations

from tests.integration.conftest import auth_header, parse_sse

USER_ID = "42"


def _chat(client, message: str, *, session: str = "sess-prof", thread: str = "th-prof"):
    return client.post(
        "/chat",
        json={"sessionId": session, "threadId": thread, "message": message},
        headers=auth_header(USER_ID),
    )


def _session_end(client, *, session: str = "sess-prof"):
    return client.post(
        "/events/session-end",
        json={"userId": int(USER_ID), "sessionId": session},
    )


def test_profile_empty_before_any_session(client, spring, llm) -> None:
    """세션 종료 전에는 프로필이 없다 — 턴 중 write 금지(transient 격리)."""
    _chat(client, "3만원 이하 여행용 파우치 추천해줘")

    body = client.get("/profile/me", headers=auth_header(USER_ID)).json()
    assert body["exists"] is False
    assert body["markdown"] is None


def test_session_end_builds_profile_visible_on_profile_me(client, spring, llm) -> None:
    """세션 종료 → 델타 추출·게이트 승격 → consolidation → GET /profile/me 에 마크다운 노출."""
    _chat(client, "3만원 이하 여행용 파우치 추천해줘")

    resp = _session_end(client)
    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"

    body = client.get("/profile/me", headers=auth_header(USER_ID)).json()
    assert body["exists"] is True
    assert "여행용품" in body["markdown"]
    assert body["userId"] == USER_ID
    # 델타(Sonnet) + consolidation(Sonnet) 각 1회 — 세션 종료에서만 LLM 을 쓴다
    assert llm.calls_of("delta") == 1
    assert llm.calls_of("consolidate") == 1


def test_session_end_is_idempotent(client, spring, llm) -> None:
    """같은 (userId, sessionId) 재전송은 중복 처리되지 않는다 (§3.5 멱등, at-least-once 대비)."""
    _chat(client, "3만원 이하 여행용 파우치 추천해줘")

    first = _session_end(client)
    second = _session_end(client)

    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert llm.calls_of("delta") == 1, "중복 통지가 LLM 을 재호출하면 안 된다"


async def test_profile_is_injected_into_next_session(client, spring, llm) -> None:
    """승격된 프로필이 다음 턴 rerank 컨텍스트로 주입된다 (개인화 루프 종단)."""
    _chat(client, "3만원 이하 여행용 파우치 추천해줘")
    _session_end(client)
    llm.calls.clear()

    resp = _chat(client, "이번엔 캐리어 추천해줘", session="sess-prof-2", thread="th-prof-2")
    assert resp.status_code == 200
    assert parse_sse(resp.text)[-1]["type"] == "done"
    # 프로필 마크다운이 rerank 프롬프트에 실렸는지는 store 조회로 확인(프롬프트 원문 비의존)
    from app.agents.profile.reader import read_profile_summary

    assert await read_profile_summary(USER_ID) is not None


async def test_remember_command_promotes_immediately(client, spring, llm) -> None:
    """ "기억해" 명시 명령은 게이트 없이 즉시 승격된다 (hot-path, 세션 종료 대기 없음)."""
    _chat(client, "나 브랜드 트래블러 좋아하니까 기억해줘")

    from app.agents.profile.store import get_profile_store

    store = await get_profile_store()
    facts = await store.get_facts(USER_ID)
    assert any("트래블러" in fact for fact in facts)


def test_guest_has_no_profile(client, spring, llm) -> None:
    """게스트는 개인화 프로필이 없다 — 정상 200 {exists:false} (오류 아님, §3.4)."""
    body = client.get("/profile/me").json()
    assert body["exists"] is False


def test_session_end_degrades_without_llm(client, spring, monkeypatch) -> None:
    """LLM 미구성이어도 세션 종료는 202 로 받는다 — best-effort degrade(§3.5, 500 금지)."""
    import app.api.events as events_api

    monkeypatch.setattr(events_api, "get_llm", lambda: None)
    resp = _session_end(client)
    assert resp.status_code == 202
