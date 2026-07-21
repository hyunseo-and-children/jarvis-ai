"""이벤트 수신 엔드포인트 — POST /events/session-end (I-20, api-spec §3.5).

Spring → AI inbound(우리가 호스팅). 세션 종료 통지를 프로필 파이프라인 조기 트리거로 받는다
(결정 12/16). best-effort·멱등(저장 대상 내용 파생키, §2.7) — 유실돼도 다음 sleep-time 배치가 회수.
서비스 토큰(레인 b) 검증. catalog/order 이벤트는 영구 미채택(§3.6).
"""

from __future__ import annotations

import contextlib
import hashlib

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


def session_end_dedup_key(user_id: str, session_id: str, buffer: list[str]) -> str:
    """session-end 멱등키 = (userId, sessionId, 저장 대상 버퍼 내용 해시).

    [PR #64] session-end 는 "세션 종료"가 아니라 프로필 "저장 체크포인트"라 한 sessionId 에
    여러 번 정당하게 온다(예: tabClose 저장 후 같은 세션이 살아남아 재활동 → inactivityTimeout
    재저장). (userId, sessionId)만으로 멱등을 잡으면 두 번째의 새 대화분을 재전송 중복으로
    오인해 씹는다. 버퍼가 비면 스토어가 item 을 삭제해 seq(워터마크)가 리셋되므로 seq 도 못 쓴다.
    대신 "이 통지가 저장할 내용" 자체의 해시를 판별자로 써, 같은 내용 재전송(at-least-once)만
    중복 처리하고 새 내용 체크포인트는 통과시킨다.

    항목 경계는 **길이 접두어**로 구분한다 — 단순 `"\\n".join` 은 발화에 줄바꿈이 들어가면
    ["a\\nb"] 와 ["a","b"] 가 같은 문자열이 돼 서로 다른 버퍼가 같은 해시를 내는 충돌이 생긴다
    (PR #64 리뷰). 각 항목 앞에 바이트 길이를 박아 경계를 모호하지 않게 만든다.
    """
    h = hashlib.sha256()
    for item in buffer:
        encoded = item.encode("utf-8")
        h.update(f"{len(encoded)}:".encode("ascii"))
        h.update(encoded)
    return f"session-end:{user_id}:{session_id}:{h.hexdigest()[:16]}"


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
    settings = get_settings()
    llm = get_llm()
    ran = False
    watermark = 0
    marked = False
    dedup_key = ""
    try:
        # get_profile_store()/get_session_ctx_snapshot/clear 도 pg-profile 연결이 필요해 실패할 수
        # 있다(운영은 폴백 없이 raise) — try 밖에 두면 일시적 DB 장애만으로 500 이 나가
        # §3.5(항상 202) 계약을 어긴다(PR #47 후속 리뷰).
        store = await get_profile_store()

        # 멱등키 = 이 통지가 저장할 "버퍼 내용"의 해시(session_end_dedup_key 참조, PR #64).
        # 버퍼가 비면 저장할 새 내용이 없으니 no-op(마킹도 남기지 않음).
        # (잔여: 스냅샷~generate 재스냅샷 사이 새 턴이 도착하면 해시가 어긋나 재전송이 중복 처리될
        #  수 있으나, add_fact dedup + watermark clear 로 데이터는 안전 — best-effort 경계, §2.7.)
        buffer, _ = await store.get_session_ctx_snapshot(key)
        if not buffer:
            return {"status": "accepted"}  # 저장할 새 내용 없음(버퍼 빔) — no-op
        dedup_key = session_end_dedup_key(user_id, event.session_id, buffer)

        # 원자적 마킹(processed_events — UNIQUE 제약 INSERT ON CONFLICT) — 짧은 간격 재전송
        # (at-least-once)이 둘 다 통과해 중복 처리되는 레이스 차단(이슈 #33).
        marked = await processed_events.mark_if_new(dedup_key)
        if not marked:
            return {"status": "duplicate"}  # 멱등 — 같은 내용(해시 동일) 재수신 무시(§2.7)

        # generate 반환: None=degrade(버퍼 보존), tuple=LLM 정상 실행(게이트 반려로 빈 목록이어도 처리됨).
        result = await generate_session_delta(user_id, key, llm=llm, settings=settings)
        if result is not None:
            _, watermark = result
            await consolidate(user_id, llm=llm, settings=settings)
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
            await processed_events.unmark_event(dedup_key)
    return {"status": "accepted"}
