"""상품 enrichment 파이프라인 — AI 생성물 갱신 배치의 1단계 (MVP, api-spec §4.8).

[정정 v0.5.1] "고도화 post-MVP" 아님 — MVP 배치 소속. AI Postgres 는 AI 생성물
(extras·search_doc·임베딩)만 저장하며(상품 원본 컬럼 사본 없음), 이 모듈은
I-17 pull 배치(spring_client.fetch_product_changes) 흐름의 enrichment 단계다:
  변경분 조회 → [enrich_product] → search_doc 조립(embedding.build_search_doc) →
  임베딩(embedding.embed_texts) → AI Postgres upsert.

Haiku(config.haiku_model_id) 배치로 상품명/설명에서 Layer 2 속성·상황 태그(extras)를
추출한다 (결정 3 Layer 2, 상품당 1회 호출).

TODO(SPEC-CATALOG-DATA-001 재범위): Haiku 배치 호출, 속성/태그 스키마 검증, upsert.
"""

from __future__ import annotations


def enrich_product(product: dict) -> dict:
    """단일 상품 enrichment (스텁, §4.8 배치 1단계). extras(속성·상황 태그)를 추가한 dict 반환."""
    raise NotImplementedError("enrichment stub — wired by the §4.8 artifacts batch (I-8)")
