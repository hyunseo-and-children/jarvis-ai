"""임베딩 파이프라인 — AI 생성물 갱신 배치의 2단계 (MVP, api-spec §4.8 v0.15.14).

I-17 pull 배치 흐름에서 search_doc 를 조립하고 Google `gemini-embedding-001` API(1536-dim,
MRL 절단)로 임베딩해 AI Postgres(artifact_store)에 upsert 한다. [2026-07-20 결정 6 개정]
셀프호스트 torch 임베딩은 폐기 — MRL 절단 응답은 사전 정규화가 안 돼 있어 여기서 수동 L2
정규화한다. 결합 방식(방식1 벡터검색 / 방식2 재정렬)은 SearchBackend 로 양쪽 구현한다
(§4.8 말미, 2026-07-20 결정).

google-genai SDK 는 함수 내부에서 LAZY import 한다(app/core/llm.py AnthropicLLM 과 동일 패턴).
테스트·배치는 embed 콜러블을 주입해 이 경로를 대체한다(주입형) — _client() 는 라이브 호출 seam.
"""

from __future__ import annotations

import math

from app.core.config import get_settings

_CLIENT_CACHE: dict[str, object] = {}


class EmbeddingError(Exception):
    """임베딩 호출 실패(오류/미구성). 상위(배치)는 그대로 전파해 자연 재개(§4.8)."""


def build_search_doc(product: dict) -> str:
    """상품 필드 + extras(tags·attributes)를 결합해 임베딩 대상 search_doc 문자열을 만든다 (§4.8).

    원본 컬럼을 저장하지는 않으나 임베딩 입력 조립에는 사용한다(산출물 계산). 빈 필드는 건너뛴다.
    """
    extras = product.get("extras") if isinstance(product.get("extras"), dict) else {}
    parts: list[str] = []
    for key in ("name", "category", "brand", "description"):
        val = product.get(key)
        if val:
            parts.append(str(val))
    attributes = product.get("attributes")
    if isinstance(attributes, dict):
        parts.extend(f"{k}: {v}" for k, v in attributes.items() if v)
    tags = extras.get("tags")
    if isinstance(tags, list) and tags:
        parts.append(" ".join(str(t) for t in tags))
    extra_attrs = extras.get("attributes")
    if isinstance(extra_attrs, dict):
        parts.extend(f"{k}: {v}" for k, v in extra_attrs.items() if v)
    return "\n".join(parts)


def _client(api_key: str):
    """genai.Client 를 api_key 별로 캐시해 반환한다 (라이브 호출 seam — 테스트가 대체 주입)."""
    from google import genai  # noqa: PLC0415

    if api_key not in _CLIENT_CACHE:
        _CLIENT_CACHE[api_key] = genai.Client(api_key=api_key)
    return _CLIENT_CACHE[api_key]


def _l2_normalize(vec: list[float]) -> list[float]:
    """MRL 절단 응답은 사전 정규화가 안 돼 있으므로 수동 L2 정규화한다 (§4.8 v0.15.14)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """텍스트 목록을 Google gemini-embedding-001 API 로 임베딩한다 (§4.8).

    config.embedding_dim 을 output_dimensionality 로 요청하고, 응답을 수동 L2 정규화한다.
    google_api_key 미구성 시 곧바로 EmbeddingError — 배치·테스트는 embed 콜러블을 주입한다.
    """
    settings = get_settings()
    if not settings.google_api_key:
        raise EmbeddingError("embed_texts: google_api_key 미구성 — Google 임베딩 API 호출 불가")

    from google.genai import types  # noqa: PLC0415

    client = _client(settings.google_api_key)
    try:
        response = client.models.embed_content(
            model=settings.embedding_model_id,
            contents=list(texts),
            config=types.EmbedContentConfig(output_dimensionality=settings.embedding_dim),
        )
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001 - SDK 예외를 EmbeddingError 로 통일 매핑
        raise EmbeddingError(str(exc)) from exc

    out = [_l2_normalize([float(x) for x in item.values]) for item in response.embeddings]
    for vec in out:
        if len(vec) != settings.embedding_dim:
            raise ValueError(f"임베딩 차원 불일치: {len(vec)} != {settings.embedding_dim}")
    return out
