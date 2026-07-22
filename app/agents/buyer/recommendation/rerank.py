"""rerank 노드 — Sonnet 1회로 후보를 프로필 기반 재랭킹 + 근거 생성 (SPEC-RECOMMEND-001 §6.4).

MVP 슬라이스: 후보 외 id 미노출(REQ-REC-081 — 값싼 부분집합 대조만)·노출 상한(config)만 강제한다.
근거 속성 결정적 대조(REQ-REC-082)·순서 무작위화(REQ-REC-080)·출력검증 degrade 상태 기록은 후속.
실패/타임아웃/유효 후보 0건은 LLMError 로 전파 — 상위가 검색순서 degrade 로 처리한다(§7).
"""

from __future__ import annotations

import json

from app.agents.buyer.recommendation.state import RerankResult, extract_json
from app.core.llm import LLMClient, LLMError
from app.schemas.spring import SpringProduct

_SYSTEM = """당신은 커머스 추천 재랭킹기입니다. 후보 상품과 사용자 질의(+프로필)를 받아
가장 적합한 순서로 재랭킹하고 상품마다 한글 40자 이내의 1문장 한국어 근거를 답니다.
반드시 아래 JSON 만 출력하세요(설명·코드펜스 금지):
{"ranked": [{"productId": int, "rationale": "한글 40자 이내 1문장 근거"}], "overallComment": "전체 1~2문장 코멘트"}
규칙:
- productId 는 반드시 후보 목록(CANDIDATES)에 있는 값만 쓰세요. 없는 id 를 만들지 마세요.
- 후보가 실제로 갖지 않은 속성(브랜드·평점 등)을 근거로 주장하지 마세요.
- rationale 은 한글 40자 이내 1문장으로 간결하게 — 개행 없이.
- 가장 적합한 순으로 정렬하고 상위만 남기세요."""


async def rerank(
    llm: LLMClient,
    *,
    query: str,
    candidates: list[SpringProduct],
    profile_summary: str | None,
    tier: str,
    expose_max: int,
) -> RerankResult:
    """Sonnet 1회 호출로 재랭킹 결과를 산출한다(후보 외 id 는 코드로 제거)."""
    cand = [
        {
            "productId": c.product_id,
            "name": c.name,
            "brand": c.brand,
            "price": c.price,
            "rating": c.rating,
            "category": c.category,
        }
        for c in candidates
    ]
    prof = profile_summary or "(없음)"
    user = (
        f"PROFILE_SUMMARY: {prof}\nQUERY: {query}\n"
        f"CANDIDATES: {json.dumps(cand, ensure_ascii=False)}"
    )

    raw = await llm.complete(system=_SYSTEM, user=user, tier=tier, max_tokens=1500)
    data = extract_json(raw)

    valid_ids = {c.product_id for c in candidates}
    ranked: list[tuple[int, str]] = []
    seen: set[int] = set()
    for item in data.get("ranked") or []:
        if not isinstance(item, dict):
            continue
        pid = item.get("productId")
        if not isinstance(pid, int) or pid not in valid_ids or pid in seen:
            continue  # 후보 외/중복 id 제거 (REQ-REC-081)
        seen.add(pid)
        ranked.append((pid, str(item.get("rationale") or "")))
        if len(ranked) >= expose_max:
            break

    if not ranked:
        raise LLMError("rerank 가 유효한 후보 id 를 내지 않음")
    return RerankResult(ranked=ranked, overall_comment=str(data.get("overallComment") or ""))
