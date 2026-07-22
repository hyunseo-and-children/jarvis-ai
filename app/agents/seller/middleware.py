"""판매자 가드레일 미들웨어 (SPEC-SELLER-001 §10-⑥ scope→PII→출력 검사, 3-6).

강의자료 06_Middleware(05-Guardrails ContentFilterMiddleware·PIIMiddleware,
04-Prebuilt ToolCallLimitMiddleware) 패턴을 따른다. 3층 구성:

1. scope — 판매자 도메인 밖 요청(타 판매자·고객 개인정보·프롬프트 유출) 차단.
   - 자유 텍스트 에이전트(general): ScopeGuardMiddleware(before_agent → end 점프).
   - 구조화 출력 레인(분석 planner 등): end 점프가 structured_response 계약을 깨므로
     **코드 경로**(orchestrator 가 check_scope 순수 함수 호출)로 차단한다.
2. PII — 입력에 섞인 고객 개인정보(이메일·전화·주민번호)를 모델 전달 전에 제거
   (prebuilt PIIMiddleware, apply_to_input). 리포 로깅 규칙(원문 로그 금지)과 정합.
3. 출력 검사 — 시크릿·주민번호 패턴 마스킹은 mask_output 순수 함수로 제공,
   SSE 계층(3-7~)이 적용한다(astream 은 토큰이 이미 흘러간 뒤라 after_agent 로는
   스트림을 소급 수정할 수 없다 — 적용 지점은 스트림 쓰기 직전).

차단 규칙은 기본 상수 + 생성자 주입(ContentFilterMiddleware 패턴) — 튜너블 숫자가
아닌 규칙 상수라 Settings 가 아닌 본 모듈이 단일 출처다(조정은 여기서).
"""

from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    hook_config,
)

from app.core.config import get_settings

# ── 1. scope — 차단 규칙 (사유 → 트리거 부분 문자열, 소문자 비교) ────────────────

SCOPE_BLOCK_RULES: dict[str, tuple[str, ...]] = {
    "타 판매자·경쟁사 데이터": (
        "다른 판매자",
        "타 판매자",
        "다른 브랜드",
        "타 브랜드",
        "경쟁사",
        "타사 매출",
    ),
    "고객 개인정보": (
        "고객 전화번호",
        "고객 연락처",
        "고객 주소",
        "고객 이메일",
        "주민등록번호",
        "고객 명단",
        "고객 개인정보",
    ),
    "시스템 내부 정보": (
        "시스템 프롬프트",
        "system prompt",
        "프롬프트 원문",
        "프롬프트를 보여",
        "내부 토큰",
        "api 키",
        "api key",
    ),
}

SCOPE_REFUSAL = (
    "죄송합니다. 판매자님 브랜드의 데이터 분석·상품 관리·일반 조회만 도와드릴 수 "
    "있습니다. 다른 판매자 정보, 고객 개인정보, 시스템 내부 정보는 제공할 수 없습니다."
)


def check_scope(text: str, rules: dict[str, tuple[str, ...]] | None = None) -> str | None:
    """도메인 밖 요청이면 거절 문안을, 정상이면 None 을 반환한다 (순수 함수).

    구조화 출력 레인(orchestrator)·미들웨어(ScopeGuardMiddleware)가 공유하는
    단일 판정점 — 규칙 변경은 SCOPE_BLOCK_RULES 만 고치면 두 경로에 함께 적용된다.
    """
    lowered = text.lower()
    for _reason, triggers in (rules or SCOPE_BLOCK_RULES).items():
        if any(trigger in lowered for trigger in triggers):
            return SCOPE_REFUSAL
    return None


class ScopeGuardMiddleware(AgentMiddleware):
    """자유 텍스트 에이전트용 scope 가드 (05-Guardrails ContentFilterMiddleware 패턴).

    마지막 사용자 메시지가 도메인 밖이면 LLM 을 호출하지 않고 거절 메시지로
    즉시 종료한다(end 점프) — 프롬프트가 아니라 구조로 차단(비용·유출 방지).
    구조화 출력 에이전트에는 붙이지 않는다(모듈 docstring 참조).
    """

    def __init__(self, rules: dict[str, tuple[str, ...]] | None = None) -> None:
        super().__init__()
        self._rules = rules or SCOPE_BLOCK_RULES

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        """에이전트 실행 전 마지막 human 메시지를 검사한다."""
        for message in reversed(state.get("messages", [])):
            if getattr(message, "type", None) != "human":
                continue
            refusal = check_scope(str(message.content), self._rules)
            if refusal:
                return {
                    "messages": [{"role": "assistant", "content": refusal}],
                    "jump_to": "end",
                }
            return None
        return None


# ── 2. PII — 입력 정제 (prebuilt PIIMiddleware, 커스텀 detector 패턴) ───────────

# 한국 휴대폰(하이픈 유무)·주민등록번호 — 이메일은 내장 탐지기 사용.
KR_PHONE_PATTERN = r"01[016789]-?\d{3,4}-?\d{4}"
KR_RRN_PATTERN = r"\d{6}-[1-4]\d{6}"


def seller_pii_middlewares() -> list[PIIMiddleware]:
    """입력 PII 정제 3종 — 모델·로그에 고객 개인정보가 흘러가지 않게 한다."""
    return [
        PIIMiddleware("email", strategy="redact", apply_to_input=True),
        PIIMiddleware(
            "kr_phone", detector=KR_PHONE_PATTERN, strategy="redact", apply_to_input=True
        ),
        PIIMiddleware("kr_rrn", detector=KR_RRN_PATTERN, strategy="redact", apply_to_input=True),
    ]


# ── 3. 출력 검사 — 시크릿 마스킹 (SSE 쓰기 직전에 적용, 순수 함수) ──────────────

MASK_REPLACEMENT = "[민감 정보 차단]"

_OUTPUT_SECRET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9\-_]{16,}"),  # API 키 형태
    re.compile(r"Bearer\s+[A-Za-z0-9\-_.]{16,}"),  # 인증 헤더 토큰
    re.compile(KR_RRN_PATTERN),  # 주민등록번호
)


def mask_output(text: str) -> str:
    """최종 출력에서 시크릿·주민번호 패턴을 마스킹한다 (SSE 계층이 호출)."""
    for pattern in _OUTPUT_SECRET_RES:
        text = pattern.sub(MASK_REPLACEMENT, text)
    return text


# ── ToolCallLimit (prebuilt) — 도구 보유 에이전트 공통 상한 ─────────────────────


def tool_call_limit_middleware() -> ToolCallLimitMiddleware:
    """전역 도구 호출 상한 — Settings(seller_tool_call_limit) 단일 출처."""
    return ToolCallLimitMiddleware(run_limit=get_settings().seller_tool_call_limit)
