"""supervisor 라우팅(orchestrator.route_question) 검증 (4-1a).

실 LLM 없음 — build_supervisor 를 스텁으로 교체한다. 검증 항목(REALIGN §4 확정):
  - 정상 분류는 그대로 통과
  - confidence 미달 → analysis 보수 재지정 (원분류 analysis 는 재지정 없음)
  - supervisor 장애(예외·타임아웃·비정형 출력) → general 폴백

confirm 선판정은 2026-07-22(FE 계약 A-2)에 message 파싱에서 요청 스키마 필드로 이관됐다 —
검증은 tests/unit/test_seller_chat_request.py(SellerChatRequest) 로 이동했다.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agents.seller import orchestrator
from app.agents.seller.context import SellerContext
from app.agents.seller.schemas import RouteDecision

_CTX = SellerContext(seller_id="7", brand_id="3")


def _settings(confidence_min: float = 0.6, timeout_s: float = 5.0) -> SimpleNamespace:
    return SimpleNamespace(
        seller_route_confidence_min=confidence_min,
        seller_route_timeout_s=timeout_s,
    )


class _StubSupervisor:
    """create_agent 대역 — ainvoke 만 흉내(정상/예외/지연/비정형)."""

    def __init__(
        self,
        decision: RouteDecision | object | None = None,
        exc: Exception | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self._decision = decision
        self._exc = exc
        self._delay_s = delay_s

    async def ainvoke(self, _input: dict, context: object = None) -> dict:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._exc is not None:
            raise self._exc
        return {"structured_response": self._decision}


def _patch(
    monkeypatch: pytest.MonkeyPatch, stub: _StubSupervisor, **settings_kwargs: float
) -> None:
    monkeypatch.setattr(orchestrator, "build_supervisor", lambda: stub)
    monkeypatch.setattr(orchestrator, "get_settings", lambda: _settings(**settings_kwargs))


def _route(question: str = "지난달 매출 어때?") -> RouteDecision:
    return asyncio.run(orchestrator.route_question(question, _CTX))


def test_confident_decision_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """confidence 충분한 정상 분류는 그대로 반환된다."""
    decision = RouteDecision(category="product", reason="가격 수정 요청", confidence=0.95)
    _patch(monkeypatch, _StubSupervisor(decision=decision))

    result = _route("가격 12,900원으로 바꿔줘")

    assert result is decision  # 재작성 없이 원본 통과


def test_low_confidence_reroutes_to_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """confidence 미달(원분류 general) → analysis 보수 재지정(SPEC 장치 ⑤)."""
    decision = RouteDecision(category="general", reason="애매", confidence=0.4)
    _patch(monkeypatch, _StubSupervisor(decision=decision), confidence_min=0.6)

    result = _route()

    assert result.category == "analysis"
    assert orchestrator.ROUTE_CONSERVATIVE_REASON in result.reason
    assert result.confidence == 0.4  # 원 confidence 보존(디버깅 재료)


def test_low_confidence_analysis_stays_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """원분류가 analysis 면 confidence 미달이어도 재지정 없이 그대로 간다."""
    decision = RouteDecision(category="analysis", reason="분석 추정", confidence=0.3)
    _patch(monkeypatch, _StubSupervisor(decision=decision))

    result = _route()

    assert result is decision


def test_supervisor_exception_falls_back_to_general(monkeypatch: pytest.MonkeyPatch) -> None:
    """supervisor 예외 → general 폴백(2026-07-19 사용자 결정 — 작동 우선)."""
    _patch(monkeypatch, _StubSupervisor(exc=RuntimeError("api down")))

    result = _route()

    assert result.category == "general"
    assert result.reason == orchestrator.ROUTE_FALLBACK_REASON


def test_supervisor_timeout_falls_back_to_general(monkeypatch: pytest.MonkeyPatch) -> None:
    """seller_route_timeout_s 초과 → general 폴백."""
    _patch(monkeypatch, _StubSupervisor(delay_s=0.2), timeout_s=0.05)

    result = _route()

    assert result.category == "general"


def test_malformed_structured_response_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """비정형 출력(RouteDecision 아님) → general 폴백(TypeError 경로)."""
    _patch(monkeypatch, _StubSupervisor(decision={"category": "analysis"}))

    result = _route()

    assert result.category == "general"


# confirm 선판정 테스트는 test_seller_chat_request.py(SellerChatRequest 스키마)로 이관됐다.
