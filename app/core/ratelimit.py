"""레이트 리밋 (api-spec §2.8) — 토큰 sub 스코프 인메모리 카운터 + IP 백스톱 + 미들웨어.

목적은 정밀 과금이 아니라 **무분별한 남용 차단**(2026-07-15 확정). MVP 소유 =
FastAPI 미들웨어 + in-memory(단일 인스턴스 전제 — 다중 인스턴스 확장 시 Redis 이관).
채팅 메시지(POST /chat·/seller/chat)에 상한(config)을 적용하고 초과 시 429 RATE_LIMITED
(§2.5 봉투)로 거절한다.

스코프 = 토큰 `sub`(§2.8). sub 는 **서명 검증을 통과한** 토큰에서만 얻는다(위조 sub 로
피해자 버킷을 고갈시키는 표적 DoS 방지). 검증의 동기 JWKS HTTP 는 run_in_threadpool 로
오프로드해 이벤트 루프 블로킹을 피한다. 미검증/무토큰과 sub 회전 우회를 막기 위해 **IP
(호스트) 백스톱 상한을 항상 병행** 적용한다(배수는 NAT 오탐을 줄이려 관대하게).
"""

from __future__ import annotations

import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.auth import AuthError, decode_token
from app.core.config import get_settings
from app.core.errors import REQUEST_ID_HEADER, error_envelope, get_request_id
from app.core.observability import emit_rejection
from app.core.logging import get_logger

logger = get_logger(__name__)

# 레이트 리밋 대상 경로 (채팅 메시지 전송만). 조회성 GET 은 제외.
_LIMITED_PATHS = frozenset({"/chat", "/seller/chat"})


class SlidingWindowLimiter:
    """스코프별 sliding-window 카운터. allow() 호출마다 분/시간 상한을 검사한다.
    상한은 호출 인자로 받아 스코프별(sub vs IP 백스톱)로 다른 값을 적용할 수 있다.

    키가 매 요청 새로 생기는 경우(IP/토큰 회전) 재접근이 없어 개별 trim 이 안 도므로,
    주기적 전역 스윕으로 만료(1시간 경과) 키를 제거해 메모리 무한 증가를 막는다."""

    _SWEEP_INTERVAL = 300.0  # 5분마다 만료 키 전역 정리

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = 0.0

    def allow(self, key: str, now: float, per_min: int, per_hour: int) -> bool:
        """호출 1건을 기록·판정한다. 상한 초과면 기록하지 않고 False."""
        self._maybe_sweep(now)
        hits = self._hits.setdefault(key, deque())
        hour_ago = now - 3600.0
        while hits and hits[0] <= hour_ago:
            hits.popleft()
        minute_hits = sum(1 for t in hits if t > now - 60.0)
        if len(hits) >= per_hour or minute_hits >= per_min:
            return False
        hits.append(now)
        return True

    def _maybe_sweep(self, now: float) -> None:
        """만료 키 전역 정리 — 최소 _SWEEP_INTERVAL 간격으로만 수행(비용 상각)."""
        if now - self._last_sweep < self._SWEEP_INTERVAL:
            return
        self._last_sweep = now
        hour_ago = now - 3600.0
        stale = []
        for key, hits in self._hits.items():
            while hits and hits[0] <= hour_ago:
                hits.popleft()
            if not hits:
                stale.append(key)
        for key in stale:
            del self._hits[key]


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _host(request: Request) -> str:
    """클라이언트 IP. 신뢰 프록시 뒤(config)에서는 X-Forwarded-For 의 **최우측 신뢰 홉**을 쓴다.

    append 형 프록시($proxy_add_x_forwarded_for)는 자사 프록시가 관측한 IP 를 우측에 붙이고,
    좌측은 클라이언트가 임의로 채울 수 있다. 따라서 우측에서 forwarded_for_trusted_hops 만큼
    센 위치를 취한다(최좌측을 쓰면 공격자가 앞부분을 회전시켜 IP 백스톱을 우회함). 직접 노출
    배포(trust_forwarded_for=False)에서는 XFF 를 신뢰하지 않고 TCP peer 를 쓴다."""
    settings = get_settings()
    if settings.trust_forwarded_for:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            hops = max(1, settings.forwarded_for_trusted_hops)
            if len(parts) >= hops:
                return parts[-hops]  # 우측에서 신뢰 홉 수만큼 센 값 = 자사 프록시 관측 IP
    return request.client.host if request.client else "unknown"


async def _verified_sub_scope(request: Request) -> str | None:
    """토큰 `sub` 스코프 키. **서명 검증을 통과한** 토큰의 subject 만 sub 스코프로 쓴다.

    서명 미검증 sub 을 키로 쓰면 공격자가 `{"sub": "<피해자 id>"}` 위조 토큰을 반복 전송해
    피해자의 sub 버킷을 고갈시키는 표적 DoS 가 가능하다(회원/판매자 id 는 순차 BIGINT).
    따라서 실제 인증 경로(decode_token)로 검증하되, jwks 의 동기 HTTP 가 이벤트 루프를
    블로킹하지 않도록 run_in_threadpool 로 오프로드한다. 위조/무효 토큰은 None → IP 백스톱만.
    """
    token = _extract_bearer(request.headers.get("authorization"))
    if not token:
        return None
    settings = get_settings()
    try:
        # deps.get_identity 와 동일 검증 항목(scope 포함)으로 단일 경로 유지 —
        # 용도 불일치 토큰이 sub 버킷을 얻지 못하게 한다(§2.3/§2.8).
        identity = await run_in_threadpool(
            decode_token,
            token,
            auth_mode=settings.auth_mode,
            jwks_url=settings.jwks_url,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            scope=settings.jwt_scope,
            jwks_timeout_s=settings.spring_timeout_s,
            jwks_cache_ttl_s=settings.jwks_cache_ttl_s,
        )
    except AuthError:
        return None  # 서명/만료 검증 실패 → sub 스코프 불가(피해자 버킷 오염 차단).
    return f"sub:{identity.subject}" if identity.subject else None


_limiter: SlidingWindowLimiter | None = None


def _get_limiter() -> SlidingWindowLimiter:
    global _limiter
    if _limiter is None:
        _limiter = SlidingWindowLimiter()
    return _limiter


async def rate_limit_middleware(request: Request, call_next):
    """채팅 전송 경로에 토큰 sub 스코프 + IP 백스톱 레이트 리밋을 적용한다."""
    if request.method == "POST" and request.url.path in _LIMITED_PATHS:
        settings = get_settings()
        limiter = _get_limiter()
        now = time.monotonic()
        ip_key = f"ip:{_host(request)}"
        sub_key = await _verified_sub_scope(request)

        # sub 스코프(있으면) 상한 + IP 백스톱 상한(회전/위조 토큰 우회 차단)을 함께 본다.
        over = False
        if sub_key is not None:
            over = not limiter.allow(
                sub_key, now, settings.rate_limit_per_min, settings.rate_limit_per_hour
            )
        if not over:
            mult = settings.rate_limit_host_multiplier
            over = not limiter.allow(
                ip_key, now, settings.rate_limit_per_min * mult, settings.rate_limit_per_hour * mult
            )

        if over:
            rid = get_request_id(request)
            scope = sub_key or ip_key
            emit_rejection(rid, "RATE_LIMITED", scope=scope, path=request.url.path)
            return JSONResponse(
                status_code=429,
                content=error_envelope("RATE_LIMITED", "요청이 너무 많습니다", rid),
                headers={REQUEST_ID_HEADER: rid},
            )
    return await call_next(request)


def reset_limiter() -> None:
    """테스트용 — 리미터 상태 초기화."""
    global _limiter
    _limiter = None
