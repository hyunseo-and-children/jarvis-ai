"""챗봇 요청/응답 스키마 (api-spec v0.7.0 §3.1).

[변경 v0.4.0] CH-2 명명 기준 채택 / [변경 v0.6.0] action.reason 재편(게스트 담기 허용):
  - SSE 이벤트명(구매자): token / conditions / action / suggestions / budget / products.ready /
    done / error (구 text.delta / products / (done) 세트 폐기). suggestions=완화/되돌리기 칩(결정 14-D/14-F, §3.1).
  - 모든 페이로드 필드는 camelCase (Pydantic alias). Python 속성은 snake_case 유지,
    직렬화 시 by_alias=True 로 camelCase 출력, 입력은 populate_by_name 으로 양쪽 허용.
  - [HARD] SSE 는 상품 카드를 싣지 않는다 (경로 B). products.ready 는 {sessionId, listId}
    상관관계 키만 나른다. 상품 카드/표시 필드는 Spring push(§4.2)+GET(§4.3)으로 이동.

[보안] ChatRequest 에는 userId 가 없다 — 신원은 토큰 클레임(sub/role)에서만 도출한다 (§3.1).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import field_validator, model_validator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """camelCase 직렬화 공통 베이스.

    alias_generator=to_camel 로 스네이크 속성을 camelCase alias 에 매핑하고,
    populate_by_name=True 로 입력 시 스네이크/카멜 양쪽을 허용한다.
    직렬화는 .model_dump(by_alias=True) 로 camelCase 출력.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ChatRequest(CamelModel):
    """POST /chat 및 POST /seller/chat 공통 요청 본문 (api-spec §3.1 / §3.2)."""

    session_id: str = Field(..., description="Spring 발급 불투명 스레드 키 (만료 없음, §2.6)")
    thread_id: str = Field(..., description="대화 스레드 식별자. 멀티턴 필터 누적 대상")
    message: str = Field(..., description="현재 턴 사용자 원문 질의 (길이 상한은 config chat_message_max_chars)")

    @field_validator("message")
    @classmethod
    def _limit_message_length(cls, v: str) -> str:
        """message 길이 상한을 config 에서 주입(하드코딩 금지). 초과 시 400(api-spec §3.1)."""
        from app.core.config import get_settings

        cap = get_settings().chat_message_max_chars
        if len(v) > cap:
            raise ValueError(f"message exceeds {cap} characters")
        return v

    @field_validator("session_id", "thread_id")
    @classmethod
    def _limit_key_length(cls, v: str) -> str:
        """불투명 식별자 길이 상한(config) — registry·저장소·로그 남용 방어. 초과 시 400."""
        from app.core.config import get_settings

        cap = get_settings().chat_key_max_chars
        if len(v) > cap:
            raise ValueError(f"identifier exceeds {cap} characters")
        return v


# ── SSE 이벤트 data 페이로드 모델 (api-spec §3.1, 6종) ──


class TokenData(CamelModel):
    """`token` 이벤트 — 근거/코멘트 토큰 증분 (구 text.delta)."""

    text: str


class ConditionChip(CamelModel):
    """`conditions` 칩 1건 — FE 제거 가능한 추출 조건 (api-spec §3.1 (2))."""

    field: str
    label: str
    value: Any


class ConditionsData(CamelModel):
    """`conditions` 이벤트 — 추출 필터 조건 칩 목록 (0~1회)."""

    chips: list[ConditionChip] = Field(default_factory=list)


class RevertRef(CamelModel):
    """`revert` — 구매 이력 억제 되돌리기 대상 카테고리 (결정 14-F)."""

    category: str


class RelaxationRef(CamelModel):
    """`relaxation` — 0건 완화 제안 대상 (결정 14-D)."""

    field: str
    value: Any


class SuggestionChip(CamelModel):
    """`suggestions` 칩 1건 — 완화(relaxation) 또는 되돌리기(revert). estCount==0 은 제외(§3.1)."""

    label: str
    revert: RevertRef | None = None
    relaxation: RelaxationRef | None = None
    est_count: int  # 재포함/완화 적용 시 예상 결과 수(COUNT)

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> "SuggestionChip":
        """§3.1 — 칩 1건은 relaxation 또는 revert 중 **정확히 하나**여야 한다."""
        if (self.revert is None) == (self.relaxation is None):
            raise ValueError("SuggestionChip 은 revert 또는 relaxation 중 정확히 하나여야 한다(§3.1)")
        return self


class SuggestionsData(CamelModel):
    """`suggestions` 이벤트 — 완화·되돌리기 제안 칩 목록 (api-spec §3.1)."""

    chips: list[SuggestionChip] = Field(default_factory=list)


class ActionData(CamelModel):
    """`action` 이벤트 — 장바구니 담기 결과 (api-spec §3.1 (3)).

    type: CART_ADDED | CART_ADD_FAILED.
    reason(실패 시, v0.6.0 재편): PRODUCT_NOT_FOUND | CART_ERROR | OUT_OF_STOCK(🔴 협의 C-3).
    GUEST_NOT_ALLOWED 폐기 — 게스트 담기 허용(결정 8 개정). 옵션 되물음(CART_OPTION_REQUIRED)은
    실패 action 이 아니라 token 재질문 멀티턴으로 처리한다(api-spec §3.1·§4.1).
    """

    type: Literal["CART_ADDED", "CART_ADD_FAILED"]
    message: str
    cart_item_id: int | None = None  # 숫자(BIGINT, cart_item.id)
    reason: (
        Literal["OUT_OF_STOCK", "PRODUCT_NOT_FOUND", "CART_ERROR"] | None
    ) = None


class ProductsReadyData(CamelModel):
    """`products.ready` 이벤트 — 목록 push 성공 상관관계 키 (정확히 1회, 카드 없음).

    [HARD] 상품 카드는 싣지 않는다. FE 는 이 키로 Spring 목록 GET(§4.3)을 호출한다.
    """

    session_id: str
    list_id: str


class DoneData(CamelModel):
    """`done` 이벤트 — 정상 종료. finishReason: stop | zero_result (api-spec §3.1 (5))."""

    finish_reason: Literal["stop", "zero_result"] = "stop"


class ErrorData(CamelModel):
    """`error` 이벤트 — 스트림 내부 오류 (api-spec §3.1 (6)).

    code 4종: LLM_TIMEOUT | LLM_UNAVAILABLE | SEARCH_FAILED | INTERNAL.
    스테이지 상세(decompose/rerank)는 서버 로그 전용 — 사용자 스트림 미노출.
    """

    code: Literal["LLM_TIMEOUT", "LLM_UNAVAILABLE", "SEARCH_FAILED", "INTERNAL"]
    message: str
