"""JWKS 테스트 헬퍼 (#34 인증 실배선) — 실 RS256 키페어 + 실 JWKS dict.

PyJWKClient 를 통째로 페이크로 바꾸지 않고 HTTP fetch 계층(fetch_data)만 패치한다.
kid→공개키 매칭·JWK 파싱·kid miss refetch 가 전부 실제 라이브러리 경로로 돌아,
Spring JWKS 엔드포인트 없이도 결정적으로 실배선을 검증한다 (api-spec §2.3).
"""

from __future__ import annotations

import datetime as dt
import json

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm

# §2.3 제안값 계열 (config 기본값과 일치 — 실값은 C-1 확정 후 env 주입)
ISSUER = "shopping-spring-auth"
AUDIENCE = "shopping-fastapi-ai"
SCOPE = "chat:stream"
JWKS_URL = "https://spring.test/.well-known/jwks.json"
KID = "kid-2026-a"


def make_rsa_key() -> rsa.RSAPrivateKey:
    """테스트용 RSA 개인키 (2048-bit)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_of(private_key: rsa.RSAPrivateKey, kid: str) -> dict:
    """개인키의 공개키를 JWK dict 로 변환한다 (kid/alg/use 포함)."""
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return jwk


def jwks_of(*key_kid_pairs: tuple[rsa.RSAPrivateKey, str]) -> dict:
    """(개인키, kid) 쌍들로 JWKS 응답 dict 를 만든다."""
    return {"keys": [jwk_of(key, kid) for key, kid in key_kid_pairs]}


def sign_ticket(private_key: rsa.RSAPrivateKey, kid: str, claims: dict) -> str:
    """주어진 클레임을 RS256 으로 서명한다 (kid 헤더 포함)."""
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def ticket_claims(**overrides) -> dict:
    """§2.3 v0.10.0 스트림 티켓 기본 클레임 (sub/sub_type/iss/aud/scope/exp)."""
    now = dt.datetime.now(tz=dt.timezone.utc)
    claims = {
        "sub": "42",
        "sub_type": "member",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "scope": SCOPE,
        "exp": now + dt.timedelta(seconds=60),
        "iat": now,
    }
    claims.update(overrides)
    return claims


def install_jwks_fetch(monkeypatch, supplier) -> dict:
    """PyJWKClient.fetch_data 를 supplier() 가 주는 JWKS dict 로 패치한다.

    실 fetch_data 처럼 tier-1 캐시(jwk_set_cache)에 put 해 캐시 의미론을 보존한다.
    반환 dict 의 "count" 로 네트워크 fetch 횟수(캐시 miss/refetch)를 검증한다.
    """
    calls = {"count": 0}

    def _fake_fetch(self: PyJWKClient):
        calls["count"] += 1
        data = supplier()
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data

    monkeypatch.setattr(PyJWKClient, "fetch_data", _fake_fetch)
    return calls
