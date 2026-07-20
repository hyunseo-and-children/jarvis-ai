"""추천 파이프라인 스트리밍 (SPEC-RECOMMEND-001 §5.3/§6, 이슈 #2 MVP 슬라이스).

decompose 산출(RouteDecision) 이후: conditions → search(Spring 위임) → rerank(Sonnet) →
근거 token → push(I-21) → products.ready(경로 B) → done.
degrade(§7): SEARCH_FAILED(error·종료) / rerank 실패→검색순서 폴백 / push 실패→products.ready 스킵.
SSE 는 상품 카드를 싣지 않는다(경로 B) — products.ready 는 {sessionId, listId} 상관키만.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.agents.buyer._frames import sse
from app.agents.buyer.recommendation.rerank import rerank
from app.agents.buyer.recommendation.state import RouteDecision, build_condition_chips
from app.core.llm import LLMClient, LLMError
from app.services import spring_client
from app.schemas.chat import (
    ConditionsData,
    DoneData,
    ErrorData,
    ProductsReadyData,
    RevertRef,
    SuggestionChip,
    SuggestionsData,
    TokenData,
)
from app.schemas.spring import ProductSearchResult, RecommendationPush
from app.services.spring_client import SpringUnavailableError

_INACTIVE_STATUSES = frozenset(
    {"CANCELED", "CANCELLED", "RETURNED"}
)  # 보유 아님(철자 양쪽 — spec §4.7 혼용) → dedup 제외 대상 아님


def _now() -> datetime:
    """현재 시각 — naive-UTC(ordered_at 정규화와 동일 기준으로 비교, 테스트 주입 지점)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def stream_recommendation(
    *,
    request,
    decision: RouteDecision,
    llm: LLMClient,
    search,
    push_fn,
    identity=None,
    profile: str | None,
    settings,
    get_purchases_fn=None,
    reverted_categories=frozenset(),
    cart_store=None,
    thread_key: str | None = None,
    observer=None,
) -> AsyncIterator[str]:
    """추천 서브그래프 스트림. 프레임(SSE str)을 순서대로 산출한다."""
    # conditions 칩 (병합 필터에서 결정론적 파생)
    chips = build_condition_chips(decision.filters)
    yield sse("conditions", ConditionsData(chips=chips).model_dump(by_alias=True))

    # dedup 소스(I-19)와 검색(§4.6)을 **병렬 실행** — §4.7 지연 가드(순차 시 최악 6s, first-token 예산 잠식).
    # dedup 은 검색 응답 뒤 사후필터라 두 호출은 독립적이다. 각 호출이 자체 실패를 삼켜 gather 는 안 깨진다.
    async def _run_search() -> ProductSearchResult | None:
        try:
            return await search(decision.filters, exclude_product_ids=None)
        except SpringUnavailableError:
            return None

    async def _fetch_purchases():
        # 게스트/비회원/판매자/비숫자 sub 는 스킵, I-19 실패는 degrade(dedup 없이 진행, §4.7).
        # [IDOR] role==SELLER 는 user_id=sub·seller_id=sub — 판매자 sub 를 memberId 로 쓰면 안 됨.
        if identity is None or identity.is_guest or not identity.user_id or identity.seller_id:
            return None
        try:
            uid = int(identity.user_id)
        except (ValueError, TypeError):
            return None
        fn = get_purchases_fn or spring_client.get_recent_purchases
        try:
            return await fn(uid)
        except SpringUnavailableError:
            return None

    search_result, purchases = await asyncio.gather(_run_search(), _fetch_purchases())
    if search_result is None:  # 검색 실패 → SEARCH_FAILED(종료)
        yield sse(
            "error",
            ErrorData(code="SEARCH_FAILED", message="상품 검색에 실패했어요.").model_dump(
                by_alias=True
            ),
        )
        return

    # 최근 구매(윈도우·취소반품 필터) → exact 제외 + 소모품 카테고리 억제(결정 14-F).
    exclude_ids: set[int] = set()
    cat_samples: dict[str, str] = {}  # 억제 소모품 카테고리 -> 최근 구매 상품명(되돌리기 칩 라벨용)
    if purchases is not None:
        since = _now() - timedelta(days=settings.dedup_recent_days)
        recent = purchases.recent_items(since=since, exclude_statuses=_INACTIVE_STATUSES)
        exclude_ids = {i.product_id for i in recent}
        consumables = set(settings.consumable_categories)
        for i in recent:
            # 소모품 카테고리인데 사용자가 되돌리지 않은 것만 억제 대상.
            if i.category and i.category in consumables and i.category not in reverted_categories:
                cat_samples.setdefault(i.category, i.product_name or i.category)

    # 사후필터: exact productId 제외 + 소모품 카테고리 억제(§4.7, C-15).
    result: ProductSearchResult = search_result
    had_candidates = bool(result.products)
    suppressed_by_cat: dict[str, int] = {}
    kept = []
    for product in result.products:
        if product.product_id in exclude_ids:
            continue
        if product.category in cat_samples:
            suppressed_by_cat[product.category] = suppressed_by_cat.get(product.category, 0) + 1
            continue
        kept.append(product)
    result = ProductSearchResult(products=kept, total_count=len(kept))

    # 되돌리기 칩 — 억제된 소모품 카테고리별(estCount==0 제외, §3.1).
    # estCount 는 **이번 검색 응답 내 억제 수**(page-local 근사) — I-1 엔 totalCount 가 없어(C-15 🔴)
    # DB 전체 매칭 수를 알 수 없으므로 가용한 최선의 추정치를 쓴다.
    revert_chips = [
        SuggestionChip(
            label=f"{cat_samples[c]}은 최근 구매 — 다시 추천받기",
            revert=RevertRef(category=c),
            est_count=n,
        )
        for c, n in suppressed_by_cat.items()
        if n > 0
    ]

    candidates = result.products
    if not candidates:
        # 3분기: 검색 자체 0건 / 소모품 카테고리 억제로 비워짐 / exact 최근구매로 비워짐 — 원인별 안내.
        if not had_candidates:
            text = "조건에 맞는 상품을 찾지 못했어요. 조건을 조금 바꿔볼까요?"
        elif suppressed_by_cat:
            text = "최근 구매하신 카테고리라 결과를 가렸어요. 아래에서 되돌리거나 다른 조건으로 찾아볼까요?"
        else:
            text = "찾은 상품이 모두 최근에 구매하신 것들이에요. 다른 상품을 추천해 드릴까요?"
        yield sse("token", TokenData(text=text).model_dump(by_alias=True))
        if revert_chips:  # 전부 억제됐어도 되돌리기 칩은 준다(사용자가 복원 가능)
            yield sse("suggestions", SuggestionsData(chips=revert_chips).model_dump(by_alias=True))
        yield sse("done", DoneData(finish_reason="zero_result").model_dump(by_alias=True))
        return

    # rerank — Sonnet 1회. 실패/타임아웃/유효후보 0건 시 검색순서 상위 N 으로 degrade(하드 제약 유지).
    if observer is not None:
        observer.record_model_call(settings.model_for_tier("smart"))
    try:
        rr = await rerank(
            llm,
            query=request.message,
            candidates=candidates,
            profile_summary=profile,
            tier="smart",
            expose_max=settings.expose_max,
        )
        ranked_ids = [pid for pid, _ in rr.ranked]
        comment = rr.overall_comment
    except LLMError:
        ranked_ids = [p.product_id for p in candidates[: settings.expose_max]]
        comment = "요청하신 조건으로 찾은 상품들이에요."

    # 노출 개수 보정 — rerank 가 expose_min 미만을 내면 검색순서(하드 제약 반영)로 채우고
    # expose_max 로 상한한다(REQ-REC-021 5~8개 계약, 후보가 부족하면 있는 만큼).
    if len(ranked_ids) < settings.expose_min:
        have = set(ranked_ids)
        for product in candidates:
            if product.product_id not in have:
                ranked_ids.append(product.product_id)
                have.add(product.product_id)
                if len(ranked_ids) >= settings.expose_min:
                    break
    ranked_ids = ranked_ids[: settings.expose_max]

    if comment:
        yield sse("token", TokenData(text=comment).model_dump(by_alias=True))

    if revert_chips:  # 소모품 카테고리 억제 되돌리기 칩(결정 14-F)
        yield sse("suggestions", SuggestionsData(chips=revert_chips).model_dump(by_alias=True))

    # push — I-21(경로 B). 성공 시에만 products.ready emit(§3.3).
    list_id = uuid4().hex
    push = RecommendationPush(
        session_id=request.session_id, list_id=list_id, product_ids=ranked_ids
    )
    try:
        pushed = bool(await push_fn(push))
    except SpringUnavailableError:
        pushed = False
    if pushed:
        yield sse(
            "products.ready",
            ProductsReadyData(session_id=request.session_id, list_id=list_id).model_dump(
                by_alias=True
            ),
        )
        # 직전 추천을 장바구니 담기(productId 해소, 경로 B)용으로 보관 — **push 성공 후에만**.
        # push 실패로 카드가 노출되지 않았으면 저장하지 않아 "그거 담아줘"가 미노출 상품을 담지 않는다.
        if cart_store is not None and thread_key is not None:
            name_by_id = {p.product_id: p.name for p in candidates}
            await cart_store.set_last_reco(
                thread_key, [(pid, name_by_id.get(pid, "")) for pid in ranked_ids]
            )
    else:
        # push 실패 → products.ready 없음. rerank 코멘트가 "찾았다"고 했으니 목록 지연을 고지하고
        # 정상 종료한다(경로 B 실패 계약 — error 아님, done 유지).
        yield sse(
            "token",
            TokenData(
                text="목록을 준비하는 데 문제가 있었어요. 잠시 후 다시 시도해 주세요."
            ).model_dump(by_alias=True),
        )

    yield sse("done", DoneData(finish_reason="stop").model_dump(by_alias=True))
