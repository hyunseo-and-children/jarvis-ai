"""요청 단위 구조화 로그 + 대화 저장 브릿지 (api-spec §6.3 b).

스트림 수명주기(#1 open_stream)에 훅으로 붙어 first-token/전체 지연·모델/토큰·streamStatus·
errorType 를 요청당 1건의 구조화 로그로 남기고, 어시스턴트 응답(부분 포함)을 대화 저장소에
마감한다.

[PII] 사용자 message **원문은 로그에 남기지 않는다** — 길이·해시만 기록한다(§6.3 b).
원문은 대화 저장소(§6.3 a)에만 존재한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from app.core.auth import Identity
from app.core.conversation import ConversationStore, TurnStatus, conversation_key
from app.core.logging import get_logger

logger = get_logger("observability")


def message_fingerprint(text: str) -> tuple[int, str]:
    """PII 안전 지문 — (길이, sha256 앞 16자). 원문은 반환하지 않는다."""
    return len(text), hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def role_of(identity: Identity) -> str:
    """로그/저장용 역할 문자열."""
    if identity.seller_id:
        return "seller"
    if identity.is_guest:
        return "guest"
    return "member"


@dataclass
class ModelCall:
    """노드별 LLM 호출 기록 (그래프 연결 후 채워짐). 스텁 단계에선 비어 있다."""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class RequestObservation:
    """요청 1건의 관측 상태. open_stream 이 훅(on_first_token/record_frame/finish)을 호출한다."""

    request_id: str
    conversation_id: str
    user_id: str | None
    role: str
    store: ConversationStore
    turn_id: str
    message_length: int
    message_hash: str
    started: float
    first_token_at: float | None = None
    assistant_parts: list[str] = field(default_factory=list)
    model_calls: list[ModelCall] = field(default_factory=list)
    finished: bool = False

    def record_model_call(self, model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        """노드별 LLM 호출 기록(model·tokens). 그래프가 호출한다."""
        self.model_calls.append(ModelCall(model, prompt_tokens, completion_tokens))

    def on_first_token(self, now: float) -> None:
        if self.first_token_at is None:
            self.first_token_at = now

    def record_frame(self, frame: str) -> None:
        """token 이벤트의 텍스트만 누적(부분 텍스트 보존용). 다른 이벤트는 무시한다."""
        text = _extract_token_text(frame)
        if text:
            self.assistant_parts.append(text)

    def finish(self, now: float, status: TurnStatus, error_type: str | None = None) -> None:
        """어시스턴트 응답을 상태와 함께 마감하고 요청 구조화 로그를 남긴다(멱등)."""
        if self.finished:
            return
        self.finished = True
        assistant_text = "".join(self.assistant_parts)
        self.store.finalize_assistant(self.turn_id, assistant_text, status)

        latency_total_ms = round((now - self.started) * 1000)
        latency_first_ms = (
            round((self.first_token_at - self.started) * 1000)
            if self.first_token_at is not None
            else None
        )
        record = {
            "event": "chat_request",
            "requestId": self.request_id,
            "userId": self.user_id,
            "role": self.role,
            "conversationId": self.conversation_id,
            "latencyFirstTokenMs": latency_first_ms,
            "latencyTotalMs": latency_total_ms,
            "model": [m.model for m in self.model_calls] or None,
            "promptTokens": sum(m.prompt_tokens for m in self.model_calls),
            "completionTokens": sum(m.completion_tokens for m in self.model_calls),
            "errorType": error_type,
            "streamStatus": status.value,
            "messageLength": self.message_length,
            "messageHash": self.message_hash,
            # [PII] 사용자 message 원문은 여기에 절대 포함하지 않는다(§6.3 b).
        }
        logger.info(json.dumps(record, ensure_ascii=False))


def _extract_token_text(frame: str) -> str | None:
    """SSE `data:` 프레임에서 token 이벤트의 text 만 추출한다."""
    try:
        line = frame.strip()
        if line.startswith("data:"):
            line = line[len("data:") :].strip()
        payload = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("type") != "token":
        return None
    data = payload.get("data") or {}
    text = data.get("text") if isinstance(data, dict) else None
    return text if isinstance(text, str) else None


def start_observation(
    *,
    request_id: str,
    identity: Identity,
    conversation_id: str,
    message: str,
    store: ConversationStore,
    now: float,
) -> RequestObservation:
    """사용자 메시지를 저장(§6.3 a)하고 관측 컨텍스트를 만든다. 원문은 저장소에만, 로그엔 지문만."""
    length, digest = message_fingerprint(message)
    role = role_of(identity)
    subject = identity.user_id or identity.subject
    # 저장 키는 신원 스코프(IDOR 방지) — 로그의 conversationId 는 원 sessionId 유지(상관관계용).
    turn_id = store.save_user_message(conversation_key(subject, conversation_id), subject, role, message)
    return RequestObservation(
        request_id=request_id,
        conversation_id=conversation_id,
        user_id=subject,
        role=role,
        store=store,
        turn_id=turn_id,
        message_length=length,
        message_hash=digest,
        started=now,
    )


def emit_rejection(request_id: str, error_type: str, **fields: object) -> None:
    """스트림 전 거부(429/409/504 등)의 구조화 로그 — 대화 턴 없이 errorType 만 집계(§6.3 b).

    레이트 리밋(§2.8)·409(§2.9 a) 발동을 상한 튜닝 근거로 관측 가능하게 남긴다.
    """
    record = {
        "event": "chat_request",
        "requestId": request_id,
        "errorType": error_type,
        "streamStatus": None,
        **fields,
    }
    logger.info(json.dumps(record, ensure_ascii=False))
