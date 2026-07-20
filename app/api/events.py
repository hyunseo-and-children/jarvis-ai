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
    # 원자적 마킹(await 전) — 짧은 간격 재전송(at-least-once)이 둘 다 통과해 중복 처리되는 레이스 차단.
    if not store.mark_if_new(event.event_id):
        return {"status": "duplicate"}  # 멱등 — 중복 수신 무시(§2.7)

    # [신뢰경계] session-end 는 Spring→AI(레인 b) — 신원(userId/sessionId)은 §3.5 계약상 본문으로
    # 오며, 호출 인가는 **서비스 토큰**(verify_service_token)이 담당한다(Spring 은 인증된 호출자).
    # 필드 길이 상한은 SessionEndEvent validator 가 강제(스토어 키 남용 방어).
    # best-effort 프로필 갱신 — LLM 미구성/버퍼 없음/오류는 no-op degrade. 어떤 오류도 202 를 막지 않는다(§3.5).
    key = conversation_key(event.user_id, event.session_id)
    settings = get_settings()
    llm = get_llm()
    ran = False
    processed_count = 0
    try:
        # generate 반환: None=degrade(버퍼 보존), tuple=LLM 정상 실행(게이트 반려로 빈 목록이어도 처리됨).
        result = await generate_session_delta(event.user_id, key, llm=llm, settings=settings)
        if result is not None:
            _, processed_count = result
            await consolidate(event.user_id, llm=llm, settings=settings)
            ran = True
    except Exception:  # noqa: BLE001 — best-effort inbound 통지(§3.5): 절대 500 금지. 실패 시 버퍼 보존.
        ran = False

    if ran:
        # LLM 이 실제 처리한 경우에만, 그 스냅샷 길이만큼만 버퍼 정리(정상 반려 포함) — LLM 호출 중
        # 새로 추가된 항목까지 통째로 삭제되지 않게 한다.
        store.clear_session_ctx_upto(key, processed_count)
    else:
        # degrade/오류 → 마킹 해제(멱등은 성공에만) + 버퍼 보존 → 재전송·다음 배치가 재처리(REQ-PROF-050/051).
        store.unmark_event(event.event_id)
    return {"status": "accepted"}
