"""Anthropic 2-tier LLM 클라이언트 (Haiku 분해 / Sonnet 재랭킹, api-spec §2.9 c).

노드는 LLMClient 프로토콜을 **주입**받는다 — 테스트는 fake, 라이브는 API 키가 있을 때만
AnthropicLLM 을 생성한다(get_llm). 타임아웃·재시도는 config(llm_timeout_s / llm_max_retries).
langchain-anthropic ChatAnthropic 을 지연 import 하여 테스트가 SDK 없이도 돈다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from app.core.config import get_settings


class LLMError(Exception):
    """LLM 호출 실패(오류/타임아웃/미구성). 상위에서 LLM_UNAVAILABLE / LLM_TIMEOUT 로 매핑한다."""


@runtime_checkable
class LLMClient(Protocol):
    """LLM 호출 계약. decompose(complete)·fallback(stream)·rerank(complete)가 소비한다."""

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1024) -> str:
        """단발 완성 텍스트를 반환한다."""
        ...

    def stream(self, *, system: str, user: str, model: str, max_tokens: int = 1024) -> AsyncIterator[str]:
        """토큰 증분을 비동기로 산출한다."""
        ...


def _as_text(content: Any) -> str:
    """langchain 메시지 content(str | 블록 리스트)를 평문으로 정규화한다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content)


class AnthropicLLM:
    """ChatAnthropic 래퍼. (model, max_tokens) 별 인스턴스를 캐시한다."""

    def __init__(self, api_key: str, *, timeout: float, max_retries: int) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._cache: dict[tuple[str, int], Any] = {}

    def _chat(self, model: str, max_tokens: int) -> Any:
        from langchain_anthropic import ChatAnthropic

        key = (model, max_tokens)
        if key not in self._cache:
            self._cache[key] = ChatAnthropic(
                model=model,
                api_key=self._api_key,
                timeout=self._timeout,
                max_retries=self._max_retries,
                max_tokens=max_tokens,
                stop=None,
            )
        return self._cache[key]

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1024) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            resp = await self._chat(model, max_tokens).ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        except Exception as exc:  # noqa: BLE001 - SDK 예외를 LLMError 로 통일 매핑
            raise LLMError(str(exc)) from exc
        return _as_text(resp.content)

    async def stream(self, *, system: str, user: str, model: str, max_tokens: int = 1024) -> AsyncIterator[str]:
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            async for chunk in self._chat(model, max_tokens).astream(
                [SystemMessage(content=system), HumanMessage(content=user)]
            ):
                text = _as_text(chunk.content)
                if text:
                    yield text
        except Exception as exc:  # noqa: BLE001
            raise LLMError(str(exc)) from exc


def get_llm() -> AnthropicLLM | None:
    """설정에 API 키가 있으면 라이브 클라이언트, 없으면 None(호출측이 LLM_UNAVAILABLE 처리).

    키가 없는 개발·CI 에서 네트워크 호출 없이 곧바로 미구성 경로로 빠지게 한다.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    return AnthropicLLM(
        settings.anthropic_api_key,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
