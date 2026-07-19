"""판매자 분석 워커 팩토리 테스트 (SPEC-SELLER-001 §2·§4 — 2-4b·2-5 묶음 1).

실 LLM 호출 없음 — 도구 배정·프롬프트 필수 요소·에이전트 조립 가능 여부만 검증한다.
워커가 늘 때마다 WORKERS 목록에 한 줄 추가하면 공통 검증이 전부 적용된다.
"""

from __future__ import annotations

import pytest

from app.agents.seller.prompts import (
    ABUSE_PROMPT,
    BEHAVIOR_PROMPT,
    CHURN_PROMPT,
    CONVERSION_PROMPT,
    GENERAL_PROMPT_TEMPLATE,
    JUDGE_PROMPT,
    PLANNER_PROMPT,
    PRODUCT_PROMPT,
    RECOMMEND_PROMPT,
    REPORT_PROMPT,
    SALES_ANOMALY_PROMPT,
    WORKER_COMMON_RULES,
)
from app.agents.seller.tools import PRODUCT_TOOLS
from app.agents.seller.workers import (
    ABUSE_TOOLS,
    BEHAVIOR_TOOLS,
    CHURN_TOOLS,
    CONVERSION_TOOLS,
    GENERAL_TOOLS,
    PRODUCT_DRAFT_TOOLS,
    RECOMMEND_TOOLS,
    SALES_ANOMALY_TOOLS,
    build_abuse_agent,
    build_analysis_planner,
    build_behavior_agent,
    build_churn_agent,
    build_conversion_agent,
    build_general_agent,
    build_product_agent,
    build_recommend_agent,
    build_report_agent,
    build_report_judge,
    build_sales_anomaly_agent,
)

# (analysis_type, 도구 목록, 프롬프트, 빌더, 배정표 기대 도구명) — 워커 추가 시 여기만 확장.
WORKERS = [
    (
        "sales_anomaly",
        SALES_ANOMALY_TOOLS,
        SALES_ANOMALY_PROMPT,
        build_sales_anomaly_agent,
        {"get_sales_timeseries", "get_order_events", "get_product_change_logs",
         "search_analysis_guide"},
    ),
    (
        "conversion",
        CONVERSION_TOOLS,
        CONVERSION_PROMPT,
        build_conversion_agent,
        {"get_funnel", "search_analysis_guide"},
    ),
    (
        "behavior",
        BEHAVIOR_TOOLS,
        BEHAVIOR_PROMPT,
        build_behavior_agent,
        {"get_behavior_events", "get_funnel", "search_analysis_guide"},
    ),
    (
        "churn",
        CHURN_TOOLS,
        CHURN_PROMPT,
        build_churn_agent,
        {"get_churn_cohort", "get_order_events", "get_product_change_logs",
         "get_account_events", "search_analysis_guide"},
    ),
    (
        "abuse",
        ABUSE_TOOLS,
        ABUSE_PROMPT,
        build_abuse_agent,
        {"get_behavior_events", "get_order_events", "get_account_events",
         "search_analysis_guide"},
    ),
]

_IDS = [w[0] for w in WORKERS]


@pytest.mark.parametrize(("analysis_type", "tools", "prompt", "builder", "expected"), WORKERS, ids=_IDS)
def test_tool_assignment_matches_table(analysis_type, tools, prompt, builder, expected) -> None:
    """배정표(HANDOFF §3)와 정확히 일치 — 초과 배정도 누락도 없다."""
    assert {t.name for t in tools} == expected


@pytest.mark.parametrize(("analysis_type", "tools", "prompt", "builder", "expected"), WORKERS, ids=_IDS)
def test_excludes_write_tools(analysis_type, tools, prompt, builder, expected) -> None:
    """쓰기 도구 3종(create/update/delete)은 분석 워커에 절대 배정되지 않는다(§4)."""
    write_names = {t.name for t in PRODUCT_TOOLS} - {"list_my_products"}
    assert {t.name for t in tools}.isdisjoint(write_names)


@pytest.mark.parametrize(("analysis_type", "tools", "prompt", "builder", "expected"), WORKERS, ids=_IDS)
def test_tools_hide_identity(analysis_type, tools, prompt, builder, expected) -> None:
    """신원 인자(runtime·brand_id·seller_id)는 LLM 노출 스키마에 없다(IDOR)."""
    for t in tools:
        for hidden in ("runtime", "brand_id", "seller_id"):
            assert hidden not in t.args


@pytest.mark.parametrize(("analysis_type", "tools", "prompt", "builder", "expected"), WORKERS, ids=_IDS)
def test_prompt_required_elements(analysis_type, tools, prompt, builder, expected) -> None:
    """확정 프롬프트 필수 요소 — analysis_type 고정·기준서 먼저·공통 규칙 결합."""
    assert analysis_type in prompt  # 워커별 analysis_type 고정 지시
    assert "search_analysis_guide" in prompt  # 기준서 검색 먼저(장치 ③)
    assert prompt.endswith(WORKER_COMMON_RULES)  # 공통 규칙이 말미에 결합됨


def test_secondary_source_rule_in_churn_abuse() -> None:
    """I-8 보조 소스 규약(HANDOFF §3) — 보조 실패는 degrade 사유가 아님을 명시한다."""
    for prompt in (CHURN_PROMPT, ABUSE_PROMPT):
        assert "get_account_events 는 보조 소스" in prompt
        assert "주 소스 결과만으로 계속" in prompt


def test_common_rules_content() -> None:
    """공통 규칙 3요소 — 코드 판정 번복 금지(§5)·degrade(§4)·기간은 planner(장치 ④)."""
    assert "번복하지 않는다" in WORKER_COMMON_RULES
    assert "데이터 확보 실패" in WORKER_COMMON_RULES
    assert "날짜를 직접 계산하지 않는다" in WORKER_COMMON_RULES


@pytest.mark.parametrize(("analysis_type", "tools", "prompt", "builder", "expected"), WORKERS, ids=_IDS)
def test_builder_compiles(analysis_type, tools, prompt, builder, expected) -> None:
    """create_agent 조립이 성공하고 실행 인터페이스(ainvoke)를 갖는다 — LLM 호출 없음."""
    agent = builder()
    assert hasattr(agent, "ainvoke")


# ── general_agent (2-6) — 분석 워커와 별개 검증 ────────────────────────────────


def test_general_tool_assignment() -> None:
    """배정표(HANDOFF §3) — 조회 3종 + calculate + 기준서, 쓰기 0."""
    assert {t.name for t in GENERAL_TOOLS} == {
        "get_sales_timeseries",
        "get_order_events",
        "list_my_products",
        "calculate",
        "search_analysis_guide",
    }
    write_names = {t.name for t in PRODUCT_TOOLS} - {"list_my_products"}
    assert {t.name for t in GENERAL_TOOLS}.isdisjoint(write_names)
    for t in GENERAL_TOOLS:
        for hidden in ("runtime", "brand_id", "seller_id"):
            assert hidden not in t.args


def test_general_prompt_principles() -> None:
    """확정 3원칙 — 해석 금지·calculate 강제·미지원 안내 + today 주입 슬롯."""
    prompt = GENERAL_PROMPT_TEMPLATE.format(today="2026-07-18")
    assert "2026-07-18" in prompt  # today 주입(기간 환산 기준)
    assert "해석 금지" in prompt
    assert "calculate" in prompt
    assert "암산·추정 금지" in prompt
    assert "미지원 안내" in prompt
    assert "지난달" in prompt  # normalize_period 와 동일 정의 문구


def test_build_general_agent_compiles() -> None:
    """today 를 주입한 조립이 성공하고 실행 인터페이스(ainvoke)를 갖는다."""
    agent = build_general_agent(today="2026-07-18")
    assert hasattr(agent, "ainvoke")


# ── product_agent (2-7) — A안: 조회만 바인딩, 쓰기는 구조적으로 차단 ───────────


def test_product_agent_binds_read_only() -> None:
    """A안 + calculate(2-9 리뷰 반영) — 조회·계산만, 쓰기 3종은 볼 수 없다."""
    assert {t.name for t in PRODUCT_DRAFT_TOOLS} == {"list_my_products", "calculate"}
    write_names = {t.name for t in PRODUCT_TOOLS} - {"list_my_products"}
    assert {t.name for t in PRODUCT_DRAFT_TOOLS}.isdisjoint(write_names)
    for t in PRODUCT_DRAFT_TOOLS:
        for hidden in ("runtime", "brand_id", "seller_id"):
            assert hidden not in t.args


def test_product_prompt_principles() -> None:
    """확정 원칙 — before 조회 강제·모호 시 되묻기·추천 적용 발화 격리·숨김 명시."""
    assert "list_my_products" in PRODUCT_PROMPT  # before 는 조회값에서만
    assert "추측·기억으로 채우지 않는다" in PRODUCT_PROMPT
    assert "clarification" in PRODUCT_PROMPT  # 모호 시 되묻기(임의 선택 금지)
    assert "N번 적용해줘" in PRODUCT_PROMPT  # §6.3 — 이력 조회 경로로 격리
    assert "ON_SALE→HIDDEN" in PRODUCT_PROMPT  # delete = soft delete 가시화
    assert "쓰기 도구는 없다" in PRODUCT_PROMPT


def test_build_product_agent_compiles() -> None:
    """create_agent 조립이 성공하고 실행 인터페이스(ainvoke)를 갖는다."""
    agent = build_product_agent()
    assert hasattr(agent, "ainvoke")


# ── analysis_planner (3-2) — 워커 선택 + 기간 분류 ─────────────────────────────


def test_planner_prompt_covers_all_workers() -> None:
    """워커 5종 전부가 선택 기준으로 설명된다 — 누락 시 해당 분석이 계획에서 실종."""
    for analysis_type in ("sales_anomaly", "conversion", "behavior", "churn", "abuse"):
        assert analysis_type in PLANNER_PROMPT


def test_planner_prompt_period_vocabulary() -> None:
    """기간 정규 어휘 4종 + 날짜 산수 금지(장치 ④) + 미지원 표현 되묻기(3-1 확정)."""
    assert "지난달" in PLANNER_PROMPT
    assert "최근 N일" in PLANNER_PROMPT
    assert "어제" in PLANNER_PROMPT
    assert "YYYY-MM-DD~YYYY-MM-DD" in PLANNER_PROMPT
    assert "날짜를 직접 계산·추론하지 않는다" in PLANNER_PROMPT
    assert "이번 달" in PLANNER_PROMPT  # 미지원 예시 명시 — 임의 변환 금지
    assert "clarification" in PLANNER_PROMPT


def test_planner_prompt_clarification_contract() -> None:
    """clarification 시 analyses 를 비운다 — resolve_plan 불성립 신호와 접속."""
    assert "analyses 를 반드시 비운다" in PLANNER_PROMPT


def test_build_analysis_planner_compiles() -> None:
    """도구 없는 create_agent 조립이 성공하고 실행 인터페이스를 갖는다 — LLM 호출 없음."""
    agent = build_analysis_planner()
    assert hasattr(agent, "ainvoke")


# ── 분석 파이프라인 후단 (2-8) — report · judge · recommend ────────────────────


def test_recommend_tool_assignment() -> None:
    """배정표(§3) — 읽기 2종만, 쓰기 0, 신원 은닉."""
    assert {t.name for t in RECOMMEND_TOOLS} == {
        "list_my_products",
        "get_product_change_logs",
    }
    write_names = {t.name for t in PRODUCT_TOOLS} - {"list_my_products"}
    assert {t.name for t in RECOMMEND_TOOLS}.isdisjoint(write_names)
    for t in RECOMMEND_TOOLS:
        for hidden in ("runtime", "brand_id", "seller_id"):
            assert hidden not in t.args


def test_report_prompt_principles() -> None:
    """report — 수치는 finding 만·번복 금지·전 finding 반영·한계 정직(D2·D3 와 짝)."""
    assert "새 수치를 만들거나" in REPORT_PROMPT
    assert "번복하지 않는다" in REPORT_PROMPT
    assert "빠짐없이 반영" in REPORT_PROMPT
    assert "데이터 한계" in REPORT_PROMPT


def test_judge_prompt_principles() -> None:
    """judge — 3축 채점·관대한 채점 금지·미달 축 중심 feedback."""
    for axis in ("accuracy", "completeness", "clarity"):
        assert axis in JUDGE_PROMPT
    assert "관대한 채점 금지" in JUDGE_PROMPT
    assert "feedback" in JUDGE_PROMPT


def test_recommend_prompt_principles() -> None:
    """recommend — product_id 실존 확인·중복 추천 회피·실행 금지·순서=우선순위."""
    assert "list_my_products" in RECOMMEND_PROMPT  # 실존 확인 강제
    assert "존재하지 않는 상품 금지" in RECOMMEND_PROMPT
    assert "중복 추천" in RECOMMEND_PROMPT
    assert "실행하지 않는다" in RECOMMEND_PROMPT
    assert "N번 적용해줘" in RECOMMEND_PROMPT  # §6.3 순서 계약


def test_pipeline_builders_compile() -> None:
    """report·judge·recommend 조립이 성공하고 실행 인터페이스를 갖는다 — LLM 호출 없음."""
    for builder in (build_report_agent, build_report_judge, build_recommend_agent):
        assert hasattr(builder(), "ainvoke")
