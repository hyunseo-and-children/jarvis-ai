"""JWT 디코드/검증 헬퍼 (api-spec §2.3, RS256 + JWKS 확정 · v0.10.0 스트림 티켓 · #34 실배선).

인증 모드 2종:
  - "dev"  : 서명 검증 없이 디코드. 헤더 없으면 게스트로 취급.
             (로컬 개발 편의 — Spring 토큰 없이도 동작)
  - "jwks" : Spring 이 서빙하는 GET /.well-known/jwks.json 공개키로 RS256 검증.
             토큰 헤더의 kid → 공개키 매핑. 검증 항목은 §2.3 확정 5종 —
             signature / exp / iss / aud / scope. kid miss 시에만 JWKS refetch
             (PyJWKClient 내장: 캐시 miss → 1회 재조회 후 재시도).

[변경] 기존 "secret"(HS256 공유 시크릿) 모드는 제거했다 — Spring 이 RS256+JWKS 로 확정.

스트림 티켓 클레임 (§2.3 v0.10.0):
  - sub      : 사용자 식별자 (회원/판매자=숫자 문자열, 게스트=UUID, §2.6)
  - sub_type : member | guest — 티켓 정본 클레임. 그 외 값은 fail-closed 거부.
  - scope    : 용도 검증 (제안값 chat:stream — 값은 config 주입, C-1 확정 대기)
  - role     : 구 클레임 폴백 + 판매자 판정(SELLER) — 판매자 티켓 형식은 🔴 C-1 잔여.
  - brandId  : 판매자(role=SELLER) 브랜드 id — {brandId} path용, 요청 본문 불신(§2.6).

[보안] 신원(user_id)·게스트 여부·판매자 스코프는 오직 토큰 클레임에서만 도출한다.
요청 본문의 식별자는 절대 신뢰하지 않는다 (사칭 방지, api-spec §2.3 a / §2.5 / §3.1 / §3.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

# 클레임 키
CLAIM_SUBJECT = "sub"
CLAIM_SUB_TYPE = "sub_type"
CLAIM_SCOPE = "scope"
CLAIM_ROLE = "role"
CLAIM_BRAND_ID = "brandId"

# sub_type 값 (§2.3 v0.10.0 확정 — member|guest 두 값만 정본)
SUB_TYPE_MEMBER = "member"
SUB_TYPE_GUEST = "guest"

# role 값 매핑 — TODO(C-1): 게스트/판매자 role 최종값을 Spring 회원 스키마 확정 시 반영.
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
    # subject: 검증된 raw `sub` 클레임 — 게스트 UUID 포함 모든 역할에 보존한다.
    # 레이트 리밋·동시성 레지스트리의 신원 스코프 키로 일관되게 쓴다(§2.8/§2.9).
    subject: str | None = None


class AuthError(Exception):
    """토큰 없음/무효. 라우터에서 401 TOKEN_INVALID 로 매핑한다 (api-spec §2.5)."""


class TokenExpiredError(AuthError):
    """토큰 exp 경과. 라우터에서 401 TOKEN_EXPIRED 로 매핑한다 (api-spec §2.3/§2.5).

    FE 는 이 코드를 받으면 CH-1b 재발급 후 원 요청을 1회 재시도한다 — 문자열 스니핑이
    아니라 예외 타입으로 구분해 매핑이 깨지지 않게 한다.
    """


def _norm_role(role: object) -> str | None:
    """role 클레임 정규화(대문자 비교용).

    api-spec §2.3 표기는 `role == "seller"`(소문자)인데 구 코드 상수는 대문자였고,
    실값 대소문자 형식은 🔴 C-1 잔여다. 값을 지어내지 않는 선에서 **대소문자 무관
    비교**로 두 표기를 모두 수용한다 (PR #39 리뷰 반영 — 소문자 발급 시 판매자
    전면 403 방지). C-1 확정 시 상수/비교를 실값으로 고정한다.
    """
    if not isinstance(role, str):
        return None
    normalized = role.strip().upper()
    # 빈/공백 문자열은 "role 없음"으로 취급 — "" 가 None 이 아니라서 fail-closed
    # 가드(require_identity_claim)를 우회해 회원으로 승인되는 구멍 방지 (리뷰 3R 반영).
    return normalized or None


def _claims_to_identity(claims: dict, *, require_identity_claim: bool = False) -> Identity:
    """검증된 클레임 dict → Identity 매핑.

    우선순위 (§2.3 v0.10.0):
      1. role == seller(대소문자 무관) → 판매자 (seller_id = sub, brand_id = brandId 클레임).
                                         판매자 티켓의 정확한 클레임 형식은 🔴 C-1 잔여.
      2. sub_type == member|guest      → 티켓 정본 클레임. 그 외 값은 fail-closed 거부.
      3. 구 role 폴백 (GUEST/USER 등)  → C-1 값 집합 확정 전 호환 유지(미지 role 은 회원 관용).

    require_identity_claim=True(jwks 실배선 레인)면 sub_type·role 이 **둘 다 없는**
    서명 유효 토큰을 거부한다 — §2.3 은 sub_type 을 티켓 필수 클레임으로 확정했고,
    신원 유형 클레임이 전무한 토큰을 회원으로 기본 승인하면 미지 sub_type 거부와
    방어 원칙이 어긋난다 (PR #39 리뷰 반영). dev 모드는 로컬 편의 레인이라 관용 유지.
    """
    subject = claims.get(CLAIM_SUBJECT)
    role = _norm_role(claims.get(CLAIM_ROLE))
    sub_type = claims.get(CLAIM_SUB_TYPE)

    if role == ROLE_SELLER:
        # 판매자는 sub 를 판매자 식별자로도 사용한다 (스코프 근거는 role 클레임).
        return Identity(
            user_id=subject,
            is_guest=False,
            seller_id=subject,
            brand_id=claims.get(CLAIM_BRAND_ID),
            subject=subject,
        )
    if sub_type is not None:
        if sub_type == SUB_TYPE_GUEST:
            return Identity(user_id=None, is_guest=True, seller_id=None, subject=subject)
        if sub_type == SUB_TYPE_MEMBER:
            return Identity(user_id=subject, is_guest=False, seller_id=None, subject=subject)
        # 미지 sub_type — 정본 값 집합(member|guest) 밖은 신원 판정 불가로 거부.
        raise AuthError(f"unknown sub_type: {sub_type}")
    if role == ROLE_GUEST:
        return Identity(user_id=None, is_guest=True, seller_id=None, subject=subject)
    if role is None and require_identity_claim:
        # 신원 유형 클레임(sub_type·role) 전무 — 실배선 레인은 회원 기본 승인 금지.
        raise AuthError("missing sub_type/role claim")
    # 구 role 폴백: 값 집합이 C-1 미확정이라 미지 role(USER 등)은 회원으로 관용 —
    # sub 는 서명 검증을 통과했고, 회원 role 실값이 달라도 전면 401 이 되지 않게 한다.
    return Identity(user_id=subject, is_guest=False, seller_id=None, subject=subject)


def _verify_scope(claims: dict, required: str) -> None:
    """scope 클레임 검증 (§2.3 확정 검증 항목 — 토큰 용도 혼용 방지).

    발급측 표현 관용: 공백 구분 문자열(OAuth 관례) 또는 리스트 모두 수용한다.
    """
    raw = claims.get(CLAIM_SCOPE)
    if isinstance(raw, str):
        granted = set(raw.split())
    elif isinstance(raw, (list, tuple)):
        granted = {str(item) for item in raw}
    else:
        granted = set()
    if required not in granted:
        raise AuthError("missing or mismatched scope")


@lru_cache
def _jwk_client(
    jwks_url: str, timeout_s: float | None = None, cache_ttl_s: float | None = None
) -> PyJWKClient:
    """JWKS 클라이언트 캐시. kid→공개키 조회를 재사용한다 (요청마다 재페치 방지).

    - timeout_s: JWKS fetch HTTP 타임아웃 — AI→Spring 전 구간 3s 기준(§2.9 c) config 주입.
    - cache_ttl_s: tier-1 JWKS 캐시 TTL — 만료 전에는 kid miss 시에만 refetch(§2.3).
    미지정(None)이면 PyJWKClient 기본값을 쓴다 (dev 등 비주입 경로 호환).
    """
    kwargs: dict = {}
    if timeout_s is not None:
        kwargs["timeout"] = timeout_s
    if cache_ttl_s is not None:
        kwargs["lifespan"] = cache_ttl_s
    return PyJWKClient(jwks_url, **kwargs)


def decode_token(
    token: str | None,
    *,
    auth_mode: str,
    jwks_url: str | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    scope: str | None = None,
    jwks_timeout_s: float | None = None,
    jwks_cache_ttl_s: float | None = None,
) -> Identity:
    """Bearer 토큰을 인증 모드에 따라 디코드/검증하고 Identity 를 반환한다.

    dev 모드에서 토큰이 없으면 게스트 Identity 를 돌려준다 (헤더 없는 로컬 호출 편의).
    그 외에는 토큰이 없거나 검증 실패 시 AuthError (만료는 TokenExpiredError).

    jwks 모드 검증 항목(§2.3 확정): signature / exp / iss / aud / scope.
    scope=None 이면 scope 검증을 생략한다 (issuer/audience=None 과 같은 규칙 —
    전환기 호환, 운영값은 config jwt_scope 주입).
    """
    if auth_mode == "dev":
        if not token:
            # dev 전용 편의: 헤더 없으면 게스트로 취급. subject 는 의도적으로 None 이다 —
            # 무토큰 익명은 식별 근거가 없어 registry_key owner 가 "anon" 으로 공유된다.
            # (프로덕션 jwks 모드는 무토큰이 401 이라 이 경로에 도달하지 않고, 실제 게스트는
            #  익명 JWT 의 sub 로 개별 스코프된다.) 여기에 요청마다 고유 subject 를 부여하면
            # 세션당 1스트림 제한(§2.9 a)이 익명에서 무력화되므로 그렇게 하지 않는다.
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
            client = _jwk_client(jwks_url, jwks_timeout_s, jwks_cache_ttl_s)
            # kid→공개키 매칭. kid 가 캐시된 JWKS 에 없으면 PyJWKClient 가 1회 refetch
            # 후 재시도한다(§2.3 "kid miss 시에만 refetch"). JWKS 도달 불가/kid 부재는
            # PyJWKClientError(PyJWTError 하위) → AuthError → 401 (fail-closed).
            signing_key = client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=audience,
                options={
                    # sub 부재 시 신원 도출 자체가 불가 — 필수 클레임(§2.3)로 강제.
                    "require": ["exp", "sub"],
                    "verify_iss": issuer is not None,
                    "verify_aud": audience is not None,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc
        if scope is not None:
            _verify_scope(claims, scope)
        # 실배선 레인 — 신원 유형 클레임(sub_type·role) 전무 토큰은 fail-closed.
        return _claims_to_identity(claims, require_identity_claim=True)

    raise AuthError(f"unknown auth_mode: {auth_mode}")
