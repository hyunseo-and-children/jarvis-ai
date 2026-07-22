"""판매자 챗봇 전용 요청 스키마 (S-4, api-spec §3.2).

구매자 `ChatRequest`(app/schemas/chat.py)를 확장해 길이 검증을 재사용하고,
판매자 HITL 승인(confirm)의 **구조화 신호**를 최상위 선택 필드(action/draftId)로
받는다. 구매자 계약(ChatRequest)은 건드리지 않는다 — 판매자 스트림만 소비한다.

[변경 2026-07-22, FE 계약 A-2] confirm 전송을 구 "message 문자열에 JSON 을 실어
코드가 파싱" 방식에서 **최상위 action/draftId 필드**로 전환한다. FE 가 message 를
이스케이프하지 않고 `{sessionId, threadId, action:"confirm", draftId}` 로 보낸다.
"발화 ≠ 동의" [HARD] 원칙은 유지된다 — 승인은 `action == "confirm"` 구조화 신호로만
성립하고, 자유 텍스트 message 는 승인이 아니다(HITL 안전장치 ②).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.chat import ChatRequest


class SellerChatRequest(ChatRequest):
    """POST /seller/chat 요청 본문 — ChatRequest + HITL 승인 필드(선택).

    - 일반 발화: `{sessionId, threadId, message}` (action 미지정).
    - 승인(confirm): `{sessionId, threadId, action:"confirm", draftId}` — message 는
      비워도 된다(승인은 발화가 아니므로). action=="confirm" 이면 draftId 필수.
    """

    action: Literal["confirm"] | None = Field(
        default=None,
        description="HITL 승인 신호. 'confirm' 이면 draftId 초안을 실행(§3.2). 미지정=일반 발화",
    )
    # alias 는 CamelModel(alias_generator=to_camel)이 draft_id → draftId 로 생성한다.
    draft_id: str | None = Field(
        default=None,
        description="action=='confirm' 일 때 실행할 draft 식별자(스트림1 draft.draftId)",
    )

    @model_validator(mode="after")
    def _confirm_requires_draft_id(self) -> "SellerChatRequest":
        """승인 신호의 형식을 강제한다 — action=='confirm' 이면 draftId 가 비어있으면 안 된다.

        형식 위반은 스트림 시작 전 400(BAD_REQUEST)으로 거른다(§3.2) — 빈 draftId 를
        일반 발화로 흘리면 승인이 조용히 무시되어 FE 가 원인을 알 수 없다.
        """
        if self.action == "confirm" and not (self.draft_id and self.draft_id.strip()):
            raise ValueError("action=='confirm' 이면 draftId 가 필요합니다")
        return self
