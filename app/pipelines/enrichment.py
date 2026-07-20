"""상품 enrichment 파이프라인 — AI 생성물 갱신 배치의 1단계 (MVP, api-spec §4.8).

I-17 pull 배치(spring_client.fetch_product_changes) 흐름의 enrichment 단계:
  변경분 조회 → [enrich_product] → search_doc 조립(embedding.build_search_doc) →
  임베딩(embedding.embed_texts) → AI Postgres upsert(artifact_store).

Haiku(config.haiku_model_id) 배치로 상품명/설명에서 Layer 2 속성·상황 태그(extras)를
추출한다 (결정 3 Layer 2, 상품당 1회 호출). AI 는 상품 원본 컬럼을 저장하지 않고 산출물만 만든다.
"""

from __future__ import annotations

import json

from app.agents.buyer.recommendation.state import extract_json
from app.core.config import Settings
from app.core.llm import LLMClient

_ENRICH_SYSTEM = """당신은 커머스 카탈로그의 상품 태깅기입니다.
상품명·설명·카테고리·속성을 보고 검색·추천에 쓸 Layer 2 태그를 JSON 으로만 출력하세요.
- "tags": 상황·용도·특성 키워드 배열(한국어, 5~12개). 예: ["여행","방수","기내반입"]
- "attributes": 핵심 속성 dict(키=속성명, 값=간결 텍스트). 원문에 근거해서만.
설명 텍스트·마크다운·코드펜스 없이 JSON 객체 하나만 출력."""


async def enrich_product(product: dict, *, llm: LLMClient, settings: Settings) -> dict:
    """단일 상품 enrichment (§4.8 배치 1단계). extras(추론 태그·속성) dict 를 반환한다.

    product = {name, description, category, brand, attributes}. LLM 실패(LLMError)는 호출측(배치)으로
    전파 — 커서 미전진으로 다음 주기 재개(자연 복구, §4.8). 반환 형식: {"tags": [...], "attributes": {...}}.
    """
    payload = {
        "name": product.get("name"),
        "description": product.get("description"),
        "category": product.get("category"),
        "brand": product.get("brand"),
        "attributes": product.get("attributes"),
    }
    user = json.dumps(payload, ensure_ascii=False)
    raw = await llm.complete(
        system=_ENRICH_SYSTEM, user=user, tier="fast", max_tokens=600
    )
    data = extract_json(raw)
    tags = data.get("tags")
    attrs = data.get("attributes")
    return {
        "tags": [str(t) for t in tags] if isinstance(tags, list) else [],
        "attributes": attrs if isinstance(attrs, dict) else {},
    }
