"""임베딩 파이프라인 — AI 생성물 갱신 배치의 2단계 (MVP, api-spec §4.8).

I-17 pull 배치 흐름에서 search_doc 를 조립하고 셀프호스트 한국어 임베딩(1024-dim, CPU, 결정 6)으로
임베딩해 AI Postgres(artifact_store)에 upsert 한다. 결합 방식(방식1 벡터검색 / 방식2 재정렬)은
SearchBackend 로 양쪽 구현한다(§4.8 말미, 2026-07-20 결정).

sentence-transformers/torch 는 embedding 그룹에만 있으므로 함수 내부에서 LAZY import 한다
(base `uv sync` 에는 미포함). 테스트·배치는 embed 콜러블을 주입해 이 무거운 경로를 대체한다(주입형).
"""

from __future__ import annotations

from app.core.config import get_settings

_MODEL_CACHE: dict = {}


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


def embed_texts(texts: list[str]) -> list[list[float]]:
    """텍스트 목록을 임베딩한다 (§4.8). sentence-transformers 는 함수 내부 LAZY import.

    config.embedding_model_id 로 CPU 모델을 로드(캐시)하고 정규화 임베딩을 반환한다. torch 미설치
    (base) 환경에서는 ImportError — 배치·테스트는 embed 콜러블을 주입한다.
    """
    settings = get_settings()
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    model = _MODEL_CACHE.get(settings.embedding_model_id)
    if model is None:
        model = SentenceTransformer(settings.embedding_model_id, device="cpu")
        _MODEL_CACHE[settings.embedding_model_id] = model
    vecs = model.encode(list(texts), normalize_embeddings=True)
    out = [[float(x) for x in vec] for vec in vecs]
    for vec in out:
        if len(vec) != settings.embedding_dim:
            raise ValueError(f"임베딩 차원 불일치: {len(vec)} != {settings.embedding_dim}")
    return out
