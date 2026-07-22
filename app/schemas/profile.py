"""프로필 스키마 (SPEC-PROFILE-001, api-spec §3.4/§3.5).

GET /profile/me 응답(마이페이지 자연어 마크다운 passthrough)과 POST /events/session-end 수신
페이로드. 와이어 포맷 camelCase (CamelModel by_alias). session-end 필드는 AI 소유 inbound
계약(결정 21) — v0.15.17에서 BE 실측 payload로 확정(이슈 #62).
"""

from __future__ import annotations

from pydantic import Field, field_validator

from app.schemas.chat import CamelModel

_BIGINT_MAX = 2**63 - 1  # PostgreSQL BIGINT 상한 — 신원 id 범위 방어
_SESSION_END_REASON_MAX_CHARS = 64  # 알려진 enum 이름은 최대 17자; 확장 허용 + inbound 남용 방어


class ProfileView(CamelModel):
    """GET /profile/me 응답 (§3.4). 게스트·신규는 exists=false·markdown=null 정상 200."""

    user_id: str
    exists: bool
    markdown: str | None = None
    generated_at: str | None = None  # ISO-8601, 요약 생성 시각


class SessionEndEvent(CamelModel):
    """POST /events/session-end 수신 (§3.5, I-20). best-effort·멱등((userId, sessionId) 고정키).

    [v0.15.17, 이슈 #62] BE 실측 payload 정렬 — 구 초안의 eventId·endedAt 제거, userId 를
    number(BIGINT)로 정정. 멱등키는 `session-end:{userId}:{sessionId}` 고정키(app/api/events.py) —
    Spring 이 쏘는 종료(NEW_CONVERSATION·LOGOUT)는 모두 세션을 삭제하므로 "하나의 sessionId = 하나의
    논리적 종료"가 성립한다. 같은 (userId, sessionId) 재전송만 중복 처리한다. Spring의 알려진 reason은
    logout | newConversation이며 enum은 강제하지 않고 최대 64자로 제한한다. inactivity timeout은 AI
    내부 스케줄러가 같은 finalizer로 처리하며 tabClose wire 신호는 사용하지 않는다.
    """

    # 세션 소유 회원 id(BIGINT, JWT sub 와 동종) — 프로필 스코프·멱등키 요소.
    # 양의 BIGINT 정수로 엄격히 제한해 문자열·실수·bool coercion과 범위 밖 키를 거부한다.
    user_id: int = Field(strict=True, gt=0, le=_BIGINT_MAX)
    # 종료된 세션 식별자(멱등키·세션 버퍼 키의 필수 요소) — 빈 문자열 거부(§3.5 essential):
    # 빈 값은 conversation_key/dedup_key 를 퇴화시키고, 최대 길이는 아래 validator 가 강제.
    session_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=_SESSION_END_REASON_MAX_CHARS)

    @field_validator("session_id")
    @classmethod
    def _limit_key_length(cls, v: str) -> str:
        """세션 식별자 길이 상한(config) — ProfileStore 딕셔너리 키 남용 방어(ChatRequest 와 동일 패턴)."""
        from app.core.config import get_settings

        cap = get_settings().chat_key_max_chars
        if len(v) > cap:
            raise ValueError(f"identifier exceeds {cap} characters")
        return v
