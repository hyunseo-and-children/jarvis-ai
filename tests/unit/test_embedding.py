"""임베딩 클라이언트 유닛 테스트 (이슈 #31, api-spec §4.8 v0.15.14).

google-genai SDK는 _client() 심(seam)을 통해 주입형 fake 로 대체한다 — 라이브 Google API
호출 없이 정규화·차원검증·미구성 오류 경로를 검증한다.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.pipelines import embedding as emb


class _FakeEmbedding:
    def __init__(self, values: list[float]) -> None:
        self.values = values


class _FakeResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.embeddings = [_FakeEmbedding(v) for v in vectors]


class _FakeModels:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors

    def embed_content(self, *, model, contents, config):
        return _FakeResponse(self._vectors)


class _FakeClient:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.models = _FakeModels(vectors)


class _CapturingModels:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.last_config = None

    def embed_content(self, *, model, contents, config):
        self.last_config = config
        return _FakeResponse(self._vectors)


class _CapturingClient:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.models = _CapturingModels(vectors)


def test_embed_texts_calls_google_and_l2_normalizes(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key", embedding_dim=3)
    monkeypatch.setattr(emb, "get_settings", lambda: settings)
    monkeypatch.setattr(emb, "_client", lambda api_key: _FakeClient([[3.0, 4.0, 0.0]]))

    out = emb.embed_texts(["hello"])

    assert len(out) == 1
    assert out[0] == pytest.approx([0.6, 0.8, 0.0])  # MRL 절단 응답 수동 L2 정규화(3-4-5)


def test_embed_texts_raises_without_api_key(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="")
    monkeypatch.setattr(emb, "get_settings", lambda: settings)

    with pytest.raises(emb.EmbeddingError):
        emb.embed_texts(["hello"])


def test_embed_texts_dim_mismatch_raises(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key", embedding_dim=4)
    monkeypatch.setattr(emb, "get_settings", lambda: settings)
    monkeypatch.setattr(emb, "_client", lambda api_key: _FakeClient([[1.0, 0.0, 0.0]]))

    with pytest.raises(ValueError):
        emb.embed_texts(["hello"])


def test_embed_texts_wraps_malformed_response_parsing_as_embedding_error(monkeypatch):
    """PR #42 리뷰 — 응답 파싱(item.values 접근)이 try 밖에 있으면 예상 밖 응답 형태(세이프티
    필터링 등)가 AttributeError/TypeError 를 원본 그대로 새게 한다. EmbeddingError 로 통일돼야 한다."""

    class _BrokenModels:
        def embed_content(self, *, model, contents, config):
            class _Response:
                embeddings = None  # 순회 시 TypeError

            return _Response()

    class _BrokenClient:
        models = _BrokenModels()

    settings = Settings(_env_file=None, google_api_key="test-key", embedding_dim=3)
    monkeypatch.setattr(emb, "get_settings", lambda: settings)
    monkeypatch.setattr(emb, "_client", lambda api_key: _BrokenClient())

    with pytest.raises(emb.EmbeddingError):
        emb.embed_texts(["hello"])


def test_embed_texts_passes_task_type_when_given(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key", embedding_dim=3)
    monkeypatch.setattr(emb, "get_settings", lambda: settings)
    client = _CapturingClient([[3.0, 4.0, 0.0]])
    monkeypatch.setattr(emb, "_client", lambda api_key: client)

    emb.embed_texts(["q"], task_type="RETRIEVAL_QUERY")

    assert client.models.last_config.task_type == "RETRIEVAL_QUERY"


def test_embed_texts_omits_task_type_by_default(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key", embedding_dim=3)
    monkeypatch.setattr(emb, "get_settings", lambda: settings)
    client = _CapturingClient([[3.0, 4.0, 0.0]])
    monkeypatch.setattr(emb, "_client", lambda api_key: client)

    emb.embed_texts(["d"])

    assert getattr(client.models.last_config, "task_type", None) is None
