"""app/agents/seller/orchestrator.py 팬아웃 검증 (3-3) — 실 LLM 없음, 스텁 에이전트 주입.

pytest-asyncio 미의존 — 동기 테스트 안에서 asyncio.run 으로 실행한다(이식성).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from typing import get_args

import pytest

from app.agents.seller import orchestrator
from app.agents.seller.context import SellerContext
from app.agents.seller.pipeline import ALL_WORKERS_FAILED_TOKEN, ResolvedPlan
from app.agents.seller.schemas import (
    ActionRecommendation,
    AnalysisFinding,
    AnalysisPlan,
    AnalysisType,
    RecommendationSet,
    ReportScore,
)


def _settings(timeout_s: float = 5.0) -> SimpleNamespace:
    return SimpleNamespace(
        seller_worker_timeout_s=timeout_s,
        seller_report_score_threshold=21,
        seller_report_max_retries=3,
        seller_recent_days_default=7,
    )


_CTX = SellerContext(seller_id="7", brand_id="3")  # 계약 타입 = str (context.py)


def _plan(*analyses: str) -> ResolvedPlan:
    return ResolvedPlan(
        analyses=analyses,  # type: ignore[arg-type]
        date_from=dt.date(2026, 6, 1),
        date_to=dt.date(2026, 6, 30),
    )


def _finding(analysis_type: str) -> AnalysisFinding:
    return AnalysisFinding(
        analysis_type=analysis_type,  # type: ignore[arg-type]
        summary=f"{analysis_type} 정상 결과",
        evidence=["x=1"],
        severity="info",
    )


class _StubAgent:
    """create_agent 대역 — ainvoke 만 흉내 낸다(정상/예외/지연/무출력)."""

    def __init__(
        self,
        finding: AnalysisFinding | None = None,
        exc: Exception | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self._finding = finding
        self._exc = exc
        self._delay_s = delay_s

    async def ainvoke(self, _input: dict, context: object = None) -> dict:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._exc is not None:
            raise self._exc
        return {"structured_response": self._finding}


def _patch(
    monkeypatch: pytest.MonkeyPatch, stubs: dict[str, _StubAgent], timeout_s: float = 5.0
) -> None:
    """WORKER_BUILDERS 와 Settings 타임아웃을 스텁으로 교체한다."""
    for analysis_type, stub in stubs.items():
        monkeypatch.setitem(orchestrator.WORKER_BUILDERS, analysis_type, lambda s=stub: s)
    monkeypatch.setattr(orchestrator, "get_settings", lambda: _settings(timeout_s))


def _collect_emit() -> tuple[list[str], orchestrator.Emit]:
    tokens: list[str] = []

    async def emit(text: str) -> None:
        tokens.append(text)

    return tokens, emit


def test_worker_builders_cover_all_analysis_types() -> None:
    """레지스트리는 AnalysisType 전 값을 커버한다(배정표 실행판 누락 방지)."""
    assert set(orchestrator.WORKER_BUILDERS) == set(get_args(AnalysisType))


def test_run_workers_happy_path_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """정상 2종 — finding 은 계획 순서, 진행 token 은 실행 전에 유형별로 방출된다."""
    _patch(
        monkeypatch,
        {
            "sales_anomaly": _StubAgent(finding=_finding("sales_anomaly")),
            "churn": _StubAgent(finding=_finding("churn")),
        },
    )
    tokens, emit = _collect_emit()

    findings = asyncio.run(
        orchestrator.run_workers("질문", _plan("sales_anomaly", "churn"), _CTX, emit=emit)
    )

    assert [f.analysis_type for f in findings] == ["sales_anomaly", "churn"]
    assert tokens == ["매출 이상 분석 중…", "고객 이탈 분석 중…"]


def test_run_workers_partial_failure_becomes_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """1종 예외 → degrade finding(확보 실패·info·빈 evidence)으로 수렴, 파이프라인 계속."""
    _patch(
        monkeypatch,
        {
            "sales_anomaly": _StubAgent(finding=_finding("sales_anomaly")),
            "abuse": _StubAgent(exc=RuntimeError("boom")),
        },
    )
    _, emit = _collect_emit()

    findings = asyncio.run(
        orchestrator.run_workers("질문", _plan("sales_anomaly", "abuse"), _CTX, emit=emit)
    )

    degraded = findings[1]
    assert degraded.analysis_type == "abuse"
    assert degraded.severity == "info"
    assert "확보 실패" in degraded.summary  # D3 탐지 문자열 유지
    assert degraded.evidence == []
    assert findings[0].summary == "sales_anomaly 정상 결과"


def test_run_workers_all_failed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """전부 예외 → AllWorkersFailedError(호출부가 사과 token 후 done, §7)."""
    _patch(
        monkeypatch,
        {
            "conversion": _StubAgent(exc=RuntimeError("a")),
            "behavior": _StubAgent(exc=RuntimeError("b")),
        },
    )
    _, emit = _collect_emit()

    with pytest.raises(orchestrator.AllWorkersFailedError):
        asyncio.run(
            orchestrator.run_workers("질문", _plan("conversion", "behavior"), _CTX, emit=emit)
        )


def test_run_workers_timeout_becomes_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """타임아웃 초과 워커는 '응답 시간 초과' degrade — 나머지는 정상 수렴."""
    _patch(
        monkeypatch,
        {
            "sales_anomaly": _StubAgent(finding=_finding("sales_anomaly")),
            "churn": _StubAgent(finding=_finding("churn"), delay_s=0.2),
        },
        timeout_s=0.05,
    )
    _, emit = _collect_emit()

    findings = asyncio.run(
        orchestrator.run_workers("질문", _plan("sales_anomaly", "churn"), _CTX, emit=emit)
    )

    assert "응답 시간 초과" in findings[1].summary
    assert findings[0].summary == "sales_anomaly 정상 결과"


def test_run_workers_missing_structured_response_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """structured_response 누락(None)도 내부 오류 degrade 로 수렴한다."""
    _patch(
        monkeypatch,
        {
            "sales_anomaly": _StubAgent(finding=None),
            "churn": _StubAgent(finding=_finding("churn")),
        },
    )
    _, emit = _collect_emit()

    findings = asyncio.run(
        orchestrator.run_workers("질문", _plan("sales_anomaly", "churn"), _CTX, emit=emit)
    )

    assert "내부 오류" in findings[0].summary
    assert findings[1].summary == "churn 정상 결과"


# ── 검증 루프 (3-4) — write_verified_report ────────────────────────────────────


class _SeqAgent:
    """호출 순서대로 행동(응답 dict 또는 예외)을 소비하는 스텁 — 입력 메시지를 기록한다."""

    def __init__(self, behaviors: list[object]) -> None:
        self._behaviors = list(behaviors)
        self.received: list[str] = []

    async def ainvoke(self, agent_input: dict, context: object = None) -> dict:
        self.received.append(agent_input["messages"][0].content)
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior  # type: ignore[return-value]


def _report_response(text: str) -> dict:
    return {"messages": [SimpleNamespace(content=text)]}


def _score(total_each: int, feedback: str = "") -> dict:
    return {
        "structured_response": ReportScore(
            accuracy=total_each,
            completeness=total_each,
            clarity=total_each,
            feedback=feedback,
        )
    }


_FINDINGS = [
    AnalysisFinding(
        analysis_type="sales_anomaly",
        summary="6월 12일 매출이 평균 대비 42.1% 급락했다.",
        evidence=["06-12 매출 180,000원 (평균 310,000원)"],
        severity="warning",
    )
]

_GROUNDED = "매출이 180,000원으로 평균 310,000원 대비 42.1% 급락했습니다."


def _patch_loop(
    monkeypatch: pytest.MonkeyPatch, report_agent: _SeqAgent, judge_agent: _SeqAgent
) -> None:
    monkeypatch.setattr(orchestrator, "build_report_agent", lambda: report_agent)
    monkeypatch.setattr(orchestrator, "build_report_judge", lambda: judge_agent)
    monkeypatch.setattr(orchestrator, "get_settings", lambda: _settings())


def test_verified_report_passes_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """결정론 통과 + 24/30 → 1회에 passed=True, report/verify token 이 각 1회."""
    report_agent = _SeqAgent([_report_response(_GROUNDED)])
    judge_agent = _SeqAgent([_score(8)])
    _patch_loop(monkeypatch, report_agent, judge_agent)
    tokens, emit = _collect_emit()

    verified = asyncio.run(orchestrator.write_verified_report(_FINDINGS, _CTX, emit=emit))

    assert verified.passed is True
    assert verified.attempts == 1
    assert verified.report == _GROUNDED
    assert tokens == ["보고서를 작성하고 있습니다…", "보고서를 검증하고 있습니다…"]


def test_verified_report_rewrites_with_combined_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    """1회차 미달(결정론 환각 + 낮은 점수) → 재작성 입력에 결정론 사유와 judge
    feedback 이 합산 주입되고, 2회차에 통과한다(2026-07-18 확정 — 합산 재작성)."""
    hallucinated = "매출이 999,999원으로 급락했습니다."
    report_agent = _SeqAgent([_report_response(hallucinated), _report_response(_GROUNDED)])
    judge_agent = _SeqAgent([_score(5, feedback="근거 수치를 인용할 것"), _score(8)])
    _patch_loop(monkeypatch, report_agent, judge_agent)
    _, emit = _collect_emit()

    verified = asyncio.run(orchestrator.write_verified_report(_FINDINGS, _CTX, emit=emit))

    assert verified.passed is True
    assert verified.attempts == 2
    rewrite_message = report_agent.received[1]
    assert "999999" in rewrite_message  # 결정론(D2) 실패 사유
    assert "근거 수치를 인용할 것" in rewrite_message  # judge feedback
    assert "[이전 보고서]" in rewrite_message


def test_verified_report_adopts_last_after_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    """3회 전부 미달 → 마지막 보고서 채택 + passed=False (§7 degrade)."""
    report_agent = _SeqAgent([_report_response(_GROUNDED)] * 3)
    judge_agent = _SeqAgent([_score(5, feedback="부족")] * 3)
    _patch_loop(monkeypatch, report_agent, judge_agent)
    _, emit = _collect_emit()

    verified = asyncio.run(orchestrator.write_verified_report(_FINDINGS, _CTX, emit=emit))

    assert verified.passed is False
    assert verified.attempts == 3
    assert verified.report == _GROUNDED
    assert verified.last_score is not None and verified.last_score.total == 15


def test_verified_report_rewrite_crash_adopts_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    """재작성(2회차) LLM 장애 → 1회차 보고서 미달 채택(Q2 결정, 추후 변경 가능)."""
    report_agent = _SeqAgent([_report_response(_GROUNDED), RuntimeError("llm down")])
    judge_agent = _SeqAgent([_score(5, feedback="부족")])
    _patch_loop(monkeypatch, report_agent, judge_agent)
    _, emit = _collect_emit()

    verified = asyncio.run(orchestrator.write_verified_report(_FINDINGS, _CTX, emit=emit))

    assert verified.passed is False
    assert verified.report == _GROUNDED


def test_verified_report_first_attempt_crash_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """1차 작성부터 실패 → 내보낼 보고서가 없어 예외 전파(호출부 사과 경로, Q2)."""
    report_agent = _SeqAgent([RuntimeError("llm down")])
    judge_agent = _SeqAgent([])
    _patch_loop(monkeypatch, report_agent, judge_agent)
    _, emit = _collect_emit()

    with pytest.raises(RuntimeError):
        asyncio.run(orchestrator.write_verified_report(_FINDINGS, _CTX, emit=emit))


def test_verified_report_judge_crash_adopts_current(monkeypatch: pytest.MonkeyPatch) -> None:
    """judge 장애 → 현재 보고서를 미검증 채택(passed=False, Q2)."""
    report_agent = _SeqAgent([_report_response(_GROUNDED)])
    judge_agent = _SeqAgent([RuntimeError("judge down")])
    _patch_loop(monkeypatch, report_agent, judge_agent)
    _, emit = _collect_emit()

    verified = asyncio.run(orchestrator.write_verified_report(_FINDINGS, _CTX, emit=emit))

    assert verified.passed is False
    assert verified.report == _GROUNDED
    assert verified.last_score is None


# ── recommend + 파이프라인 통합 (3-5) ──────────────────────────────────────────

_REC_SET = RecommendationSet(
    recommendations=[
        ActionRecommendation(
            action_type="price_adjust",
            product_id=101,
            title="감귤청 가격 10% 인하",
            rationale="42.1% 급락",
            expected_effect="전환율 회복",
        )
    ],
    summary="가격 중심 1건",
)


def test_run_recommend_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    """정상 — RecommendationSet 반환 + recommend 진행 token 방출."""
    agent = _SeqAgent([{"structured_response": _REC_SET}])
    monkeypatch.setattr(orchestrator, "build_recommend_agent", lambda: agent)
    monkeypatch.setattr(orchestrator, "get_settings", lambda: _settings())
    tokens, emit = _collect_emit()

    result = asyncio.run(orchestrator.run_recommend(_FINDINGS, _GROUNDED, _CTX, emit=emit))

    assert result is _REC_SET
    assert tokens == ["개선 방안을 정리하고 있습니다…"]
    assert "[검증된 보고서]" in agent.received[0]


def test_run_recommend_failure_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """추천 실패(예외·C2 ValidationError 포함) → 빈 추천으로 계속(보고서 보호)."""
    agent = _SeqAgent([RuntimeError("boom")])
    monkeypatch.setattr(orchestrator, "build_recommend_agent", lambda: agent)
    monkeypatch.setattr(orchestrator, "get_settings", lambda: _settings())
    _, emit = _collect_emit()

    result = asyncio.run(orchestrator.run_recommend(_FINDINGS, _GROUNDED, _CTX, emit=emit))

    assert result.recommendations == []


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, plan: AnalysisPlan) -> None:
    """planner·워커·report·judge·recommend 전부 스텁 — 정상 경로 구성."""
    monkeypatch.setattr(
        orchestrator,
        "build_analysis_planner",
        lambda: _SeqAgent([{"structured_response": plan}]),
    )
    monkeypatch.setitem(
        orchestrator.WORKER_BUILDERS,
        "sales_anomaly",
        lambda: _StubAgent(finding=_FINDINGS[0]),
    )
    monkeypatch.setattr(
        orchestrator,
        "build_report_agent",
        lambda: _SeqAgent([_report_response(_GROUNDED)]),
    )
    monkeypatch.setattr(orchestrator, "build_report_judge", lambda: _SeqAgent([_score(8)]))
    monkeypatch.setattr(
        orchestrator,
        "build_recommend_agent",
        lambda: _SeqAgent([{"structured_response": _REC_SET}]),
    )
    monkeypatch.setattr(orchestrator, "get_settings", lambda: _settings())


def test_pipeline_happy_path_composes_report_and_recommendations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전 구간 통합 — kind=report, 보고서+1번 추천 조립, 진행 token 순서."""
    plan = AnalysisPlan(analyses=["sales_anomaly"], period_expr="지난달", reason="r")
    _patch_pipeline(monkeypatch, plan)
    tokens, emit = _collect_emit()

    result = asyncio.run(
        orchestrator.run_analysis_pipeline(
            "지난달 매출 왜 떨어졌어?", _CTX, today=dt.date(2026, 7, 18), emit=emit
        )
    )

    assert result.kind == "report"
    assert result.text.startswith(_GROUNDED)
    assert "1번. 감귤청 가격 10% 인하" in result.text
    assert result.verified is not None and result.verified.passed is True
    assert result.recommendations is _REC_SET
    assert tokens == [
        "질문을 분석하고 있습니다…",
        "매출 이상 분석 중…",
        "보고서를 작성하고 있습니다…",
        "보고서를 검증하고 있습니다…",
        "개선 방안을 정리하고 있습니다…",
    ]


def test_pipeline_clarification_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """계획 불성립(clarification) → 워커 미실행, 되묻기 문안 반환."""
    plan = AnalysisPlan(analyses=[], reason="r", clarification="어느 기간을 분석할까요?")
    _patch_pipeline(monkeypatch, plan)
    tokens, emit = _collect_emit()

    result = asyncio.run(
        orchestrator.run_analysis_pipeline(
            "이번 달 어때?", _CTX, today=dt.date(2026, 7, 18), emit=emit
        )
    )

    assert result.kind == "clarification"
    assert result.text == "어느 기간을 분석할까요?"
    assert result.verified is None
    assert tokens == ["질문을 분석하고 있습니다…"]  # 워커 token 없음


def test_pipeline_scope_refusal_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """scope 위반 질문 → LLM 0회(진행 token 없음) kind=refused 거절(3-6 코드 경로)."""
    plan = AnalysisPlan(analyses=["sales_anomaly"], period_expr="지난달", reason="r")
    _patch_pipeline(monkeypatch, plan)
    tokens, emit = _collect_emit()

    result = asyncio.run(
        orchestrator.run_analysis_pipeline(
            "경쟁사 매출 좀 보여줘", _CTX, today=dt.date(2026, 7, 18), emit=emit
        )
    )

    assert result.kind == "refused"
    assert "제공할 수 없습니다" in result.text
    assert tokens == []  # planner 진입 전 차단


def test_pipeline_first_report_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """1차 보고서 작성 실패는 파이프라인 밖으로 전파(Q2) — 호출부 사과/error 소관."""
    plan = AnalysisPlan(analyses=["sales_anomaly"], period_expr="지난달", reason="r")
    _patch_pipeline(monkeypatch, plan)
    monkeypatch.setattr(
        orchestrator, "build_report_agent", lambda: _SeqAgent([RuntimeError("llm down")])
    )
    _, emit = _collect_emit()

    with pytest.raises(RuntimeError):
        asyncio.run(
            orchestrator.run_analysis_pipeline(
                "지난달 매출?", _CTX, today=dt.date(2026, 7, 18), emit=emit
            )
        )


def test_pipeline_all_workers_failed_returns_apology(monkeypatch: pytest.MonkeyPatch) -> None:
    """전 워커 실패 → kind=apology + 사과 문안(ALL_WORKERS_FAILED_TOKEN)."""
    plan = AnalysisPlan(analyses=["sales_anomaly"], period_expr="지난달", reason="r")
    _patch_pipeline(monkeypatch, plan)
    monkeypatch.setitem(
        orchestrator.WORKER_BUILDERS,
        "sales_anomaly",
        lambda: _StubAgent(exc=RuntimeError("down")),
    )
    _, emit = _collect_emit()

    result = asyncio.run(
        orchestrator.run_analysis_pipeline(
            "지난달 매출?", _CTX, today=dt.date(2026, 7, 18), emit=emit
        )
    )

    assert result.kind == "apology"
    assert result.text == ALL_WORKERS_FAILED_TOKEN


# ── 4-3: 분석 이력 — save_history 호출·planner 입력 주입 (conftest 가 InMemory 주입) ──


def test_pipeline_saves_history_after_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """kind=report 완료 시 이력 저장 — §6.3 'N번 적용해줘'·planner 주입의 원천."""
    from app.agents.seller import history

    plan = AnalysisPlan(analyses=["sales_anomaly"], period_expr="지난달", reason="r")
    _patch_pipeline(monkeypatch, plan)
    _, emit = _collect_emit()

    asyncio.run(
        orchestrator.run_analysis_pipeline(
            "지난달 매출 왜 떨어졌어?", _CTX, today=dt.date(2026, 7, 18), emit=emit
        )
    )
    entries = asyncio.run(history.load_recent("7"))

    assert len(entries) == 1
    assert entries[0].question == "지난달 매출 왜 떨어졌어?"
    assert entries[0].analyses == ["sales_anomaly"]
    assert entries[0].date_from == "2026-06-01" and entries[0].date_to == "2026-06-30"
    saved = RecommendationSet.model_validate(entries[0].recommendations)
    assert saved.recommendations[0].title == "감귤청 가격 10% 인하"  # 순서=N번 계약 보존


def test_pipeline_injects_history_into_planner_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """이력이 있으면 planner 입력에 [최근 분석 이력] 블록 — 프롬프트 불변, 메시지 주입."""
    from app.agents.seller import history

    asyncio.run(
        history.save_history(
            "7",
            question="6월 매출 분석",
            analyses=["sales_anomaly"],
            date_from="2026-06-01",
            date_to="2026-06-30",
            report="이전 보고서",
            recommendations=RecommendationSet(),
        )
    )
    plan = AnalysisPlan(analyses=["sales_anomaly"], period_expr="지난달", reason="r")
    _patch_pipeline(monkeypatch, plan)
    planner = _SeqAgent([{"structured_response": plan}])
    monkeypatch.setattr(orchestrator, "build_analysis_planner", lambda: planner)
    _, emit = _collect_emit()

    asyncio.run(
        orchestrator.run_analysis_pipeline(
            "이번엔 7월은?", _CTX, today=dt.date(2026, 7, 18), emit=emit
        )
    )

    assert planner.received[0].startswith("[최근 분석 이력]")
    assert "6월 매출 분석" in planner.received[0]
    assert planner.received[0].endswith("[이번 질문] 이번엔 7월은?")


def test_pipeline_history_failure_does_not_break_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이력 조회·저장 장애 → 주입/기록 없이 분석은 정상 완료(degrade)."""
    from app.agents.seller import history

    async def _boom(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(history, "load_recent", _boom)
    monkeypatch.setattr(history, "save_history", _boom)
    plan = AnalysisPlan(analyses=["sales_anomaly"], period_expr="지난달", reason="r")
    _patch_pipeline(monkeypatch, plan)
    _, emit = _collect_emit()

    result = asyncio.run(
        orchestrator.run_analysis_pipeline(
            "지난달 매출?", _CTX, today=dt.date(2026, 7, 18), emit=emit
        )
    )

    assert result.kind == "report"
