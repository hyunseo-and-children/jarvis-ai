"""FastAPI 인증 의존성 (api-spec §2.2 + REALIGN-SELLER-20260719 D1).

구매자 대면 API(/chat)는 사용자 JWT 를 검증해 Identity 를 만든다.

[변경 2026-07-19, REALIGN D1/F1] 판매자 챗은 FE 직접 호출이 아니라 **Spring 패스스루**
(아키텍처 확정 — nginx 는 fastapi 미노출, S-4 를 Spring 이 호출하고 SSE 를 그대로 통과)다.
판매자 레인 인증 = X-Internal-Token 서비스 토큰 + Spring 이 검증해 주입하는 메아리 신원
(require_seller_internal). 구 require_seller(RS256 티켓)는 미사용 전환 — 제거하지 않고 남긴다.

[변경] /events/* 서비스 토큰 의존성은 이벤트 채널이 고도화(post-MVP)로 이동해 제거했다.

[보안] 구매자 Identity 는 오직 토큰에서 도출된다 — 요청 본문의 식별자는 신뢰하지 않는다.
판매자 레인의 신뢰 주체는 Spring 서비스 토큰이며, 신원은 Spring 주입 헤더가 근거다
(사용자 주장값이 아니라 Spring 이 JWT 검증을 마친 값의 메아리 — IDOR 원칙 유지).
"""

from __future__ import annotations

import logging
import secrets

from fastapi import Header, HTTPException, status

from app.core.auth import AuthError, Identity, decode_token
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# dev 모드(service_token 미설정) 검증 스킵 경고를 프로세스당 1회만 남기기 위한 플래그.
_dev_skip_warned = False


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
        # §2.5: 401 코드는 TOKEN_EXPIRED / TOKEN_INVALID 2종. 만료는 메시지로 구분.
        code = "TOKEN_EXPIRED" if "expired" in str(exc).lower() else "TOKEN_INVALID"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": code, "message": "인증 실패"},
        ) from exc


def require_seller_internal(
    x_internal_token: str | None = Header(default=None),
    x_seller_id: str | None = Header(default=None),
    x_brand_id: str | None = Header(default=None),
) -> Identity:
    """판매자 챗 Spring 패스스루 인증 의존성 (REALIGN-SELLER-20260719 D1).

    Spring 이 S-4({AI_SERVER}/seller/chat)를 호출할 때:
      1. X-Internal-Token — settings.service_token 과 상수시간 비교.
         결손/불일치 = 401 UNAUTHORIZED. 미설정(dev)이면 스킵 + warning 1회.
      2. X-Seller-Id / X-Brand-Id — Spring 이 판매자 JWT 검증을 마치고 주입하는
         메아리 신원 (🔴 헤더명은 AI측 제안 — BE 확정 시 이 함수 한 곳만 수정).
         결손 = 400 (권한 문제가 아니라 Spring 호출 오류이므로 403 아님).

    반환 Identity 는 기존 데이터클래스를 재사용한다(하류 SellerContext 무변경).
    """
    global _dev_skip_warned
    settings: Settings = get_settings()

    if settings.service_token:
        if not x_internal_token or not secrets.compare_digest(
            x_internal_token, settings.service_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHORIZED", "message": "invalid service token"},
            )
    elif not _dev_skip_warned:
        logger.warning(
            "SERVICE_TOKEN 미설정 — 판매자 internal 인증을 건너뜁니다 (dev 전용, 운영 금지)"
        )
        _dev_skip_warned = True

    if not x_seller_id or not x_brand_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BAD_REQUEST",
                "message": "X-Seller-Id / X-Brand-Id headers required",
            },
        )
    return Identity(user_id=None, is_guest=False, seller_id=x_seller_id, brand_id=x_brand_id)


def require_seller(authorization: str | None = Header(default=None)) -> Identity:
    """[미사용 전환 2026-07-19] 구 FE 직접 호출 레인의 판매자 티켓 의존성.

    판매자 스코프(seller_id)가 없는 토큰의 /seller/chat 호출은 403 으로 거부한다.
    반환 Identity 의 brand_id(§4.4/§4.5 {brandId} path용)는 검증된 토큰 클레임 유래다.

    REALIGN-SELLER-20260719 D1/F1 로 판매자 챗이 Spring 패스스루로 확정되어
    /seller/chat 은 require_seller_internal 을 쓴다. 이 함수는 제거하지 않고
    남긴다(구매자 티켓 인증 코드와 같은 계보 — 계약 재변경 대비).
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
