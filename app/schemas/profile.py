"""프로필 스키마 (SPEC-PROFILE-001, api-spec §3.4/§3.5).

GET /profile/me 응답(마이페이지 자연어 마크다운 passthrough)과 POST /events/session-end 수신
페이로드. 와이어 포맷 camelCase (CamelModel by_alias). session-end 필드는 AI 소유 inbound
계약(결정 21) — v0.15.15에서 BE 실측 payload로 확정(이슈 #62).
"""

from __future__ import annotations

from pydantic import Field, field_validator

from app.schemas.chat import CamelModel

_BIGINT_MAX = 2**63 - 1  # PostgreSQL BIGINT 상한 — 신원 id 범위 방어


class ProfileView(CamelModel):
    """GET /profile/me 응답 (§3.4). 게스트·신규는 exists=false·markdown=null 정상 200."""

    user_id: str
    exists: bool
    markdown: str | None = None
    generated_at: str | None = None  # ISO-8601, 요약 생성 시각


class SessionEndEvent(CamelModel):
    """POST /events/session-end 수신 (§3.5, I-20). best-effort·멱등(userId+sessionId 파생키).

    [v0.15.15, 이슈 #62] BE 실측 payload 정렬 — 구 초안의 eventId·endedAt 제거, userId 를
    number(BIGINT)로 정정. 멱등키는 별도 필드 없이 (userId, sessionId)에서 파생(§2.7).
    reason: logout | tabClose | inactivityTimeout | newConversation 등 — enum 미강제(방어적 수용).
    """

    # 세션 소유 회원 id(BIGINT, JWT sub 와 동종) — 프로필 스코프·멱등키 요소.
    # 양의 BIGINT 범위로 제한해 int 전환으로 사라진 키 남용 방어(구 길이 상한)를 유지한다.
    user_id: int = Field(gt=0, le=_BIGINT_MAX)
    # 종료된 세션 식별자(멱등키·세션 버퍼 키의 필수 요소) — 빈 문자열 거부(§3.5 essential):
    # 빈 값은 conversation_key/dedup_key 를 퇴화시키고, 최대 길이는 아래 validator 가 강제.
    session_id: str = Field(min_length=1)
    reason: str | None = None

    @field_validator("session_id")
    @classmethod
    def _limit_key_length(cls, v: str) -> str:
        """세션 식별자 길이 상한(config) — ProfileStore 딕셔너리 키 남용 방어(ChatRequest 와 동일 패턴)."""
        from app.core.config import get_settings

        cap = get_settings().chat_key_max_chars
        if len(v) > cap:
            raise ValueError(f"identifier exceeds {cap} characters")
        return v
