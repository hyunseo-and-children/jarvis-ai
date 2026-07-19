"""app/agents/seller/history.py 4-3 분석 이력 검증 — 실 LLM·PG·HTTP 없음.

InMemoryStore 주입으로 저장·조회·planner 주입·"N번 적용해줘" 변환(§6.3)을 검증한다.
설계 확정(2026-07-20): 적용 발화는 입구 코드 선판정(엄격 전체-문장 패턴).
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.agents.seller import history, hitl
from app.agents.seller.context import SellerContext
from app.agents.seller.pipeline import parse_apply_message
from app.agents.seller.schemas import (
    ActionRecommendation,
    ProposedChange,
    RecommendationSet,
)
from app.schemas.spring import SellerProductList, SellerProductRow
from app.services.spring_client import set_spring_client

_CTX = SellerContext(seller_id="7", brand_id="3")


@pytest.fixture(autouse=True)
def _fresh_backends():
    """테스트마다 격리된 InMemory store/checkpointer — PG 연결 시도 차단."""
    history.set_store(InMemoryStore())
    hitl.set_checkpointer(InMemorySaver())
    yield
    history.set_store(None)
    hitl.set_checkpointer(None)
    set_spring_client(None)


def _rec_set(*recs: ActionRecommendation) -> RecommendationSet:
    return RecommendationSet(recommendations=list(recs), summary="요약")


def _rec(product_id: int = 101, changes: list[ProposedChange] | None = None):
    return ActionRecommendation(
        action_type="price_adjust",
        product_id=product_id,
        title="감귤청 가격 10% 인하",
        rationale="매출 하락 구간과 가격 인상 시점 일치",
        changes=changes if changes is not None else [ProposedChange(field="price", after="13500")],
    )


async def _save(question: str = "지난달 매출 분석", recs: RecommendationSet | None = None):
    await history.save_history(
        "7",
        question=question,
        analyses=["sales_anomaly"],
        date_from="2026-06-01",
        date_to="2026-06-30",
        report="6월 매출 보고서 본문",
        recommendations=recs if recs is not None else _rec_set(_rec()),
    )


_ROW = SellerProductRow(productId=101, name="감귤청", price=15000, stockQuantity=100)


class _StubSpring:
    def __init__(self, rows: list[SellerProductRow] | None = None):
        self.rows = rows if rows is not None else [_ROW]

    async def list_products(self, brand_id, status=None, q=None, limit=None, offset=None):
        start = offset or 0
        return SellerProductList(rows=self.rows[start : start + (limit or 20)])


# ── parse_apply_message — 입구 ①.5 코드 선판정(엄격 전체-문장) ──────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("1번 적용해줘", 1),
        ("3번 추천 적용해줘", 3),
        (" 2번 적용 ", 2),
        ("5번을 적용해 주세요.", 5),
        ("12번 적용해줘!", 12),
    ],
)
def test_parse_apply_matches_canonical_forms(message: str, expected: int) -> None:
    assert parse_apply_message(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "2번 상품에 할인 적용해줘",  # 여분 토큰 — 일반 수정 요청(라우팅으로)
        "아까 그 두번째 거 적용해줘",  # 숫자 없음
        "적용해줘",
        "0번 적용해줘",  # 1 미만
        '{"action": "confirm", "draftId": "d-1"}',
        "지난달 매출 분석해줘",
    ],
)
def test_parse_apply_rejects_non_canonical(message: str) -> None:
    assert parse_apply_message(message) is None


# ── save/load — 최신순·상한·요약 절단 ───────────────────────────────────────────


def test_save_and_load_recent_newest_first() -> None:
    async def run():
        await _save(question="첫 분석")
        await _save(question="둘째 분석")
        return await history.load_recent("7", 5)

    entries = asyncio.run(run())

    assert [e.question for e in entries] == ["둘째 분석", "첫 분석"]
    assert entries[0].analyses == ["sales_anomaly"]
    assert entries[0].date_from == "2026-06-01"


def test_load_recent_respects_limit_and_isolation() -> None:
    async def run():
        for i in range(7):
            await _save(question=f"분석 {i}")
        other = await history.load_recent("999", 5)  # 다른 판매자 — 격리
        mine = await history.load_recent("7", 5)
        return other, mine

    other, mine = asyncio.run(run())

    assert other == []
    assert len(mine) == 5 and mine[0].question == "분석 6"


def test_save_trims_to_max_items_and_truncates_report(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config

    settings = config.Settings(seller_history_max_items=3, seller_history_report_max_chars=5)
    monkeypatch.setattr(history, "get_settings", lambda: settings)

    async def run():
        for i in range(5):
            await _save(question=f"분석 {i}")
        return await history.load_recent("7", 10)

    entries = asyncio.run(run())

    assert len(entries) == 3  # 상한 초과분은 오래된 것부터 폐기
    assert entries[0].report_summary == "6월 매출"[:5]  # 요약 절단


# ── build_planner_input — 이력 주입(프롬프트 불변, 입력 메시지만) ────────────────


def test_planner_input_without_history_is_question_verbatim() -> None:
    assert history.build_planner_input("이번 주 매출?", []) == "이번 주 매출?"


def test_planner_input_with_history_appends_block() -> None:
    async def run():
        await _save(question="지난달 매출 분석")
        return await history.load_recent("7")

    entries = asyncio.run(run())
    text = history.build_planner_input("이번 주는?", entries)

    assert text.startswith("[최근 분석 이력]")
    assert "sales_anomaly" in text and "2026-06-01~2026-06-30" in text
    assert text.endswith("[이번 질문] 이번 주는?")


# ── apply_recommendation — §6.3 변환(대화 재해석 금지) ──────────────────────────


def test_apply_converts_recommendation_to_draft_with_current_before() -> None:
    """recommendations[N-1] → draft — before 는 저장값이 아니라 I-9 현재값."""
    set_spring_client(_StubSpring())

    async def run():
        await _save()
        return await history.apply_recommendation(1, _CTX)

    record, problem = asyncio.run(run())

    assert problem is None and record is not None
    assert record.op == "update" and record.product_id == 101
    assert record.changes[0].field == "price"
    assert record.changes[0].before == "15000"  # I-9 조회 시점 현재값
    assert record.changes[0].after == "13500"
    assert record.summary == "감귤청 가격 10% 인하"
    assert record.brand_id == "3"  # confirm 소유 검증 재료


def test_apply_without_history_asks_for_analysis() -> None:
    record, problem = asyncio.run(history.apply_recommendation(1, _CTX))
    assert record is None and "이력이 없습니다" in problem


def test_apply_out_of_range_reports_valid_range() -> None:
    set_spring_client(_StubSpring())

    async def run():
        await _save(recs=_rec_set(_rec(), _rec(product_id=102)))
        return await history.apply_recommendation(5, _CTX)

    record, problem = asyncio.run(run())
    assert record is None and "1번~2번" in problem


def test_apply_changeless_recommendation_is_refused() -> None:
    """promotion 등 필드 변경이 없는 추천 — 자동 적용 불가 안내(§6.3-4)."""

    async def run():
        await _save(recs=_rec_set(_rec(changes=[])))
        return await history.apply_recommendation(1, _CTX)

    record, problem = asyncio.run(run())
    assert record is None and "자동 적용" in problem


def test_apply_missing_product_is_refused() -> None:
    set_spring_client(_StubSpring(rows=[]))

    async def run():
        await _save()
        return await history.apply_recommendation(1, _CTX)

    record, problem = asyncio.run(run())
    assert record is None and "찾을 수 없습니다" in problem


def test_applied_draft_flows_into_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E: 적용 draft 가 4-2 confirm 흐름에 합류 — I-11 이 추천 after 로 실행된다."""
    from app.schemas.spring import ProductUpdateResult

    class _WritableSpring(_StubSpring):
        def __init__(self):
            super().__init__()
            self.patches = []

        async def update_product(self, brand_id, product_id, patch):
            self.patches.append((brand_id, product_id, patch))
            return ProductUpdateResult(productId=product_id)

    spring = _WritableSpring()
    set_spring_client(spring)

    async def run():
        await _save()
        record, problem = await history.apply_recommendation(1, _CTX)
        assert problem is None
        await hitl.start_draft(record)
        return await hitl.confirm_draft(record.draft_id, brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "executed"
    assert spring.patches[0][2].price == 13500  # 추천 after 그대로 — 재해석 없음
