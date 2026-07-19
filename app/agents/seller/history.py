"""판매자 분석 이력 저장소 (4-3 — SPEC-SELLER-001 §9.1·§6.3).

역할 3가지:
1. **save_history**: 분석 파이프라인 성공(kind=report) 시 질문·분석 유형·기간·보고서
   요약·**구조화 recommendations** 를 pg-profile 에 저장한다 — §6.3 "N번 적용해줘"의
   조회 원천이자 planner 이력 주입(§9.1) 재료.
2. **load_recent / build_planner_input**: 최근 N건(seller_history_recent_n)을
   planner **입력 메시지**에 주입한다 — PLANNER_PROMPT 는 불변(2026-07-19 확정).
3. **apply_recommendation**: "N번 적용해줘"(입구 코드 선판정, 2026-07-20 사용자 확정)
   → 최신 이력의 recommendations[N-1] 을 **대화 재해석 없이** DraftProposal 로 변환,
   before 는 I-9 현재값으로 채워 4-2 HITL 경로(validate→start_draft→confirm)에 합류.

저장 구조(SPEC §9.1 각색): AsyncPostgresStore(pg-profile) 네임스페이스
("sellers", {sellerId}) + 키 "analysis_history" 에 **최신순 목록 1건**으로 보관 —
per-item 키 대신 단일 목록을 쓰는 이유는 (a) "N번"의 기준인 '가장 최근 분석'과
'최근 N건' 조회가 원자적 1회 읽기가 되고 (b) store.asearch 의 정렬 비보장을 피하기
위해서다(정렬을 코드가 소유). 상한 seller_history_max_items 로 잘라 무한 성장 방지.
동시 분석 2건의 읽고-쓰기 경합은 MVP 허용(마지막 쓰기 승리 — 이력은 부가 데이터).
워커별 탐지 상세 테이블(analysis_detections)은 4-3 범위 밖(SPEC §9.1, 고도화).

checkpointer(hitl.py)와 동일한 dev 폴백 규약: pg-profile 연결 실패 시 InMemoryStore
+ 경고 1회, 운영(auth_mode=jwks)은 폴백 금지.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel, Field

from app.agents.seller import hitl
from app.agents.seller.context import SellerContext
from app.agents.seller.schemas import (
    ActionRecommendation,
    DraftChange,
    DraftProposal,
    RecommendationSet,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_NAMESPACE_ROOT = "sellers"
_HISTORY_KEY = "analysis_history"


class HistoryEntry(BaseModel):
    """분석 이력 1건 — recommendations 는 §6.3 조회를 위해 **구조화 그대로** 보존한다."""

    question: str
    analyses: list[str] = Field(default_factory=list)
    date_from: str  # ISO date — 분석 기간(planner 주입·4-4 캐시 동일 기간 판정 재료)
    date_to: str
    report_summary: str  # 전문이 아닌 요약(절단) — SPEC §9.1, 전문 재활용은 4-4 캐시 소관
    recommendations: dict = Field(default_factory=dict)  # RecommendationSet dump(순서=N번 계약)
    created_at: str  # ISO8601(UTC)


# ── store 싱글턴 (hitl.checkpointer 와 동일 규약 — set_store 테스트 주입) ─────────

_store: BaseStore | None = None
_store_ctx: object | None = None  # AsyncPostgresStore cm — 앱 수명 동안 GC 방지
_fallback_warned = False


def set_store(store: BaseStore | None) -> None:
    """store 교체(테스트용) — None 이면 다음 사용 시 재초기화한다."""
    global _store, _store_ctx
    _store = store
    _store_ctx = None


async def _get_store() -> BaseStore:
    """AsyncPostgresStore(pg-profile) 지연 초기화 — 실패 시 dev 한정 InMemoryStore 폴백."""
    global _store, _store_ctx, _fallback_warned
    if _store is None:
        settings = get_settings()
        try:
            from langgraph.store.postgres.aio import AsyncPostgresStore

            ctx = AsyncPostgresStore.from_conn_string(settings.profile_db_url)
            store = await asyncio.wait_for(
                ctx.__aenter__(), timeout=settings.seller_checkpoint_connect_timeout_s
            )
            await store.setup()
            _store_ctx = ctx
            _store = store
        except Exception as exc:
            if settings.auth_mode == "jwks":
                raise  # 운영 — 폴백 금지(이력·추천 적용이 조용히 증발하면 안 된다)
            if not _fallback_warned:
                logger.warning(
                    "pg-profile store 연결 실패(%s) — InMemoryStore 폴백 "
                    "(dev 전용: 프로세스 재시작 시 분석 이력 증발)",
                    exc,
                )
                _fallback_warned = True
            _store = InMemoryStore()
    return _store


def _namespace(seller_id: str) -> tuple[str, str]:
    return (_NAMESPACE_ROOT, seller_id)


# ── 저장·조회 ────────────────────────────────────────────────────────────────────


async def save_history(
    seller_id: str,
    *,
    question: str,
    analyses: list[str],
    date_from: str,
    date_to: str,
    report: str,
    recommendations: RecommendationSet,
) -> None:
    """분석 1건을 최신순 목록 맨 앞에 저장한다 (compose 후 — orchestrator 소관 호출).

    보고서는 seller_history_report_max_chars 로 절단(요약) — planner 주입·이력
    맥락 용도라 전문이 필요 없다. 목록은 seller_history_max_items 로 자른다.
    """
    settings = get_settings()
    entry = HistoryEntry(
        question=question,
        analyses=analyses,
        date_from=date_from,
        date_to=date_to,
        report_summary=report[: settings.seller_history_report_max_chars],
        recommendations=recommendations.model_dump(),
        created_at=datetime.now(UTC).isoformat(),
    )
    store = await _get_store()
    namespace = _namespace(seller_id)
    item = await store.aget(namespace, _HISTORY_KEY)
    items: list[dict] = list(item.value.get("items", [])) if item else []
    items.insert(0, entry.model_dump())
    del items[settings.seller_history_max_items :]
    await store.aput(namespace, _HISTORY_KEY, {"items": items})


async def load_recent(seller_id: str, n: int | None = None) -> list[HistoryEntry]:
    """최근 n건(기본 seller_history_recent_n)을 최신순으로 반환한다."""
    limit = n if n is not None else get_settings().seller_history_recent_n
    store = await _get_store()
    item = await store.aget(_namespace(seller_id), _HISTORY_KEY)
    if not item:
        return []
    return [HistoryEntry.model_validate(raw) for raw in item.value.get("items", [])[:limit]]


# ── planner 이력 주입 (§9.1 — 프롬프트 불변, 입력 메시지에만 주입) ────────────────


def build_planner_input(question: str, entries: list[HistoryEntry]) -> str:
    """planner 입력 메시지 조립 — 이력이 없으면 질문 원문 그대로(기존 계약 불변).

    이력 블록은 참고 맥락일 뿐 분류 대상이 아님을 라벨([이번 질문])로 구분한다.
    """
    if not entries:
        return question
    lines = ["[최근 분석 이력]"]
    for entry in entries:
        day = entry.created_at[:10]
        lines.append(
            f"- {day} {'+'.join(entry.analyses)} ({entry.date_from}~{entry.date_to}) "
            f"질문: {entry.question}"
        )
    return "\n".join(lines) + f"\n\n[이번 질문] {question}"


# ── "N번 적용해줘" → draft 변환 (§6.3 — 대화 재해석 금지, 조회·변환은 전부 코드) ──


def _current_value_str(row: object, field: str) -> str:
    value = getattr(row, field, None)
    return "" if value is None else str(value)


async def apply_recommendation(
    n: int, context: SellerContext
) -> tuple[hitl.DraftRecord | None, str | None]:
    """최신 분석의 recommendations[n-1] 을 DraftRecord 로 변환 — 불성립은 (None, 안내).

    §6.3 절차: 이력 조회 → 인덱스 검증 → before 를 I-9 현재값으로 채워 DraftProposal
    구성 → hitl.validate_draft(4-2 재사용). 조회 실패·인덱스 불일치·적용 불가 유형은
    실행하지 않고 안내 문구를 돌려준다(되묻기). Spring 장애는 전파(호출부 error 경로).
    """
    entries = await load_recent(context.seller_id, 1)
    if not entries:
        return None, (
            "적용할 분석 추천 이력이 없습니다. 먼저 분석을 요청하시면 추천을 만들어 드립니다."
        )
    recommendations = RecommendationSet.model_validate(entries[0].recommendations)
    items: list[ActionRecommendation] = recommendations.recommendations
    if not items:
        return None, "가장 최근 분석에는 적용할 추천이 없었습니다. 새 분석을 요청해 주세요."
    if not 1 <= n <= len(items):
        return None, (f"최근 분석의 추천은 1번~{len(items)}번까지입니다. 몇 번을 적용할까요?")
    rec = items[n - 1]
    if not rec.changes:
        return None, (
            f"'{rec.title}' 추천은 자동 적용할 필드 변경이 없는 유형입니다. "
            "구체적으로 무엇을 바꿀지 말씀해 주시면 초안을 만들어 드리겠습니다."
        )

    row = await hitl._find_product(context.brand_id, rec.product_id)
    if row is None:
        return None, (
            f"추천 대상 상품(productId={rec.product_id})을 상품 목록에서 찾을 수 없습니다. "
            "상품이 삭제되었을 수 있어요. 다시 확인 후 요청해 주세요."
        )

    proposal = DraftProposal(
        op="update",
        product_id=rec.product_id,
        changes=[
            DraftChange(
                field=change.field,
                before=_current_value_str(row, change.field),  # 조회 시점 현재값 = diff 기준
                after=change.after,
            )
            for change in rec.changes
        ],
        summary=rec.title,
    )
    return hitl.validate_draft(proposal, seller_id=context.seller_id, brand_id=context.brand_id)
