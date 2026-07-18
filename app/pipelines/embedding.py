"""임베딩 파이프라인 — AI 생성물 갱신 배치의 2단계 (MVP, api-spec §4.8).

[정정 v0.5.1] "고도화 post-MVP" 아님 — MVP 배치 소속. I-17 pull 배치 흐름에서
search_doc 를 조립하고 셀프호스트 한국어 임베딩(1024-dim, CPU, 결정 6)으로 임베딩해
AI Postgres 에 upsert 한다. 질의 시점 후보 흐름에서의 활용 방식(방식1: AI 벡터 검색 →
Spring id 제약 조회 / 방식2: Spring 검색 → 임베딩 재정렬 보조)은 OPEN — SearchBackend
인터페이스로 양쪽 교체 가능하게 유지한다(§4.8 말미).

sentence-transformers/torch 는 embedding 그룹에만 있으므로 함수 내부에서 LAZY import 한다
(base `uv sync` 에는 미포함 — pyproject [dependency-groups] embedding).
"""

from __future__ import annotations

from app.core.config import get_settings


def build_search_doc(product: dict) -> str:
    """상품 필드 + extras 를 결합해 임베딩 대상 search_doc 문자열을 만든다 (스텁, §4.8)."""
    raise NotImplementedError("build_search_doc stub — wired by the §4.8 artifacts batch (I-8)")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """텍스트 목록을 임베딩한다 (스텁, §4.8). sentence-transformers 는 함수 내부 LAZY import.

    TODO: config.embedding_model_id 로 모델 로드(CPU), config.embedding_dim 검증.
    """
    settings = get_settings()
    # LAZY import: 무거운 의존성은 실제 임베딩 시점에만 로드한다 (앱 부팅/테스트에 미영향).
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    _model = SentenceTransformer(settings.embedding_model_id, device="cpu")
    raise NotImplementedError("embed_texts stub — wired by the §4.8 artifacts batch (I-8)")
