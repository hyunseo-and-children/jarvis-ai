"""판매자 상품 쓰기 HITL 실행 (4-2 — SPEC-SELLER-001 §6, api-spec §3.2 2-스트림).

설계 확정(2026-07-20 사용자 결정 3건):
1. **실행 주체 = 코드**: confirm resume 시 checkpoint 의 draft 를 코드가 그대로
   I-10/11/12(§4.5)에 매핑한다 — "보여준 diff == 실행하는 쓰기"(안전장치 ①)를
   구조로 보장하고, 실행 시점 LLM 은 0회다. 강의(06_Middleware/02-HITL-V1)의
   interrupt/Command(resume) 개념은 그대로 쓰되 HumanInTheLoopMiddleware(LLM
   도구 호출 재개)는 채택하지 않는다 — 실행 인자가 LLM 산물이면 안전장치 ①이
   프롬프트 품질에 걸리기 때문.
2. **checkpointer = AsyncPostgresSaver(pg-profile)**, dev 폴백 허용: 연결 실패 시
   InMemorySaver + 경고 1회(service_token dev 스킵 선례). 운영(auth_mode=jwks)은
   폴백 금지 — 프로세스 재시작 시 draft 증발은 운영에서 허용 불가.
3. **stale 검증(S-5 병존, REALIGN F7) = stock 제외 비교**: confirm 시점 I-9 재조회로
   changes[].before 를 재검증하되 stock_quantity 는 주문 재고 차감(F6)으로 자연
   변동하므로 제외한다. stock 변동은 실행 결과 안내에 현재값 표기로 보완.

안전장치 5종(§6.2) 구현 지점:
① draftId 바인딩 — thread_id=f"seller-draft:{draftId}", checkpoint 가 draft 원본 보유.
② 명시 액션만 — confirm 판정은 pipeline.parse_confirm_message(입구 ①, 코드 선판정).
③ 멱등성 — 실행 완료 스레드(result 보유) 재confirm 은 재실행 없이 안내만.
④ Spring 소유권 하드게이트 — brandId 불일치 confirm 은 존재 비노출 거절(+Spring 최종 방어).
⑤ 대기 TTL — created_at 기준 seller_draft_ttl_minutes 경과 시 만료 안내(실행 금지).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from app.agents.seller.schemas import DraftChange, DraftProposal
from app.core.config import get_settings
from app.schemas.spring import ProductCreate, ProductUpdate, SellerProductRow
from app.services.spring_client import get_spring_client

logger = logging.getLogger(__name__)

# ── draft 정규화 (스트림 1 — emit 전에 실행 가능성을 코드로 선검증) ──────────────

# after(str) → 정수 캐스팅 대상 필드(ProposedChange "수치도 문자열" 계약의 역변환 지점).
_INT_FIELDS = frozenset({"price", "original_price", "stock_quantity"})
_STATUS_VALUES = frozenset({"ON_SALE", "HIDDEN"})
# 도구 출력("가격 15,000원 재고 100건")을 옮겨적은 값 관용 처리용 단위 접미사.
_INT_SUFFIXES = ("원", "건", "개")
# C4 + D3(REALIGN, ⚠️ BE 미확인): create 는 image_url/status 지정 불가 —
# image_url 은 BE 기본값/NULL 처리 가정, status 는 I-10 이 ON_SALE 로 발급.
_CREATE_FORBIDDEN_FIELDS = frozenset({"image_url", "status"})
# I-10 필수 본문(api-spec §4.5) — 누락 draft 는 등록 자체가 불가하므로 되묻기.
_CREATE_REQUIRED_FIELDS = frozenset({"name", "price", "stock_quantity"})


class DraftRecord(BaseModel):
    """checkpoint 에 저장되는 draft 원본 — 실행(confirm resume)이 참조하는 유일한 정본.

    DraftProposal(LLM 출력)과 달리 draftId·신원·created_at 은 **코드가 발급**한다
    (ReportScore.total 과 같은 '계약값은 코드' 원칙). brand_id 는 confirm 시점
    요청 신원과 대조해 타 판매자의 draftId 추측 승인을 차단한다(안전장치 ④ 보강).
    """

    draft_id: str
    op: Literal["create", "update", "delete"]
    product_id: int | None = None
    changes: list[DraftChange] = Field(default_factory=list)
    summary: str = ""
    seller_id: str
    brand_id: str
    created_at: str  # ISO8601(UTC) — TTL(안전장치 ⑤) 판정 기준


def _parse_int(raw: str) -> int:
    """draft 의 문자열 수치를 정수로 — 콤마·공백·단위 접미사 관용, 그 외 실패는 ValueError."""
    text = raw.strip().replace(",", "").replace(" ", "")
    for suffix in _INT_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    if not text.isdigit():  # 가격·재고는 음수 없음 — 부호 미허용
        raise ValueError(f"정수로 해석할 수 없습니다: {raw!r}")
    return int(text)


def _typed_after(change: DraftChange) -> int | str:
    """changes[].after(str) → I-10/11 본문 타입. 실패는 ValueError 전파(호출부 되묻기)."""
    if change.field in _INT_FIELDS:
        return _parse_int(change.after)
    if change.field == "status":
        value = change.after.strip()
        if value not in _STATUS_VALUES:
            raise ValueError(f"status 는 ON_SALE/HIDDEN 만 가능합니다: {change.after!r}")
        return value
    return change.after


def validate_draft(
    proposal: DraftProposal, *, seller_id: str, brand_id: str
) -> tuple[DraftRecord | None, str | None]:
    """DraftProposal(LLM) → DraftRecord(실행 정본). 불성립은 (None, 되묻기 문구).

    emit 전에 검증하는 이유: 실행 불가능한 draft 를 FE diff 카드로 보여주고 confirm
    받은 뒤에야 실패하는 것보다, 스트림 1에서 되묻는 쪽이 계약(보여준 것==실행)에
    부합한다. 여기서 통과한 draft 는 confirm 시점에 캐스팅이 실패하지 않는다.
    """
    changes = list(proposal.changes)
    fields = {c.field for c in changes}

    if proposal.op in ("update", "delete") and proposal.product_id is None:
        return None, (
            "어느 상품을 변경할지 특정하지 못했습니다. 상품명이나 상품 번호를 알려주세요."
        )
    if proposal.op == "update" and not changes:
        return None, "무엇을 어떻게 바꿀지 파악하지 못했습니다. 변경할 내용을 알려주세요."
    if proposal.op == "create":
        forbidden = fields & _CREATE_FORBIDDEN_FIELDS
        if forbidden:
            return None, (
                "상품 등록 시에는 이미지·상태를 함께 지정할 수 없습니다. "
                "등록 후 수정으로 다시 요청해 주세요."
            )
        missing = _CREATE_REQUIRED_FIELDS - fields
        if missing:
            return None, (
                "상품 등록에는 상품명·가격·재고 수량이 필요합니다. "
                f"누락된 항목({', '.join(sorted(missing))})을 알려주세요."
            )
    for change in changes:
        try:
            _typed_after(change)
        except ValueError:
            return None, (
                f"'{change.field}' 값 '{change.after}' 을(를) 해석하지 못했습니다. "
                "값을 다시 확인해 주세요."
            )

    record = DraftRecord(
        draft_id=str(uuid.uuid4()),
        op=proposal.op,
        product_id=None if proposal.op == "create" else proposal.product_id,
        changes=changes,
        summary=proposal.summary,
        seller_id=seller_id,
        brand_id=brand_id,
        created_at=datetime.now(UTC).isoformat(),
    )
    return record, None


# ── stale 검증 (S-5 병존 — confirm 시점 I-9 재조회 대조) ─────────────────────────

# 주문 재고 차감(F6)으로 판매자 행위 없이 변하는 필드 — 비교 제외(2026-07-20 확정).
_STALE_EXEMPT_FIELDS = frozenset({"stock_quantity"})


def find_stale_changes(
    row: SellerProductRow, changes: list[DraftChange]
) -> list[tuple[str, str, str]]:
    """draft.changes 의 before 를 현재 상품값과 대조 — 불일치 (field, before, current) 목록.

    int 필드는 표기 차이("15,000" vs "15000")로 인한 오탐을 막기 위해 정수 비교,
    문자열 필드는 strip 후 비교한다. stock_quantity 는 제외(모듈 docstring 3).
    """
    mismatches: list[tuple[str, str, str]] = []
    for change in changes:
        if change.field in _STALE_EXEMPT_FIELDS:
            continue
        current = getattr(row, change.field, None)
        current_str = "" if current is None else str(current)
        if change.field in _INT_FIELDS:
            try:
                before_val: int | None = (
                    _parse_int(change.before) if change.before.strip() else None
                )
            except ValueError:
                before_val = None
            if before_val != current:
                mismatches.append((change.field, change.before, current_str))
        elif change.before.strip() != current_str.strip():
            mismatches.append((change.field, change.before, current_str))
    return mismatches


async def _find_product(brand_id: str, product_id: int) -> SellerProductRow | None:
    """I-9 목록에서 대상 상품 행을 찾는다 — productId 필터가 없어 페이지 순회.

    페이지 크기·상한은 Settings 주입(seller_list_default_limit·
    seller_draft_lookup_max_pages). 상한 내 미발견은 None(삭제/미귀속 가능성) —
    호출부가 stale 로 처리한다. Spring 장애는 SpringUnavailableError 전파(재시도 가능).
    """
    settings = get_settings()
    page_size = settings.seller_list_default_limit
    offset = 0
    for _ in range(settings.seller_draft_lookup_max_pages):
        result = await get_spring_client().list_products(brand_id, None, None, page_size, offset)
        for row in result.rows:
            if row.product_id == product_id:
                return row
        if len(result.rows) < page_size:
            return None
        offset += page_size
    return None


# ── 실행 (confirm resume 후 — LLM 0회, draft 그대로 I-10/11/12 매핑) ────────────

_STALE_RETRY_GUIDE = (
    "변경을 원하시면 다시 요청해 주세요. 최신 값으로 새 초안을 만들어 드리겠습니다."
)


async def _execute_draft(record: DraftRecord) -> tuple[str, str]:
    """draft 를 op 별 Spring 쓰기에 매핑 — (outcome, 사용자 안내 text) 반환.

    outcome: "executed"(반영 완료) | "stale"(불일치/미발견 — 실행 중단, 되묻기).
    Spring 장애(SpringUnavailableError)는 잡지 않는다 — 노드 예외로 전파되면
    checkpoint 가 interrupt 지점에 남아 동일 draftId 로 재confirm 이 가능하다.
    """
    client = get_spring_client()

    if record.op == "create":
        values = {c.field: _typed_after(c) for c in record.changes}
        payload = ProductCreate(
            name=str(values["name"]),
            price=int(values["price"]),
            stock_quantity=int(values["stock_quantity"]),
            original_price=(int(values["original_price"]) if "original_price" in values else None),
            category=str(values["category"]) if "category" in values else None,
            description=str(values["description"]) if "description" in values else None,
        )
        created = await client.create_product(record.brand_id, payload)
        return (
            "executed",
            f"상품을 등록했습니다 (productId={created.product_id}, status={created.status}).",
        )

    assert record.product_id is not None  # validate_draft 가 보장
    row = await _find_product(record.brand_id, record.product_id)
    if row is None:
        return (
            "stale",
            f"대상 상품(productId={record.product_id})을 상품 목록에서 찾을 수 없어 "
            f"반영을 중단했습니다. 삭제되었거나 변경된 것 같습니다. {_STALE_RETRY_GUIDE}",
        )

    mismatches = find_stale_changes(row, record.changes)
    if mismatches:
        lines = [
            f"- {field}: 초안 기준 '{before}' → 현재 '{current}'"
            for field, before, current in mismatches
        ]
        return (
            "stale",
            "초안 작성 이후 상품 정보가 변경되어 반영을 중단했습니다.\n"
            + "\n".join(lines)
            + f"\n{_STALE_RETRY_GUIDE}",
        )

    # stock 은 stale 비교 제외 대신 변동 사실을 결과 안내에 표기(2026-07-20 확정).
    stock_note = ""
    for change in record.changes:
        if change.field != "stock_quantity":
            continue
        try:
            before_stock: int | None = _parse_int(change.before) if change.before.strip() else None
        except ValueError:
            before_stock = None
        if before_stock != row.stock_quantity:
            stock_note = (
                f" 참고: 초안 작성 후 재고가 {row.stock_quantity}건으로 변동되어 "
                "있었습니다(주문 처리 등)."
            )

    if record.op == "delete":
        deleted = await client.delete_product(record.brand_id, record.product_id)
        return (
            "executed",
            f"상품을 삭제(숨김) 처리했습니다 (productId={deleted.product_id}, "
            f"status={deleted.status}). 물리 삭제는 아니며 노출만 중단됩니다.",
        )

    patch = ProductUpdate(**{c.field: _typed_after(c) for c in record.changes})
    updated = await client.update_product(record.brand_id, record.product_id, patch)
    summary_part = f" {record.summary}" if record.summary else ""
    return (
        "executed",
        f"변경을 반영했습니다 (productId={updated.product_id}).{summary_part}{stock_note}",
    )


# ── HITL 그래프 (draft 저장 → interrupt → resume 실행) ──────────────────────────


class HitlState(TypedDict, total=False):
    """HITL 스레드 상태 — draft 가 입력이자 정본, outcome/result 는 실행 후 기록."""

    draft: dict
    outcome: str
    result: str


async def _hitl_node(state: HitlState) -> HitlState:
    """단일 노드: interrupt 로 승인 대기 → resume 시 코드 실행.

    interrupt() 이전 구간은 노드 재실행(resume) 시 다시 돌므로 부수효과를 두지
    않는다. resume 값 자체는 쓰지 않는다 — confirm 판정·신원/TTL/멱등 검사는
    confirm_draft(코드)가 resume 이전에 끝낸다.
    """
    record = DraftRecord.model_validate(state["draft"])
    interrupt({"draftId": record.draft_id, "op": record.op})
    outcome, text = await _execute_draft(record)
    return {"outcome": outcome, "result": text}


# checkpointer 싱글턴 — set_checkpointer(테스트 주입) / 미주입 시 지연 초기화.
_checkpointer: BaseCheckpointSaver | None = None
_checkpointer_ctx: object | None = None  # AsyncPostgresSaver cm — 앱 수명 동안 GC 방지
_graph = None
_fallback_warned = False


def set_checkpointer(checkpointer: BaseCheckpointSaver | None) -> None:
    """checkpointer 교체(테스트용) — None 이면 다음 사용 시 재초기화한다."""
    global _checkpointer, _checkpointer_ctx, _graph
    _checkpointer = checkpointer
    _checkpointer_ctx = None
    _graph = None


async def _init_checkpointer() -> BaseCheckpointSaver:
    """AsyncPostgresSaver(pg-profile) 초기화 — 실패 시 dev 한정 InMemorySaver 폴백.

    운영(auth_mode=jwks)은 폴백 금지 — 재시작 시 draft 증발은 허용 불가(모듈
    docstring 2). setup() 은 checkpoint 테이블 생성 멱등 호출이다.
    """
    global _checkpointer_ctx, _fallback_warned
    settings = get_settings()
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        ctx = AsyncPostgresSaver.from_conn_string(settings.profile_db_url)
        saver = await asyncio.wait_for(
            ctx.__aenter__(), timeout=settings.seller_checkpoint_connect_timeout_s
        )
        await saver.setup()
        _checkpointer_ctx = ctx
        return saver
    except Exception as exc:
        if settings.auth_mode == "jwks":
            raise  # 운영 — 폴백 금지, 기동/요청 실패로 드러낸다
        if not _fallback_warned:
            logger.warning(
                "pg-profile checkpointer 연결 실패(%s) — InMemorySaver 폴백 "
                "(dev 전용: 프로세스 재시작 시 draft 증발)",
                exc,
            )
            _fallback_warned = True
        return InMemorySaver()


async def _get_graph():
    """HITL 그래프 싱글턴 — checkpointer 준비 후 1회 컴파일."""
    global _checkpointer, _graph
    if _graph is None:
        if _checkpointer is None:
            _checkpointer = await _init_checkpointer()
        builder = StateGraph(HitlState)
        builder.add_node("hitl", _hitl_node)
        builder.add_edge(START, "hitl")
        builder.add_edge("hitl", END)
        _graph = builder.compile(checkpointer=_checkpointer)
    return _graph


def _thread_config(draft_id: str) -> dict:
    """draftId ↔ checkpoint 바인딩(안전장치 ①) — thread_id 가 곧 바인딩이다."""
    return {"configurable": {"thread_id": f"seller-draft:{draft_id}"}}


# ── 공개 API (app/api/seller.py 소비) ────────────────────────────────────────────


async def start_draft(record: DraftRecord) -> None:
    """스트림 1: draft 를 checkpoint 에 저장하고 interrupt 대기 상태로 만든다."""
    graph = await _get_graph()
    result = await graph.ainvoke(
        {"draft": record.model_dump()}, config=_thread_config(record.draft_id)
    )
    if "__interrupt__" not in result:
        raise RuntimeError("HITL draft 저장 실패 — interrupt 가 발생하지 않았다")


@dataclass(frozen=True)
class ConfirmOutcome:
    """confirm 처리 결과 — text 는 그대로 사용자 token 이 된다."""

    status: Literal["executed", "stale", "already_done", "not_found", "expired"]
    text: str


# 미존재·소유 불일치 공용 문구 — 타 판매자에게 draft 존재 여부를 노출하지 않는다(④).
_NOT_FOUND_TEXT = (
    "해당 승인 요청을 찾을 수 없습니다. 초안이 만료됐거나 잘못된 요청입니다. "
    "변경 내용을 다시 말씀해 주시면 새 초안을 만들어 드리겠습니다."
)


async def confirm_draft(draft_id: str, *, brand_id: str) -> ConfirmOutcome:
    """스트림 2: confirm — 코드 검사(존재→소유→멱등→TTL) 통과 시에만 resume 실행.

    검사를 resume 이전에 두는 이유: 실패 사유(만료·소유 불일치)로 스레드를
    실행/종결시키면 안 되기 때문 — 특히 소유 불일치는 draft 를 죽여서도 안 된다.
    Spring 장애는 예외 전파 — checkpoint 는 interrupt 에 남아 재confirm 가능.
    """
    graph = await _get_graph()
    config = _thread_config(draft_id)
    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    draft_data = values.get("draft")
    if not draft_data:
        return ConfirmOutcome("not_found", _NOT_FOUND_TEXT)

    record = DraftRecord.model_validate(draft_data)
    if record.brand_id != brand_id:
        logger.warning("draft 소유 불일치 confirm 차단 (draftId=%s)", draft_id)
        return ConfirmOutcome("not_found", _NOT_FOUND_TEXT)

    if values.get("result"):  # 실행 완료 스레드 — 멱등(안전장치 ③)
        return ConfirmOutcome(
            "already_done",
            f"이미 처리된 승인 요청입니다 — 중복 실행하지 않았습니다. 이전 결과: {values['result']}",
        )

    settings = get_settings()
    created = datetime.fromisoformat(record.created_at)
    if datetime.now(UTC) - created > timedelta(minutes=settings.seller_draft_ttl_minutes):
        return ConfirmOutcome(
            "expired",
            f"초안이 만료됐습니다(유효 {settings.seller_draft_ttl_minutes}분). "
            "변경 내용을 다시 말씀해 주시면 새 초안을 만들어 드리겠습니다.",
        )

    result = await graph.ainvoke(Command(resume=True), config=config)
    return ConfirmOutcome(result.get("outcome", "executed"), result.get("result", ""))
