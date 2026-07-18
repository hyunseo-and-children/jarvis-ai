"""JWKS(RS256) 인증 모드 단위 테스트 (확정 2026-07-15).

로컬에서 RSA 키 쌍을 생성해 토큰을 서명하고, PyJWKClient 의 서명키 조회를 로컬 공개키로
패치해 decode_token 의 jwks 모드가 서명·exp·iss·aud 를 검증하고 role→Identity 를 매핑하는지 확인한다.
라이브 JWKS 엔드포인트 없이 순수 단위로 검증한다.
"""

from __future__ import annotations

import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import auth
from app.core.auth import AuthError, decode_token

ISSUER = "shopping-spring-auth"
AUDIENCE = "shopping-fastapi-ai"


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    """테스트용 RSA 개인키 (2048-bit)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_token(private_key: rsa.RSAPrivateKey, claims: dict) -> str:
    """주어진 클레임으로 RS256 토큰을 서명한다 (kid 헤더 포함)."""
    # jwt.encode(payload, key, algorithm=...) — payload 가 첫 인자.
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-kid"})


@pytest.fixture(autouse=True)
def _patch_jwk_client(monkeypatch: pytest.MonkeyPatch, rsa_key: rsa.RSAPrivateKey) -> None:
    """PyJWKClient.get_signing_key_from_jwt 를 로컬 공개키 반환으로 패치.

    캐시(_jwk_client lru_cache)도 비워 매 테스트가 패치된 경로를 타게 한다.
    """
    public_key = rsa_key.public_key()

    class _FakeSigningKey:
        key = public_key

    class _FakeJWKClient:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: D107
            pass

        def get_signing_key_from_jwt(self, _token: str) -> _FakeSigningKey:
            return _FakeSigningKey()

    auth._jwk_client.cache_clear()
    monkeypatch.setattr(auth, "PyJWKClient", _FakeJWKClient)


def _decode(token: str):
    return decode_token(
        token,
        auth_mode="jwks",
        jwks_url="https://spring.example/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
    )


def _base_claims(**overrides) -> dict:
    now = dt.datetime.now(tz=dt.timezone.utc)
    claims = {
        "sub": "user-42",
        "role": "USER",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + dt.timedelta(minutes=5),
        "iat": now,
    }
    claims.update(overrides)
    return claims


def test_valid_user_token_maps_to_member(rsa_key: rsa.RSAPrivateKey) -> None:
    """유효한 USER 토큰 → 일반 회원 Identity (user_id=sub, 게스트 아님, 판매자 아님)."""
    token = _make_token(rsa_key, _base_claims())
    identity = _decode(token)
    assert identity.user_id == "user-42"
    assert identity.is_guest is False
    assert identity.seller_id is None


def test_seller_role_grants_seller_scope(rsa_key: rsa.RSAPrivateKey) -> None:
    """SELLER role → 판매자 스코프 부여 (seller_id=sub)."""
    token = _make_token(rsa_key, _base_claims(sub="seller-7", role=auth.ROLE_SELLER))
    identity = _decode(token)
    assert identity.seller_id == "seller-7"
    assert identity.is_guest is False


def test_seller_token_preserves_brand_id(rsa_key: rsa.RSAPrivateKey) -> None:
    """SELLER 토큰의 brandId 클레임 → Identity.brand_id 보존 ({brandId} path용, IDOR 방지)."""
    token = _make_token(rsa_key, _base_claims(sub="seller-7", role=auth.ROLE_SELLER, brandId="brand-99"))
    identity = _decode(token)
    assert identity.seller_id == "seller-7"
    assert identity.brand_id == "brand-99"


def test_guest_role_has_no_user_id(rsa_key: rsa.RSAPrivateKey) -> None:
    """GUEST role → 게스트 Identity (user_id 없음)."""
    token = _make_token(rsa_key, _base_claims(role=auth.ROLE_GUEST))
    identity = _decode(token)
    assert identity.user_id is None
    assert identity.is_guest is True


def test_expired_token_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    """만료 토큰은 AuthError."""
    now = dt.datetime.now(tz=dt.timezone.utc)
    token = _make_token(rsa_key, _base_claims(exp=now - dt.timedelta(minutes=1)))
    with pytest.raises(AuthError):
        _decode(token)


def test_wrong_audience_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    """aud 불일치 토큰은 AuthError."""
    token = _make_token(rsa_key, _base_claims(aud="some-other-service"))
    with pytest.raises(AuthError):
        _decode(token)


def test_wrong_issuer_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    """iss 불일치 토큰은 AuthError."""
    token = _make_token(rsa_key, _base_claims(iss="evil-issuer"))
    with pytest.raises(AuthError):
        _decode(token)


def test_wrong_signature_rejected() -> None:
    """다른 키로 서명한 토큰은 AuthError (서명 검증 실패)."""
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _make_token(other_key, _base_claims())
    with pytest.raises(AuthError):
        _decode(token)


def test_missing_token_rejected() -> None:
    """jwks 모드에서 토큰 없음은 AuthError."""
    with pytest.raises(AuthError):
        _decode(None)
