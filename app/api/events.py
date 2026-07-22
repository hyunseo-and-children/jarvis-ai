"""이벤트 수신 엔드포인트 — POST /events/session-end (I-20, api-spec §3.5).

Spring → AI inbound(우리가 호스팅). 세션 종료 통지를 프로필 파이프라인 조기 트리거로 받는다
(결정 12/16). best-effort·멱등((userId, sessionId) 고정키, §2.7) — 유실돼도 다음 sleep-time 배치가 회수.
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
    # sessionId 길이·userId(BIGINT) 범위 상한은 SessionEndEvent 가 강제(스토어 키 남용 방어).
    # best-effort 프로필 갱신 — LLM 미구성/버퍼 없음/오류는 no-op degrade. 어떤 오류도 202 를 막지 않는다(§3.5).
    # store/builder 는 문자열 신원 키를 쓰므로 int userId 를 문자열화(JWT sub·conversation_key 와 정합).
    user_id = str(event.user_id)
    key = conversation_key(user_id, event.session_id)
    # 멱등키 = (userId, sessionId) 고정키. Spring 이 쏘는 종료(NEW_CONVERSATION·LOGOUT)는 모두 세션을
    # 삭제하므로 "하나의 sessionId = 하나의 논리적 종료" 가 성립한다(BE 실측: tabClose·idle 은 미발화).
    # 같은 (userId, sessionId) 재전송(at-least-once)만 중복 처리하고, 세션당 한 번만 승격한다.
    dedup_key = f"session-end:{user_id}:{event.session_id}"
    ran = False
    marked = False
    try:
        # 원자적 마킹(processed_events — UNIQUE 제약 INSERT ON CONFLICT)은 버퍼 조회보다 먼저 한다.
        # 이미 정상 수신한 종료 통지는 첫 처리 뒤 버퍼가 비어 있어도 반드시 duplicate 로 응답해야 한다.
        marked = await processed_events.mark_if_new(dedup_key)
        if not marked:
            return {"status": "duplicate"}  # 멱등 — 같은 세션 종료 재수신 무시(§2.7)

        # get_profile_store()/get_session_ctx_snapshot/clear 도 pg-profile 연결이 필요해 실패할 수
        # 있다(운영은 폴백 없이 raise) — try 밖에 두면 일시적 DB 장애만으로 500 이 나가
        # §3.5(항상 202) 계약을 어긴다(PR #47 후속 리뷰).
        store = await get_profile_store()

        # 저장할 새 내용(버퍼)이 없으면 정상 no-op 으로 수락한다. 마킹은 유지하므로 같은 통지의
        # 재전송은 duplicate 가 된다(검증 → 멱등 판정 → 버퍼 처리 순서, §3.5).
        buffer, _ = await store.get_session_ctx_snapshot(key)
        if not buffer:
            return {"status": "accepted"}

        # generate 반환: None=degrade(버퍼 보존), tuple=LLM 정상 실행(게이트 반려로 빈 목록이어도 처리됨).
        settings = get_settings()
        llm = get_llm()
        result = await generate_session_delta(user_id, key, llm=llm, settings=settings)
        if result is not None:
            _, watermark = result
            await consolidate(user_id, llm=llm, settings=settings)
            # 스냅샷 워터마크 이하만 버퍼 정리(정상 반려 포함) — LLM 호출 중 새로 추가된 항목까지
            # 통째로 삭제되지 않게 한다(cap 트리밍 상황에서도 seq 기준이라 안전).
            await store.clear_session_ctx_upto(key, watermark)
            ran = True
    except Exception:  # noqa: BLE001 — best-effort inbound 통지(§3.5): 절대 500 금지. 실패 시 버퍼 보존.
        ran = False

    if not ran and marked:
        # degrade/오류 → 마킹 해제(멱등은 성공에만) + 버퍼 보존 → 재전송·다음 배치가 재처리(REQ-PROF-050/051).
        # unmark 자체도 pg-profile 호출이라 실패할 수 있다 — 이마저 실패해도 202 는 나가야 한다.
        with contextlib.suppress(Exception):
            await processed_events.unmark_event(dedup_key)
    return {"status": "accepted"}
