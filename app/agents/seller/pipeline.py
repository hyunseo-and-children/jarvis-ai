"""판매자 분석 파이프라인 입출력 계약 (SPEC-SELLER-001 §2, 3-1 — 2026-07-18 사용자 확정).

이 모듈은 파이프라인의 **계약 상수·순수 함수만** 둔다(LLM·IO 없음, verifier 와 동일 원칙):
- ResolvedPlan: AnalysisPlan(LLM 출력)의 기간을 코드가 환산한 내부 실행 계획.
- resolve_plan: AnalysisPlan → ResolvedPlan. 모든 불성립(clarification·빈 워커·환산
  실패)은 ValueError 로 통일 — 호출부(3-3)는 이를 받아 되묻기 token 으로 전환한다.
- format_worker_input: 워커 입력 메시지 포맷(기간 주입 규약, 장치 ④ 접속점).
- PROGRESS_TOKENS·WORKER_PROGRESS_TOKENS·ALL_WORKERS_FAILED_TOKEN: 진행 token 문구.

오케스트레이션(asyncio.gather 팬아웃·검증 루프·SSE 배선)은 3-3 이후 소관.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import get_args

from app.agents.seller import calc
from app.agents.seller.schemas import (
    AnalysisFinding,
    AnalysisPlan,
    AnalysisType,
    RecommendationSet,
)


@dataclass(frozen=True)
class ResolvedPlan:
    """기간이 코드로 환산된 실행 계획 — LLM 비노출 내부 계약.

    analyses 는 tuple(불변) — AnalysisPlan validator 가 중복을 제거했고
    순서는 팬아웃에서 무의미하지만 진행 token 방출 순서로는 쓰인다.
    """

    analyses: tuple[AnalysisType, ...]
    date_from: date
    date_to: date


def resolve_plan(plan: AnalysisPlan, *, today: date, recent_default_days: int) -> ResolvedPlan:
    """AnalysisPlan(LLM) → ResolvedPlan(코드) — 불성립은 전부 ValueError.

    - plan.clarification 이 있으면 계획 불성립: 되묻기 질문을 그대로 ValueError
      메시지로 올린다(호출부가 token 으로 전달).
    - analyses 가 비면 planner 오류로 간주하고 되묻기 처리.
    - 기간 환산은 calc.normalize_period 소관 — 미지원 표현("이번 달" 등,
      2026-07-18 확정)의 ValueError 도 그대로 전파된다.
    """
    if plan.clarification:
        raise ValueError(plan.clarification)
    if not plan.analyses:
        raise ValueError(
            "어떤 분석을 원하시는지 파악하지 못했습니다. 조금 더 구체적으로 알려주세요."
        )
    date_from, date_to = calc.normalize_period(
        plan.period_expr, today=today, recent_default_days=recent_default_days
    )
    return ResolvedPlan(analyses=tuple(plan.analyses), date_from=date_from, date_to=date_to)


# ── 워커 입력 메시지 포맷 (기간 주입 규약 — prompts.WORKER_COMMON_RULES 접속점) ──

# 워커 프롬프트의 "기간은 입력에 주어진 from/to 를 그대로" 계약이 참조하는 포맷.
# 단일 기간만 주입한다(R3 두 기간 비교는 보류 — REVIEW-SELLER-STAGE2 §2).
WORKER_INPUT_TEMPLATE = """\
[분석 기간] from={date_from} to={date_to}
[판매자 질문] {question}"""


def format_worker_input(question: str, plan: ResolvedPlan) -> str:
    """워커에 넣을 HumanMessage 본문을 만든다 — 전 워커 공통 1건."""
    return WORKER_INPUT_TEMPLATE.format(
        date_from=plan.date_from.isoformat(),
        date_to=plan.date_to.isoformat(),
        question=question,
    )


# ── report·judge 입력 포맷 (3-4 검증 루프 — REPORT/JUDGE_PROMPT 의 "입력" 계약) ──


def format_findings_block(findings: list[AnalysisFinding]) -> str:
    """AnalysisFinding 목록을 report/judge 공용 번호 블록으로 직렬화한다.

    번호·유형·심각도·요약·근거를 사람이 읽는 형태로 — JSON 덤프보다 Sonnet 서술
    품질이 안정적이고, judge 의 축별 대조(수치·누락)도 같은 표현을 본다.
    """
    lines: list[str] = []
    for i, finding in enumerate(findings, start=1):
        lines.append(
            f"{i}. [{finding.analysis_type}] (severity={finding.severity}) {finding.summary}"
        )
        for item in finding.evidence:
            lines.append(f"   - 근거: {item}")
        if finding.recommendation:
            lines.append(f"   - 조치 힌트: {finding.recommendation}")
    return "\n".join(lines)


def format_report_input(findings: list[AnalysisFinding]) -> str:
    """report_agent 1차 작성 입력."""
    return f"[분석 결과]\n{format_findings_block(findings)}"


def format_rewrite_input(findings: list[AnalysisFinding], prev_report: str, feedback: str) -> str:
    """report_agent 재작성 입력 — 결정론 실패 사유 + judge feedback 합산본을 주입한다."""
    return (
        f"[분석 결과]\n{format_findings_block(findings)}\n\n"
        f"[이전 보고서]\n{prev_report}\n\n"
        f"[개선 지시]\n{feedback}\n\n"
        "위 개선 지시를 반영해 보고서를 처음부터 다시 작성하라."
    )


def format_judge_input(findings: list[AnalysisFinding], report: str) -> str:
    """judge 채점 입력 — (1) 분석 결과 (2) 보고서 (JUDGE_PROMPT 입력 계약)."""
    return f"[분석 결과]\n{format_findings_block(findings)}\n\n[보고서]\n{report}"


def format_recommend_input(findings: list[AnalysisFinding], report: str) -> str:
    """recommend 입력 — (1) 분석 결과 (2) 검증된 보고서 (RECOMMEND_PROMPT 입력 계약)."""
    return f"[분석 결과]\n{format_findings_block(findings)}\n\n[검증된 보고서]\n{report}"


# ── compose_response (3-5) — 최종 응답 조립 (순수 함수, SPEC §2 COMP) ──────────

# "N번 적용해줘" 안내 — §6.3 조회 계약(목록 순서=N번)의 사용자측 표면.
_APPLY_GUIDE = '적용을 원하시면 "N번 적용해줘"라고 말씀해 주세요.'


def compose_response(report: str, recommendations: RecommendationSet) -> str:
    """검증된 보고서 + 추천을 최종 응답 텍스트로 조립한다.

    번호("1번.")는 RecommendationSet 목록 순서 그대로 — §6.3 이 recommendations[N-1]
    로 조회하는 그 순서다(순서가 계약). 추천이 비면 보고서만(사유 summary 가 있으면
    한 줄 덧붙임) — 억지 추천 금지(RECOMMEND_PROMPT)와 짝.
    """
    items = recommendations.recommendations
    if not items:
        if recommendations.summary:
            return f"{report}\n\n[추천 행동]\n{recommendations.summary}"
        return report

    lines = [report, "", "[추천 행동]"]
    if recommendations.summary:
        lines.append(recommendations.summary)
    for i, rec in enumerate(items, start=1):
        lines.append(f"{i}번. {rec.title}")
        if rec.expected_effect:
            lines.append(f"   기대 효과: {rec.expected_effect}")
    lines.append("")
    lines.append(_APPLY_GUIDE)
    return "\n".join(lines)


# ── 진행 token 문구 (SPEC §2 — first-token 10s·체감 대기 완화, 2026-07-18 확정) ──

# 파이프라인 단계 진입 시 방출 — 키는 단계 식별자(오케스트레이션 3-3~3-5 가 소비).
PROGRESS_TOKENS: dict[str, str] = {
    "planner": "질문을 분석하고 있습니다…",
    "report": "보고서를 작성하고 있습니다…",
    "verify": "보고서를 검증하고 있습니다…",
    "recommend": "개선 방안을 정리하고 있습니다…",
}

# 워커 시작 시 유형별 방출 — AnalysisType 전 값을 커버해야 한다(테스트 강제).
WORKER_PROGRESS_TOKENS: dict[AnalysisType, str] = {
    "sales_anomaly": "매출 이상 분석 중…",
    "conversion": "구매전환 분석 중…",
    "behavior": "고객 행동 분석 중…",
    "churn": "고객 이탈 분석 중…",
    "abuse": "어뷰징 점검 중…",
}

# 전 워커 실패(집계 전부 실패) 시 사과 후 done 종료(SPEC §4·§7 degrade).
ALL_WORKERS_FAILED_TOKEN = (
    "죄송합니다. 지금 데이터 조회가 원활하지 않아 분석을 완료하지 못했습니다. "
    "잠시 후 다시 시도해 주세요."
)


def _assert_worker_tokens_cover_all_types() -> None:
    """모듈 로드 시 자기검증 — AnalysisType 추가 시 token 누락을 즉시 드러낸다."""
    missing = set(get_args(AnalysisType)) - set(WORKER_PROGRESS_TOKENS)
    if missing:
        raise RuntimeError(f"WORKER_PROGRESS_TOKENS 누락: {sorted(missing)}")


_assert_worker_tokens_cover_all_types()


# confirm 판정은 요청 스키마로 이관됐다 (2026-07-22, FE 계약 A-2): 승인은 message 가
# 아니라 최상위 `action`/`draftId` 구조화 필드로 받는다(app/schemas/seller.py
# SellerChatRequest). 입구 판정은 app/api/seller.py `_seller_stream` 이 request.action
# 으로 직접 수행한다 — 구 parse_confirm_message(message JSON 파싱)는 제거됐다.


# ── "N번 적용해줘" 코드 선판정 (4-3 §6.3 — 입구 ①.5, 2026-07-20 사용자 확정) ─────

# 문장 **전체**가 적용 발화일 때만 매칭 — "2번 상품에 할인 적용해줘" 같은 일반 수정
# 요청(여분 토큰 존재)은 통과시켜 supervisor 라우팅으로 흘린다(오매칭 방지).
# _APPLY_GUIDE("N번 적용해줘")가 안내하는 정형 발화 + 가벼운 변형(추천/조사/존대)만.
_APPLY_RE = re.compile(
    r"^\s*(\d{1,3})\s*번(?:\s*추천)?(?:\s*[을를])?\s*적용"
    r"\s*(?:해\s*(?:줘|주세요|줘요)?|부탁해요?|하기)?\s*[.!?~]*\s*$"
)


def parse_apply_message(message: str) -> int | None:
    """메시지가 정형 추천 적용 발화("N번 적용해줘")면 N(1 이상)을, 아니면 None.

    코드 선판정 이유(confirm 과 동일 철학): N 추출은 어차피 코드 몫이고(§6.3 —
    대화 재해석 금지), LLM 라우팅은 정확도를 더해주지 않는다. 판정 후 실제 조회·
    변환은 history.apply_recommendation 이 수행한다.
    """
    match = _APPLY_RE.match(message)
    if not match:
        return None
    n = int(match.group(1))
    return n if n >= 1 else None
