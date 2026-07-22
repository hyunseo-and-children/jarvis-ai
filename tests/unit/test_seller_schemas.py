"""app/agents/seller/schemas.py 구조화 출력 스키마 검증 (일관성 장치 ⑤ — Literal·ge/le)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.seller.schemas import (
    SCORE_AXES,
    ActionRecommendation,
    AnalysisFinding,
    AnalysisPlan,
    DraftChange,
    DraftProposal,
    ProposedChange,
    RecommendationSet,
    ReportScore,
    RouteDecision,
)


# ── 3-1: AnalysisPlan ────────────────────────────────────────────────────────


def test_analysis_plan_defaults_and_construction() -> None:
    """정상 계획 — period_expr 기본값은 '최근', clarification 기본값은 빈 문자열."""
    plan = AnalysisPlan(analyses=["sales_anomaly", "conversion"], reason="매출 급락 질문")
    assert plan.analyses == ["sales_anomaly", "conversion"]
    assert plan.period_expr == "최근"
    assert plan.clarification == ""


def test_analysis_plan_dedupes_preserving_order() -> None:
    """중복 워커는 거부하지 않고 첫 등장만 남긴다(ToolStrategy 재시도 루프 방지)."""
    plan = AnalysisPlan(analyses=["churn", "sales_anomaly", "churn"], reason="이탈+매출")
    assert plan.analyses == ["churn", "sales_anomaly"]


def test_analysis_plan_rejects_unknown_type_and_overflow() -> None:
    """AnalysisType 밖 값·5종 초과(중복 아닌 6개는 불가능하지만 Literal 위반 우선)는 거부."""
    with pytest.raises(ValidationError):
        AnalysisPlan(analyses=["revenue"], reason="r")
    with pytest.raises(ValidationError):
        AnalysisPlan(
            analyses=["sales_anomaly", "conversion", "behavior", "churn", "abuse", "sales_anomaly"],
            reason="r",
        )  # max_length=5 는 validator(dedupe) 이전에 걸린다


def test_route_decision_accepts_valid_categories() -> None:
    """analysis/product/general 세 값 모두 정상 생성된다."""
    for category in ("analysis", "product", "general"):
        decision = RouteDecision(category=category, reason="근거", confidence=0.9)
        assert decision.category == category


def test_route_decision_rejects_unknown_category() -> None:
    """Literal 밖 카테고리는 ValidationError — LLM 이 신규값을 지어낼 수 없다."""
    with pytest.raises(ValidationError):
        RouteDecision(category="chitchat", reason="근거", confidence=0.9)


def test_route_decision_confidence_bounds() -> None:
    """confidence 는 0~1 범위를 벗어나면 거부된다(ge/le)."""
    with pytest.raises(ValidationError):
        RouteDecision(category="general", reason="근거", confidence=1.5)
    with pytest.raises(ValidationError):
        RouteDecision(category="general", reason="근거", confidence=-0.1)


def test_analysis_finding_full_construction() -> None:
    """정상 finding — 5종 Literal 유형과 심각도, 근거 목록이 그대로 보존된다."""
    finding = AnalysisFinding(
        analysis_type="sales_anomaly",
        summary="6월 12일 매출이 직전 7일 평균 대비 42% 급락했다.",
        evidence=["06-12 매출 180,000원 (직전 7일 평균 310,000원)", "동일 06-11 가격 인상 이력"],
        severity="warning",
        recommendation="가격 인상 폭 재검토",
    )
    assert finding.analysis_type == "sales_anomaly"
    assert len(finding.evidence) == 2
    assert finding.chart_data_hint == ""  # 차트 보류(§12) — 기본값 빈 문자열


def test_analysis_finding_rejects_unknown_type_and_severity() -> None:
    """analysis_type(5종)·severity(3종) Literal 위반은 거부된다."""
    with pytest.raises(ValidationError):
        AnalysisFinding(analysis_type="revenue", summary="s", severity="warning")
    with pytest.raises(ValidationError):
        AnalysisFinding(analysis_type="churn", summary="s", severity="fatal")


def test_analysis_finding_degrade_shape() -> None:
    """조회 실패 degrade finding(SPEC §4) — evidence 빈 목록·recommendation 생략 가능."""
    finding = AnalysisFinding(
        analysis_type="abuse",
        summary="데이터 확보 실패 — I-13/I-14 조회가 타임아웃되어 분석을 생략했다.",
        severity="info",
    )
    assert finding.evidence == []
    assert finding.recommendation == ""


# ── 2-2b: ReportScore · RecommendationSet ─────────────────────────────────────


def test_report_score_total_is_code_sum() -> None:
    """총점은 LLM 필드가 아니라 코드 property — 3축 합산이 그대로 나온다."""
    score = ReportScore(accuracy=8, completeness=7, clarity=6, feedback="근거 수치 보강 필요")
    assert score.total == 21  # 통과 임계(21/30)와 같은 값 — 판정 자체는 verifier 코드 소관


def test_report_score_axis_bounds() -> None:
    """축 점수는 0~10 을 벗어나면 거부된다(ge/le) — judge 가 배점을 지어낼 수 없다."""
    with pytest.raises(ValidationError):
        ReportScore(accuracy=11, completeness=5, clarity=5, feedback="f")
    with pytest.raises(ValidationError):
        ReportScore(accuracy=5, completeness=-1, clarity=5, feedback="f")


def test_score_axes_constant_matches_model_fields() -> None:
    """SCORE_AXES(확장 지점)는 실제 모델 필드와 어긋나면 안 된다 — total 합산의 안전망."""
    for axis in SCORE_AXES:
        assert axis in ReportScore.model_fields


def test_recommendation_set_preserves_order() -> None:
    """목록 순서가 곧 'N번'(§6.3) — recommendations[N-1] 조회 계약을 보존한다."""
    first = ActionRecommendation(
        action_type="price_adjust",
        product_id=101,
        title="1번: 감귤청 가격 10% 인하",
        rationale="매출 급락 3일이 가격 인상 직후와 겹침",
        changes=[ProposedChange(field="price", after="12900")],
        expected_effect="전환율 회복",
    )
    second = ActionRecommendation(
        action_type="description_update",
        product_id=102,
        title="2번: 상세 설명에 용량 표기 추가",
        rationale="상세 이탈률이 유사 상품 대비 높음",
        changes=[ProposedChange(field="description", after="500ml 대용량...")],
    )
    rec_set = RecommendationSet(recommendations=[first, second], summary="가격·설명 2건")
    assert rec_set.recommendations[0].title.startswith("1번")
    assert rec_set.recommendations[1].action_type == "description_update"
    assert rec_set.recommendations[0].changes[0].after == "12900"  # 수치도 str 통일


def test_action_recommendation_rejects_unknown_action_type_and_field() -> None:
    """action_type(5종)·ProposedChange.field(8종) Literal 위반은 거부된다."""
    with pytest.raises(ValidationError):
        ActionRecommendation(action_type="discount_event", product_id=101, title="t", rationale="r")
    with pytest.raises(ValidationError):
        ProposedChange(field="seller_id", after="x")  # 신원 필드는 애초에 8종에 없다


def test_action_recommendation_requires_product_id() -> None:
    """product_id 는 필수(2026-07-18 확정) — 없으면 draft 변환이 불가능하다."""
    with pytest.raises(ValidationError):
        ActionRecommendation(action_type="promotion", title="t", rationale="r")


def test_recommendation_set_degrade_and_max_length() -> None:
    """빈 목록 degrade 는 허용, 5건 초과는 거부된다(max_length)."""
    assert RecommendationSet().recommendations == []
    one = ActionRecommendation(action_type="promotion", product_id=101, title="t", rationale="r")
    assert one.changes == []  # promotion — 필드 변경 없는 유형은 changes 빈 목록
    with pytest.raises(ValidationError):
        RecommendationSet(recommendations=[one] * 6)


# ── 2-7: DraftChange · DraftProposal ─────────────────────────────────────────


def test_draft_proposal_update_shape() -> None:
    """update draft — before/after 쌍이 보존되고 draftId 필드는 존재하지 않는다."""
    draft = DraftProposal(
        op="update",
        product_id=101,
        changes=[DraftChange(field="price", before="15000", after="12900")],
        summary="가격 12,900원으로 인하",
    )
    assert draft.changes[0].before == "15000"
    assert draft.clarification == ""  # 기본값 — draft 성립 상태
    assert "draft_id" not in DraftProposal.model_fields  # draftId 는 코드 발급(4단계)


def test_draft_proposal_delete_as_status_change() -> None:
    """delete draft — soft delete 를 status ON_SALE→HIDDEN 1건으로 가시화한다."""
    draft = DraftProposal(
        op="delete",
        product_id=102,
        changes=[DraftChange(field="status", before="ON_SALE", after="HIDDEN")],
        summary="상품 숨김 처리(물리 삭제 아님)",
    )
    assert draft.changes[0].after == "HIDDEN"


def test_draft_proposal_clarification_pattern() -> None:
    """대상 모호 시 — clarification 채움 + changes 비움 + product_id 기본 null(F2 숫자 전환)."""
    draft = DraftProposal(
        op="update",
        summary="",
        clarification="'감귤' 상품이 3건입니다. 어느 상품인가요? (p-1/p-2/p-3)",
    )
    assert draft.clarification != ""  # 호출부가 되묻기 token 으로 전환하는 판정 재료
    assert draft.changes == []
    assert draft.product_id is None  # [변경 2026-07-19] 숫자 전환 — create/미정은 null


def test_draft_rejects_unknown_op_and_field() -> None:
    """op(3종)·DraftChange.field(8종) Literal 위반은 거부된다."""
    with pytest.raises(ValidationError):
        DraftProposal(op="archive", summary="s")
    with pytest.raises(ValidationError):
        DraftChange(field="brand_id", before="", after="x")  # 신원 필드는 8종에 없다
