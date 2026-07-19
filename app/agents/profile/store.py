"""프로필 저장소 — 인메모리 placeholder (SPEC-PROFILE-001 §5.3).

프로덕션은 LangGraph PostgresStore(BaseStore) + pgvector semantic 인덱스로 이관한다
(네임스페이스 profile/facts/episodes, 셀프호스트 임베딩 1024차원). MVP 는 신원 스코프
인메모리로 동작만 재현한다 — 다른 스토어(ThreadFilterStore·CartStateStore)와 동일 패턴.

보관:
  - summary       : user_id → 압축 프로필 요약(markdown, generated_at) — reader·GET 소스
  - facts         : user_id → 승격된 장기 fact 목록(위키, 단순화)
  - session_ctx   : conversation_key(user:thread) → transient 후보 버퍼(승격 전, 격리)
  - processed     : 처리한 session-end eventId(멱등, §2.7)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProfileSummary:
    """압축 프로필 요약 (§5.1 3섹션 마크다운 + 생성 시각)."""

    markdown: str
    generated_at: str  # ISO-8601


class ProfileStore:
    """프로필 인메모리 placeholder (신원 스코프)."""

    def __init__(self) -> None:
        self._summary: dict[str, ProfileSummary] = {}
        self._facts: dict[str, list[str]] = {}
        self._session_ctx: dict[str, list[str]] = {}
        self._processed: set[str] = set()

    # ── 요약 (reader·GET·consolidation) ──
    def get_summary(self, user_id: str) -> ProfileSummary | None:
        return self._summary.get(user_id)

    def set_summary(self, user_id: str, markdown: str, generated_at: str) -> None:
        self._summary[user_id] = ProfileSummary(markdown=markdown, generated_at=generated_at)

    # ── 장기 fact (승격 결과·consolidation 입력) ──
    def get_facts(self, user_id: str) -> list[str]:
        return list(self._facts.get(user_id, []))

    def add_fact(self, user_id: str, fact: str, *, cap: int | None = None) -> None:
        if not fact:
            return
        facts = self._facts.setdefault(user_id, [])
        facts.append(fact)
        if cap and cap > 0 and len(facts) > cap:
            del facts[: len(facts) - cap]  # 최신 cap 개만 유지(recency-wins, 무제한 누적 방어)

    # ── transient 세션 버퍼 (승격 전 격리, REQ-PROF transient) ──
    def append_session_ctx(self, key: str, text: str, *, cap: int | None = None) -> None:
        if not text:
            return
        buf = self._session_ctx.setdefault(key, [])
        buf.append(text)
        if cap and cap > 0 and len(buf) > cap:
            del buf[: len(buf) - cap]  # 최신 cap 개만 유지(무제한 누적 방어)

    def get_session_ctx(self, key: str) -> list[str]:
        return list(self._session_ctx.get(key, []))

    def clear_session_ctx(self, key: str) -> None:
        self._session_ctx.pop(key, None)

    # ── 멱등 (session-end eventId) ──
    def seen_event(self, event_id: str) -> bool:
        return event_id in self._processed

    def mark_event(self, event_id: str) -> None:
        self._processed.add(event_id)

    def mark_if_new(self, event_id: str) -> bool:
        """미처리면 마킹하고 True, 이미 처리됐으면 False (원자적 check-and-set, 멱등 레이스 차단)."""
        if event_id in self._processed:
            return False
        self._processed.add(event_id)
        return True

    def unmark_event(self, event_id: str) -> None:
        """마킹 해제 — 처리 실패 시 재전송이 재처리 가능하게(멱등은 성공에만 적용)."""
        self._processed.discard(event_id)

    def clear(self) -> None:
        self._summary.clear()
        self._facts.clear()
        self._session_ctx.clear()
        self._processed.clear()


_store = ProfileStore()


def get_profile_store() -> ProfileStore:
    """프로필 스토어 싱글턴."""
    return _store


def reset_profile_store() -> None:
    """테스트 격리용."""
    _store.clear()
