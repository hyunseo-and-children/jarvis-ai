"""embedding.py 스모크 테스트 (이슈 #31) — 실제 Google gemini-embedding-001 API 호출.

`uv run pytest tests/smoke -m smoke` 로 명시적으로만 실행한다(기본 pytest 실행에서는
pyproject.toml addopts 로 제외). GOOGLE_API_KEY 가 .env 에 없으면 스킵 — CI 기본 실행에
포함하지 않는 이유는 호출마다 실제 과금이 발생하기 때문이다(§4.8 v0.15.14).
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.pipelines.embedding import embed_texts

pytestmark = pytest.mark.smoke


def _require_api_key() -> None:
    if not get_settings().google_api_key:
        pytest.skip("GOOGLE_API_KEY 미설정 — 스모크 테스트 스킵")


def test_embed_texts_returns_correct_dimension_and_unit_norm():
    _require_api_key()
    settings = get_settings()

    vectors = embed_texts(["여행용 방수 파우치, 기내 반입 가능"])

    assert len(vectors) == 1
    vec = vectors[0]
    assert len(vec) == settings.embedding_dim

    norm = sum(x * x for x in vec) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-3)  # 수동 L2 정규화 확인(MRL 절단 응답)


def test_embed_texts_multiple_inputs_returns_distinct_vectors():
    _require_api_key()

    vectors = embed_texts(["여행용 방수 파우치", "무선 블루투스 이어폰"])

    assert len(vectors) == 2
    assert vectors[0] != vectors[1]  # 서로 다른 텍스트는 다른 벡터
