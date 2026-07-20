"""구매자 챗봇 그래프 진입점 (SPEC-RECOMMEND-001, 이슈 #2 MVP 슬라이스).

흐름 (product.md 결정 12-A / structure.md §3):
    entry → 프로필 조회(reader, 동기) → decompose(Haiku 1회, intent 라우팅) →
        - recommend: 추천 서브그래프(decompose→search(Spring 위임)→rerank→push, 경로 B)
        - general  : fallback 서브그래프(일반 대화)

멀티턴: 스레드별 누적 필터를 ThreadFilterStore(인메모리 placeholder)에 신원 스코프 키로 보관 —
프로덕션은 LangGraph Postgres checkpointer(§6.3, conversation.py 와 동일 이관 패턴).
장바구니 서브그래프(결정 7, I-2/I-18)는 이슈 #3 소관 — 본 슬라이스 미포함.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.agents.buyer._frames import sse
from app.agents.buyer.cart.graph import stream_cart_add, stream_cart_view
from app.agents.buyer.cart.state import get_cart_store
from app.agents.buyer.fallback import stream_fallback
from app.agents.buyer.recommendation.decompose import decompose
from app.agents.buyer.recommendation.state import get_revert_store
from app.agents.buyer.recommendation.graph import stream_recommendation
from app.agents.profile.builder import record_remember
from app.agents.profile.gate import is_remember_command
from app.agents.profile.reader import read_profile_summary
from app.agents.profile.store import get_profile_store
from app.core.config import get_settings
from app.core.conversation import conversation_key
from app.core.llm import LLMError, get_llm
from app.agents.buyer.recommendation.state import CartIntent
from app.schemas.chat import DoneData, ErrorData
from app.schemas.spring import ProductSearchFilters
from app.services import search_service, spring_client


class ThreadFilterStore:
    """스레드별 누적 필터(멀티턴) — 인메모리 placeholder.

    프로덕션은 LangGraph Postgres checkpointer 로 이관한다(§6.3). 키는 신원 스코프
    (conversation_key: owner:thread_id) — 타인 스레드 필터 열람 금지(IDOR 방지, §2.6).
    """

    def __init__(self) -> None:
        self._filters: dict[str, ProductSearchFilters] = {}

    def get(self, key: str) -> ProductSearchFilters | None:
        return self._filters.get(key)

    def put(self, key: str, filters: ProductSearchFilters) -> None:
        self._filters[key] = filters

    def clear(self) -> None:
        self._filters.clear()


_thread_store = ThreadFilterStore()


def get_thread_store() -> ThreadFilterStore:
    """스레드 필터 스토어 싱글턴."""
    return _thread_store


def reset_thread_store() -> None:
    """테스트 격리용 — 누적 필터를 비운다."""
    _thread_store.clear()


def _is_timeout(exc: Exception) -> bool:
    """LLMError 메시지에서 타임아웃 여부를 추정한다(LLM_TIMEOUT vs LLM_UNAVAILABLE 매핑용)."""
    return "timeout" in str(exc).lower()


async def run_buyer_turn(
    request,
    identity,
    *,
    llm=None,
    search=None,
    push_fn=None,
    observer=None,
) -> AsyncIterator[str]:
    """구매자 1턴을 SSE 프레임으로 스트리밍한다(open_stream 이 감싸는 inner).

    llm/search/push_fn 미지정 시 라이브 기본값 — 테스트는 fake 를 주입한다.
    LLM 미구성(개발·CI)이면 네트워크 호출 없이 곧바로 LLM_UNAVAILABLE error 를 낸다.
    """
    settings = get_settings()
    llm = llm or get_llm()
    if llm is None:
        yield sse("error", ErrorData(code="LLM_UNAVAILABLE", message="LLM 이 구성되지 않았어요.").model_dump(by_alias=True))
        return
    search = search or search_service.search_catalog
    push_fn = push_fn or spring_client.push_recommendations

    # 멀티턴 누적 필터 로드 (신원 스코프 키)
    subject = identity.user_id or identity.subject
    thread_key = conversation_key(subject, request.thread_id)
    prior = _thread_store.get(thread_key)

    # 프로필 주입 (회원만, read-only) — 게스트/신규는 None(개인화 스킵, 결정 8)
    profile = None
    if not identity.is_guest and identity.user_id and not identity.seller_id:
        summary = read_profile_summary(identity.user_id)
        profile = summary.get("markdown") if summary else None
        # transient 세션 버퍼에 발화 누적(승격 전 격리, SPEC-PROFILE-001) — 세션 종료 델타 소스.
        # "기억해"류 명시 명령은 게이트 없이 즉시 승격(hot-path, REQ-PROF).
        pstore = get_profile_store()
        pstore.append_session_ctx(conversation_key(identity.user_id, request.session_id), request.message, cap=settings.profile_session_buffer_cap)
        if is_remember_command(request.message):
            record_remember(identity.user_id, request.message)

    # 장바구니 문맥 — 직전 추천(담기 productId 해소)·옵션 되물음 대기 상태.
    cart_store = get_cart_store()
    pending = cart_store.get_pending(thread_key)
    pending_dict = None
    if pending is not None:
        pending_dict = {
            "productId": pending.product_id,
            "options": [{"optionId": o.option_id, "name": o.name} for o in pending.options],
        }

    # decompose — Haiku 1회 (intent 4-way 라우팅 + 필터 + 장바구니 의도)
    if observer is not None:
        observer.record_model_call(settings.model_for_tier("fast"))
    try:
        decision = await decompose(
            llm,
            query=request.message,
            prior_filters=prior,
            profile_summary=profile,
            tier="fast",
            last_recommendations=cart_store.get_last_reco(thread_key),
            pending_cart=pending_dict,
        )
    except LLMError as exc:
        code = "LLM_TIMEOUT" if _is_timeout(exc) else "LLM_UNAVAILABLE"
        yield sse("error", ErrorData(code=code, message="질의를 이해하지 못했어요.").model_dump(by_alias=True))
        return

    # 되물음 대기 중 사용자가 담기 아닌 의도로 전환(취소·조회·추천)하면 stale pending 을 정리한다
    # (프롬프트가 약속한 "옛 상품에 갇히지 않게"와 실제 동작 일치).
    if decision.intent != "cart_add" and pending is not None:
        cart_store.clear_pending(thread_key)

    if decision.intent == "general":
        async for frame in stream_fallback(decision, observer=observer):
            yield frame
        yield sse("done", DoneData(finish_reason="stop").model_dump(by_alias=True))
        return

    if decision.intent == "cart_view":
        async for frame in stream_cart_view(identity=identity, observer=observer):
            yield frame
        return

    if decision.intent == "cart_add":
        allowed = {pid for pid, _ in cart_store.get_last_reco(thread_key)}
        async for frame in stream_cart_add(
            identity=identity,
            cart=decision.cart or CartIntent(),
            cart_store=cart_store,
            thread_key=thread_key,
            settings=settings,
            allowed_product_ids=allowed,
            observer=observer,
        ):
            yield frame
        return

    # recommend — 멀티턴 병합 필터는 추천 intent 에서만 저장(담기/조회가 덮어쓰지 않게).
    _thread_store.put(thread_key, decision.filters)
    # 소모품 억제 되돌리기(결정 14-F) — 이번 턴 revert + 스레드 누적을 합쳐 억제 제외.
    # LLM 이 뽑은 임의 문자열을 무한 누적하지 않게 소모품 화이트리스트(억제 대상)와 대조해 통과분만 저장.
    revert_store = get_revert_store()
    consumable_set = set(settings.consumable_categories)
    revert_store.add(thread_key, [c for c in decision.revert_categories if c in consumable_set])
    reverted = revert_store.get(thread_key)
    async for frame in stream_recommendation(
        request=request,
        decision=decision,
        llm=llm,
        search=search,
        push_fn=push_fn,
        identity=identity,
        profile=profile,
        settings=settings,
        reverted_categories=reverted,
        cart_store=cart_store,
        thread_key=thread_key,
        observer=observer,
    ):
        yield frame
