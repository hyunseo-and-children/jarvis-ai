"""임베딩/카탈로그 배치 Settings 신규 필드 테스트 (이슈 #31, api-spec §4.8 v0.15.14).

Google gemini-embedding-001 API 전환 이후 값(dim 1536)이 기본값으로 로드되는지 확인한다.
"""

from __future__ import annotations

from app.core.config import Settings


def test_embedding_settings_reflect_google_api() -> None:
    """[2026-07-20 결정] 셀프호스트 torch → Google gemini-embedding-001, dim 1024→1536."""
    settings = Settings(_env_file=None)
    assert settings.embedding_model_id == "gemini-embedding-001"
    assert settings.embedding_dim == 1536
    assert settings.google_api_key == ""


def test_catalog_batch_interval_default() -> None:
    """주기 증분 pull 배치 스케줄러 간격(초) — config 주입, 하드코딩 금지."""
    settings = Settings(_env_file=None)
    assert settings.catalog_batch_interval_s == 300.0
