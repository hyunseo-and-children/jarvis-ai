"""대화 저장 (api-spec §6.3 a) — user/assistant 턴 + 상태 + 부분 텍스트 보존.

MVP 는 인메모리 스토어다. api-spec §6.3 이 명시한 LangGraph checkpointer(AI Postgres,
sessionId = thread 키)로의 이관은 그래프 연결(#2 이후) 시점에 **동일 인터페이스로** 교체한다
— 프로필 파이프라인의 세션 종료 스캔이 이 저장소를 원천으로 삼는다.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import Enum


class TurnStatus(str, Enum):
    """어시스턴트 응답 저장 상태 (api-spec §6.3 a). PENDING 은 user 저장 직후 초기값."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class Turn:
    """한 턴 = user 메시지 원문 + assistant 응답(부분 포함) + 상태."""

    turn_id: str
    conversation_id: str
    user_id: str | None
    role: str
    user_text: str
    assistant_text: str = ""
    status: TurnStatus = TurnStatus.PENDING


class ConversationStore:
    """인메모리 대화 저장소. conversationId(=sessionId) 별로 턴을 순서대로 보관한다."""

    def __init__(self) -> None:
        self._turns: dict[str, Turn] = {}
        self._by_conversation: dict[str, list[str]] = {}
        self._seq = itertools.count(1)

    def save_user_message(
        self, conversation_id: str, user_id: str | None, role: str, text: str
    ) -> str:
        """사용자 메시지 수신 즉시 저장(§6.3 a). turn_id 를 반환한다(assistant 마감에 사용)."""
        turn_id = f"turn-{next(self._seq)}"
        self._turns[turn_id] = Turn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            user_text=text,
        )
        self._by_conversation.setdefault(conversation_id, []).append(turn_id)
        return turn_id

    def finalize_assistant(self, turn_id: str, assistant_text: str, status: TurnStatus) -> None:
        """어시스턴트 응답을 상태와 함께 마감한다. FAILED/CANCELLED 도 부분 텍스트를 보존한다."""
        turn = self._turns.get(turn_id)
        if turn is None:
            return
        turn.assistant_text = assistant_text
        turn.status = status

    def get_turn(self, turn_id: str) -> Turn | None:
        return self._turns.get(turn_id)

    def turns_for(self, conversation_id: str) -> list[Turn]:
        return [self._turns[t] for t in self._by_conversation.get(conversation_id, [])]


_store = ConversationStore()


def conversation_key(subject: str | None, session_id: str) -> str:
    """대화 저장 키를 **신원에 스코프**한다(registry_key와 동일 IDOR 방지).

    session_id(요청 본문 유래)만으로 키잉하면 다른 신원이 같은 session_id 를 실어 한 대화에
    턴을 혼입시킬 수 있다(프로필 스캔 오염·히스토리 노출). subject(검증된 sub)를 접두어로 묶어
    사용자 간 대화 혼입을 막는다. 신원 없음(dev 무토큰)은 "anon".
    """
    return f"{subject or 'anon'}:{session_id}"


def get_conversation_store() -> ConversationStore:
    """대화 저장소 싱글턴."""
    return _store


def reset_store() -> None:
    """테스트용 — 저장소 초기화."""
    global _store
    _store = ConversationStore()
