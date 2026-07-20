"""판매자 Settings 신규 필드 테스트 (DESIGN-SELLER-TOOLS-STAGE1 §5).

임계값·타임아웃을 코드 하드코딩이 아니라 Settings 로 주입하는지 확인한다.
"""

from __future__ import annotations

from app.core.config import Settings


def test_seller_settings_defaults() -> None:
    """§5 표의 판매자 임계값 기본값이 그대로 로드된다 (env 미설정 시)."""
    settings = Settings(_env_file=None)
    # [통일 2026-07-20] 서비스 토큰은 팀 규약 internal_api_token 단일 키(기본 미설정).
    assert settings.internal_api_token == ""
    assert settings.seller_ma_window == 7
    assert settings.seller_anomaly_deviation_pct == 30.0
    assert settings.seller_conversion_drop_pct == 20.0
    assert settings.seller_churn_inactive_days == 30
    assert settings.seller_recent_days_default == 7
    assert settings.seller_report_score_threshold == 21
    assert settings.seller_report_max_retries == 3
    assert settings.seller_draft_ttl_minutes == 10
    assert settings.seller_history_recent_n == 5
    assert settings.seller_tool_call_limit == 8


def test_seller_model_temperatures() -> None:
    """SPEC-SELLER-001 §8 — Haiku t=0 / Sonnet t=0.2 기본값 (2-3 모델 팩토리 재료)."""
    settings = Settings(_env_file=None)
    assert settings.seller_haiku_temperature == 0.0
    assert settings.seller_sonnet_temperature == 0.2


def test_spring_timeout_default_is_3s() -> None:
    """AI→Spring 전 구간 타임아웃 기본값은 3.0s (api-spec §2.9 c)."""
    settings = Settings(_env_file=None)
    assert settings.spring_timeout_s == 3.0
