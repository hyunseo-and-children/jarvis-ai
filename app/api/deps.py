"""FastAPI 인증 의존성 (api-spec §2.2, RS256 + JWKS 확정 2026-07-15).

사용자 대면 API(/chat·/seller/chat)는 사용자 JWT 를 검증해 Identity 를 만든다.

[변경] /events/* 서비스 토큰 의존성은 이벤트 채널이 고도화(post-MVP)로 이동해 제거했다.

[보안] Identity 는 오직 토큰에서 도출된다 — 요청 본문의 식별자는 신뢰하지 않는다.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.auth import AuthError, Identity, decode_token
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
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": str(exc)},
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
    return identity
