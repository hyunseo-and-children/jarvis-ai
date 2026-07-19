"""decompose 노드 — Haiku 1회로 질의 분해 + intent 라우팅 (SPEC-RECOMMEND-001 §6.1, 이슈 #2 MVP).

멀티턴: 직전 필터를 규약 JSON 으로 함께 넘겨 병합(add/replace)을 **프롬프트 안에서** 처리한다
(REQ-REC-051 — 병합 로직을 코드에 두지 않음). intent(recommend/general)도 같은 출력에서 파생 —
별도 분류 호출을 두지 않는다(EX-7). reset/carry·priority·sources 태깅·예산 scope 정밀화는 후속.
"""

from __future__ import annotations

from app.agents.buyer.recommendation.state import RouteDecision, extract_json
from app.core.llm import LLMClient
from app.schemas.spring import ProductSearchFilters

_SYSTEM = """당신은 커머스 추천 어시스턴트의 질의 분해기입니다.
사용자 발화를 구조화 필터와 의미 쿼리로 분해하고, 상품 추천 요청인지 일반 대화인지 판별합니다.
반드시 아래 JSON 만 출력하세요(설명·코드펜스 금지):
{
  "intent": "recommend" | "general",
  "reply": "intent가 general일 때만 줄 짧은 한국어 답변, recommend면 빈 문자열",
  "case": 1 | 2 | 3,
  "semanticQuery": "정형 제약을 제외한 벡터 검색용 자연어",
  "filters": {
    "category": string|null, "priceMin": int|null, "priceMax": int|null,
    "brand": [string]|null, "ratingMin": number|null, "keyword": string|null
  }
}
규칙:
- 상품명이 뚜렷하면 case 1, 구조화 필터 위주면 case 2, 상황/목적("여행 갈 때 필요한 것")이면 case 3.
- 정확한 수치·카테고리 제약은 filters 에 넣고 semanticQuery 로 근사하지 마세요.
- PRIOR_FILTERS 가 있으면 후속 발화를 병합하세요: 범위를 좁히면 추가(add), 모순되면 교체(replace).
- 추천과 무관한 잡담·질문이면 intent=general 로 하고 reply 에 짧게 답하세요."""


async def decompose(
    llm: LLMClient,
    *,
    query: str,
    prior_filters: ProductSearchFilters | None,
    profile_summary: str | None,
    model: str,
) -> RouteDecision:
    """Haiku 1회 호출로 병합 필터·의미쿼리·case·intent 를 산출한다.

    prior_filters(직전 턴 누적)를 규약 JSON 으로 프롬프트에 실어 병합을 위임한다.
    LLM 오류/타임아웃/JSON 파싱 실패는 LLMError 로 전파 — 상위가 error 이벤트로 낸다.
    """
    import json

    prior_json = (
        "null"
        if prior_filters is None
        else json.dumps(prior_filters.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False)
    )
    prof = profile_summary or "(없음)"
    user = f"PRIOR_FILTERS: {prior_json}\nPROFILE_SUMMARY: {prof}\nUSER_MESSAGE: {query}"

    raw = await llm.complete(system=_SYSTEM, user=user, model=model, max_tokens=800)
    data = extract_json(raw)

    intent = "general" if data.get("intent") == "general" else "recommend"
    filters = ProductSearchFilters.model_validate(data.get("filters") or {})
    return RouteDecision(
        intent=intent,
        filters=filters,
        semantic_query=str(data.get("semanticQuery") or query),
        case=int(data.get("case") or 2),
        reply=str(data.get("reply") or ""),
    )
