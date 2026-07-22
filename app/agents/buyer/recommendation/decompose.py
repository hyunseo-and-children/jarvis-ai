"""decompose 노드 — Haiku 1회로 질의 분해 + intent 라우팅 (SPEC-RECOMMEND-001 §6.1, 이슈 #2 MVP).

멀티턴: 직전 필터를 규약 JSON 으로 함께 넘겨 병합(add/replace)을 **프롬프트 안에서** 처리한다
(REQ-REC-051 — 병합 로직을 코드에 두지 않음). intent(recommend/general)도 같은 출력에서 파생 —
별도 분류 호출을 두지 않는다(EX-7). reset/carry·priority·sources 태깅·예산 scope 정밀화는 후속.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.agents.buyer.recommendation.state import (
    CartIntent,
    CategoryQuery,
    RouteDecision,
    extract_json,
)
from app.core.llm import LLMClient, LLMError
from app.schemas.spring import ProductSearchFilters

_SYSTEM = """당신은 커머스 어시스턴트의 질의 분해기입니다.
사용자 발화를 분석해 intent 를 정하고, 추천이면 구조화 필터/의미쿼리를, 장바구니면 상품/옵션/수량을 산출합니다.
반드시 아래 JSON 만 출력하세요(설명·코드펜스 금지):
{
  "intent": "recommend" | "cart_add" | "cart_view" | "general",
  "reply": "intent가 general일 때만 줄 짧은 한국어 답변, 아니면 빈 문자열",
  "case": 1 | 2 | 3,
  "semanticQuery": "정형 제약을 제외한 벡터 검색용 자연어",
  "categoryQueries": [ {"category": string|null, "query": string|null} ],
  "filters": {
    "priceMin": int|null, "priceMax": int|null,
    "brand": [string]|null, "ratingMin": number|null, "keyword": string|null
  },
  "cart": { "productId": int|null, "optionId": int|null, "quantity": int },
  "revertCategories": [string]
}
규칙:
- intent 판별: 상품을 찾아달라는 요청이면 recommend, "담아줘/장바구니에 넣어"면 cart_add,
  "장바구니 보여줘/뭐 있어?"면 cart_view, 그 외 잡담·무관 질문이면 general.
- recommend: 정확한 수치 제약은 filters 에 넣고 semanticQuery 로 근사하지 마세요.
  PRIOR_FILTERS 가 있으면 병합(좁히면 add, 모순되면 replace)하세요.
- categoryQueries: 사용자가 원하는 상품/목적별로 **카테고리를 최대한 추출**하세요.
  단일 상품 질의("무선 이어폰")면 1개, 상황형 질의("유럽여행 준비물")면 필요한 카테고리를
  여러 개 나눠 담으세요(예: 여행용품·전자기기·의류). category 는 best-guess(정말 모르면 null),
  query 는 그 카테고리 검색용 짧은 키워드입니다. 카테고리는 최대 CATEGORY_FANOUT_MAX 개까지.
- cart_add: LAST_RECOMMENDATIONS(직전 추천 목록: productId+이름)에서 사용자가 가리킨 상품의
  productId 를 고르세요. 못 고르면 productId=null. quantity 기본 1.
- PENDING_CART(옵션 되물음 대기)가 있으면 보통 이번 발화는 옵션 답변입니다 — options 목록에서
  사용자 답에 맞는 optionId 를 골라 intent=cart_add, cart.optionId 로 주세요. 단,
  사용자가 다른 상품을 담으려 하면 LAST_RECOMMENDATIONS 의 그 productId 로 cart_add,
  담기를 취소·중단하려 하면 intent=general 로 전환하세요(옛 상품에 갇히지 않게).
- revertCategories: 사용자가 특정 카테고리를 \"다시 추천받기\"(되돌리기 칩) 하거나 최근 구매로
  가려진 카테고리를 다시 보고 싶어하면 그 카테고리명을 넣으세요(예: [\"조미료\"]). 아니면 [].
- general: intent=general, reply 에 짧게 답하세요."""


async def decompose(
    llm: LLMClient,
    *,
    query: str,
    prior_filters: ProductSearchFilters | None,
    profile_summary: str | None,
    tier: str,
    last_recommendations: list[tuple[int, str]] | None = None,
    pending_cart: dict | None = None,
    category_fanout_max: int = 5,
) -> RouteDecision:
    """Haiku 1회 호출로 intent(추천/담기/조회/일반)·필터·장바구니 의도를 산출한다.

    prior_filters(추천 멀티턴)·last_recommendations(담기 productId 해소)·pending_cart(옵션 되물음)를
    프롬프트에 실어 문맥을 위임한다. LLM 오류/타임아웃/JSON·스키마 파싱 실패는 LLMError 로 전파.
    """
    import json

    prior_json = (
        "null"
        if prior_filters is None
        else json.dumps(
            prior_filters.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False
        )
    )
    reco_json = json.dumps(
        [{"productId": pid, "name": name} for pid, name in (last_recommendations or [])],
        ensure_ascii=False,
    )
    pending_json = "null" if not pending_cart else json.dumps(pending_cart, ensure_ascii=False)
    prof = profile_summary or "(없음)"
    user = (
        f"CATEGORY_FANOUT_MAX: {category_fanout_max}\n"
        f"PRIOR_FILTERS: {prior_json}\n"
        f"LAST_RECOMMENDATIONS: {reco_json}\n"
        f"PENDING_CART: {pending_json}\n"
        f"PROFILE_SUMMARY: {prof}\n"
        f"USER_MESSAGE: {query}"
    )

    raw = await llm.complete(system=_SYSTEM, user=user, tier=tier, max_tokens=800)
    data = extract_json(raw)

    intent_raw = data.get("intent")
    intent = (
        intent_raw
        if intent_raw in ("recommend", "cart_add", "cart_view", "general")
        else "recommend"
    )
    # JSON 파싱은 됐지만 필드 값이 스키마와 안 맞을 수 있다 → extract_json 처럼 LLMError 로 통일해
    # 상위(graph.py)의 LLM_* error 이벤트로 흐르게 한다(첫 프레임 이전 raw 예외 → 500 방지).
    try:
        filters = ProductSearchFilters.model_validate(data.get("filters") or {})
        case = int(data.get("case") or 2)
        cart = _parse_cart(data.get("cart"))
        raw_revert = data.get("revertCategories")
        revert_categories = (
            [str(c) for c in raw_revert if isinstance(c, str) and c]
            if isinstance(raw_revert, list)
            else []
        )
        category_queries = _parse_category_queries(data.get("categoryQueries"), category_fanout_max)
    except (ValidationError, ValueError, TypeError) as exc:
        raise LLMError("decompose 필터/케이스/장바구니 파싱 실패") from exc
    return RouteDecision(
        intent=intent,
        filters=filters,
        semantic_query=str(data.get("semanticQuery") or query),
        case=case,
        reply=str(data.get("reply") or ""),
        cart=cart,
        revert_categories=revert_categories,
        category_queries=category_queries,
    )


def _parse_category_queries(raw: object, fanout_max: int) -> list[CategoryQuery]:
    """decompose 의 categoryQueries → list[CategoryQuery] (방식 A, 이슈 #59).

    리스트가 아니면 빈 리스트(그래프에서 발화 폴백). 각 원소 dict 에서 category(str|None)·
    query(str|None)를 관대 파싱하고, fanout_max 로 개수를 절단한다(하드코딩 금지 상한).
    """
    if not isinstance(raw, list):
        return []
    out: list[CategoryQuery] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cat = item.get("category")
        qry = item.get("query")
        out.append(
            CategoryQuery(
                raw_category=str(cat) if isinstance(cat, str) and cat else None,
                query=str(qry) if isinstance(qry, str) and qry else None,
            )
        )
        if len(out) >= fanout_max:
            break
    return out


def _as_int(value: object) -> int | None:
    """LLM JSON 변형(int/float/숫자문자열)을 관대하게 int 로 변환한다(bool 제외).

    LLM 이 "quantity": 2.0 이나 "2" 처럼 내보내도 조용한 폴백(수량 1·productId None) 없이
    의도대로 해석되게 한다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _parse_cart(raw: object) -> CartIntent | None:
    """decompose 의 cart 객체 → CartIntent (없거나 형식 오류면 빈 의도)."""
    if not isinstance(raw, dict):
        return CartIntent()
    qty = _as_int(raw.get("quantity"))
    return CartIntent(
        product_id=_as_int(raw.get("productId")),
        option_id=_as_int(raw.get("optionId")),
        # api-spec §4.1 수량 1~99 — 상한 초과 발화("100개")가 AddToCartRequest 검증에서
        # ValidationError 로 스트림을 끊지 않게 파싱 시점에 클램프한다.
        quantity=min(max(qty, 1), 99) if qty is not None else 1,
    )
