"""app/agents/seller/pipeline.py 파이프라인 입출력 계약 검증 (3-1)."""

from __future__ import annotations

import datetime as dt
from typing import get_args

import pytest

from app.agents.seller import pipeline
from app.agents.seller.schemas import (
    ActionRecommendation,
    AnalysisFinding,
    AnalysisPlan,
    AnalysisType,
    RecommendationSet,
)


def _plan(**overrides: object) -> AnalysisPlan:
    """테스트용 기본 계획 — 필요한 필드만 덮어쓴다."""
    base: dict = {"analyses": ["sales_anomaly"], "reason": "테스트"}
    base.update(overrides)
    return AnalysisPlan(**base)


def test_resolve_plan_happy_path() -> None:
    """'지난달' 계획 → 전월 1일~말일 ResolvedPlan(코드 환산, 장치 ④)."""
    plan = _plan(analyses=["sales_anomaly", "churn"], period_expr="지난달")
    resolved = pipeline.resolve_plan(plan, today=dt.date(2026, 7, 18), recent_default_days=7)
    assert resolved.analyses == ("sales_anomaly", "churn")
    assert resolved.date_from == dt.date(2026, 6, 1)
    assert resolved.date_to == dt.date(2026, 6, 30)


def test_resolve_plan_default_period_uses_recent_default() -> None:
    """기간 미언급(period_expr 기본 '최근') → recent_default_days 일, 오늘 제외."""
    resolved = pipeline.resolve_plan(_plan(), today=dt.date(2026, 7, 18), recent_default_days=7)
    assert resolved.date_from == dt.date(2026, 7, 11)
    assert resolved.date_to == dt.date(2026, 7, 17)


def test_resolve_plan_clarification_raises_with_question() -> None:
    """clarification 이 있으면 계획 불성립 — 되물을 질문이 ValueError 메시지로 올라온다."""
    plan = _plan(analyses=[], clarification="어느 기간의 분석을 원하시나요?")
    with pytest.raises(ValueError, match="어느 기간"):
        pipeline.resolve_plan(plan, today=dt.date(2026, 7, 18), recent_default_days=7)


def test_resolve_plan_empty_analyses_raises() -> None:
    """clarification 없이 워커도 비면 planner 오류 — 되묻기 ValueError."""
    with pytest.raises(ValueError):
        pipeline.resolve_plan(_plan(analyses=[]), today=dt.date(2026, 7, 18), recent_default_days=7)


def test_resolve_plan_unsupported_period_propagates() -> None:
    """미지원 기간 표현("이번 달")은 normalize_period 의 ValueError 가 전파된다."""
    with pytest.raises(ValueError):
        pipeline.resolve_plan(
            _plan(period_expr="이번 달"), today=dt.date(2026, 7, 18), recent_default_days=7
        )


def test_format_worker_input_contains_period_and_question() -> None:
    """워커 입력 포맷 — from/to(ISO)와 질문이 규약 형태로 들어간다(기간 주입 규약)."""
    resolved = pipeline.ResolvedPlan(
        analyses=("sales_anomaly",),
        date_from=dt.date(2026, 6, 1),
        date_to=dt.date(2026, 6, 30),
    )
    text = pipeline.format_worker_input("지난달 매출이 왜 떨어졌어?", resolved)
    assert "[분석 기간] from=2026-06-01 to=2026-06-30" in text
    assert "[판매자 질문] 지난달 매출이 왜 떨어졌어?" in text


def test_format_findings_block_numbers_and_details() -> None:
    """번호·유형·심각도·요약·근거·조치 힌트가 규약 형태로 직렬화된다(report/judge 공용)."""
    findings = [
        AnalysisFinding(
            analysis_type="sales_anomaly",
            summary="급락 발견",
            evidence=["06-12 매출 180,000원"],
            severity="warning",
            recommendation="가격 재검토",
        ),
        AnalysisFinding(
            analysis_type="abuse", summary="데이터 확보 실패", evidence=[], severity="info"
        ),
    ]
    block = pipeline.format_findings_block(findings)
    assert "1. [sales_anomaly] (severity=warning) 급락 발견" in block
    assert "   - 근거: 06-12 매출 180,000원" in block
    assert "   - 조치 힌트: 가격 재검토" in block
    assert "2. [abuse] (severity=info) 데이터 확보 실패" in block


def test_format_rewrite_and_judge_inputs() -> None:
    """재작성 입력은 이전 보고서+개선 지시를, judge 입력은 보고서를 포함한다(3-4 계약)."""
    findings = [
        AnalysisFinding(
            analysis_type="churn",
            summary="이탈 증가",
            evidence=["이탈률 12.5%"],
            severity="warning",
        )
    ]
    rewrite = pipeline.format_rewrite_input(findings, "이전 본문", "수치 근거를 인용할 것")
    assert "[이전 보고서]\n이전 본문" in rewrite
    assert "[개선 지시]\n수치 근거를 인용할 것" in rewrite
    assert "[분석 결과]" in rewrite

    judge = pipeline.format_judge_input(findings, "보고서 본문")
    assert "[보고서]\n보고서 본문" in judge
    assert "[분석 결과]" in judge


def _recommendation(title: str, effect: str = "") -> ActionRecommendation:
    return ActionRecommendation(
        action_type="price_adjust",
        product_id=101,
        title=title,
        rationale="근거",
        expected_effect=effect,
    )


def test_compose_response_numbers_follow_list_order() -> None:
    """번호("N번.")는 목록 순서 그대로 — §6.3 recommendations[N-1] 조회 계약의 표면."""
    recs = RecommendationSet(
        recommendations=[
            _recommendation("감귤청 가격 10% 인하", "전환율 회복"),
            _recommendation("품절 상품 재입고"),
        ],
        summary="가격·재고 중심 2건",
    )
    text = pipeline.compose_response("보고서 본문", recs)
    assert text.startswith("보고서 본문")
    assert "[추천 행동]" in text
    assert "1번. 감귤청 가격 10% 인하" in text
    assert "   기대 효과: 전환율 회복" in text
    assert "2번. 품절 상품 재입고" in text
    assert "가격·재고 중심 2건" in text  # summary 는 목록 앞에 포함(마감 리뷰 테스트 공백)
    assert "N번 적용해줘" in text


def test_compose_response_empty_recommendations() -> None:
    """빈 추천 — 보고서만(사유 summary 가 있으면 한 줄 덧붙임), 안내 문구 없음."""
    assert pipeline.compose_response("본문", RecommendationSet()) == "본문"

    with_reason = pipeline.compose_response(
        "본문", RecommendationSet(recommendations=[], summary="추천할 근거가 없습니다")
    )
    assert with_reason == "본문\n\n[추천 행동]\n추천할 근거가 없습니다"
    assert "N번" not in with_reason


def test_format_recommend_input_contract() -> None:
    """recommend 입력 — 분석 결과 + 검증된 보고서 (RECOMMEND_PROMPT 계약)."""
    findings = [
        AnalysisFinding(
            analysis_type="conversion", summary="병목", evidence=["전환율 2.1%"], severity="warning"
        )
    ]
    text = pipeline.format_recommend_input(findings, "검증 본문")
    assert "[분석 결과]" in text
    assert "[검증된 보고서]\n검증 본문" in text


def test_worker_progress_tokens_cover_all_analysis_types() -> None:
    """진행 token 은 AnalysisType 5종 전부를 커버한다(누락 시 모듈 로드도 실패)."""
    assert set(pipeline.WORKER_PROGRESS_TOKENS) == set(get_args(AnalysisType))


def test_progress_token_stages() -> None:
    """단계 token 키 계약 — 오케스트레이션(3-3~3-5)이 소비하는 4단계."""
    assert set(pipeline.PROGRESS_TOKENS) == {"planner", "report", "verify", "recommend"}
    assert pipeline.ALL_WORKERS_FAILED_TOKEN.startswith("죄송합니다")
