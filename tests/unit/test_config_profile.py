"""프로필/I-20 설정 검증."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_session_end_claim_ttl_default_covers_two_llm_stages() -> None:
    settings = Settings()

    assert settings.session_end_claim_ttl_s == 180.0
    assert settings.session_end_claim_ttl_s > (
        settings.llm_timeout_s * (settings.llm_max_retries + 1) * 2
    )


def test_session_end_claim_ttl_must_exceed_processing_budget() -> None:
    with pytest.raises(ValidationError, match="must exceed the two-stage LLM timeout budget"):
        Settings(session_end_claim_ttl_s=0)
