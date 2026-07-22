"""판매자 그래프 구조화 출력 스키마 (SPEC-SELLER-001 §3, 일관성 장치 ⑤ — Literal·ge/le).

create_agent 의 response_format 으로 강제되는 **내부 계약**이다 — 와이어(SSE/HTTP)에
직접 나가지 않으므로 CamelModel 이 아닌 일반 BaseModel 을 쓴다(snake_case 유지).
2-2a: RouteDecision·AnalysisFinding (2026-07-18 사용자 확정).
2-2b: ReportScore·ProposedChange·ActionRecommendation·RecommendationSet (2026-07-18 사용자 확정).
2-7: DraftChange·DraftProposal (2026-07-18 잠정 확정 — 4단계 HITL 배선 시 조정 가능).
3-1: AnalysisPlan (2026-07-18 사용자 확정 — "이번 달" 등 미지원 표현은 되묻기).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# 분석 워커 5종 식별자 — supervisor 라우팅·planner 분류·finding 이 공유하는 단일 출처.
AnalysisType = Literal["sales_anomaly", "conversion", "behavior", "churn", "abuse"]


class RouteDecision(BaseModel):
    """supervisor 의 3분기 라우팅 결과 (SPEC §2 — 구조화 출력 라우팅, Haiku t=0).

    category 는 Literal 로 세 값만 허용 — LLM 이 신규 카테고리를 지어낼 수 없다(장치 ⑤).
    confidence 는 "애매하면 analysis 보수적 라우팅" 규칙의 코드 분기 재료다.
    """

    category: Literal["analysis", "product", "general"] = Field(description="질문 카테고리")
    reason: str = Field(description="분류 근거 한 문장 — 오라우팅 디버깅·회귀 테스트용")
    confidence: float = Field(ge=0.0, le=1.0, description="분류 확신도(0~1)")


class AnalysisFinding(BaseModel):
    """분석 워커 5종의 공통 반환 형식 (SPEC §2 — response_format=ToolStrategy(AnalysisFinding)).

    팬인 시 report_agent 가 이질적 텍스트가 아닌 동일 스키마 목록을 받게 한다(설계서 §4.2.2).
    조회 실패 시에는 severity="info" + summary="데이터 확보 실패..." 의 degrade finding 을
    반환하고 파이프라인은 계속된다(SPEC §4) — 그래서 evidence 는 빈 목록을 허용한다.
    """

    analysis_type: AnalysisType = Field(description="실행한 분석 유형")
    summary: str = Field(description="핵심 발견 요약(2~3문장)")
    evidence: list[str] = Field(
        default_factory=list,
        description="근거 수치/사실 목록 — degrade finding 은 비어 있을 수 있다",
    )
    severity: Literal["info", "warning", "critical"] = Field(description="심각도")
    recommendation: str = Field(
        default="",
        description="간단 조치 힌트(선택) — 정식 행동 추천은 recommend_agent 소관(2-2b)",
    )
    chart_data_hint: str = Field(
        default="",
        description="차트로 그릴 데이터 설명 — 차트는 MVP 보류(SPEC §12), 복원 대비 보존",
    )


# ── 3-1: AnalysisPlan (analysis_planner 구조화 출력 — 파이프라인 입력 계약) ────


class AnalysisPlan(BaseModel):
    """analysis_planner 의 구조화 출력 (SPEC §2 — 워커 선택 + 기간 표현, Haiku t=0).

    기간의 날짜 환산은 LLM 소관이 아니다(장치 ④) — planner 는 질문의 기간 표현을
    정규 어휘(period_expr)로 재표현만 하고, 실제 (from, to) 환산은 파이프라인 코드가
    `calc.normalize_period` 로 수행한다(pipeline.resolve_plan). 환산 불가 표현은
    ValueError → 되묻기 token 경로(2026-07-18 확정 — "이번 달" 등은 지원하지 않는다).

    clarification 이 비어있지 않으면 계획 불성립 — 호출부는 팬아웃 대신 되묻기
    token 으로 전환한다(DraftProposal.clarification 과 동일 패턴).
    """

    analyses: list[AnalysisType] = Field(
        default_factory=list,
        max_length=5,
        description="실행할 분석 워커 선택(1~5종) — clarification 시에는 빈 목록",
    )
    period_expr: str = Field(
        default="최근",
        description=(
            "정규 어휘로 재표현한 기간 — '지난달'/'최근 N일'/'어제'/"
            "'YYYY-MM-DD~YYYY-MM-DD'(질문에 명시된 날짜 옮겨적기만). "
            "기간 언급이 없으면 '최근'(기본 일수는 코드 소관)"
        ),
    )
    reason: str = Field(description="분석 선택 근거 한 문장 — 오분류 디버깅·회귀 테스트용")
    clarification: str = Field(
        default="",
        description="기간·범위 해석 불능 시 되물을 질문 — 비어있지 않으면 계획 불성립",
    )

    @field_validator("analyses")
    @classmethod
    def _dedupe_preserving_order(cls, value: list[AnalysisType]) -> list[AnalysisType]:
        """중복 워커는 첫 등장만 남긴다 — 거부(ValidationError) 대신 관용 처리.

        하드 거부는 ToolStrategy 재시도 루프만 늘린다 — 중복은 의미가 같으므로
        조용히 정리한다(팬아웃 이중 실행 방지가 목적).
        """
        return list(dict.fromkeys(value))


# ── 2-2b: ReportScore · RecommendationSet ─────────────────────────────────────

# judge 채점 축의 단일 출처 — 축 확장 시 ReportScore 에 필드 1개 추가 + 여기 등록(total 자동 합산).
SCORE_AXES: tuple[str, ...] = ("accuracy", "completeness", "clarity")
SCORE_AXIS_MAX = (
    10  # 축당 만점 — 통과 임계(현재 21/30)·루프 횟수는 Settings·verifier 코드 소관(장치 ⑦)
)


class ReportScore(BaseModel):
    """report_verifier 의 Haiku judge 채점 결과 (SPEC §10-⑦ — 21/30 판정 재료, ≤3회 루프).

    총점·통과 여부는 LLM 필드로 두지 않는다 — LLM 산수 오류를 배제하기 위해
    total 은 코드 property 로 합산하고, 임계·루프 횟수는 verifier 노드(3단계)가
    Settings 에서 읽어 판정한다. 축 확장: 필드 추가 + SCORE_AXES 등록이면 끝.
    """

    accuracy: int = Field(
        ge=0,
        le=SCORE_AXIS_MAX,
        description="수치 정합 — 보고서의 수치가 finding evidence 와 일치하는가",
    )
    completeness: int = Field(
        ge=0,
        le=SCORE_AXIS_MAX,
        description="완전성 — 전달된 finding 전부를 반영하고 요청 범위를 커버하는가",
    )
    clarity: int = Field(
        ge=0,
        le=SCORE_AXIS_MAX,
        description="명료성 — 판매자가 이해하고 실행할 수 있는 서술인가",
    )
    feedback: str = Field(
        description="미달 축 중심의 개선 지시 — 재작성 루프에서 report_agent 프롬프트에 주입",
    )

    @property
    def total(self) -> int:
        """축 합산 총점(현재 만점 30) — 판정 재료. 판정 자체는 verifier 코드 소관."""
        return sum(getattr(self, axis) for axis in SCORE_AXES)


# update_product 인자 8종과 1:1 — 도구 시그니처가 바뀌면 여기도 함께 갱신한다.
ProductField = Literal[
    "name",
    "price",
    "original_price",
    "description",
    "category",
    "image_url",
    "status",
    "stock_quantity",
]


class ProposedChange(BaseModel):
    """추천이 제안하는 개별 필드 변경 초안 — §6.3 draft 변환의 재료.

    after 는 str 통일(2026-07-18 확정) — 가격·재고도 문자열로 받고, draft 변환
    코드(4단계)가 필드별로 캐스팅한다(Union 타입은 구조화 출력 실패율을 높임).
    before 는 저장하지 않는다 — 실행 시점에 I-9 조회로 확보한다(SPEC §3 draft 규약).
    """

    field: ProductField = Field(description="변경 대상 필드 — update_product 인자와 동일 8종")
    after: str = Field(description="변경 후 값 초안 — 수치도 문자열(예: '12900')")


class ActionRecommendation(BaseModel):
    """recommend_agent 의 개별 행동 추천 — §6.3 'N번 적용해줘'가 조회하는 저장 원천.

    product_id 는 필수(2026-07-18 확정) — promotion 유형도 특정 상품 대상으로
    한정한다(브랜드 단위 추천은 MVP 범위 밖, draft 변환에 productId 가 반드시 필요).
    [변경 2026-07-19, REALIGN F2/D2] productId 는 숫자(BE·FE 확정, DB BIGINT) — str 폐기.
    """

    action_type: Literal[
        "price_adjust",
        "description_update",
        "stock_adjust",
        "product_visibility",
        "promotion",
    ] = Field(description="추천 유형")
    product_id: int = Field(description="대상 상품 식별자(숫자) — draft 변환의 키(필수)")
    title: str = Field(description="짧은 제목 — '1번: 감귤청 가격 10% 인하' 식 표시 단위")
    rationale: str = Field(description="근거 — 어떤 finding·수치에서 나온 추천인지")
    changes: list[ProposedChange] = Field(
        default_factory=list,
        description="구체 변경 초안 — promotion 등 필드 변경이 아닌 유형은 빈 목록 허용",
    )
    expected_effect: str = Field(default="", description="기대 효과 한 문장(선택)")


# ── 2-7: DraftProposal (product_agent draft 생성 — 잠정 확정, 4단계 HITL 배선 시 조정 가능) ──


class DraftChange(BaseModel):
    """draft 의 개별 필드 변경 (api-spec §3.2 changes[] 원소의 내부 계약).

    before 는 반드시 list_my_products 조회값에서 옮긴다(프롬프트 강제) —
    create 시에는 "" 로 둔다. 와이어 변환(SSE draft, CamelModel)은 4단계 소관.
    """

    field: ProductField = Field(description="변경 대상 필드 — update_product 인자와 동일 8종")
    before: str = Field(description="변경 전 값 — list_my_products 조회값 그대로, create 는 ''")
    after: str = Field(description="변경 후 값 — 수치도 문자열")


class DraftProposal(BaseModel):
    """product_agent 의 draft 구조화 출력 (SPEC §6 — 2-7 은 draft 생성까지).

    draftId 는 LLM 필드가 아니다 — 실행 계층(4단계)이 uuid 로 발급해 checkpoint 에
    바인딩한다(§6.2-①, ReportScore.total 과 같은 '계약값은 코드' 원칙).
    clarification 이 비어있지 않으면 draft 불성립 — 호출부는 draft 대신 되묻기
    token 으로 전환한다(§6.3-4 패턴). 2026-07-18 잠정 확정 — 4단계에서 조정 가능.
    """

    op: Literal["create", "update", "delete"] = Field(description="작업 종류(api-spec §3.2)")
    # [변경 2026-07-19, REALIGN F2/D2] productId 숫자 확정 — create 는 null(구 "" 폐기).
    product_id: int | None = Field(
        default=None,
        description="대상 상품(숫자) — update/delete 필수, create 는 null(코드가 검증)",
    )
    changes: list[DraftChange] = Field(
        default_factory=list,
        description="변경 목록 — delete 는 status ON_SALE→HIDDEN 1건으로 표현(soft delete 가시화)",
    )
    summary: str = Field(description="diff 카드 보조 한 줄 요약(예: '가격 12,900원으로 인하')")
    clarification: str = Field(
        default="",
        description="대상 모호·제약 위반·추천 적용 발화 시 되물을 질문 — 비어있지 않으면 draft 불성립",
    )


MAX_RECOMMENDATIONS = 5  # 추천 개수 상한 — 스키마 계약(와이어 아님)이라 Settings 가 아닌 상수


class RecommendationSet(BaseModel):
    """recommend_agent 구조화 출력 (response_format=ToolStrategy(RecommendationSet)).

    recommendations 의 **목록 순서가 곧 'N번'** — save_history 가 이 순서 그대로
    저장하고 §6.3 이 recommendations[N-1] 로 조회한다(순서가 계약이다).
    조회 실패·추천 없음 degrade 를 위해 빈 목록을 허용한다.
    """

    recommendations: list[ActionRecommendation] = Field(
        default_factory=list,
        max_length=MAX_RECOMMENDATIONS,
        description="행동 추천 목록(≤5) — 순서 보존이 §6.3 조회 계약",
    )
    summary: str = Field(default="", description="추천 전체 한 줄 요약 — compose_response 용(선택)")
