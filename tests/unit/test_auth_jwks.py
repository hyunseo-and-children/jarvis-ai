"""JWKS(RS256) 인증 모드 단위 테스트 (api-spec §2.3, #34 실배선).

RSA 키페어로 실 JWKS dict 를 구성하고 PyJWKClient 의 HTTP fetch 계층만 패치한다 —
kid→공개키 매칭·JWK 파싱·kid miss refetch 가 실제 라이브러리 경로로 돈다(tests/unit/_jwks.py).

검증 항목(§2.3 확정): signature / exp / iss / aud / scope.
클레임 매핑: sub_type(member|guest, v0.10.0 티켓 정본) 우선 + 구 role 폴백(C-1 잔여).
"""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import auth
from app.core.auth import AuthError, TokenExpiredError, decode_token
from tests.unit._jwks import (
    AUDIENCE,
    ISSUER,
    JWKS_URL,
    KID,
    SCOPE,
    install_jwks_fetch,
    jwks_of,
    make_rsa_key,
    sign_ticket,
    ticket_claims,
)


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    """테스트용 RSA 개인키 (모듈 공유 — 키 생성 비용 절약)."""
    return make_rsa_key()


@pytest.fixture(autouse=True)
def _fresh_jwk_client_cache():
    """_jwk_client lru_cache 를 테스트마다 비워 JWKS 캐시 상태 누수를 막는다."""
    auth._jwk_client.cache_clear()
    yield
    auth._jwk_client.cache_clear()


@pytest.fixture
def jwks_calls(monkeypatch: pytest.MonkeyPatch, rsa_key: rsa.RSAPrivateKey) -> dict:
    """기본 JWKS(단일 kid)를 서빙하고 fetch 횟수를 센다."""
    return install_jwks_fetch(monkeypatch, lambda: jwks_of((rsa_key, KID)))


def _decode(token: str | None, *, scope: str | None = SCOPE):
    return decode_token(
        token,
        auth_mode="jwks",
        jwks_url=JWKS_URL,
        issuer=ISSUER,
        audience=AUDIENCE,
        scope=scope,
        jwks_timeout_s=3.0,
        jwks_cache_ttl_s=300.0,
    )


# ── 티켓 클레임 매핑 (§2.3 v0.10.0: sub_type 정본, 구 role 폴백) ──


def test_member_ticket_maps_to_member(rsa_key, jwks_calls) -> None:
    """sub_type=member 티켓 → 회원 Identity (user_id=sub, 숫자 문자열)."""
    identity = _decode(sign_ticket(rsa_key, KID, ticket_claims(sub="42")))
    assert identity.user_id == "42"
    assert identity.is_guest is False
    assert identity.seller_id is None
    assert identity.subject == "42"


def test_guest_ticket_maps_to_guest(rsa_key, jwks_calls) -> None:
    """sub_type=guest 티켓(sub=UUID) → 게스트 Identity (user_id 없음, subject 보존)."""
    guest_uuid = "3f2b8a54-8f2e-4b1a-9c60-000000000001"
    identity = _decode(sign_ticket(rsa_key, KID, ticket_claims(sub=guest_uuid, sub_type="guest")))
    assert identity.user_id is None
    assert identity.is_guest is True
    assert identity.subject == guest_uuid


def test_unknown_sub_type_rejected(rsa_key, jwks_calls) -> None:
    """미지 sub_type 값은 fail-closed 거부 (member|guest 만 정본, §2.3)."""
    token = sign_ticket(rsa_key, KID, ticket_claims(sub_type="admin"))
    with pytest.raises(AuthError):
        _decode(token)


def test_legacy_role_user_fallback(rsa_key, jwks_calls) -> None:
    """sub_type 없는 구 role=USER 토큰 → 회원 폴백 매핑 유지 (C-1 형식 확정 전 호환)."""
    claims = ticket_claims(sub="7")
    del claims["sub_type"]
    claims["role"] = auth.ROLE_USER
    identity = _decode(sign_ticket(rsa_key, KID, claims))
    assert identity.user_id == "7"
    assert identity.is_guest is False


def test_legacy_role_guest_fallback(rsa_key, jwks_calls) -> None:
    """sub_type 없는 구 role=GUEST 토큰 → 게스트 폴백 매핑 유지."""
    claims = ticket_claims()
    del claims["sub_type"]
    claims["role"] = auth.ROLE_GUEST
    identity = _decode(sign_ticket(rsa_key, KID, claims))
    assert identity.user_id is None
    assert identity.is_guest is True


def test_seller_role_with_brand_id(rsa_key, jwks_calls) -> None:
    """role=SELLER + brandId 클레임 → 판매자 스코프 + brand_id 보존 ({brandId} path용, §2.3)."""
    claims = ticket_claims(sub="9")
    claims["role"] = auth.ROLE_SELLER
    claims["brandId"] = "77"
    identity = _decode(sign_ticket(rsa_key, KID, claims))
    assert identity.seller_id == "9"
    assert identity.brand_id == "77"
    assert identity.is_guest is False


def test_lowercase_seller_role_accepted(rsa_key, jwks_calls) -> None:
    """§2.3 표기 그대로 role="seller"(소문자) → 판매자 스코프 (대소문자 무관 비교, 리뷰 반영).

    C-1 로 role 실값 형식이 미확정이라 소문자 발급 시 전면 403 이 되지 않게 한다.
    """
    claims = ticket_claims(sub="9")
    claims["role"] = "seller"
    claims["brandId"] = "77"
    identity = _decode(sign_ticket(rsa_key, KID, claims))
    assert identity.seller_id == "9"
    assert identity.brand_id == "77"


def test_token_without_identity_claims_rejected(rsa_key, jwks_calls) -> None:
    """sub_type·role 둘 다 없는 서명 유효 토큰 → 거부 (jwks 레인 fail-closed, 리뷰 반영).

    §2.3 은 sub_type 을 티켓 필수 클레임으로 확정 — 신원 유형 클레임이 전무한 토큰을
    회원으로 기본 승인하지 않는다 (미지 sub_type 거부와 방어 원칙 일관).
    """
    claims = ticket_claims()
    del claims["sub_type"]
    token = sign_ticket(rsa_key, KID, claims)
    with pytest.raises(AuthError):
        _decode(token)


def test_unrecognized_role_falls_back_to_member(rsa_key, jwks_calls) -> None:
    """미지 role 값(예: MEMBER)은 회원 관용 폴백 — C-1 값 집합 미확정 상태에서
    회원 role 실값이 예상과 달라도 전면 401 이 되지 않게 한다 (의도된 관용, 리뷰 반영)."""
    claims = ticket_claims(sub="42")
    del claims["sub_type"]
    claims["role"] = "MEMBER"
    identity = _decode(sign_ticket(rsa_key, KID, claims))
    assert identity.user_id == "42"
    assert identity.is_guest is False


# ── 검증 항목: signature / exp / iss / aud / scope (§2.3 확정) ──


def test_expired_ticket_raises_typed_error(rsa_key, jwks_calls) -> None:
    """만료 티켓 → TokenExpiredError (deps 가 401 TOKEN_EXPIRED 로 매핑, §2.5)."""
    now = dt.datetime.now(tz=dt.timezone.utc)
    token = sign_ticket(rsa_key, KID, ticket_claims(exp=now - dt.timedelta(seconds=1)))
    with pytest.raises(TokenExpiredError):
        _decode(token)


def test_expired_error_is_auth_error(rsa_key, jwks_calls) -> None:
    """TokenExpiredError 는 AuthError 하위 타입 — 기존 except AuthError 경로 호환."""
    assert issubclass(TokenExpiredError, AuthError)


def test_wrong_audience_rejected(rsa_key, jwks_calls) -> None:
    """aud 불일치(로그인 AT 혼용 방지) → AuthError."""
    token = sign_ticket(rsa_key, KID, ticket_claims(aud="shopping-spring-api"))
    with pytest.raises(AuthError):
        _decode(token)


def test_wrong_issuer_rejected(rsa_key, jwks_calls) -> None:
    """iss 불일치 → AuthError."""
    token = sign_ticket(rsa_key, KID, ticket_claims(iss="evil-issuer"))
    with pytest.raises(AuthError):
        _decode(token)


def test_wrong_signature_rejected(jwks_calls) -> None:
    """JWKS 에 없는 키로 서명(같은 kid 참칭) → 서명 검증 실패 AuthError."""
    other_key = make_rsa_key()
    token = sign_ticket(other_key, KID, ticket_claims())
    with pytest.raises(AuthError):
        _decode(token)


def test_scope_mismatch_rejected(rsa_key, jwks_calls) -> None:
    """scope 불일치(다른 용도 토큰 혼용) → AuthError (§2.3 검증 항목)."""
    token = sign_ticket(rsa_key, KID, ticket_claims(scope="profile:read"))
    with pytest.raises(AuthError):
        _decode(token)


def test_scope_missing_rejected(rsa_key, jwks_calls) -> None:
    """scope 클레임 누락 → AuthError (검증 요구 시 필수)."""
    claims = ticket_claims()
    del claims["scope"]
    token = sign_ticket(rsa_key, KID, claims)
    with pytest.raises(AuthError):
        _decode(token)


def test_scope_list_claim_accepted(rsa_key, jwks_calls) -> None:
    """scope 가 리스트 형태여도 요구 scope 포함이면 통과 (발급측 표현 관용)."""
    token = sign_ticket(rsa_key, KID, ticket_claims(scope=["chat:stream", "other"]))
    assert _decode(token).user_id == "42"


def test_scope_check_skipped_when_not_required(rsa_key, jwks_calls) -> None:
    """요구 scope 미설정(config None)이면 scope 검증 생략 — 전환기 호환."""
    claims = ticket_claims()
    del claims["scope"]
    token = sign_ticket(rsa_key, KID, claims)
    assert _decode(token, scope=None).user_id == "42"


def test_missing_sub_rejected(rsa_key, jwks_calls) -> None:
    """sub 누락 → AuthError (신원 도출 불가 — 필수 클레임, §2.3)."""
    claims = ticket_claims()
    del claims["sub"]
    token = sign_ticket(rsa_key, KID, claims)
    with pytest.raises(AuthError):
        _decode(token)


def test_missing_token_rejected(jwks_calls) -> None:
    """jwks 모드에서 토큰 없음 → AuthError."""
    with pytest.raises(AuthError):
        _decode(None)


# ── JWKS 캐시·refetch (§2.3: kid miss 시에만 refetch, 요청마다 왕복 금지) ──


def test_jwks_cache_reused_between_decodes(rsa_key, jwks_calls) -> None:
    """같은 kid 반복 검증은 최초 1회만 fetch — 캐시 재사용."""
    _decode(sign_ticket(rsa_key, KID, ticket_claims()))
    _decode(sign_ticket(rsa_key, KID, ticket_claims(sub="43")))
    assert jwks_calls["count"] == 1


def test_kid_miss_triggers_refetch(monkeypatch, rsa_key) -> None:
    """키 회전으로 새 kid 도착 → 캐시 miss → refetch 1회 후 성공 (§2.3)."""
    key_b = make_rsa_key()
    state = {"jwks": jwks_of((rsa_key, KID))}
    calls = install_jwks_fetch(monkeypatch, lambda: state["jwks"])

    assert _decode(sign_ticket(rsa_key, KID, ticket_claims())).user_id == "42"
    assert calls["count"] == 1

    # Spring 키 회전: JWKS 에 kid-b 추가 후 kid-b 서명 티켓 도착.
    state["jwks"] = jwks_of((rsa_key, KID), (key_b, "kid-2026-b"))
    identity = _decode(sign_ticket(key_b, "kid-2026-b", ticket_claims(sub="77")))
    assert identity.user_id == "77"
    assert calls["count"] == 2


def test_unknown_kid_rejected_after_refetch(rsa_key, jwks_calls) -> None:
    """JWKS 에 끝내 없는 kid → refetch 후에도 실패 → AuthError (401 매핑)."""
    other_key = make_rsa_key()
    token = sign_ticket(other_key, "kid-unknown", ticket_claims())
    with pytest.raises(AuthError):
        _decode(token)


# ── 클라이언트 튜너블 주입 (§2.9 c: AI→Spring 3s, 캐시 TTL config) ──


def test_jwk_client_injects_timeout_and_ttl() -> None:
    """_jwk_client 가 JWKS fetch 타임아웃·캐시 TTL 을 config 값으로 주입한다."""
    client = auth._jwk_client(JWKS_URL, timeout_s=3.0, cache_ttl_s=120.0)
    assert client.timeout == pytest.approx(3.0)
    assert client.jwk_set_cache is not None
    assert client.jwk_set_cache.lifespan == pytest.approx(120.0)
