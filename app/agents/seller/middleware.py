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
from app.core.text import _security_skeleton, _strip_unsafe_multiline_controls
from app.core.unicode_security import UnicodeSequenceStreamSanitizer

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
    """은닉 문자를 무시해 탐지한 시크릿·주민번호의 원문 범위를 마스킹한다."""
    skeleton = _security_skeleton(text)
    spans = sorted(
        skeleton.source_span(*match.span())
        for pattern in _OUTPUT_SECRET_RES
        for match in pattern.finditer(skeleton.text)
    )
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    for start, end in reversed(merged):
        text = text[:start] + MASK_REPLACEMENT + text[end:]
    return text


_API_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_BEARER_TOKEN_CHARS = _API_TOKEN_CHARS | {"."}
_MIN_SECRET_TOKEN_LENGTH = 16
_MAX_SENSITIVE_PREFIX = max(
    len("sk-") + _MIN_SECRET_TOKEN_LENGTH - 1,
    len("Bearer ") + _MIN_SECRET_TOKEN_LENGTH - 1,
    len("000000-1000000") - 1,
)


def _is_api_key_prefix(text: str) -> bool:
    if "sk-".startswith(text):
        return True
    token = text[3:] if text.startswith("sk-") else ""
    return (
        bool(token)
        and len(token) < _MIN_SECRET_TOKEN_LENGTH
        and all(char in _API_TOKEN_CHARS for char in token)
    )


def _is_bearer_prefix(text: str) -> bool:
    if "Bearer".startswith(text):
        return True
    if not text.startswith("Bearer"):
        return False
    rest = text[len("Bearer") :]
    whitespace_length = len(rest) - len(rest.lstrip())
    if whitespace_length == 0:
        return False
    token = rest[whitespace_length:]
    return len(token) < _MIN_SECRET_TOKEN_LENGTH and all(
        char in _BEARER_TOKEN_CHARS for char in token
    )


def _is_rrn_prefix(text: str) -> bool:
    if len(text) > 14:
        return False
    for index, char in enumerate(text):
        if index < 6 or 8 <= index:
            if not char.isascii() or not char.isdigit():
                return False
        elif index == 6:
            if char != "-":
                return False
        elif char not in "1234":
            return False
    return True


def _sensitive_suffix_start(text: str) -> int | None:
    minimum_start = max(0, len(text) - _MAX_SENSITIVE_PREFIX)
    for start in range(minimum_start, len(text)):
        suffix = text[start:]
        if _is_api_key_prefix(suffix) or _is_bearer_prefix(suffix) or _is_rrn_prefix(suffix):
            return start
    return None


def _overlong_bearer_prefix_start(text: str) -> int | None:
    """whitespace가 보류 상한을 소진한 Bearer 후보를 fail-closed로 찾는다."""
    start = text.rfind("Bearer")
    if start < 0 or len(text) - start <= _MAX_SENSITIVE_PREFIX:
        return None
    rest = text[start + len("Bearer") :]
    whitespace_length = len(rest) - len(rest.lstrip())
    if whitespace_length == 0:
        return None
    token = rest[whitespace_length:]
    if len(token) >= _MIN_SECRET_TOKEN_LENGTH:
        return None
    return start if all(char in _BEARER_TOKEN_CHARS for char in token) else None


def _earliest_secret_match(text: str) -> tuple[int, re.Match[str]] | None:
    matches = (
        (index, match)
        for index, pattern in enumerate(_OUTPUT_SECRET_RES)
        if (match := pattern.search(text)) is not None
    )
    return min(matches, key=lambda item: item[1].start(), default=None)


class StreamingOutputGuard:
    """seller general 출력의 Unicode 문맥과 청크 경계 시크릿을 함께 보호한다."""

    def __init__(self) -> None:
        self._unicode = UnicodeSequenceStreamSanitizer()
        self._pending = ""
        self._previous_ended_space = False
        self._consume_rule: int | None = None

    def feed(self, text: str) -> list[str]:
        """새 청크를 정제하고 확정된 안전 출력 조각을 반환한다."""
        self._append_cleaned(self._unicode.feed(text))
        return self._drain(final=False)

    def flush(self) -> list[str]:
        """스트림 종료 시 보류한 정상 문자를 확정하고 반환한다."""
        self._append_cleaned(self._unicode.flush())
        return self._drain(final=True)

    def _append_cleaned(self, text: str) -> None:
        if not text:
            return
        framed = _strip_unsafe_multiline_controls(f"\ue000{text}\ue001")
        cleaned = framed[1:-1]
        if self._previous_ended_space and cleaned.startswith(" "):
            cleaned = cleaned[1:]
        if not cleaned:
            return
        self._previous_ended_space = cleaned.endswith(" ")
        self._pending += cleaned

    def _drain(self, *, final: bool) -> list[str]:
        fragments: list[str] = []
        while self._pending:
            if self._consume_rule is not None:
                if self._consume_token_continuation():
                    break

            skeleton = _security_skeleton(self._pending)
            secret_match = _earliest_secret_match(skeleton.text)
            if secret_match is not None:
                rule_index, match = secret_match
                start, end = skeleton.source_span(*match.span())
                fragments.extend((self._pending[:start], MASK_REPLACEMENT))
                self._pending = self._pending[end:]
                if match.end() == len(skeleton.text):
                    self._consume_rule = rule_index
                continue

            overlong_start = _overlong_bearer_prefix_start(skeleton.text)
            if overlong_start is not None:
                source_start = skeleton.source_starts[overlong_start]
                fragments.extend((self._pending[:source_start], MASK_REPLACEMENT))
                self._pending = ""
                self._consume_rule = 1
                break

            if final:
                fragments.append(self._pending)
                self._pending = ""
                break

            suffix_start = _sensitive_suffix_start(skeleton.text)
            if suffix_start is None:
                fragments.append(self._pending)
                self._pending = ""
            else:
                source_start = skeleton.source_starts[suffix_start]
                fragments.append(self._pending[:source_start])
                self._pending = self._pending[source_start:]
            break

        visible = mask_output("".join(fragments))
        return [visible] if visible else []

    def _consume_token_continuation(self) -> bool:
        skeleton = _security_skeleton(self._pending)
        allowed = (
            _API_TOKEN_CHARS
            if self._consume_rule == 0
            else _BEARER_TOKEN_CHARS
            if self._consume_rule == 1
            else frozenset()
        )
        delimiter_index = next(
            (index for index, char in enumerate(skeleton.text) if char not in allowed),
            None,
        )
        if delimiter_index is None:
            self._pending = ""
            return True
        source_start = skeleton.source_starts[delimiter_index]
        self._pending = self._pending[source_start:]
        self._consume_rule = None
        return False


# ── ToolCallLimit (prebuilt) — 도구 보유 에이전트 공통 상한 ─────────────────────


def tool_call_limit_middleware() -> ToolCallLimitMiddleware:
    """전역 도구 호출 상한 — Settings(seller_tool_call_limit) 단일 출처."""
    return ToolCallLimitMiddleware(run_limit=get_settings().seller_tool_call_limit)
