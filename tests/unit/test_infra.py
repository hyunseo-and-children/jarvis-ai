"""공통 인프라 (이슈 #1) — SSE 수명주기·레이트 리밋·오류 봉투 (api-spec §2.5/2.8/2.9)."""

from __future__ import annotations

import asyncio
import types

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.stream import get_registry, open_stream
from app.main import app

client = TestClient(app)


def _chat(session_id: str, headers: dict | None = None):
    return client.post(
        "/chat",
        json={"sessionId": session_id, "threadId": "t", "message": "m"},
        headers=headers or {},
    )


def _bearer(sub: str) -> dict:
    """dev 디코드는 서명 검증을 안 하므로 임의 서명 unsigned-ish JWT 로 sub 만 실어 보낸다."""
    token = jwt.encode({"sub": sub}, "test-secret-key-0123456789abcdef", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


class _FakeRequest:
    """open_stream 단위 테스트용 최소 Request 더미 (is_disconnected 만 사용)."""

    def __init__(self, disconnected: bool = False) -> None:
        self._disc = disconnected
        self.state = types.SimpleNamespace()

    async def is_disconnected(self) -> bool:
        return self._disc


# ─────────── §2.9 (a) 동시 스트림 제한 ───────────


def test_concurrent_same_session_returns_409() -> None:
    """동일 sessionId 활성 스트림 존재 시 새 요청은 409 STREAM_IN_PROGRESS."""
    # dev 게스트 → registry_key owner="guest" → "guest:busy-sess"
    get_registry().acquire("guest:busy-sess")
    try:
        r = _chat("busy-sess")
        assert r.status_code == 409
        env = r.json()["error"]
        assert env["code"] == "STREAM_IN_PROGRESS"
        assert env["requestId"]
    finally:
        get_registry().release("guest:busy-sess")


def test_registry_released_after_stream() -> None:
    """정상 스트림 종료 후 레지스트리에서 세션이 해제된다(다음 요청 가능)."""
    r = _chat("done-sess")
    assert r.status_code == 200
    _ = r.text  # 스트림 소비 → 제너레이터 완료 → finally 해제
    assert not get_registry().is_active("guest:done-sess")


def test_different_sessions_not_blocked() -> None:
    """서로 다른 세션은 서로를 막지 않는다."""
    r1 = _chat("sess-a")
    _ = r1.text
    r2 = _chat("sess-b")
    _ = r2.text
    assert r1.status_code == 200
    assert r2.status_code == 200


# ─────────── §2.9 (c) 타임아웃 ───────────


async def test_first_token_timeout_returns_504(monkeypatch: pytest.MonkeyPatch) -> None:
    """first-token 상한 초과(첫 이벤트 도착 전) → 504 UPSTREAM_TIMEOUT + 레지스트리 해제."""
    s = get_settings()
    monkeypatch.setattr(s, "stream_first_token_timeout_s", 0.05)

    async def slow():
        await asyncio.sleep(0.5)
        yield "data: x\n\n"

    with pytest.raises(HTTPException) as ei:
        await open_stream(_FakeRequest(), "to-sess", slow)
    assert ei.value.status_code == 504
    assert ei.value.detail["code"] == "UPSTREAM_TIMEOUT"
    assert not get_registry().is_active("to-sess")


async def test_total_cap_truncates_with_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """전체 상한 초과 시 done(finishReason stop)으로 정상 절단하고 이후 이벤트는 미방출."""
    s = get_settings()
    monkeypatch.setattr(s, "stream_first_token_timeout_s", 5.0)
    monkeypatch.setattr(s, "stream_total_timeout_s", 0.15)
    monkeypatch.setattr(s, "stream_disconnect_poll_s", 0.02)

    async def slow():
        yield 'data: {"type":"token","data":{"text":"hi"}}\n\n'
        await asyncio.sleep(2.0)
        yield "data: never\n\n"

    resp = await open_stream(_FakeRequest(), "cap-sess", slow)
    parts = [c if isinstance(c, str) else c.decode() async for c in resp.body_iterator]
    text = "".join(parts)
    assert '"token"' in text
    assert '"done"' in text
    assert "never" not in text
    assert not get_registry().is_active("cap-sess")


async def test_first_frame_error_releases_registry() -> None:
    """첫 프레임 전 상류 오류(비-타임아웃) 시에도 레지스트리를 해제한다(§409 누수 방지)."""

    async def boom():
        raise RuntimeError("upstream boom")
        yield  # pragma: no cover - 도달 불가

    with pytest.raises(RuntimeError):
        await open_stream(_FakeRequest(), "boom-sess", boom)
    assert not get_registry().is_active("boom-sess")


async def test_disconnect_cancels_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """연결 종료 감지 시 스트림을 중단하고 레지스트리를 해제한다(§2.9 b)."""
    s = get_settings()
    monkeypatch.setattr(s, "stream_first_token_timeout_s", 5.0)
    monkeypatch.setattr(s, "stream_total_timeout_s", 5.0)
    monkeypatch.setattr(s, "stream_disconnect_poll_s", 0.02)

    async def slow():
        yield "data: first\n\n"
        await asyncio.sleep(2.0)
        yield "data: never\n\n"

    resp = await open_stream(_FakeRequest(disconnected=True), "disc-sess", slow)
    parts = [c if isinstance(c, str) else c.decode() async for c in resp.body_iterator]
    text = "".join(parts)
    assert "first" in text
    assert "never" not in text
    assert not get_registry().is_active("disc-sess")


# ─────────── §2.8 레이트 리밋 ───────────


def test_rate_limit_returns_429() -> None:
    """동일 sub 토큰의 분당 상한(기본 10) 초과 시 11번째 요청은 429."""
    h = _bearer("rl-user")
    codes = [_chat(f"rl-{i}", h).status_code for i in range(11)]
    assert codes.count(200) == 10
    assert codes[-1] == 429


def test_rate_limit_429_envelope() -> None:
    """429 응답도 §2.5 봉투(code RATE_LIMITED + requestId)."""
    h = _bearer("rlx-user")
    for i in range(10):
        _chat(f"rlx-{i}", h)
    r = _chat("rlx-over", h)
    assert r.status_code == 429
    env = r.json()["error"]
    assert env["code"] == "RATE_LIMITED"
    assert env["requestId"]


def test_ip_backstop_limits_token_rotation() -> None:
    """토큰 sub 를 매번 바꿔도(회전 우회) IP 백스톱 상한(기본 50)에서 결국 429."""
    limit = 10 * 5  # per_min * host_multiplier
    codes = [_chat(f"rot-{i}", _bearer(f"user-{i}")).status_code for i in range(limit + 1)]
    assert codes.count(200) == limit
    assert codes[-1] == 429


# ─────────── §2.5 오류 봉투 (코드 매핑) ───────────


def test_validation_error_400_envelope() -> None:
    """본문 검증 실패 → 400 BAD_REQUEST 봉투(422 아님, §2.5 표 정합)."""
    r = client.post("/chat", json={"sessionId": "x"})  # threadId/message 누락
    assert r.status_code == 400
    env = r.json()["error"]
    assert env["code"] == "BAD_REQUEST"
    assert env["requestId"]


def test_bad_token_401_envelope() -> None:
    """무효 토큰 → 401 TOKEN_INVALID 봉투."""
    r = client.post(
        "/chat",
        json={"sessionId": "tok", "threadId": "t", "message": "m"},
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert r.status_code == 401
    env = r.json()["error"]
    assert env["code"] == "TOKEN_INVALID"
    assert env["requestId"]


def test_seller_scope_403_envelope() -> None:
    """판매자 스코프 없는 토큰 → 403 FORBIDDEN 봉투."""
    r = client.post(
        "/seller/chat", json={"sessionId": "s", "threadId": "t", "message": "m"}
    )
    assert r.status_code == 403
    env = r.json()["error"]
    assert env["code"] == "FORBIDDEN"
    assert env["requestId"]


def test_request_id_header_present() -> None:
    """모든 응답에 X-Request-Id 헤더가 실린다(로그 상관관계)."""
    r = client.get("/health")
    assert r.headers.get("x-request-id")


def test_unhandled_exception_returns_500_envelope() -> None:
    """라우터 미처리 예외(500)도 §2.5 봉투(code INTERNAL)로 감싼다."""
    from app.main import create_app

    app2 = create_app()

    async def _boom() -> None:
        raise RuntimeError("intentional test failure")

    app2.add_api_route("/_boom", _boom, methods=["GET"])
    c = TestClient(app2, raise_server_exceptions=False)
    r = c.get("/_boom")
    assert r.status_code == 500
    env = r.json()["error"]
    assert env["code"] == "INTERNAL"
    assert env["requestId"]


def test_registry_key_binds_identity() -> None:
    """레지스트리 키가 인증 신원에 묶여 사용자 간 슬롯 침범을 막는다(§2.9 a)."""
    from app.core.auth import Identity
    from app.core.stream import registry_key

    member = Identity(user_id="u1", is_guest=False, seller_id=None)
    guest = Identity(user_id=None, is_guest=True, seller_id=None)
    seller = Identity(user_id="s1", is_guest=False, seller_id="s1", brand_id="b1")
    assert registry_key(member, "sess") == "u1:sess"
    assert registry_key(guest, "sess") == "guest:sess"
    assert registry_key(seller, "sess") == "s1:sess"
    # 서로 다른 사용자는 같은 session_id 라도 키가 다르다.
    assert registry_key(member, "x") != registry_key(guest, "x")
