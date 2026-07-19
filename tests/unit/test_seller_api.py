"""app/api/seller.py SSE 1차 배선 검증 (3-7) — 실 LLM·HTTP 서버 없음.

_general_stream 제너레이터를 직접 소비한다(스텁 에이전트 주입). SSE 와이어 포맷
(data: {"type": ..., "data": {...}}\n\n)과 이벤트 순서·마스킹·오류 매핑을 검증한다.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.seller import hitl
from app.api import seller as seller_api
from app.core.auth import Identity
from app.schemas.chat import ChatRequest

_IDENTITY = Identity(user_id=None, is_guest=False, seller_id="7", brand_id="3")


@pytest.fixture(autouse=True)
def _hitl_memory_checkpointer():
    """4-2: product/confirm 레인이 hitl 그래프를 쓰므로 PG 연결 없이 InMemory 주입."""
    hitl.set_checkpointer(InMemorySaver())
    yield
    hitl.set_checkpointer(None)


def _request(message: str) -> ChatRequest:
    return ChatRequest(session_id="s-1", thread_id="t-1", message=message)


class _StubStreamAgent:
    """astream 만 흉내 — (AIMessageChunk, metadata) 튜플을 순서대로 방출한다."""

    def __init__(self, chunks: list[object], exc: Exception | None = None) -> None:
        self._chunks = chunks
        self._exc = exc

    async def astream(self, _input: dict, context: object = None, stream_mode: str = ""):
        for chunk in self._chunks:
            yield (chunk, {"langgraph_node": "model"})
        if self._exc is not None:
            raise self._exc


def _collect(request: ChatRequest) -> list[dict]:
    """스트림을 전부 소비해 SSE 페이로드(dict) 목록으로 파싱한다."""

    async def run() -> list[str]:
        return [line async for line in seller_api._general_stream(request, _IDENTITY)]

    lines = asyncio.run(run())
    payloads = []
    for line in lines:
        assert line.startswith("data: ") and line.endswith("\n\n")  # SSE 와이어 규약
        payloads.append(json.loads(line[len("data: ") :]))
    return payloads


def test_stream_tokens_then_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """모델 청크 → token(text 증분) 순서 보존 → 마지막은 done(stop)."""
    agent = _StubStreamAgent(
        [
            AIMessageChunk(content="지난달 매출은 "),
            AIMessageChunk(content="1,200,000원입니다."),
        ]
    )
    monkeypatch.setattr(seller_api, "build_general_agent", lambda today: agent)

    events = _collect(_request("지난달 매출 알려줘"))

    assert [e["type"] for e in events] == ["token", "token", "done"]
    assert events[0]["data"]["text"] == "지난달 매출은 "
    assert events[2]["data"]["finishReason"] == "stop"  # CamelModel by_alias


def test_stream_skips_tool_use_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_use 블록(도구 호출 인자)은 사용자 스트림에 흘리지 않는다."""
    agent = _StubStreamAgent(
        [
            AIMessageChunk(content=[{"type": "tool_use", "name": "get_sales_timeseries"}]),
            AIMessageChunk(content=[{"type": "text", "text": "조회 결과입니다."}]),
        ]
    )
    monkeypatch.setattr(seller_api, "build_general_agent", lambda today: agent)

    events = _collect(_request("매출 조회"))

    assert [e["type"] for e in events] == ["token", "done"]
    assert events[0]["data"]["text"] == "조회 결과입니다."


def test_stream_masks_output_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """출력 검사(§10-⑥) — 청크에 섞인 시크릿 패턴이 마스킹되어 나간다."""
    agent = _StubStreamAgent([AIMessageChunk(content="키는 sk-abcdefghijklmnop1234 입니다")])
    monkeypatch.setattr(seller_api, "build_general_agent", lambda today: agent)

    events = _collect(_request("설정 알려줘"))

    assert "sk-abcdefghijklmnop1234" not in events[0]["data"]["text"]
    assert "[민감 정보 차단]" in events[0]["data"]["text"]


def test_stream_scope_refusal_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """scope 위반 → 에이전트 미빌드(LLM 0회), 거절 token + done."""

    def _fail_build(today: str):
        raise AssertionError("scope 차단 시 에이전트를 빌드하면 안 된다")

    monkeypatch.setattr(seller_api, "build_general_agent", _fail_build)

    events = _collect(_request("경쟁사 매출 알려줘"))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "제공할 수 없습니다" in events[0]["data"]["text"]


def test_stream_error_event_on_build_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """에이전트 빌드 실패도 error 이벤트 봉투로 종료 — 무봉투 파손 금지(마감 리뷰 M2)."""

    def _boom(today: str):
        raise RuntimeError("settings broken")

    monkeypatch.setattr(seller_api, "build_general_agent", _boom)

    events = _collect(_request("매출 알려줘"))

    assert [e["type"] for e in events] == ["error"]
    assert events[0]["data"]["code"] == "INTERNAL"


def test_stream_error_event_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """스트림 내부 예외 → error 이벤트(INTERNAL)로 종료(§2.7 — 봉투 아님)."""
    agent = _StubStreamAgent([AIMessageChunk(content="일부 ")], exc=RuntimeError("boom"))
    monkeypatch.setattr(seller_api, "build_general_agent", lambda today: agent)

    events = _collect(_request("매출 알려줘"))

    assert events[0]["type"] == "token"
    assert events[-1]["type"] == "error"
    assert events[-1]["data"]["code"] == "INTERNAL"


# ── 4-1b: _seller_stream 3분기 디스패치 ──────────────────────────────────────


def _collect_seller(request: ChatRequest) -> list[dict]:
    """_seller_stream(통합 입구)을 전부 소비해 SSE 페이로드 목록으로 파싱한다."""

    async def run() -> list[str]:
        return [line async for line in seller_api._seller_stream(request, _IDENTITY)]

    lines = asyncio.run(run())
    payloads = []
    for line in lines:
        assert line.startswith("data: ") and line.endswith("\n\n")
        payloads.append(json.loads(line[len("data: ") :]))
    return payloads


def _route_stub(category: str, confidence: float = 0.9):
    from app.agents.seller.schemas import RouteDecision

    async def stub(question, context):
        return RouteDecision(category=category, reason="stub", confidence=confidence)

    return stub


def _no_route(question, context):
    raise AssertionError("이 경로에서는 라우팅(LLM)을 호출하면 안 된다")


def test_confirm_message_short_circuits_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """① confirm 선판정 — 라우팅·LLM 없이 confirm 레인(4-2)으로 위임된다.

    미존재 draftId 는 not_found 안내 token + done (hitl.confirm_draft 코드 판정).
    """
    monkeypatch.setattr(seller_api, "route_question", _no_route)

    events = _collect_seller(_request('{"action": "confirm", "draftId": "d-1"}'))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "찾을 수 없습니다" in events[0]["data"]["text"]


def test_confirm_executed_result_streams_token_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """confirm 레인 — 실행 결과 text 가 그대로 token 으로 나간다(LLM 0회)."""
    monkeypatch.setattr(seller_api, "route_question", _no_route)

    async def fake_confirm(draft_id, *, brand_id):
        assert (draft_id, brand_id) == ("d-9", "3")  # 신원은 검증된 Identity 에서
        return hitl.ConfirmOutcome("executed", "변경을 반영했습니다 (productId=101).")

    monkeypatch.setattr(seller_api, "confirm_draft", fake_confirm)

    events = _collect_seller(_request('{"action": "confirm", "draftId": "d-9"}'))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "반영했습니다" in events[0]["data"]["text"]


def test_confirm_spring_down_maps_to_apology_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirm 중 Spring 장애 — 사과 token(초안 유지 안내) + error(INTERNAL)."""
    from app.services.spring_client import SpringUnavailableError

    monkeypatch.setattr(seller_api, "route_question", _no_route)

    async def fake_confirm(draft_id, *, brand_id):
        raise SpringUnavailableError("conn refused")

    monkeypatch.setattr(seller_api, "confirm_draft", fake_confirm)

    events = _collect_seller(_request('{"action": "confirm", "draftId": "d-9"}'))

    assert [e["type"] for e in events] == ["token", "error"]
    assert "초안은 유지" in events[0]["data"]["text"]
    assert events[1]["data"]["code"] == "INTERNAL"


def test_scope_refusal_short_circuits_before_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """② scope 선차단 — 라우팅 이전에 거절 token + done (LLM 0회)."""
    monkeypatch.setattr(seller_api, "route_question", _no_route)

    events = _collect_seller(_request("경쟁사 매출 알려줘"))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "제공할 수 없습니다" in events[0]["data"]["text"]


def test_analysis_route_relays_progress_and_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """analysis 분기 — 진행 token(emit 중계) → 최종 text token → done."""
    from app.agents.seller.orchestrator import PipelineResult

    async def fake_pipeline(question, context, *, today, emit):
        await emit("매출 이상 분석 중…")
        return PipelineResult(kind="report", text="6월 매출 보고서 본문")

    monkeypatch.setattr(seller_api, "route_question", _route_stub("analysis"))
    monkeypatch.setattr(seller_api, "run_analysis_pipeline", fake_pipeline)

    events = _collect_seller(_request("지난달 매출 분석해줘"))

    assert [e["type"] for e in events] == ["token", "token", "done"]
    assert events[0]["data"]["text"] == "매출 이상 분석 중…"
    assert events[1]["data"]["text"] == "6월 매출 보고서 본문"


def test_analysis_route_clarification_is_token_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """되묻기(kind=clarification)도 동일 계약 — text→token→done (error 아님)."""
    from app.agents.seller.orchestrator import PipelineResult

    async def fake_pipeline(question, context, *, today, emit):
        return PipelineResult(kind="clarification", text="기간을 명시해 주세요.")

    monkeypatch.setattr(seller_api, "route_question", _route_stub("analysis"))
    monkeypatch.setattr(seller_api, "run_analysis_pipeline", fake_pipeline)

    events = _collect_seller(_request("매출 분석"))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "기간" in events[0]["data"]["text"]


def test_analysis_route_exception_maps_to_apology_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예외 전파(planner 장애 등) → 사과 token + error(INTERNAL) 종료(§5-2 매핑)."""

    async def fake_pipeline(question, context, *, today, emit):
        await emit("분석 계획 수립 중…")
        raise RuntimeError("planner down")

    monkeypatch.setattr(seller_api, "route_question", _route_stub("analysis"))
    monkeypatch.setattr(seller_api, "run_analysis_pipeline", fake_pipeline)

    events = _collect_seller(_request("매출 분석해줘"))

    assert [e["type"] for e in events] == ["token", "token", "error"]
    assert "죄송합니다" in events[1]["data"]["text"]
    assert events[2]["data"]["code"] == "INTERNAL"


def test_analysis_route_timeout_maps_to_llm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """파이프라인 TimeoutError → 사과 token + error(LLM_TIMEOUT)."""

    async def fake_pipeline(question, context, *, today, emit):
        raise TimeoutError("planner timeout")

    monkeypatch.setattr(seller_api, "route_question", _route_stub("analysis"))
    monkeypatch.setattr(seller_api, "run_analysis_pipeline", fake_pipeline)

    events = _collect_seller(_request("매출 분석해줘"))

    assert events[-1]["type"] == "error"
    assert events[-1]["data"]["code"] == "LLM_TIMEOUT"


class _StubProductAgent:
    def __init__(self, proposal) -> None:
        self._proposal = proposal

    async def ainvoke(self, _input: dict, context: object = None) -> dict:
        return {"structured_response": self._proposal}


def test_product_route_emits_draft_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """product 분기 — DraftProposal → SSE draft(camelCase 페이로드) + done."""
    from app.agents.seller.schemas import DraftChange, DraftProposal

    proposal = DraftProposal(
        op="update",
        product_id=101,
        changes=[DraftChange(field="price", before="15000", after="12900")],
        summary="가격 12,900원으로 인하",
    )
    monkeypatch.setattr(seller_api, "route_question", _route_stub("product"))
    monkeypatch.setattr(seller_api, "build_product_agent", lambda: _StubProductAgent(proposal))

    events = _collect_seller(_request("감귤청 가격 12900원으로 바꿔줘"))

    assert [e["type"] for e in events] == ["draft", "done"]
    draft = events[0]["data"]
    assert draft["op"] == "update"
    assert draft["productId"] == 101  # F2 — 숫자 id
    assert draft["draftId"]  # 발급됨(실행 바인딩은 4-2)
    assert draft["changes"] == [{"field": "price", "before": "15000", "after": "12900"}]


def test_product_route_clarification_is_token_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """clarification 이 차 있으면 draft 불성립 — 되묻기 token + done."""
    from app.agents.seller.schemas import DraftProposal

    proposal = DraftProposal(
        op="update", summary="", clarification="'감귤' 상품이 3건입니다. 어느 상품인가요?"
    )
    monkeypatch.setattr(seller_api, "route_question", _route_stub("product"))
    monkeypatch.setattr(seller_api, "build_product_agent", lambda: _StubProductAgent(proposal))

    events = _collect_seller(_request("감귤 가격 바꿔줘"))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "어느 상품" in events[0]["data"]["text"]


def test_product_route_invalid_draft_becomes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_draft 불성립(4-2 코드 선검증) — draft 미발행, 되묻기 token + done."""
    from app.agents.seller.schemas import DraftProposal

    proposal = DraftProposal(op="update", product_id=None, summary="")  # 대상 미특정
    monkeypatch.setattr(seller_api, "route_question", _route_stub("product"))
    monkeypatch.setattr(seller_api, "build_product_agent", lambda: _StubProductAgent(proposal))

    events = _collect_seller(_request("가격 바꿔줘"))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "상품" in events[0]["data"]["text"]


def test_product_route_draft_is_confirmable(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E: 스트림 1 draft 의 draftId 로 confirm(스트림 2) — checkpoint 바인딩 검증."""
    from app.agents.seller.schemas import DraftChange, DraftProposal
    from app.schemas.spring import ProductUpdateResult, SellerProductList, SellerProductRow
    from app.services.spring_client import set_spring_client

    class _Spring:
        def __init__(self):
            self.patches = []

        async def list_products(self, brand_id, status=None, q=None, limit=None, offset=None):
            row = SellerProductRow(productId=101, name="감귤청", price=15000, stockQuantity=100)
            return SellerProductList(rows=[row])

        async def update_product(self, brand_id, product_id, patch):
            self.patches.append((brand_id, product_id, patch))
            return ProductUpdateResult(productId=product_id)

    spring = _Spring()
    set_spring_client(spring)
    proposal = DraftProposal(
        op="update",
        product_id=101,
        changes=[DraftChange(field="price", before="15000", after="12900")],
        summary="가격 인하",
    )
    monkeypatch.setattr(seller_api, "route_question", _route_stub("product"))
    monkeypatch.setattr(seller_api, "build_product_agent", lambda: _StubProductAgent(proposal))
    try:
        draft_events = _collect_seller(_request("감귤청 가격 12900원으로"))
        draft_id = draft_events[0]["data"]["draftId"]

        confirm_events = _collect_seller(
            _request(json.dumps({"action": "confirm", "draftId": draft_id}))
        )
    finally:
        set_spring_client(None)

    assert [e["type"] for e in confirm_events] == ["token", "done"]
    assert "반영했습니다" in confirm_events[0]["data"]["text"]
    assert spring.patches[0][1] == 101 and spring.patches[0][2].price == 12900


def test_apply_message_short_circuits_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """①.5 적용 선판정 — 라우팅·LLM 없이 적용 레인(4-3). 이력 없음 → 되묻기 token."""
    monkeypatch.setattr(seller_api, "route_question", _no_route)

    events = _collect_seller(_request("1번 적용해줘"))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "이력이 없습니다" in events[0]["data"]["text"]


def test_apply_message_with_history_emits_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    """①.5 → 이력 recommendations[N-1] 이 draft 이벤트로 — before 는 I-9 현재값."""
    from app.agents.seller import history
    from app.agents.seller.schemas import ActionRecommendation, ProposedChange, RecommendationSet
    from app.schemas.spring import SellerProductList, SellerProductRow
    from app.services.spring_client import set_spring_client

    class _Spring:
        async def list_products(self, brand_id, status=None, q=None, limit=None, offset=None):
            row = SellerProductRow(productId=101, name="감귤청", price=15000, stockQuantity=100)
            return SellerProductList(rows=[row])

    set_spring_client(_Spring())
    monkeypatch.setattr(seller_api, "route_question", _no_route)
    recs = RecommendationSet(
        recommendations=[
            ActionRecommendation(
                action_type="price_adjust",
                product_id=101,
                title="감귤청 가격 10% 인하",
                rationale="r",
                changes=[ProposedChange(field="price", after="13500")],
            )
        ]
    )
    try:
        asyncio.run(
            history.save_history(
                "7",
                question="지난달 매출 분석",
                analyses=["sales_anomaly"],
                date_from="2026-06-01",
                date_to="2026-06-30",
                report="보고서",
                recommendations=recs,
            )
        )

        events = _collect_seller(_request("1번 적용해줘"))
    finally:
        set_spring_client(None)

    assert [e["type"] for e in events] == ["draft", "done"]
    draft = events[0]["data"]
    assert draft["op"] == "update" and draft["productId"] == 101
    assert draft["changes"] == [{"field": "price", "before": "15000", "after": "13500"}]
    assert draft["summary"] == "감귤청 가격 10% 인하"


def test_general_route_uses_general_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """general 분기 — 기존 astream 스트림 경로로 위임된다."""
    agent = _StubStreamAgent([AIMessageChunk(content="안녕하세요, 무엇을 도와드릴까요?")])
    monkeypatch.setattr(seller_api, "route_question", _route_stub("general"))
    monkeypatch.setattr(seller_api, "build_general_agent", lambda today: agent)

    events = _collect_seller(_request("안녕"))

    assert [e["type"] for e in events] == ["token", "done"]
    assert "도와드릴까요" in events[0]["data"]["text"]
