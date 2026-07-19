"""프로필 파이프라인 (이슈 #6, SPEC-PROFILE-001) — reader·gate·builder·엔드포인트·멱등·transient.

LLM(델타·consolidation)은 주입형 fake 로 구동(라이브 Anthropic 불필요). GET /profile/me·
POST /events/session-end 는 TestClient. 저장소는 인메모리 placeholder(conftest reset).
"""

from __future__ import annotations

import json

import jwt
import pytest
from fastapi.testclient import TestClient

from app.agents.profile.builder import consolidate, generate_session_delta, record_remember
from app.agents.profile.gate import is_remember_command, should_promote
from app.agents.profile.reader import read_profile_summary
from app.agents.profile.store import get_profile_store
from app.core.config import get_settings
from app.core.conversation import conversation_key
from app.main import app

client = TestClient(app)


def _member_bearer(sub: str) -> dict:
    token = jwt.encode({"sub": sub}, "test-secret-key-0123456789abcdef", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


class _ProfileLLM:
    """델타 추출(JSON)·consolidation(마크다운)을 system 프롬프트로 분기하는 fake."""

    def __init__(self, deltas=None, summary="# 취향 요약\n- 3~5만원 무선이어폰 선호") -> None:
        self._deltas = deltas if deltas is not None else [
            {"fact": "3~5만원 무선이어폰 선호", "salience": 0.9, "explicit": True, "repetitionEma": 0.0}
        ]
        self._summary = summary

    async def complete(self, *, system, user, model, max_tokens=1024):
        if "델타 추출기" in system:
            return json.dumps({"deltas": self._deltas}, ensure_ascii=False)
        return self._summary

    async def stream(self, *, system, user, model, max_tokens=1024):
        yield "x"


# ─────────── reader ───────────


def test_reader_none_for_guest_and_missing() -> None:
    assert read_profile_summary(None) is None
    assert read_profile_summary("") is None
    assert read_profile_summary("999") is None  # 미보유


def test_reader_returns_stored_summary() -> None:
    get_profile_store().set_summary("5", "# 요약\n- x", "2026-07-20T00:00:00+00:00")
    got = read_profile_summary("5")
    assert got and got["markdown"] == "# 요약\n- x" and got["generated_at"].startswith("2026")


# ─────────── gate ───────────


def test_should_promote_gate_rule() -> None:
    # salient AND (explicit OR repeated)
    assert should_promote(salience=0.8, explicit=True, repetition_ema=0.0)  # 명시
    assert should_promote(salience=0.8, explicit=False, repetition_ema=0.7)  # 반복
    assert not should_promote(salience=0.8, explicit=False, repetition_ema=0.1)  # 현저하나 미반복·비명시
    assert not should_promote(salience=0.2, explicit=True, repetition_ema=0.9)  # 현저성 미달


def test_is_remember_command() -> None:
    assert is_remember_command("이거 기억해줘")
    assert is_remember_command("remember this please")
    assert not is_remember_command("무선 이어폰 추천해줘")


# ─────────── builder (델타·consolidation) ───────────


async def test_generate_session_delta_promotes_via_gate() -> None:
    store = get_profile_store()
    key = conversation_key("7", "s1")
    store.append_session_ctx(key, "3만원대 무선 이어폰 위주로 보고 있어요")
    promoted = await generate_session_delta("7", key, llm=_ProfileLLM(), settings=get_settings())
    assert promoted == ["3~5만원 무선이어폰 선호"]
    assert "3~5만원 무선이어폰 선호" in store.get_facts("7")


async def test_generate_session_delta_gate_rejects_low_signal() -> None:
    store = get_profile_store()
    key = conversation_key("7", "s2")
    store.append_session_ctx(key, "음")
    llm = _ProfileLLM(deltas=[{"fact": "잡담", "salience": 0.1, "explicit": False, "repetitionEma": 0.0}])
    promoted = await generate_session_delta("7", key, llm=llm, settings=get_settings())
    assert promoted == [] and store.get_facts("7") == []


async def test_generate_session_delta_degrades_without_llm_or_buffer() -> None:
    store = get_profile_store()
    # 버퍼 없음
    assert await generate_session_delta("7", conversation_key("7", "empty"), llm=_ProfileLLM(), settings=get_settings()) == []
    # LLM 미구성
    store.append_session_ctx(conversation_key("7", "s3"), "x")
    assert await generate_session_delta("7", conversation_key("7", "s3"), llm=None, settings=get_settings()) == []


async def test_consolidate_writes_summary() -> None:
    store = get_profile_store()
    store.add_fact("8", "무선이어폰 선호")
    ok = await consolidate("8", llm=_ProfileLLM(), settings=get_settings())
    assert ok is True
    summary = store.get_summary("8")
    assert summary and "취향 요약" in summary.markdown and summary.generated_at


async def test_consolidate_degrades_without_facts() -> None:
    assert await consolidate("nofacts", llm=_ProfileLLM(), settings=get_settings()) is False


def test_record_remember_hot_path() -> None:
    record_remember("9", "겨울 등산 자주 감")
    assert "겨울 등산 자주 감" in get_profile_store().get_facts("9")


async def test_consolidate_respects_char_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "profile_summary_char_cap", 10)
    get_profile_store().add_fact("10", "x")
    await consolidate("10", llm=_ProfileLLM(summary="가" * 50), settings=get_settings())
    assert len(get_profile_store().get_summary("10").markdown) == 10


# ─────────── GET /profile/me ───────────


def test_profile_me_guest_exists_false() -> None:
    r = client.get("/profile/me")  # 무토큰 dev 게스트
    assert r.status_code == 200
    assert r.json()["exists"] is False


def test_profile_me_member_no_profile() -> None:
    r = client.get("/profile/me", headers=_member_bearer("321"))
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False and body["markdown"] is None


def test_profile_me_member_with_profile_camelcase() -> None:
    get_profile_store().set_summary("321", "# 취향\n- 무선이어폰", "2026-07-20T09:00:00+00:00")
    r = client.get("/profile/me", headers=_member_bearer("321"))
    body = r.json()
    assert body["exists"] is True
    assert body["userId"] == "321"  # camelCase
    assert "무선이어폰" in body["markdown"]
    assert body["generatedAt"].startswith("2026")  # camelCase


# ─────────── POST /events/session-end ───────────


def test_session_end_202_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.events as ev

    monkeypatch.setattr(ev, "get_llm", lambda: _ProfileLLM())
    store = get_profile_store()
    key = conversation_key("777", "sess-9")
    store.append_session_ctx(key, "3만원 무선이어폰 찾아줘")
    payload = {"eventId": "se-1", "userId": "777", "sessionId": "sess-9", "reason": "logout"}
    r1 = client.post("/events/session-end", json=payload)
    assert r1.status_code == 202 and r1.json()["status"] == "accepted"
    # 프로필 생성됨 + 버퍼 정리됨
    assert store.get_summary("777") is not None
    assert store.get_session_ctx(key) == []
    # 멱등 — 같은 eventId 재수신은 duplicate
    r2 = client.post("/events/session-end", json=payload)
    assert r2.status_code == 202 and r2.json()["status"] == "duplicate"


def test_session_end_service_token_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "internal_api_token", "secret-xyz")
    payload = {"eventId": "se-2", "userId": "1", "sessionId": "s"}
    # 토큰 없음 → 401
    assert client.post("/events/session-end", json=payload).status_code == 401
    # 일치 → 202
    r = client.post("/events/session-end", json=payload, headers={"X-Internal-Token": "secret-xyz"})
    assert r.status_code == 202


def test_session_end_degrades_without_llm() -> None:
    """LLM 미구성이어도 세션 종료는 202(best-effort, 프로필 미갱신)."""
    get_profile_store().append_session_ctx(conversation_key("55", "s"), "x")
    r = client.post("/events/session-end", json={"eventId": "se-3", "userId": "55", "sessionId": "s"})
    assert r.status_code == 202
    assert get_profile_store().get_summary("55") is None  # LLM 없어 미갱신


# ─────────── e2e (채팅 transient → 세션종료 → 조회) ───────────


async def test_end_to_end_profile_from_chat(monkeypatch: pytest.MonkeyPatch, buyer_fakes) -> None:
    """회원 채팅 → 세션 종료 → 프로필 생성 → GET /profile/me 노출."""
    import app.api.events as ev

    monkeypatch.setattr(ev, "get_llm", lambda: _ProfileLLM())
    hdr = _member_bearer("888")
    # 회원 채팅 1턴 → transient 버퍼 누적
    client.post("/chat", json={"sessionId": "e2e", "threadId": "t", "message": "3만원 무선이어폰 추천"}, headers=hdr)
    assert get_profile_store().get_session_ctx(conversation_key("888", "e2e"))  # 버퍼에 쌓임
    # 세션 종료 → 델타·consolidation
    client.post("/events/session-end", json={"eventId": "e2e-end", "userId": "888", "sessionId": "e2e"})
    # 조회
    body = client.get("/profile/me", headers=hdr).json()
    assert body["exists"] is True and "무선이어폰" in body["markdown"]
