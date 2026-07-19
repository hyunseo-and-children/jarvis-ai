"""이벤트 수신 엔드포인트 — POST /events/session-end (I-20, api-spec §3.5).

Spring → AI inbound(우리가 호스팅). 세션 종료 통지를 프로필 파이프라인 조기 트리거로 받는다
(결정 12/16). best-effort·멱등(eventId, §2.7) — 유실돼도 다음 sleep-time 배치가 회수.
서비스 토큰(레인 b) 검증. catalog/order 이벤트는 영구 미채택(§3.6).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agents.profile.builder import consolidate, generate_session_delta
from app.agents.profile.store import get_profile_store
from app.api.deps import verify_service_token
from app.core.config import get_settings
from app.core.conversation import conversation_key
from app.core.llm import get_llm
from app.schemas.profile import SessionEndEvent

router = APIRouter(tags=["events"])


@router.post("/events/session-end", status_code=202)
async def session_end(event: SessionEndEvent, _token: None = Depends(verify_service_token)) -> dict:
    """세션 종료 → 프로필 델타 추출 + consolidation(best-effort·멱등, 202 Accepted)."""
    store = get_profile_store()
    if store.seen_event(event.event_id):
        return {"status": "duplicate"}  # 멱등 — 중복 수신 무시(§2.7)
    store.mark_event(event.event_id)

    # best-effort 프로필 갱신 — LLM 미구성/버퍼 없음이면 내부에서 no-op degrade(§3.5).
    key = conversation_key(event.user_id, event.session_id or "")
    settings = get_settings()
    llm = get_llm()
    await generate_session_delta(event.user_id, key, llm=llm, settings=settings)
    await consolidate(event.user_id, llm=llm, settings=settings)
    store.clear_session_ctx(key)
    return {"status": "accepted"}
