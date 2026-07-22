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


def test_profile_idle_scheduler_defaults_are_bounded() -> None:
    settings = Settings(_env_file=None)

    assert settings.profile_session_idle_timeout_s == 600.0
    assert settings.profile_idle_sweep_interval_s == 60.0
    assert settings.profile_idle_sweep_batch_size == 10
    assert settings.profile_idle_max_concurrency == 2
    assert settings.profile_idle_claim_ttl_s == 900.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_session_idle_timeout_s", 0),
        ("profile_idle_sweep_interval_s", 0),
        ("profile_idle_sweep_batch_size", 0),
        ("profile_idle_max_concurrency", 0),
        ("profile_idle_claim_ttl_s", 0),
    ],
)
def test_profile_idle_scheduler_settings_must_be_positive(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_profile_idle_claim_ttl_must_cover_every_configured_batch_wave() -> None:
    with pytest.raises(ValidationError, match="all configured batch waves"):
        Settings(
            _env_file=None,
            profile_idle_sweep_batch_size=11,
            profile_idle_max_concurrency=2,
            profile_idle_claim_ttl_s=700,
        )
