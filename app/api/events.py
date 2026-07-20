"""이벤트 수신 엔드포인트 — POST /events/session-end (I-20, api-spec §3.5).

Spring → AI inbound(우리가 호스팅). 세션 종료 통지를 프로필 파이프라인 조기 트리거로 받는다
(결정 12/16). best-effort·멱등(eventId, §2.7) — 유실돼도 다음 sleep-time 배치가 회수.
서비스 토큰(레인 b) 검증. catalog/order 이벤트는 영구 미채택(§3.6).
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, Depends

from app.agents.profile import processed_events
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
    # [신뢰경계] session-end 는 Spring→AI(레인 b) — 신원(userId/sessionId)은 §3.5 계약상 본문으로
    # 오며, 호출 인가는 **서비스 토큰**(verify_service_token)이 담당한다(Spring 은 인증된 호출자).
    # 필드 길이 상한은 SessionEndEvent validator 가 강제(스토어 키 남용 방어).
    # best-effort 프로필 갱신 — LLM 미구성/버퍼 없음/오류는 no-op degrade. 어떤 오류도 202 를 막지 않는다(§3.5).
    key = conversation_key(event.user_id, event.session_id)
    settings = get_settings()
    llm = get_llm()
    ran = False
    watermark = 0
    marked = False
    try:
        # 원자적 마킹(processed_events — UNIQUE 제약 INSERT ON CONFLICT) — 짧은 간격 재전송
        # (at-least-once)이 둘 다 통과해 중복 처리되는 레이스 차단(이슈 #33).
        marked = await processed_events.mark_if_new(event.event_id)
        if not marked:
            return {"status": "duplicate"}  # 멱등 — 중복 수신 무시(§2.7)

        # get_profile_store()/clear_session_ctx_upto 도 pg-profile 연결이 필요해 실패할 수
        # 있다(운영은 폴백 없이 raise) — try 밖에 두면 일시적 DB 장애만으로 500 이 나가
        # §3.5(항상 202) 계약을 어긴다(PR #47 후속 리뷰).
        store = await get_profile_store()
        # generate 반환: None=degrade(버퍼 보존), tuple=LLM 정상 실행(게이트 반려로 빈 목록이어도 처리됨).
        result = await generate_session_delta(event.user_id, key, llm=llm, settings=settings)
        if result is not None:
            _, watermark = result
            await consolidate(event.user_id, llm=llm, settings=settings)
            ran = True
        if ran:
            # LLM 이 실제 처리한 경우에만, 그 스냅샷 워터마크 이하만 버퍼 정리(정상 반려 포함) — LLM 호출
            # 중 새로 추가된 항목까지 통째로 삭제되지 않게 한다(cap 트리밍 상황에서도 seq 기준이라 안전).
            await store.clear_session_ctx_upto(key, watermark)
    except Exception:  # noqa: BLE001 — best-effort inbound 통지(§3.5): 절대 500 금지. 실패 시 버퍼 보존.
        ran = False

    if not ran and marked:
        # degrade/오류 → 마킹 해제(멱등은 성공에만) + 버퍼 보존 → 재전송·다음 배치가 재처리(REQ-PROF-050/051).
        # unmark 자체도 pg-profile 호출이라 실패할 수 있다 — 이마저 실패해도 202 는 나가야 한다.
        with contextlib.suppress(Exception):
            await processed_events.unmark_event(event.event_id)
    return {"status": "accepted"}
