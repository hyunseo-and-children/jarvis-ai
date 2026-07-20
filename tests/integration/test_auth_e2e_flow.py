"""실인증(jwks) 구매자 흐름 E2E 스모크 (이슈 #35 — #34 머지분 위에서).

다른 E2E 는 dev 인증 레인(게스트 우회)으로 흐름 자체를 검증한다. 이 파일은 **운영 레인**
(RS256 스트림 티켓 + JWKS 로컬 검증, api-spec §2.3)에서도 같은 구매자 흐름이 끝까지
도는지 확인한다 — 인증이 켜진 상태의 종단 스모크.

키/JWKS 대역은 #34 가 만든 tests/unit/_jwks.py 헬퍼를 재사용한다(실 JWKS dict + fetch 계층만
패치 — kid 매칭·서명 검증이 실제 라이브러리 경로로 돈다). 라이브 Spring/Anthropic 불필요.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.api import deps
from app.core import auth
from app.core.config import Settings
from tests.integration.conftest import event_types, first_of, parse_sse
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

BODY = {"sessionId": "sess-jwks", "threadId": "th-jwks", "message": "여행용 파우치 추천해줘"}


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    """테스트용 RSA 개인키 (모듈 공유 — 키 생성 비용 절약)."""
    return make_rsa_key()


@pytest.fixture
def jwks_auth(monkeypatch: pytest.MonkeyPatch, rsa_key: rsa.RSAPrivateKey):
    """앱을 jwks 인증 모드로 전환하고 JWKS 를 로컬 키페어로 서빙한다 (운영 레인 재현)."""
    settings = Settings(
        _env_file=None,
        auth_mode="jwks",
        jwks_url=JWKS_URL,
        jwt_scope=SCOPE,
        pii_hash_pepper="e2e-pepper",
        internal_api_token="e2e-internal-token",
    )
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    auth._jwk_client.cache_clear()
    install_jwks_fetch(monkeypatch, lambda: jwks_of((rsa_key, KID)))
    yield settings
    auth._jwk_client.cache_clear()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_buyer_flow_completes_under_real_jwt(client, spring, llm, jwks_auth, rsa_key) -> None:
    """유효 스트림 티켓(RS256) → 구매자 흐름이 경로 B 까지 완주한다."""
    token = sign_ticket(rsa_key, KID, ticket_claims(sub="42"))

    resp = client.post("/chat", json=BODY, headers=_bearer(token))
    assert resp.status_code == 200

    events = parse_sse(resp.text)
    assert event_types(events)[-1] == "done"
    ready = first_of(events, "products.ready")
    assert ready is not None and set(ready) == {"sessionId", "listId"}


def test_identity_comes_from_verified_token_not_body(client, spring, llm, jwks_auth, rsa_key) -> None:
    """역호출 신원은 **검증된 티켓 sub** 에서만 도출된다 (IDOR 방지, §2.3·§2.6).

    본문에는 신원이 없고, I-19 경로의 memberId 는 토큰 sub 와 일치해야 한다.
    """
    token = sign_ticket(rsa_key, KID, ticket_claims(sub="777"))

    client.post("/chat", json=BODY, headers=_bearer(token))

    orders = spring.requests_to("/internal/members/")
    assert orders and orders[0]["path"] == "/internal/members/777/orders"
    assert "userId" not in BODY


def test_guest_ticket_streams_without_profile(client, spring, llm, jwks_auth, rsa_key) -> None:
    """게스트 티켓(sub_type=guest, sub=UUID)도 추천 흐름은 완주하되 이력 조회는 없다."""
    guest_uuid = "3f2b8a54-8f2e-4b1a-9c60-000000000001"
    token = sign_ticket(rsa_key, KID, ticket_claims(sub=guest_uuid, sub_type="guest"))

    resp = client.post("/chat", json=BODY, headers=_bearer(token))
    assert event_types(parse_sse(resp.text))[-1] == "done"
    assert spring.requests_to("/internal/members/") == []


def test_unauthenticated_request_is_rejected_before_spring(client, spring, llm, jwks_auth) -> None:
    """무토큰 → 401. 상류(Spring/LLM)를 건드리기 전에 거절된다 (§2.5 봉투)."""
    resp = client.post("/chat", json=BODY)

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_INVALID"
    assert spring.requests == [], "인증 실패 요청이 Spring 을 호출하면 안 된다"
