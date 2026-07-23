"""추천 파이프라인 스트리밍 (SPEC-RECOMMEND-001 §5.3/§6, 이슈 #2 MVP 슬라이스).

decompose 산출(RouteDecision) 이후: conditions → search(Spring 위임) → rerank(Sonnet) →
근거 token → push(I-21) → products.ready(경로 B) → done.
degrade(§7): SEARCH_FAILED(error·종료) / rerank 실패→검색순서 폴백 / push 실패→products.ready 스킵.
SSE 는 상품 카드를 싣지 않는다(경로 B) — products.ready 는 {sessionId, listId} 상관키만.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.agents.buyer._frames import sse
from app.agents.buyer.recommendation.rerank import rerank
from app.agents.buyer.recommendation.state import RouteDecision, build_condition_chips
from app.core.llm import LLMClient, LLMError, resolve_model_id
from app.core.text import _strip_unsafe
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
from app.schemas.spring import (
    ProductSearchResult,
    RecoReason,
    RecommendationPush,
    SpringProduct,
)
from app.services.spring_client import SpringUnavailableError

logger = logging.getLogger(__name__)

_INACTIVE_STATUSES = frozenset(
    {"CANCELED", "CANCELLED", "RETURNED"}
)  # 보유 아님(철자 양쪽 — spec §4.7 혼용) → dedup 제외 대상 아님


def _now() -> datetime:
    """현재 시각 — naive-UTC(ordered_at 정규화와 동일 기준으로 비교, 테스트 주입 지점)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sanitize_reason(text: str, max_len: int) -> str:
    """I-21 reason 방어 정제 — 제어·포맷 문자 제거 + 연속 공백 접기 + 안전 상한 truncate.

    rerank rationale 은 판매자 입력(상품명·브랜드)에 영향받는 자유 텍스트라 신뢰경계(→Spring→CH-5→FE)를
    넘기 전에 정제한다(§4.2 이슈 #61). (1) 비-whitespace 제어문자(NUL·ESC·DEL 등)와 zero-width·bidi
    포맷 문자를 제거하고(`\\s` 로는 안 걸리는 표시 조작/주입 문자), (2) 남은 공백류(개행 포함)를 단일
    공백으로 접은 뒤, (3) max_len 방어캡으로 자른다. 표시 목표(한글 40자)는 프롬프트로 유도하고, max_len
    은 비정상 초장문·인젝션성 텍스트를 막는 넉넉한 캡이라 정상값은 걸리지 않는다. 초과 시 말줄임표 부착.
    """
    collapsed = _strip_unsafe(text)
    if max_len <= 0:  # 오설정 방어 — 0 이하 상한은 음수 슬라이스로 뒤집히지 않게 차단
        return ""
    if len(collapsed) > max_len:
        collapsed = collapsed[: max_len - 1].rstrip() + "…"
    return collapsed


def _merge_fanout_results(results: list[ProductSearchResult], cap: int) -> ProductSearchResult:
    """fan-out leg 결과를 round-robin 인터리브 + productId dedup + cap 절단으로 병합한다(§6).

    leg 순서대로 한 상품씩 번갈아 뽑아(한 카테고리가 rerank 입력을 독점하지 않게) 최초 등장
    productId 만 남기고, cap 으로 절단해 rerank 입력 상한을 지킨다. 빈 leg 는 건너뛴다.
    """
    lists = [r.products for r in results]
    depth = max((len(pl) for pl in lists), default=0)
    seen: set[int] = set()
    merged: list[SpringProduct] = []
    for i in range(depth):
        for pl in lists:
            if i >= len(pl):
                continue
            product = pl[i]
            if product.product_id in seen:
                continue
            seen.add(product.product_id)
            merged.append(product)
    # slice 절단 — decompose 의 _parse_category_queries·_dedup_truncate 와 동일 규약
    # (cap<=0 이면 정확히 0개; append 후 체크는 첫 상품이 남아 절단 의미가 어긋난다, PR #73 리뷰).
    merged = merged[:cap]
    return ProductSearchResult(products=merged, total_count=len(merged))


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
    # conditions 칩 (병합 필터에서 결정론적 파생) — fan-out 이면 canonical 전체를 표시한다(§3.1)
    chips = build_condition_chips(
        decision.filters, categories=[c for c, _ in decision.category_legs]
    )
    yield sse("conditions", ConditionsData(chips=chips).model_dump(by_alias=True))

    # dedup 소스(I-19)와 검색(§4.6)을 **병렬 실행** — §4.7 지연 가드(순차 시 최악 6s, first-token 예산 잠식).
    # dedup 은 검색 응답 뒤 사후필터라 두 호출은 독립적이다. 각 호출이 자체 실패를 삼켜 gather 는 안 깨진다.
    async def _run_search() -> ProductSearchResult | None:
        legs = decision.category_legs
        if not legs:
            # 카테고리 매핑 결과 없음(매핑 degrade·비-매핑 경로) → 단일 filters 검색(기존 경로).
            try:
                return await search(decision.filters, exclude_product_ids=None)
            except SpringUnavailableError:
                return None
            except Exception as exc:  # noqa: BLE001 - 예상외 예외도 삼켜 SEARCH_FAILED 로 degrade
                # 검색 호출이 SpringUnavailable 아닌 예외를 던져도 SSE 스트림을 미처리 예외로 죽이지
                # 않는다 — None → 상위에서 SEARCH_FAILED(§6). CancelledError(BaseException)는 전파.
                logger.warning("search_failed", extra={"reason": str(exc)})
                return None

        # fan-out — canonical 카테고리마다 leg 를 병렬 검색(§6). leg 별 filters 는 category·
        # keyword(그 카테고리 query, 없으면 base)·size 만 교체한다.
        # 단일 카테고리(leg 1개)는 후보 폭을 좁히지 않게 merge_cap(=단일 rerank 입력 예산)을 쓰고,
        # 멀티 fan-out 일 때만 leg 당 per_cat_limit 으로 제한한다(합쳐서 merge_cap 로 재절단).
        leg_limit = (
            settings.category_fanout_per_cat_limit
            if len(legs) > 1
            else settings.category_fanout_merge_cap
        )

        async def _leg(canonical: str, query: str | None) -> ProductSearchResult | None:
            # leg 전체를 try 로 감싼다 — model_copy·search 어디서 실패해도 그 leg 만 드롭한다.
            try:
                leg_filters = decision.filters.model_copy(
                    update={
                        "category": canonical,
                        "keyword": query or decision.filters.keyword,
                        "limit": leg_limit,
                    }
                )
                return await search(leg_filters, exclude_product_ids=None)
            except SpringUnavailableError:
                return None  # leg 별 실패는 삼켜 다른 leg 는 계속(§6)
            except Exception as exc:  # noqa: BLE001 - 예상외 예외도 그 leg 만 격리(SSE 스트림 보호)
                # SpringUnavailable 아닌 예외가 gather → 스트림 상위로 전파돼 SSE 전체가 죽지 않게
                # 격리한다. return_exceptions 대신 여기서 잡아 로그 + None — CancelledError(BaseException)
                # 는 전파돼 협조적 취소가 보존된다. category_mapping fan-out(§6)과 격리 목적 일관.
                logger.warning("search_leg_failed", extra={"reason": str(exc)})
                return None

        leg_results = await asyncio.gather(*(_leg(c, q) for c, q in legs))
        survived = [r for r in leg_results if r is not None]
        if not survived:  # 전량 leg 실패 → SEARCH_FAILED(§6)
            return None
        return _merge_fanout_results(survived, settings.category_fanout_merge_cap)

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
        except Exception as exc:  # noqa: BLE001 - I-19 실패는 degrade(dedup 없이 진행, SSE 유지)
            # 최근구매 조회가 예상외 예외를 던져도 추천 스트림을 죽이지 않는다 — None → dedup 스킵(§4.7).
            logger.warning("purchases_fetch_failed", extra={"reason": str(exc)})
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
            label=_strip_unsafe(f"{cat_samples[c]}은 최근 구매 — 다시 추천받기"),
            revert=RevertRef(category=_strip_unsafe(c)),
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

    # rerank — smart tier 1회. 실패/타임아웃/유효후보 0건 시 검색순서 상위 N 으로 degrade(하드 제약 유지).
    if observer is not None:
        observer.record_model_call(resolve_model_id(settings, "smart"))
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
        reason_by_id = dict(rr.ranked)  # 상품별 근거(§4.2) — (productId, rationale) 튜플 → 맵
        comment = _strip_unsafe(rr.overall_comment)
    except LLMError:
        ranked_ids = [p.product_id for p in candidates[: settings.expose_max]]
        reason_by_id = {}  # degrade 경로엔 rerank 근거 없음 — reasons 는 빈 배열(계약상 선택)
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
    # reasons — 근거가 있는 상품만(빈 rationale·expose_min 보충 상품은 제외). productId 로 키잉,
    # 순서 권위는 product_ids 라 정렬 불필요(부분집합 허용, §4.2 이슈 #61).
    # push(신뢰경계) 직전 정제 — 개행 제거·안전 상한(config, 판매자 입력 영향 자유 텍스트 방어).
    reasons = [
        RecoReason(product_id=pid, reason=cleaned)
        for pid in ranked_ids
        if (cleaned := _sanitize_reason(reason_by_id.get(pid, ""), settings.reason_max_len))
    ]
    push = RecommendationPush(
        session_id=request.session_id,
        list_id=list_id,
        product_ids=ranked_ids,
        reasons=reasons,
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
