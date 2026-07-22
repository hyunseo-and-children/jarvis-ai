"""현재 프로세스 환경의 OpenAI 키와 fast/smart 모델 호출 가능 여부를 검사한다.

이 스크립트는 ``.env`` 파일이나 애플리케이션 Settings를 로드하지 않는다.
실행 전에 호출할 셸에 세 환경변수를 직접 설정해야 한다.

    OPENAI_API_KEY=... \
    OPENAI_FAST_MODEL_ID=... \
    OPENAI_SMART_MODEL_ID=... \
    OPENAI_FAST_REASONING_EFFORT=minimal \
    OPENAI_SMART_REASONING_EFFORT=medium \
    uv run python scripts/check_openai_env.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

REQUIRED_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_FAST_MODEL_ID",
    "OPENAI_SMART_MODEL_ID",
)

PROBE_MAX_TOKENS = 256


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _required_environment() -> dict[str, str]:
    """필수 값을 현재 프로세스 환경에서만 읽는다."""
    values = {name: os.environ.get(name, "").strip() for name in REQUIRED_ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        names = ", ".join(missing)
        raise ValueError(f"필수 환경변수가 없습니다: {names}")
    return values


def _safe_error(exc: Exception, api_key: str) -> str:
    """예외 문자열에 키가 포함되더라도 콘솔에 노출하지 않는다."""
    return str(exc).replace(api_key, "<redacted>")


async def _check_api_key(api_key: str) -> CheckResult:
    """모델 목록 API로 키 인증 자체를 확인한다."""
    client = AsyncOpenAI(api_key=api_key, timeout=30.0, max_retries=0)
    try:
        await client.models.list()
    except Exception as exc:  # noqa: BLE001 - 진단 스크립트는 SDK 오류를 결과로 보고한다.
        return CheckResult("OPENAI_API_KEY", False, _safe_error(exc, api_key))
    finally:
        await client.close()
    return CheckResult("OPENAI_API_KEY", True, "인증 성공")


async def _check_model(
    *, api_key: str, variable_name: str, model: str, reasoning_effort: str
) -> CheckResult:
    """운영 코드와 같은 ChatOpenAI 호출 방식으로 지정 모델을 확인한다."""
    chat = ChatOpenAI(
        model=model,
        api_key=api_key,
        timeout=60.0,
        max_retries=0,
        # 이 상한은 visible output과 hidden reasoning이 공유한다. 64는 nano/low에서
        # reasoning만으로 소진될 수 있어 모델 가용성을 잘못 판정한다.
        max_tokens=PROBE_MAX_TOKENS,
        reasoning_effort=reasoning_effort,
    )
    try:
        response = await chat.ainvoke(
            [
                SystemMessage(content="You are a connectivity check. Follow the user exactly."),
                HumanMessage(content="Reply with exactly OK."),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - 진단 스크립트는 SDK 오류를 결과로 보고한다.
        return CheckResult(variable_name, False, _safe_error(exc, api_key))

    content = response.content
    has_content = isinstance(content, str) and bool(content.strip())
    if not has_content:
        return CheckResult(
            variable_name,
            False,
            f"모델 {model!r} 호출은 완료됐지만 응답 본문이 비어 있음 "
            f"(reasoning_effort={reasoning_effort!r}, max_tokens={PROBE_MAX_TOKENS})",
        )
    return CheckResult(variable_name, True, f"모델 {model!r} 호출 성공")


async def _run() -> int:
    try:
        env = _required_environment()
    except ValueError as exc:
        print(f"[FAIL] 환경 확인: {exc}", file=sys.stderr)
        print(".env는 자동으로 읽지 않습니다.", file=sys.stderr)
        return 2

    api_key = env["OPENAI_API_KEY"]
    fast_reasoning_effort = os.environ.get("OPENAI_FAST_REASONING_EFFORT", "minimal").strip()
    smart_reasoning_effort = os.environ.get("OPENAI_SMART_REASONING_EFFORT", "medium").strip()
    # 모델 목록 권한이 제한된 키도 추론 권한은 있을 수 있으므로 모델 호출은 항상 시도한다.
    results = [
        await _check_api_key(api_key),
        await _check_model(
            api_key=api_key,
            variable_name="OPENAI_FAST_MODEL_ID",
            model=env["OPENAI_FAST_MODEL_ID"],
            reasoning_effort=fast_reasoning_effort,
        ),
        await _check_model(
            api_key=api_key,
            variable_name="OPENAI_SMART_MODEL_ID",
            model=env["OPENAI_SMART_MODEL_ID"],
            reasoning_effort=smart_reasoning_effort,
        ),
    ]

    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")

    return 0 if all(result.ok for result in results) else 1


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
