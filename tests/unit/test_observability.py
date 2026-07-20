"""대화 저장 & 관측 (이슈 #8) — api-spec §6.3 (대화 저장 + 구조화 로그·PII)."""

from __future__ import annotations

import asyncio
import json
import logging
import types

import jwt

import pytest
from fastapi.testclient import TestClient

from app.core.auth import Identity
from app.core.conversation import TurnStatus, conversation_key, get_conversation_store
from app.core.config import get_settings
from app.core.observability import message_fingerprint, start_observation
from app.core.stream import get_registry
from app.core.stream import open_stream
from app.main import app

client = TestClient(app)


class _FakeRequest:
    def __init__(self, disconnected: bool = False) -> None:
        self._disc = disconnected
        self.state = types.SimpleNamespace()

    async def is_disconnected(self) -> bool:
        return self._disc


async def _obs(conversation_id: str, message: str = "질문"):
    identity = Identity(user_id="u1", is_guest=False, seller_id=None, subject="u1")
    return start_observation(
        request_id="req-1",
        identity=identity,
        conversation_id=conversation_id,
        message=message,
        store=await get_conversation_store(),
        now=asyncio.get_event_loop().time(),
    )


def _bearer(sub: str) -> dict:
    token = jwt.encode({"sub": sub}, "test-secret-key-0123456789abcdef", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


# ─────────── §6.3 (a) 대화 저장 ───────────


async def test_chat_records_completed_turn(buyer_fakes) -> None:
    """정상 스트림 완료 후 턴이 COMPLETED + user 원문 + assistant 부분 누적으로 저장된다."""
    msg = "여행용 방수 케이스 추천해줘"
    r = client.post("/chat", json={"sessionId": "c1", "threadId": "t", "message": msg})
    assert r.status_code == 200
    _ = r.text  # 스트림 소비 → finalize
    store = await get_conversation_store()
    turns = await store.turns_for(conversation_key(None, "c1"))
    assert len(turns) == 1
    assert turns[0].user_text == msg
    assert turns[0].status == TurnStatus.COMPLETED
    assert turns[0].assistant_text  # assistant 부분 텍스트 누적 저장


async def test_partial_text_preserved_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """클라이언트 취소 시 상태 CANCELLED + 부분 생성 텍스트를 보존한다(§6.3 a)."""
    monkeypatch.setattr(get_settings(), "stream_disconnect_poll_s", 0.02)
    obs = await _obs("cx")

    async def token_then_idle():
        yield 'data: {"type":"token","data":{"text":"부분응답"}}\n\n'
        await asyncio.sleep(2.0)
        yield "data: never\n\n"

    resp = await open_stream(
        _FakeRequest(disconnected=True), "member:cx", token_then_idle, observer=obs
    )
    _ = [c async for c in resp.body_iterator]
    store = await get_conversation_store()
    turn = await store.get_turn(obs.turn_id)
    assert turn is not None
    assert turn.status == TurnStatus.CANCELLED
    assert "부분응답" in turn.assistant_text


async def test_partial_text_preserved_on_error() -> None:
    """스트림 중 상류 오류 시 상태 FAILED + 부분 텍스트를 보존한다(§6.3 a)."""
    obs = await _obs("ce")

    async def token_then_boom():
        yield 'data: {"type":"token","data":{"text":"조금"}}\n\n'
        raise RuntimeError("mid-stream boom")

    resp = await open_stream(_FakeRequest(), "member:ce", token_then_boom, observer=obs)
    _ = [c async for c in resp.body_iterator]
    store = await get_conversation_store()
    turn = await store.get_turn(obs.turn_id)
    assert turn is not None
    assert turn.status == TurnStatus.FAILED
    assert "조금" in turn.assistant_text


async def test_stream_completes_when_finalize_assistant_fails() -> None:
    """finish()(대화 저장 DB I/O)가 실패해도 스트림 소비 자체는 예외 없이 끝난다(PR #48 후속 리뷰).

    _wrapped() 의 finally 는 이미 SSE 헤더/프레임이 전송된 뒤 실행된다 — 여기서 finish() 가
    막히지 않고 예외를 던지면 done/error 종결 프레임 없이 스트림이 끊기거나(§2.9/§3.1 위반),
    CancelledError 취소 전파 중이었다면 그 취소가 이 새 예외로 대체된다. finalize_assistant
    가 raise 하도록 만들어도 body_iterator 소비 자체가 예외 없이 끝나는지 검증한다.
    """
    obs = await _obs("finalize-boom")

    async def fail_finalize(*args, **kwargs):
        raise RuntimeError("conversation store 일시 장애")

    obs.store.finalize_assistant = fail_finalize

    async def token_then_done():
        yield 'data: {"type":"token","data":{"text":"응답"}}\n\n'

    resp = await open_stream(_FakeRequest(), "member:finalize-boom", token_then_done, observer=obs)
    chunks = [c async for c in resp.body_iterator]  # 예외 없이 끝나야 한다
    assert any("응답" in c for c in chunks)


# ─────────── §6.3 (b) 구조화 로그 + PII ───────────


def test_structured_log_has_fields_and_hides_raw_message(
    caplog: pytest.LogCaptureFixture, buyer_fakes
) -> None:
    """요청 구조화 로그가 §6.3 b 필드를 담되, 사용자 message 원문은 남기지 않는다(길이/해시만)."""
    msg = "SECRET_QUERY_비밀질의_XYZ"
    with caplog.at_level(logging.INFO, logger="observability"):
        r = client.post("/chat", json={"sessionId": "c2", "threadId": "t", "message": msg})
        _ = r.text
    logs = [rec.getMessage() for rec in caplog.records if rec.name == "observability"]
    assert logs, "관측 로그가 없음"
    record = json.loads(logs[-1])
    for key in (
        "requestId",
        "conversationId",
        "latencyTotal",
        "streamStatus",
        "messageLength",
        "messageHash",
        "model",
        "promptTokens",
        "completionTokens",
    ):
        assert key in record, f"필드 누락: {key}"
    assert record["streamStatus"] == "COMPLETED"
    assert record["conversationId"] == "c2"
    assert record["messageLength"] == len(msg)
    # [PII] 원문은 로그 어디에도 없고, 해시만 있다.
    assert msg not in logs[-1]
    _, digest = message_fingerprint(msg)
    assert record["messageHash"] == digest


def test_message_fingerprint_is_not_raw() -> None:
    """지문은 (길이, 해시)이며 원문을 그대로 노출하지 않는다."""
    length, digest = message_fingerprint("hello")
    assert length == 5
    assert digest != "hello"
    assert len(digest) == 16


async def test_graph_error_frame_marks_failed() -> None:
    """그래프가 자체 in-stream error 프레임을 emit하면 저장/로그가 FAILED 로 마감된다(§6.3)."""
    obs = await _obs("ge")

    async def token_then_error():
        yield 'data: {"type":"token","data":{"text":"부분"}}\n\n'
        yield 'data: {"type":"error","data":{"code":"LLM_UNAVAILABLE","message":"x"}}\n\n'

    resp = await open_stream(_FakeRequest(), "member:ge", token_then_error, observer=obs)
    _ = [c async for c in resp.body_iterator]
    store = await get_conversation_store()
    turn = await store.get_turn(obs.turn_id)
    assert turn is not None
    assert turn.status == TurnStatus.FAILED


async def test_conversation_scoped_by_identity() -> None:
    """서로 다른 신원이 같은 session_id 를 써도 대화가 섞이지 않는다(IDOR 방지)."""
    store = await get_conversation_store()
    id_a = Identity(user_id="A", is_guest=False, seller_id=None, subject="A")
    id_b = Identity(user_id="B", is_guest=False, seller_id=None, subject="B")
    await start_observation(
        request_id="r", identity=id_a, conversation_id="s1", message="a", store=store, now=0.0
    ).commit_user_message()
    await start_observation(
        request_id="r", identity=id_b, conversation_id="s1", message="b", store=store, now=0.0
    ).commit_user_message()
    turns_a = await store.turns_for(conversation_key("A", "s1"))
    turns_b = await store.turns_for(conversation_key("B", "s1"))
    assert len(turns_a) == 1 and turns_a[0].user_text == "a"
    assert len(turns_b) == 1 and turns_b[0].user_text == "b"


async def test_inner_factory_sync_error_releases_and_marks_failed() -> None:
    """inner_factory 동기 예외 시 슬롯 해제 + 턴 FAILED 마감(PENDING 영구 잔존 방지)."""
    obs = await _obs("if1")

    def bad_factory():
        raise RuntimeError("factory boom")

    with pytest.raises(RuntimeError):
        await open_stream(_FakeRequest(), "member:if1", bad_factory, observer=obs)
    assert not get_registry().is_active("member:if1")
    store = await get_conversation_store()
    turn = await store.get_turn(obs.turn_id)
    assert turn is not None and turn.status == TurnStatus.FAILED


async def test_commit_user_message_failure_releases_slot() -> None:
    """대화 저장(commit_user_message) 실패 시에도 스트림 슬롯이 해제된다(영구 누수 방지, PR #48 리뷰).

    이전(인메모리 dict) 구현은 저장이 예외를 던질 수 없었지만, pg-profile 이관 후
    실제 DB 오류로 던질 수 있다 — registry.acquire() 이후 release 담당 try/except
    이전에 있으면 예외가 그대로 전파돼 슬롯이 프로세스 재시작까지 영구히 잠긴다.
    """
    obs = await _obs("slotfail")

    class _FailingStore:
        async def save_user_message(self, *a, **k):
            raise RuntimeError("db down")

        async def finalize_assistant(self, *a, **k):
            return None

        async def get_turn(self, *a, **k):
            return None

        async def turns_for(self, *a, **k):
            return []

    obs.store = _FailingStore()

    async def unreachable():
        yield "data: never\n\n"

    with pytest.raises(RuntimeError):
        await open_stream(_FakeRequest(), "member:slotfail", unreachable, observer=obs)
    assert not get_registry().is_active("member:slotfail")


def test_rate_limit_emits_structured_observation(caplog: pytest.LogCaptureFixture) -> None:
    """429 발동도 errorType=RATE_LIMITED 구조화 로그로 관측된다(§6.3 b)."""
    headers = _bearer("rl-obs")
    with caplog.at_level(logging.INFO, logger="observability"):
        for i in range(11):
            client.post(
                "/chat",
                json={"sessionId": f"rlo-{i}", "threadId": "t", "message": "m"},
                headers=headers,
            )
    logs = [json.loads(r.getMessage()) for r in caplog.records if r.name == "observability"]
    rate_logs = [entry for entry in logs if entry.get("errorType") == "RATE_LIMITED"]
    assert rate_logs, "429 구조화 로그 없음"
    assert rate_logs[0]["streamStatus"] is None


async def test_error_frame_terminates_stream() -> None:
    """in-stream error 후 스트림을 종결 — 이후 이벤트가 응답·저장소를 오염시키지 않는다."""
    obs = await _obs("et")

    async def token_error_token():
        yield 'data: {"type":"token","data":{"text":"before"}}\n\n'
        yield 'data: {"type":"error","data":{"code":"LLM_UNAVAILABLE","message":"x"}}\n\n'
        yield 'data: {"type":"token","data":{"text":"AFTER"}}\n\n'

    resp = await open_stream(_FakeRequest(), "member:et", token_error_token, observer=obs)
    text = "".join([c async for c in resp.body_iterator])
    assert "before" in text and "error" in text
    assert "AFTER" not in text  # error 이후 종결
    store = await get_conversation_store()
    turn = await store.get_turn(obs.turn_id)
    assert turn is not None and turn.status == TurnStatus.FAILED
    assert "AFTER" not in turn.assistant_text  # 저장 오염 없음


def test_message_length_limit_rejected() -> None:
    """상한 초과 message 는 400(메모리·PII 방어)."""
    r = client.post("/chat", json={"sessionId": "ml", "threadId": "t", "message": "x" * 100000})
    assert r.status_code == 400


async def test_store_evicts_oldest_beyond_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """저장소가 상한을 넘으면 오래된 턴부터 축출한다(무제한 증가 방지, 인메모리 폴백 한정)."""
    store = await get_conversation_store()
    monkeypatch.setattr(type(store), "_MAX_TURNS", 2)
    for text in ("a", "b", "c"):
        tid = await store.save_user_message("k", "u", "member", text)
        await store.finalize_assistant(tid, "x", TurnStatus.COMPLETED)  # 확정 → 축출 대상
    assert len(store._turns) == 2  # 확정된 오래된 턴(a) 축출


def test_fingerprint_uses_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    """pepper 가 바뀌면 지문이 달라진다(HMAC — salt 없는 sha256 역산 방어)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "pii_hash_pepper", "pep1")
    _, d1 = message_fingerprint("hello")
    monkeypatch.setattr(settings, "pii_hash_pepper", "pep2")
    _, d2 = message_fingerprint("hello")
    assert d1 != d2


def test_pepper_required_in_jwks_mode() -> None:
    """운영(jwks)에서 pii_hash_pepper 미주입이면 Settings 기동이 실패한다."""
    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(auth_mode="jwks", pii_hash_pepper="", jwks_url="http://x")
    # dev 모드는 빈 pepper 허용
    Settings(auth_mode="dev", pii_hash_pepper="")


async def test_eviction_skips_pending_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """진행 중(PENDING) 턴은 상한을 넘겨도 축출되지 않는다(응답 유실 방지, 인메모리 폴백 한정)."""
    store = await get_conversation_store()
    monkeypatch.setattr(type(store), "_MAX_TURNS", 1)
    pending = await store.save_user_message("k", "u", "member", "in-flight")  # 미완(PENDING)
    for i in range(3):
        tid = await store.save_user_message("k", "u", "member", f"m{i}")
        await store.finalize_assistant(tid, "done", TurnStatus.COMPLETED)
    turn = await store.get_turn(pending)
    assert turn is not None and turn.status == TurnStatus.PENDING


async def test_409_does_not_store_ghost_turn() -> None:
    """409(동시 스트림 거절) 요청은 대화 저장소에 유령 턴을 남기지 않는다(save-after-acquire)."""
    from app.core.conversation import conversation_key
    from app.core.stream import get_registry

    store = await get_conversation_store()
    # dev 게스트 → registry_key/conversation_key owner="anon"
    get_registry().acquire("anon:dup")  # 슬롯 선점 → 다음 요청은 409
    try:
        r = client.post("/chat", json={"sessionId": "dup", "threadId": "t", "message": "중복요청"})
        assert r.status_code == 409
    finally:
        get_registry().release("anon:dup")
    assert await store.turns_for(conversation_key(None, "dup")) == []  # 유령 턴 없음


def test_identifier_length_limit_rejected() -> None:
    """상한 초과 sessionId/threadId 는 400(불투명 키 남용 방어)."""
    r = client.post("/chat", json={"sessionId": "s" * 10000, "threadId": "t", "message": "m"})
    assert r.status_code == 400
