"""FastAPI 인증 의존성 (api-spec §2.2, RS256 + JWKS 확정 2026-07-15).

사용자 대면 API(/chat·/seller/chat)는 사용자 JWT 를 검증해 Identity 를 만든다.

[I-20] /events/session-end는 MVP inbound 채널로 유지하며 X-Internal-Token을 검증한다.

[보안] Identity 는 오직 토큰에서 도출된다 — 요청 본문의 식별자는 신뢰하지 않는다.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.auth import AuthError, Identity, TokenExpiredError, decode_token
from app.core.config import Settings, get_settings


def _extract_bearer(authorization: str | None) -> str | None:
    """`Authorization: Bearer <token>` 헤더에서 토큰만 추출한다."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def get_identity(authorization: str | None = Header(default=None)) -> Identity:
    """사용자 JWT → Identity 의존성.

    dev 모드에서 헤더가 없으면 게스트 Identity 를 반환한다 (core.auth 참고).
    무효/만료 토큰은 401 로 매핑한다 (api-spec §2.4).
    """
    settings: Settings = get_settings()
    token = _extract_bearer(authorization)
    try:
        return decode_token(
            token,
            auth_mode=settings.auth_mode,
            jwks_url=settings.jwks_url,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            scope=settings.jwt_scope,
            jwks_timeout_s=settings.spring_timeout_s,
            jwks_cache_ttl_s=settings.jwks_cache_ttl_s,
        )
    except TokenExpiredError as exc:
        # §2.5: 만료는 TOKEN_EXPIRED — FE 가 CH-1b 재발급 후 1회 재시도하는 신호.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TOKEN_EXPIRED", "message": "인증 실패"},
        ) from exc
    except AuthError as exc:
        # §2.5: 그 외(없음/서명·형식·scope 불일치)는 TOKEN_INVALID.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TOKEN_INVALID", "message": "인증 실패"},
        ) from exc


def require_seller(authorization: str | None = Header(default=None)) -> Identity:
    """판매자 스코프 필수 의존성 (api-spec §3.2).

    판매자 스코프(seller_id)가 없는 토큰의 /seller/chat 호출은 403 으로 거부한다.
    반환 Identity 의 brand_id(§4.4/§4.5 {brandId} path용)는 검증된 토큰 클레임 유래다.
    """
    identity = get_identity(authorization)
    if not identity.seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "seller scope required"},
        )
    if not identity.brand_id:
        # §2.3: 판매자 토큰엔 brandId 클레임 필수 — 없으면 판매자 역호출(§4.4/§4.5) 불가.
        # 요청 본문/발화로 우회하지 않도록 검증된 클레임 부재 시 거부한다(IDOR 방지).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "seller token missing brandId claim"},
        )
    return identity


def verify_service_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Spring → AI inbound(레인 b) 서비스 토큰 검증 (api-spec §3.5).

    config internal_api_token 이 설정돼 있으면 헤더 일치를 요구하고, 비어 있으면(dev) 허용한다.
    """
    settings: Settings = get_settings()
    # dev(로컬)만 미검증 편의 허용. 운영(jwks)은 inbound write 엔드포인트라 **fail-closed** —
    # 토큰 미설정·불일치 모두 401(프로필 오염 IDOR 방지, 리뷰 반영).
    if settings.auth_mode == "dev":
        return
    if not settings.internal_api_token or x_internal_token != settings.internal_api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INTERNAL_TOKEN_INVALID", "message": "서비스 토큰 필요/불일치"},
        )
