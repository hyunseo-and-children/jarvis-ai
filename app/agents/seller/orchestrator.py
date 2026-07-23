"""판매자 분석 파이프라인 오케스트레이션 (SPEC-SELLER-001 §2·§4·§7 — 3-3 팬아웃).

pipeline.py(순수 계약, LLM·IO 없음)와 달리 이 모듈은 **LLM 실행·비동기 IO**를 가진다.
설계서의 Send 팬아웃은 create_agent 확정(HANDOFF §1 — StateGraph 수작업 조립 금지)에
따라 순수 파이썬 asyncio.gather 로 구현한다. 검증 루프(3-4)·compose(3-5)도 여기 쌓인다.

진행 token 은 Emit 콜백으로 방출한다 — SSE 계층(3-7~)이 큐 넣기 함수를 꽂고,
테스트는 리스트 수집 함수를 꽂는다(2026-07-18 확정).

degrade 수렴 3층(§4·§7):
- 도구 실패("Error:" 문자열) → 워커 자신이 degrade finding 반환(프롬프트 규약, 코드 무개입).
- 워커 예외·타임아웃·구조화 출력 누락 → 코드가 degrade finding 생성(본 모듈).
- 선택 워커 **전부 예외** → AllWorkersFailedError → 호출부가 사과 token 후 done.
  워커가 스스로 반환한 degrade finding 은 실패로 세지 않는다(문자열 판정 의존 회피).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from app.agents.seller import history
from app.agents.seller.context import SellerContext
from app.agents.seller.middleware import check_scope
from app.agents.seller.pipeline import (
    ALL_WORKERS_FAILED_TOKEN,
    PROGRESS_TOKENS,
    WORKER_PROGRESS_TOKENS,
    ResolvedPlan,
    compose_response,
    format_judge_input,
    format_recommend_input,
    format_report_input,
    format_rewrite_input,
    format_worker_input,
    resolve_plan,
)
from app.agents.seller.schemas import (
    AnalysisFinding,
    AnalysisPlan,
    AnalysisType,
    RecommendationSet,
    ReportScore,
    RouteDecision,
)
from app.agents.seller.verifier import run_deterministic_checks
from app.agents.seller.workers import (
    build_abuse_agent,
    build_analysis_planner,
    build_behavior_agent,
    build_churn_agent,
    build_conversion_agent,
    build_recommend_agent,
    build_report_agent,
    build_report_judge,
    build_sales_anomaly_agent,
    build_supervisor,
)
from app.core.config import get_settings
from app.core.llm import LLMNotConfigured

logger = logging.getLogger(__name__)

# 진행 token 방출 콜백 — SSE 계층이 주입한다(예: 큐 put). 테스트는 리스트 수집.
Emit = Callable[[str], Awaitable[None]]

# 배정표(HANDOFF §3)의 실행판 — AnalysisType 전 값 커버를 테스트가 강제한다.
WORKER_BUILDERS: dict[AnalysisType, Callable[[], CompiledStateGraph]] = {
    "sales_anomaly": build_sales_anomaly_agent,
    "conversion": build_conversion_agent,
    "behavior": build_behavior_agent,
    "churn": build_churn_agent,
    "abuse": build_abuse_agent,
}


# ── supervisor 라우팅 (4-1a) ──────────────────────────────────────────────────

# 폴백 사유 문구 — 회귀 테스트·로그가 참조하는 계약값(코드 단일 출처).
ROUTE_FALLBACK_REASON = "라우팅 장애 — general 폴백(코드 지정)"
ROUTE_CONSERVATIVE_REASON = "confidence 미달 — analysis 보수 재지정(코드 지정)"


async def route_question(question: str, context: SellerContext) -> RouteDecision:
    """supervisor 3분기 라우팅 + 코드 후처리 (4-1a, REALIGN §4 확정).

    코드가 최종 판정한다(LLM 은 제안만):
      - supervisor 장애(타임아웃·예외·비정형 출력) → **general 폴백** + warning
        로그(2026-07-19 사용자 결정 — MVP '작동 우선', 최소한의 답변 보장).
      - confidence < settings.seller_route_confidence_min → **analysis 보수
        재지정**(SPEC 장치 ⑤ — 분석 질문을 잡담으로 흘리는 오류가 더 비싸다).
        원분류가 analysis 면 재지정 불필요.
    scope 선차단·confirm 코드 선판정은 호출부(SSE 배선) 소관 — 이 함수는
    라우팅만 담당한다(관심사 분리).
    """
    settings = get_settings()
    try:
        supervisor = build_supervisor()
        result = await asyncio.wait_for(
            supervisor.ainvoke({"messages": [HumanMessage(content=question)]}, context=context),
            timeout=settings.seller_route_timeout_s,
        )
        decision = result.get("structured_response")
        if not isinstance(decision, RouteDecision):
            raise TypeError("supervisor 가 RouteDecision 을 반환하지 않았다")
    except LLMNotConfigured:
        raise
    except Exception:
        logger.warning("supervisor 라우팅 장애 — general 폴백", exc_info=True)
        return RouteDecision(category="general", reason=ROUTE_FALLBACK_REASON, confidence=0.0)
    if (
        decision.category != "analysis"
        and decision.confidence < settings.seller_route_confidence_min
    ):
        logger.info(
            "라우팅 보수 재지정: %s(%.2f) → analysis (%s)",
            decision.category,
            decision.confidence,
            decision.reason,
        )
        return RouteDecision(
            category="analysis",
            reason=f"{ROUTE_CONSERVATIVE_REASON} — 원분류 {decision.category}: {decision.reason}",
            confidence=decision.confidence,
        )
    return decision


class AllWorkersFailedError(RuntimeError):
    """선택된 워커 전부가 예외로 실패 — 호출부는 ALL_WORKERS_FAILED_TOKEN 후 done(§7)."""


def _degrade_finding(analysis_type: AnalysisType, cause: str) -> AnalysisFinding:
    """워커 예외를 degrade finding 으로 변환 — D3 탐지 문자열("확보 실패") 유지."""
    return AnalysisFinding(
        analysis_type=analysis_type,
        summary=f"데이터 확보 실패 — 분석 실행 오류({cause})",
        evidence=[],
        severity="info",
    )


async def _run_one_worker(
    analysis_type: AnalysisType,
    message: str,
    context: SellerContext,
    timeout_s: float,
) -> AnalysisFinding:
    """워커 1종 실행 — 요청마다 빌드(C1 철학·상태 공유 방지), 구조화 출력을 반환.

    타임아웃·예외는 여기서 처리하지 않고 올린다 — 수렴은 run_workers 소관.
    """
    agent = WORKER_BUILDERS[analysis_type]()
    result = await asyncio.wait_for(
        agent.ainvoke({"messages": [HumanMessage(content=message)]}, context=context),
        timeout=timeout_s,
    )
    finding = result.get("structured_response")
    if not isinstance(finding, AnalysisFinding):
        raise TypeError(f"워커 {analysis_type} 가 AnalysisFinding 을 반환하지 않았다")
    return finding


async def run_workers(
    question: str,
    plan: ResolvedPlan,
    context: SellerContext,
    *,
    emit: Emit,
) -> list[AnalysisFinding]:
    """선택된 워커를 병렬 실행하고 finding 목록으로 수렴한다 (팬아웃 → 팬인, §2).

    - 시작 시 워커별 진행 token 을 계획 순서대로 emit(first-token·체감 대기, §7).
    - 실행은 asyncio.gather 병렬 — 반환 순서는 plan.analyses 순서를 유지한다.
    - 일부 실패는 degrade finding 으로 수렴해 부분 보고서로 계속(§4).
    - provider 미구성은 전역 설정 오류라 degrade하지 않고 API 경계까지 전파한다.
    - 전부 예외면 AllWorkersFailedError — 부분 보고서조차 불가능한 경우만이다.
    """
    settings = get_settings()
    message = format_worker_input(question, plan)

    for analysis_type in plan.analyses:
        await emit(WORKER_PROGRESS_TOKENS[analysis_type])

    results = await asyncio.gather(
        *(
            _run_one_worker(t, message, context, settings.seller_worker_timeout_s)
            for t in plan.analyses
        ),
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, LLMNotConfigured):
            raise result

    findings: list[AnalysisFinding] = []
    failures = 0
    for analysis_type, result in zip(plan.analyses, results, strict=True):
        if isinstance(result, BaseException):
            failures += 1
            logger.warning("분석 워커 %s 실패: %r", analysis_type, result)
            cause = "응답 시간 초과" if isinstance(result, asyncio.TimeoutError) else "내부 오류"
            findings.append(_degrade_finding(analysis_type, cause))
        else:
            findings.append(result)

    if failures and failures == len(plan.analyses):
        raise AllWorkersFailedError("선택된 분석 워커가 전부 실패했다")
    return findings


# ── 검증 루프 (3-4) — 결정론 검사 + judge 채점 → ≤N회 재작성 (SPEC §10-⑦) ──────


@dataclass(frozen=True)
class VerifiedReport:
    """검증 루프 결과 — save_history(4단계)·로그·테스트 재료.

    passed=False 는 두 경우다: 루프 소진(미달 채택, §7) 또는 루프 중 LLM 장애로
    기존 보고서를 채택(Q2 결정). attempts 는 완료된 작성 시도 수.
    """

    report: str
    passed: bool
    attempts: int
    last_score: ReportScore | None


def _content_to_text(content: object) -> str:
    """provider별 문자열·블록 메시지 content를 텍스트로 정규화한다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


async def write_verified_report(
    findings: list[AnalysisFinding],
    context: SellerContext,
    *,
    emit: Emit,
) -> VerifiedReport:
    """보고서 작성 → 검증 → 재작성 루프 (판정은 전부 코드 소관, LLM 필드 없음).

    한 시도 = report(smart) 작성 → 결정론 검사(D1~D3) → judge(fast) 채점.
    통과 = 결정론 실패 0건 AND score.total >= Settings 임계(21/30).
    미달 시 결정론 사유 + judge feedback 을 **합산**해 재작성에 주입한다
    (2026-07-18 확정 — 결정론 실패여도 judge 는 항상 실행).

    degrade(Q2, 2026-07-18 위임 결정 — 추후 변경 가능):
    - 루프 소진 → 마지막 보고서 채택 + warning 로그 (§7).
    - 재작성/judge 중 LLM 장애 → 이미 가진 보고서를 미달 채택(passed=False).
      1차 작성부터 실패하면 보고서가 없으므로 예외 전파(호출부 사과 경로).
    """
    settings = get_settings()
    timeout_s = settings.seller_worker_timeout_s
    threshold = settings.seller_report_score_threshold
    max_attempts = settings.seller_report_max_retries

    report_agent = build_report_agent()
    judge_agent = build_report_judge()

    report: str | None = None
    last_score: ReportScore | None = None
    feedback = ""

    for attempt in range(1, max_attempts + 1):
        await emit(PROGRESS_TOKENS["report"])
        message = (
            format_report_input(findings)
            if attempt == 1
            else format_rewrite_input(findings, report or "", feedback)
        )
        try:
            result = await asyncio.wait_for(
                report_agent.ainvoke(
                    {"messages": [HumanMessage(content=message)]}, context=context
                ),
                timeout=timeout_s,
            )
            report = _content_to_text(result["messages"][-1].content)
        except Exception as exc:
            if report is None:
                raise  # 1차 작성 실패 — 내보낼 보고서가 없다(호출부 사과 경로)
            logger.warning("보고서 재작성 %d회차 실패(%r) — 기존 보고서 미달 채택", attempt, exc)
            return VerifiedReport(report, passed=False, attempts=attempt - 1, last_score=last_score)

        await emit(PROGRESS_TOKENS["verify"])
        det_reasons = run_deterministic_checks(report, findings)
        try:
            judge_result = await asyncio.wait_for(
                judge_agent.ainvoke(
                    {"messages": [HumanMessage(content=format_judge_input(findings, report))]},
                    context=context,
                ),
                timeout=timeout_s,
            )
            score = judge_result.get("structured_response")
            if not isinstance(score, ReportScore):
                raise TypeError("judge 가 ReportScore 를 반환하지 않았다")
        except Exception as exc:
            logger.warning("judge %d회차 실패(%r) — 현재 보고서 미검증 채택", attempt, exc)
            return VerifiedReport(report, passed=False, attempts=attempt, last_score=last_score)

        last_score = score
        if not det_reasons and score.total >= threshold:
            return VerifiedReport(report, passed=True, attempts=attempt, last_score=score)

        feedback = "\n".join([*det_reasons, score.feedback])
        logger.info(
            "보고서 검증 미달 %d회차 — 결정론 %d건, 점수 %d/%d",
            attempt,
            len(det_reasons),
            score.total,
            threshold,
        )

    logger.warning("보고서 검증 %d회 미달 — 마지막 보고서 채택(§7 degrade)", max_attempts)
    return VerifiedReport(report or "", passed=False, attempts=max_attempts, last_score=last_score)


# ── recommend + 파이프라인 통합 (3-5) — SPEC §2 REC·COMP ───────────────────────


async def run_recommend(
    findings: list[AnalysisFinding],
    report: str,
    context: SellerContext,
    *,
    emit: Emit,
) -> RecommendationSet:
    """행동 추천 실행 — 실패는 빈 추천으로 degrade(보고서를 죽이지 않는다).

    추천은 부가 가치다: LLM 장애·타임아웃·구조화 출력 실패(6건 초과
    ValidationError — 이월 C2 포함)가 나도 검증된 보고서는 그대로 나간다.
    빈 RecommendationSet 은 §6.3 조회 시 "해당 추천 없음" 경로로 자연 합류한다.
    """
    await emit(PROGRESS_TOKENS["recommend"])
    agent = build_recommend_agent()
    try:
        result = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [HumanMessage(content=format_recommend_input(findings, report))]},
                context=context,
            ),
            timeout=get_settings().seller_worker_timeout_s,
        )
        recommendations = result.get("structured_response")
        if not isinstance(recommendations, RecommendationSet):
            raise TypeError("recommend 가 RecommendationSet 을 반환하지 않았다")
        return recommendations
    except Exception as exc:
        logger.warning("recommend 실패(%r) — 추천 없이 계속(C2 degrade)", exc)
        return RecommendationSet(recommendations=[], summary="")


@dataclass(frozen=True)
class PipelineResult:
    """분석 파이프라인 최종 산출 — SSE 계층·save_history(4단계)가 소비한다.

    kind: report(정상 보고서) / clarification(되묻기 — 파이프라인 미실행) /
    apology(전 워커 실패 사과). text 는 세 경우 모두 사용자에게 보낼 최종 문안.
    """

    kind: Literal["report", "clarification", "apology", "refused"]
    text: str
    verified: VerifiedReport | None = None
    recommendations: RecommendationSet | None = None


async def run_analysis_pipeline(
    question: str,
    context: SellerContext,
    *,
    today: date,
    emit: Emit,
) -> PipelineResult:
    """분석 레인 전체: planner → resolve → 팬아웃 → 검증 루프 → recommend → compose.

    되묻기(계획 불성립·미지원 기간)와 전 워커 실패 사과는 예외가 아니라
    PipelineResult 로 반환한다 — 호출부(SSE)는 kind 와 무관하게 text 를 token 으로
    흘리고 done 하면 된다. **예외 전파는 두 경우다**: planner 자체 장애, 그리고
    1차 보고서 작성 실패(Q2 — 내보낼 보고서가 없음). 호출부는 둘 다 사과/error
    경로로 처리해야 한다.

    scope 가드(3-6): 구조화 출력 레인은 end 점프 미들웨어를 쓸 수 없어(계약 파손)
    **파이프라인 입구에서 check_scope 코드 검사**로 차단한다 — LLM 호출 0회 거절.
    """
    settings = get_settings()

    refusal = check_scope(question)
    if refusal:
        return PipelineResult(kind="refused", text=refusal)

    # 4-3 §9.1: 최근 이력을 planner **입력 메시지**에 주입(프롬프트 불변).
    # 이력은 부가 맥락 — 조회 실패는 주입 없이 계속(분석을 죽이지 않는다).
    planner_input = question
    try:
        entries = await history.load_recent(context.seller_id)
        planner_input = history.build_planner_input(question, entries)
    except Exception:
        logger.warning("분석 이력 조회 실패 — 이력 주입 없이 진행", exc_info=True)

    await emit(PROGRESS_TOKENS["planner"])
    planner = build_analysis_planner()
    result = await asyncio.wait_for(
        planner.ainvoke({"messages": [HumanMessage(content=planner_input)]}, context=context),
        timeout=settings.seller_worker_timeout_s,
    )
    plan = result.get("structured_response")
    if not isinstance(plan, AnalysisPlan):
        raise TypeError("planner 가 AnalysisPlan 을 반환하지 않았다")

    try:
        resolved = resolve_plan(
            plan, today=today, recent_default_days=settings.seller_recent_days_default
        )
    except ValueError as exc:
        return PipelineResult(kind="clarification", text=str(exc))

    try:
        findings = await run_workers(question, resolved, context, emit=emit)
    except AllWorkersFailedError:
        return PipelineResult(kind="apology", text=ALL_WORKERS_FAILED_TOKEN)

    verified = await write_verified_report(findings, context, emit=emit)
    recommendations = await run_recommend(findings, verified.report, context, emit=emit)

    # 4-3 §9.1: compose 후 save_history — §6.3 "N번 적용해줘"·planner 주입의 원천.
    # 저장 실패는 응답을 죽이지 않는다(이력은 부가 데이터 — degrade + warning).
    try:
        await history.save_history(
            context.seller_id,
            question=question,
            analyses=list(resolved.analyses),
            date_from=resolved.date_from.isoformat(),
            date_to=resolved.date_to.isoformat(),
            report=verified.report,
            recommendations=recommendations,
        )
    except Exception:
        logger.warning("분석 이력 저장 실패 — 응답은 계속", exc_info=True)

    return PipelineResult(
        kind="report",
        text=compose_response(verified.report, recommendations),
        verified=verified,
        recommendations=recommendations,
    )
