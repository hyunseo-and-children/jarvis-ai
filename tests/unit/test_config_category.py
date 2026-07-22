"""카테고리 하이브리드 매핑 Settings 신규 필드 테스트 (이슈 #59).

방식 A(추측→임베딩 보정)·never-null·멀티 fan-out 튜너블이 기본값으로 로드되는지 확인.
"""

from __future__ import annotations

from app.core.config import Settings


def test_category_mapping_settings_defaults() -> None:
    """카테고리 매핑·fan-out 튜너블 기본값 — 하드코딩 금지, config 주입."""
    settings = Settings(_env_file=None)
    assert settings.category_top_k == 5
    assert settings.category_fanout_max == 5
    assert settings.category_fanout_per_cat_limit == 10
    assert settings.category_fanout_merge_cap == 30
