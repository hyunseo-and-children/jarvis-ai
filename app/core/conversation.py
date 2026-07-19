"""대화 저장 (api-spec §6.3 a) — user/assistant 턴 + 상태 + 부분 텍스트 보존.

MVP 는 인메모리 스토어다. api-spec §6.3 이 명시한 LangGraph checkpointer(AI Postgres,
sessionId = thread 키)로의 이관은 그래프 연결(#2 이후) 시점에 **동일 인터페이스로** 교체한다
— 프로필 파이프라인의 세션 종료 스캔이 이 저장소를 원천으로 삼는다.
"""

from __future__ import annotations

import itertools
from collections import deque
from dataclasses import dataclass
from enum import Enum

from app.core.logging import get_logger


logger = get_logger(__name__)


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

    # 인메모리 안전 상한(placeholder) — Postgres checkpointer 이관 시 불필요. 초과 시 FIFO 축출.
    # [한계] 전역 FIFO라 한 사용자가 상한을 채우면 무관한 타 사용자의 확정 턴도 축출될 수 있다
    # (cross-tenant). Postgres 이관 전까지의 MVP 한계이며, 사용자/대화 단위 쿼터는 post-MVP.
    _MAX_TURNS = 5000

    def __init__(self) -> None:
        self._turns: dict[str, Turn] = {}
        self._by_conversation: dict[str, list[str]] = {}
        self._order: deque[str] = deque()
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
        self._order.append(turn_id)
        self._evict_if_needed()
        return turn_id

    def _evict_if_needed(self) -> None:
        """상한 초과 시 **확정된** 턴부터 축출(무제한 메모리 증가 방지).

        진행 중(PENDING) 턴은 건너뛴다 — 응답 중 축출되면 finalize 가 유실되기 때문.
        모두 PENDING인 병리적 경우엔 축출을 보류(상한 일시 초과, 곧 확정되며 해소)."""
        attempts = len(self._order)
        while len(self._turns) > self._MAX_TURNS and attempts > 0:
            attempts -= 1
            old_id = self._order.popleft()
            turn = self._turns.get(old_id)
            if turn is None:
                continue  # 이미 제거된 참조
            if turn.status is TurnStatus.PENDING:
                self._order.append(old_id)  # 진행 중 — 축출 보류, 뒤로 미룸
                continue
            self._turns.pop(old_id, None)
            ids = self._by_conversation.get(turn.conversation_id)
            if ids and old_id in ids:
                ids.remove(old_id)
                if not ids:
                    del self._by_conversation[turn.conversation_id]

    def finalize_assistant(self, turn_id: str, assistant_text: str, status: TurnStatus) -> None:
        """어시스턴트 응답을 상태와 함께 마감한다. FAILED/CANCELLED 도 부분 텍스트를 보존한다."""
        turn = self._turns.get(turn_id)
        if turn is None:
            # 축출됐거나 미지의 turn — 응답이 저장소에서 유실됨(관측 가능하게 경고).
            logger.warning("finalize on evicted/unknown turn_id=%s (assistant 응답 유실)", turn_id)
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
