"""SellerChatRequest 스키마 검증 (app/schemas/seller.py, FE 계약 A-2/A-3).

판매자 챗의 confirm 승인을 message 문자열이 아니라 최상위 action/draftId 구조화
필드로 받는 계약을 검증한다 — 구매자 ChatRequest 무변경(별도 서브클래스).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.seller import SellerChatRequest


def test_normal_request_has_no_action() -> None:
    """일반 발화 — action/draftId 미지정이 기본값 None."""
    r = SellerChatRequest(session_id="s", thread_id="t", message="지난달 매출 어때?")
    assert r.action is None
    assert r.draft_id is None


def test_confirm_request_camelcase_input_and_output() -> None:
    """A-2 — camelCase(draftId) 입력을 받고 by_alias 로 draftId 로 직렬화한다."""
    r = SellerChatRequest.model_validate(
        {"sessionId": "s", "threadId": "t", "message": "", "action": "confirm", "draftId": "d-9"}
    )
    assert r.action == "confirm"
    assert r.draft_id == "d-9"
    dumped = r.model_dump(by_alias=True)
    assert dumped["draftId"] == "d-9"
    assert dumped["action"] == "confirm"


def test_confirm_without_draft_id_is_rejected() -> None:
    """A-2 — action=='confirm' 인데 draftId 누락은 400(BAD_REQUEST)로 거른다."""
    with pytest.raises(ValidationError):
        SellerChatRequest.model_validate(
            {"sessionId": "s", "threadId": "t", "message": "", "action": "confirm"}
        )


def test_confirm_with_blank_draft_id_is_rejected() -> None:
    """공백뿐인 draftId 도 승인 신호로 인정하지 않는다(발화 ≠ 동의 [HARD])."""
    with pytest.raises(ValidationError):
        SellerChatRequest(
            session_id="s", thread_id="t", message="", action="confirm", draft_id="   "
        )


def test_unknown_action_value_is_rejected() -> None:
    """action 은 Literal['confirm'] — 임의 값은 거부(계약 밖 신호 차단)."""
    with pytest.raises(ValidationError):
        SellerChatRequest.model_validate(
            {"sessionId": "s", "threadId": "t", "message": "x", "action": "cancel"}
        )


def test_thread_id_is_required() -> None:
    """A-3 — threadId 필수 유지(구매자 계약과 일관)."""
    with pytest.raises(ValidationError):
        SellerChatRequest.model_validate({"sessionId": "s", "message": "hi"})


def test_message_length_validator_inherited() -> None:
    """구매자 ChatRequest 의 message 길이 상한 validator 가 상속돼 동작한다."""
    from app.core.config import get_settings

    cap = get_settings().chat_message_max_chars
    with pytest.raises(ValidationError):
        SellerChatRequest(session_id="s", thread_id="t", message="x" * (cap + 1))
