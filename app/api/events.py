"""이벤트 수신 엔드포인트 — POST /events/session-end (I-20, api-spec §3.5).

Spring → AI inbound(우리가 호스팅). 세션 종료 통지를 프로필 파이프라인 조기 트리거로 받는다
(결정 12/16). best-effort·멱등((userId, sessionId) 고정키, §2.7) — 유실돼도 AI inactivity sweep이 회수.
서비스 토큰(레인 b) 검증. catalog/order 이벤트는 영구 미채택(§3.6).
"""

from __future__ import annotations

import asyncio  # noqa: F401 - 기존 내부 helper 테스트가 module-level monkeypatch에 사용
import logging

from fastapi import APIRouter, Depends

from app.agents.profile import processed_events  # noqa: F401 - 기존 monkeypatch 경계 호환
from app.agents.profile import finalizer as profile_finalizer
from app.agents.profile.store import get_profile_store
from app.api.deps import verify_service_token
from app.core.config import get_settings
from app.core.llm import get_llm
from app.schemas.profile import SessionEndEvent

router = APIRouter(tags=["events"])
logger = logging.getLogger(__name__)


async def _release_claim_best_effort(event_id: str, token: str) -> None:
    """기존 테스트·내부 호출 호환 wrapper. 실제 lifecycle helper는 공통 finalizer 소유."""
    await profile_finalizer.release_processed_claim_best_effort(event_id, token, log=logger)


@router.post("/events/session-end", status_code=202)
async def session_end(event: SessionEndEvent, _token: None = Depends(verify_service_token)) -> dict:
    """세션 종료 → 프로필 델타 추출 + consolidation(best-effort·멱등, 202 Accepted)."""
    # [신뢰경계] session-end 는 Spring→AI(레인 b) — 신원(userId/sessionId)은 §3.5 계약상 본문으로
    # 오며, 호출 인가는 **서비스 토큰**(verify_service_token)이 담당한다(Spring 은 인증된 호출자).
    # sessionId 길이·userId(BIGINT) 범위 상한은 SessionEndEvent 가 강제(스토어 키 남용 방어).
    # best-effort 프로필 갱신 — LLM 미구성/버퍼 없음/오류는 no-op degrade. 어떤 오류도 202 를 막지 않는다(§3.5).
    # store/builder 는 문자열 신원 키를 쓰므로 int userId 를 문자열화(JWT sub·conversation_key 와 정합).
    result = await profile_finalizer.finalize_profile_session(
        event.user_id,
        event.session_id,
        settings=get_settings(),
        store_factory=get_profile_store,
        llm_factory=get_llm,
        log=logger,
    )
    # I-20 wire는 내부 retryable도 best-effort accepted로 강등한다. duplicate만 구분 노출한다.
    status = (
        "duplicate"
        if result.status is profile_finalizer.FinalizationStatus.DUPLICATE
        else "accepted"
    )
    return {"status": status}
