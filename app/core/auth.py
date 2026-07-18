"""JWT 디코드/검증 헬퍼 (api-spec §2.2, RS256 + JWKS 확정 2026-07-15).

인증 모드 2종:
  - "dev"  : 서명 검증 없이 디코드. 헤더 없으면 게스트로 취급.
             (로컬 개발 편의 — Spring 토큰 없이도 동작)
  - "jwks" : Spring 이 서빙하는 GET /.well-known/jwks.json 공개키로 RS256 검증.
             토큰 헤더의 kid → 공개키 매핑, 서명·exp·iss·aud 를 확인한다.

[변경] 기존 "secret"(HS256 공유 시크릿) 모드는 제거했다 — Spring 이 RS256+JWKS 로 확정.

클레임:
  - sub  : 사용자 식별자
  - role : 권한 (예: USER). 게스트/판매자 role 값은 TBD (아래 매핑 참고).
  - brandId : 판매자(role=SELLER) 브랜드 id — {brandId} path용, 요청 본문 불신(§2.6).

[보안] 신원(user_id)·게스트 여부·판매자 스코프는 오직 토큰 클레임에서만 도출한다.
요청 본문의 식별자는 절대 신뢰하지 않는다 (사칭 방지, api-spec §2.2 a / §2.5 / §3.1 / §3.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

# 클레임 키
CLAIM_SUBJECT = "sub"
CLAIM_ROLE = "role"
CLAIM_BRAND_ID = "brandId"

# role 값 매핑 — TODO(C-10): 게스트/판매자 role 최종값을 Spring 회원 스키마 확정 시 반영.
ROLE_USER = "USER"
ROLE_GUEST = "GUEST"  # TODO: 최종 게스트 role 값 확정 대기
ROLE_SELLER = "SELLER"  # TODO: 최종 판매자 role 값 확정 대기


@dataclass(frozen=True)
class Identity:
    """토큰에서 도출한 호출자 신원. 요청 본문이 아니라 오직 토큰이 근거다.

    brand_id 는 role==SELLER 토큰의 `brandId` 클레임 — 판매자 역호출(§4.4/§4.5)의
    `{brandId}` path 에 쓴다. 요청 본문/발화에서 받지 않는다 (IDOR 방지, §2.6).
    """

    user_id: str | None
    is_guest: bool
    seller_id: str | None
    brand_id: str | None = None


class AuthError(Exception):
    """토큰 없음/무효/만료. 라우터에서 401로 매핑한다 (api-spec §2.4)."""


def _claims_to_identity(claims: dict) -> Identity:
    """검증된 클레임 dict → Identity 매핑.

    role 기반 매핑 (TODO C-10 최종값 확정 대기):
      - role == GUEST  → 게스트 (user_id 없음, 개인화/장바구니 불가)
      - role == SELLER → 판매자 스코프 부여 (seller_id = sub, brand_id = brandId 클레임)
      - 그 외(USER 등) → 일반 회원 (user_id = sub)
    """
    subject = claims.get(CLAIM_SUBJECT)
    role = claims.get(CLAIM_ROLE)

    if role == ROLE_GUEST:
        return Identity(user_id=None, is_guest=True, seller_id=None)
    if role == ROLE_SELLER:
        # 판매자는 sub 를 판매자 식별자로도 사용한다 (스코프 근거는 role 클레임).
        return Identity(
            user_id=subject, is_guest=False, seller_id=subject,
            brand_id=claims.get(CLAIM_BRAND_ID),
        )
    # 기본: 일반 회원.
    return Identity(user_id=subject, is_guest=False, seller_id=None)


@lru_cache
def _jwk_client(jwks_url: str) -> PyJWKClient:
    """JWKS 클라이언트 캐시. kid→공개키 조회를 재사용한다 (요청마다 재페치 방지)."""
    return PyJWKClient(jwks_url)


def decode_token(
    token: str | None,
    *,
    auth_mode: str,
    jwks_url: str | None = None,
    issuer: str | None = None,
    audience: str | None = None,
) -> Identity:
    """Bearer 토큰을 인증 모드에 따라 디코드/검증하고 Identity 를 반환한다.

    dev 모드에서 토큰이 없으면 게스트 Identity 를 돌려준다 (헤더 없는 로컬 호출 편의).
    그 외에는 토큰이 없거나 검증 실패 시 AuthError.
    """
    if auth_mode == "dev":
        if not token:
            # dev 전용 편의: 헤더 없으면 게스트로 취급.
            return Identity(user_id=None, is_guest=True, seller_id=None)
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc
        return _claims_to_identity(claims)

    if auth_mode == "jwks":
        if not token:
            raise AuthError("missing token")
        if not jwks_url:
            raise AuthError("server misconfigured: JWKS_URL unset")
        try:
            signing_key = _jwk_client(jwks_url).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=audience,
                options={
                    "require": ["exp"],
                    "verify_iss": issuer is not None,
                    "verify_aud": audience is not None,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc
        return _claims_to_identity(claims)

    raise AuthError(f"unknown auth_mode: {auth_mode}")
