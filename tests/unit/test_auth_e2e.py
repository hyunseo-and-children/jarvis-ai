"""인증 실배선 앱 레벨 검증 (#34, api-spec §2.3/§2.5) — jwks 모드 401/403 봉투 + 서비스 토큰 레인.

FastAPI 앱을 jwks 설정으로 구동해 실 RS256 티켓 검증 흐름을 확인한다:
  - §2.3(a) 사용자 티켓: 무토큰/무효 401 TOKEN_INVALID · 만료 401 TOKEN_EXPIRED ·
    유효 티켓 SSE 200 · /seller/chat 스코프 403 FORBIDDEN (§2.5 봉투 + requestId)
  - §2.3(b) 서비스 토큰: 인바운드 verify_service_token fail-closed ·
    아웃바운드 spring_client X-Internal-Token 부착
  - jwks 모드 기동 검증(Settings validator): jwks_url·pepper·internal 토큰 필수

tests/integration/ E2E 하니스(#35)와 별개의 순수 단위 검증 — 라이브 의존 없음.
"""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import deps
from app.core import auth
from app.core.config import Settings
from app.main import app
from tests.unit._jwks import (
    JWKS_URL,
    KID,
    SCOPE,
    install_jwks_fetch,
    jwks_of,
    make_rsa_key,
    sign_ticket,
    ticket_claims,
)

client = TestClient(app)


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    """테스트용 RSA 개인키 (모듈 공유)."""
    return make_rsa_key()


def _jwks_settings(**overrides) -> Settings:
    """jwks 모드 Settings — 운영 필수값(pepper·internal 토큰·jwks_url) 포함, .env 미참조."""
    kwargs = dict(
        _env_file=None,
        auth_mode="jwks",
        jwks_url=JWKS_URL,
        jwt_scope=SCOPE,
        pii_hash_pepper="test-pepper",
        internal_api_token="svc-token",
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


@pytest.fixture
def jwks_app(monkeypatch: pytest.MonkeyPatch, rsa_key: rsa.RSAPrivateKey) -> Settings:
    """deps 를 jwks 설정으로 패치하고 JWKS fetch 를 로컬 키페어로 고정한다."""
    settings = _jwks_settings()
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    auth._jwk_client.cache_clear()
    install_jwks_fetch(monkeypatch, lambda: jwks_of((rsa_key, KID)))
    yield settings
    auth._jwk_client.cache_clear()


def _chat_body() -> dict:
    return {"sessionId": "s-auth-1", "threadId": "t-auth-1", "message": "여행용 파우치 추천해줘"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── §2.3(a) FE→AI 사용자 티켓: 401/403 봉투 (§2.5) ──


def test_chat_without_token_returns_401_invalid(jwks_app) -> None:
    """jwks 모드 무토큰 → 401 TOKEN_INVALID 봉투 + requestId (dev 게스트 우회 없음)."""
    resp = client.post("/chat", json=_chat_body())
    assert resp.status_code == 401
    body = resp.json()["error"]
    assert body["code"] == "TOKEN_INVALID"
    assert body["requestId"]


def test_chat_with_expired_ticket_returns_401_expired(jwks_app, rsa_key) -> None:
    """만료 티켓 → 401 TOKEN_EXPIRED — FE 는 CH-1b 재발급 후 1회 재시도 (§2.3/§2.5)."""
    now = dt.datetime.now(tz=dt.timezone.utc)
    token = sign_ticket(rsa_key, KID, ticket_claims(exp=now - dt.timedelta(seconds=1)))
    resp = client.post("/chat", json=_chat_body(), headers=_bearer(token))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_chat_with_wrong_scope_returns_401(jwks_app, rsa_key) -> None:
    """scope 불일치 티켓(용도 혼용) → 401 TOKEN_INVALID (§2.3 검증 항목)."""
    token = sign_ticket(rsa_key, KID, ticket_claims(scope="profile:read"))
    resp = client.post("/chat", json=_chat_body(), headers=_bearer(token))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_INVALID"


def test_chat_with_garbage_token_returns_401(jwks_app) -> None:
    """형식 불량 토큰 → 401 TOKEN_INVALID."""
    resp = client.post("/chat", json=_chat_body(), headers=_bearer("not-a-jwt"))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_INVALID"


def test_chat_with_valid_member_ticket_streams(jwks_app, rsa_key, buyer_fakes) -> None:
    """유효 회원 티켓 → SSE 200 스트리밍 (실 JWT 검증 통과 후 그래프 구동)."""
    token = sign_ticket(rsa_key, KID, ticket_claims(sub="42"))
    resp = client.post("/chat", json=_chat_body(), headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_seller_chat_with_member_ticket_returns_403(jwks_app, rsa_key) -> None:
    """판매자 스코프 없는 티켓의 /seller/chat → 403 FORBIDDEN 봉투 (§2.3)."""
    token = sign_ticket(rsa_key, KID, ticket_claims(sub="42"))
    resp = client.post("/seller/chat", json=_chat_body(), headers=_bearer(token))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_seller_chat_without_brand_id_returns_403(jwks_app, rsa_key) -> None:
    """role=SELLER 인데 brandId 클레임 누락 → 403 (본문 우회 금지, §2.3/§2.6)."""
    claims = ticket_claims(sub="9")
    claims["role"] = auth.ROLE_SELLER
    token = sign_ticket(rsa_key, KID, claims)
    resp = client.post("/seller/chat", json=_chat_body(), headers=_bearer(token))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


# ── §2.3(b) 인바운드 서비스 토큰 (Spring→AI) — fail-closed ──


def test_service_token_match_passes(monkeypatch) -> None:
    """jwks 모드 + 토큰 일치 → 통과."""
    monkeypatch.setattr(deps, "get_settings", lambda: _jwks_settings())
    assert deps.verify_service_token(x_internal_token="svc-token") is None


def test_service_token_mismatch_401(monkeypatch) -> None:
    """jwks 모드 + 토큰 불일치 → 401 INTERNAL_TOKEN_INVALID."""
    monkeypatch.setattr(deps, "get_settings", lambda: _jwks_settings())
    with pytest.raises(HTTPException) as exc:
        deps.verify_service_token(x_internal_token="wrong")
    assert exc.value.status_code == 401


def test_service_token_missing_401(monkeypatch) -> None:
    """jwks 모드 + 헤더 누락 → 401 (fail-closed)."""
    monkeypatch.setattr(deps, "get_settings", lambda: _jwks_settings())
    with pytest.raises(HTTPException) as exc:
        deps.verify_service_token(x_internal_token=None)
    assert exc.value.status_code == 401


def test_service_token_dev_mode_bypasses(monkeypatch) -> None:
    """dev 모드는 미검증 편의 허용 (로컬 전용)."""
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(_env_file=None, auth_mode="dev"))
    assert deps.verify_service_token(x_internal_token=None) is None


# ── §2.3(b) 아웃바운드 서비스 토큰 (AI→Spring) — X-Internal-Token 부착 ──


def test_module_client_attaches_internal_token(monkeypatch) -> None:
    """spring_client._client() 가 X-Internal-Token 헤더 + 3s 타임아웃을 부착한다."""
    import app.services.spring_client as sc

    monkeypatch.setattr(
        sc, "get_settings", lambda: Settings(_env_file=None, internal_api_token="svc-token")
    )
    http_client = sc._client()
    assert http_client.headers["X-Internal-Token"] == "svc-token"
    assert http_client.timeout.read == pytest.approx(3.0)


# ── jwks 모드 기동 검증 (Settings validator — 조용한 미설정 방지) ──


def test_settings_jwks_requires_jwks_url() -> None:
    """auth_mode=jwks 인데 JWKS_URL 미설정 → 기동 실패 (런타임 401 폭주 대신 fail-fast)."""
    with pytest.raises(ValueError):
        _jwks_settings(jwks_url=None)


def test_settings_jwks_requires_internal_token() -> None:
    """auth_mode=jwks 인데 INTERNAL_API_TOKEN 미설정 → 기동 실패 (기존 규칙 회귀 가드)."""
    with pytest.raises(ValueError):
        _jwks_settings(internal_api_token="")


def test_settings_jwt_scope_defaults_to_none() -> None:
    """jwt_scope 기본값은 None(검증 생략) — C-1 확정 전 미확정 추정값을 활성 강제하면
    Spring 발급 티켓과 어긋나는 순간 전면 401 장애가 된다 (PR #39 리뷰 반영).
    운영 전환 시 확정값을 env JWT_SCOPE 로 명시 주입한다."""
    assert Settings(_env_file=None).jwt_scope is None


def test_settings_jwks_without_scope_warns(caplog: pytest.LogCaptureFixture) -> None:
    """jwks 모드 + JWT_SCOPE 미설정 → 기동 경고 로그 — scope 검증이 조용히 비활성인 채
    운영되는 것을 드러낸다 (fail-fast 는 C-1 미확정이라 불가, PR #39 4R 리뷰 반영)."""
    import logging

    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        _jwks_settings(jwt_scope=None)
    assert any("JWT_SCOPE" in record.message for record in caplog.records)
