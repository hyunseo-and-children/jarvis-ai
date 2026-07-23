"""카테고리 하이브리드 매핑 Settings 신규 필드 테스트 (이슈 #59).

방식 A(추측→임베딩 보정)·canonical-or-null·멀티 fan-out 튜너블이 기본값으로 로드되는지 확인.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_category_mapping_settings_defaults() -> None:
    """카테고리 매핑·fan-out 튜너블 기본값 — 하드코딩 금지, config 주입."""
    settings = Settings(_env_file=None)
    assert settings.category_top_k == 5
    assert settings.category_fanout_max == 5
    assert settings.category_fanout_per_cat_limit == 10
    assert settings.category_fanout_merge_cap == 30


def test_pool_max_size_covers_fanout_concurrency() -> None:
    """검색 풀 max_size 는 fan-out 동시 조회(최대 category_fanout_max leg)를 커버해야 한다(PR #73 리뷰).

    psycopg_pool 기본 max_size(4) < fanout(5) 이면 5번째 leg 가 커넥션을 기다려 gather 병렬화가
    부분 무력화된다 — 암묵 하드코딩(psycopg 기본값)을 config 로 빼고 fanout 이상으로 명시한다.
    """
    settings = Settings(_env_file=None)
    assert settings.category_search_pool_max_size >= settings.category_fanout_max


def test_negative_fanout_max_rejected() -> None:
    """음수 fanout_max 는 로드 시 거부한다 — out[:fanout_max] 가 음수면 '뒤에서' 잘려 앞 항목이

    남아 "fanout_max<=0 이면 정확히 0개"라는 절단 불변식이 조용히 깨진다(category_mapping·
    decompose 두 슬라이스 공통). 소스에서 ge=0 으로 막는다(PR #73 리뷰).
    """
    with pytest.raises(ValidationError):
        Settings(_env_file=None, category_fanout_max=-1)
