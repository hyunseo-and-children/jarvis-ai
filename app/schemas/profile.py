"""프로필 스키마 (SPEC-PROFILE-001, api-spec §3.4/§3.5).

GET /profile/me 응답(마이페이지 자연어 마크다운 passthrough)과 POST /events/session-end 수신
페이로드. 와이어 포맷 camelCase (CamelModel by_alias). session-end 필드는 AI 소유 inbound
계약(결정 21)의 초안 — C-8 BE 확정 시 조정.
"""

from __future__ import annotations

from app.schemas.chat import CamelModel


class ProfileView(CamelModel):
    """GET /profile/me 응답 (§3.4). 게스트·신규는 exists=false·markdown=null 정상 200."""

    user_id: str
    exists: bool
    markdown: str | None = None
    generated_at: str | None = None  # ISO-8601, 요약 생성 시각


class SessionEndEvent(CamelModel):
    """POST /events/session-end 수신 (§3.5, I-20). best-effort·멱등(eventId).

    reason: logout | tabClose | inactivityTimeout | newConversation (C-8 초안, 방어적 수용).
    """

    event_id: str
    user_id: str
    session_id: str  # 세션 버퍼 키의 필수 요소(§3.5 예시도 값 채움)
    ended_at: str | None = None
    reason: str | None = None
