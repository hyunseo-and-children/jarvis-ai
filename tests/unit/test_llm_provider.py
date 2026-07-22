"""LLM provider 토글 + tier 추상화 (이슈 #40).

get_llm() 이 settings.llm_provider 로 Anthropic/OpenAI 를 분기하고, 각 provider 가
tier(fast/smart) → 자기 모델 id 로 매핑하는지 검증한다. 네트워크·SDK 없이 도는
순수 단위 테스트 — ChatAnthropic/ChatOpenAI 는 _chat 에서 지연 import 되므로
resolve/분기 로직만 확인한다.
"""

from __future__ import annotations

import pytest

from app.core import llm as llm_mod
from app.core.config import Settings
from app.core.llm import AnthropicLLM, LLMError, OpenAILLM, get_llm


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


# ─────────── tier 매핑 ───────────


def test_anthropic_resolves_tiers() -> None:
    c = AnthropicLLM(
        "k",
        fast_model="claude-haiku-4-5",
        smart_model="claude-sonnet-5",
        timeout=1.0,
        max_retries=0,
    )
    assert c._resolve("fast") == "claude-haiku-4-5"
    assert c._resolve("smart") == "claude-sonnet-5"


def test_anthropic_unknown_tier_raises() -> None:
    c = AnthropicLLM("k", fast_model="h", smart_model="s", timeout=1.0, max_retries=0)
    with pytest.raises(LLMError):
        c._resolve("turbo")


def test_openai_resolves_tiers() -> None:
    c = OpenAILLM(
        "k",
        fast_model="gpt-5-nano",
        smart_model="gpt-5.6-luna",
        timeout=1.0,
        max_retries=0,
        fast_reasoning_effort="low",
        smart_reasoning_effort="medium",
    )
    assert c._resolve("fast") == ("gpt-5-nano", "low")
    assert c._resolve("smart") == ("gpt-5.6-luna", "medium")


def test_openai_unknown_tier_raises() -> None:
    c = OpenAILLM("k", fast_model="a", smart_model="b", timeout=1.0, max_retries=0)
    with pytest.raises(LLMError):
        c._resolve("turbo")


# ─────────── settings.model_for_tier (관측용 telemetry) ───────────


def test_settings_model_for_tier_anthropic() -> None:
    s = _settings(llm_provider="anthropic")
    assert s.model_for_tier("fast") == s.haiku_model_id
    assert s.model_for_tier("smart") == s.sonnet_model_id


def test_settings_model_for_tier_openai() -> None:
    s = _settings(llm_provider="openai")
    assert s.model_for_tier("fast") == s.openai_fast_model_id
    assert s.model_for_tier("smart") == s.openai_smart_model_id
    assert s.openai_fast_reasoning_effort == "minimal"


def test_settings_model_for_tier_unknown_raises() -> None:
    with pytest.raises(ValueError):
        _settings().model_for_tier("turbo")


# ─────────── get_llm 분기 ───────────


def test_get_llm_anthropic(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_mod, "get_settings", lambda: _settings(llm_provider="anthropic", anthropic_api_key="k")
    )
    assert isinstance(get_llm(), AnthropicLLM)


def test_get_llm_anthropic_no_key(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_mod, "get_settings", lambda: _settings(llm_provider="anthropic", anthropic_api_key="")
    )
    assert get_llm() is None


def test_get_llm_openai(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_mod, "get_settings", lambda: _settings(llm_provider="openai", openai_api_key="k")
    )
    assert isinstance(get_llm(), OpenAILLM)


def test_get_llm_openai_no_key(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_mod, "get_settings", lambda: _settings(llm_provider="openai", openai_api_key="")
    )
    assert get_llm() is None


def test_get_llm_defaults_to_openai(monkeypatch) -> None:
    # llm_provider 미지정 → config.py 기본값(openai)
    monkeypatch.setattr(llm_mod, "get_settings", lambda: _settings(openai_api_key="k"))
    assert isinstance(get_llm(), OpenAILLM)


# ─────────── OpenAI json_output 토글 + 캐시 키 (리뷰 #44 회귀) ───────────


def _openai(**kw) -> OpenAILLM:
    base = dict(
        fast_model="gpt-x",
        smart_model="gpt-x",
        timeout=5.0,
        max_retries=0,
        fast_reasoning_effort="low",
        smart_reasoning_effort="medium",
    )
    base.update(kw)
    return OpenAILLM("sk-test", **base)


def test_openai_json_output_toggles_response_format() -> None:
    """complete(json_output=False) 는 response_format 을 붙이지 않는다 — 마크다운 태스크(consolidate)용."""
    llm = _openai()
    c_json = llm._chat("fast", 100, json_mode=True)
    c_plain = llm._chat("fast", 100, json_mode=False)
    assert c_json.model_kwargs.get("response_format") == {"type": "json_object"}
    assert "response_format" not in c_plain.model_kwargs
    assert c_json is not c_plain  # json 여부가 캐시 키에 반영


def test_openai_cache_key_includes_tier_effort() -> None:
    """fast/smart 가 같은 모델이어도 reasoning_effort 가 tier 별로 유지된다(캐시 키에 tier 포함)."""
    llm = _openai()  # fast/smart 모두 gpt-x, effort 만 low/medium
    c_fast = llm._chat("fast", 100, json_mode=True)
    c_smart = llm._chat("smart", 100, json_mode=True)
    assert c_fast is not c_smart
    assert c_fast.reasoning_effort == "low"
    assert c_smart.reasoning_effort == "medium"
