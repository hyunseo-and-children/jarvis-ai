"""AI 내부 inactivity timeout과 공통 session finalizer (이슈 #79)."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.agents.profile import finalizer, idle_timeout, processed_events, session_activity
from app.agents.profile.store import get_profile_store
from app.agents.profile.session_activity import ActivityClaim
from app.core.config import get_settings
from app.core.conversation import conversation_key
from app.core.llm import LLMError
from app.core.stream import get_registry


class _LLM:
    async def complete(self, *, system, user, tier, max_tokens=1024, json_output=True):
        if "델타 추출기" in system:
            return json.dumps(
                {
                    "deltas": [
                        {
                            "fact": "무선이어폰 선호",
                            "salience": 0.9,
                            "explicit": True,
                            "repetitionEma": 0.0,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return "# 취향\n- 무선이어폰 선호"

    async def stream(self, **kwargs):
        yield "x"


async def _expired_claim(monkeypatch: pytest.MonkeyPatch, user_id: int, session_id: str):
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    await session_activity.touch_session(user_id, session_id)
    now = 600.0
    claims = await session_activity.claim_expired_sessions(
        idle_timeout_s=600,
        lease_s=900,
        batch_size=1,
    )
    assert len(claims) == 1
    return claims[0]


async def test_idle_sweep_processes_expired_buffer_without_http_self_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finalizer, "get_llm", lambda: _LLM())
    settings = get_settings()
    monkeypatch.setattr(settings, "profile_session_idle_timeout_s", 600.0)
    monkeypatch.setattr(settings, "profile_idle_claim_ttl_s", 900.0)
    monkeypatch.setattr(settings, "profile_idle_sweep_batch_size", 10)
    monkeypatch.setattr(settings, "profile_idle_max_concurrency", 2)
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)

    store = await get_profile_store()
    key = conversation_key("71", "idle")
    await store.append_session_ctx(key, "무선이어폰 추천해줘")
    await session_activity.touch_session(71, "idle")
    now = 600.0

    result = await idle_timeout.run_idle_sweep()

    assert result.claimed == 1 and result.accepted == 1
    assert await store.get_session_ctx(key) == []
    assert await store.get_summary("71") is not None
    row = await session_activity.get_session(71, "idle")
    assert row is not None and row.status == "completed"


async def test_idle_sweep_skips_active_stream_and_releases_activity_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "profile_session_idle_timeout_s", 600.0)
    monkeypatch.setattr(settings, "profile_idle_claim_ttl_s", 900.0)
    monkeypatch.setattr(settings, "profile_idle_sweep_batch_size", 10)
    monkeypatch.setattr(settings, "profile_idle_max_concurrency", 2)
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    await session_activity.touch_session(72, "active")
    now = 600.0

    key = conversation_key("72", "active")
    assert get_registry().acquire(key)
    try:
        result = await idle_timeout.run_idle_sweep()
    finally:
        get_registry().release(key)

    assert result.claimed == 1 and result.skipped == 1
    row = await session_activity.get_session(72, "active")
    assert row is not None and row.status == "active"


async def test_idle_failure_preserves_buffer_and_returns_activity_to_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingLLM(_LLM):
        async def complete(self, **kwargs):
            raise LLMError("temporary")

    monkeypatch.setattr(finalizer, "get_llm", lambda: _FailingLLM())
    settings = get_settings()
    monkeypatch.setattr(settings, "profile_session_idle_timeout_s", 600.0)
    monkeypatch.setattr(settings, "profile_idle_claim_ttl_s", 900.0)
    monkeypatch.setattr(settings, "profile_idle_sweep_batch_size", 10)
    monkeypatch.setattr(settings, "profile_idle_max_concurrency", 2)
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    store = await get_profile_store()
    key = conversation_key("73", "retry")
    await store.append_session_ctx(key, "파란색 좋아해")
    await session_activity.touch_session(73, "retry")
    now = 600.0

    result = await idle_timeout.run_idle_sweep()

    assert result.retryable == 1
    assert await store.get_session_ctx(key) == ["파란색 좋아해"]
    row = await session_activity.get_session(73, "retry")
    assert row is not None and row.status == "active"
    assert await processed_events.get_status("session-end:73:retry") is None


async def test_internal_timeout_and_explicit_end_share_one_idempotent_finalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finalizer, "get_llm", lambda: _LLM())
    claim = await _expired_claim(monkeypatch, 74, "race")
    store = await get_profile_store()
    key = conversation_key("74", "race")
    await store.append_session_ctx(key, "무선이어폰 좋아해")

    explicit, timeout = await asyncio.gather(
        finalizer.finalize_profile_session("74", "race"),
        finalizer.finalize_profile_session(
            "74",
            "race",
            activity_claim=claim,
            terminal=False,
        ),
    )

    assert {explicit.status, timeout.status} == {
        finalizer.FinalizationStatus.ACCEPTED,
        finalizer.FinalizationStatus.DUPLICATE,
    }
    assert await store.get_session_ctx(key) == []
    assert await processed_events.get_status("session-end:74:race") == "completed"
    row = await session_activity.get_session(74, "race")
    assert row is not None and row.status == "completed"


async def test_idle_checkpoint_allows_same_session_to_resume_and_flush_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CountingLLM(_LLM):
        delta_calls = 0

        async def complete(self, **kwargs):
            if "델타 추출기" in kwargs["system"]:
                self.delta_calls += 1
            return await super().complete(**kwargs)

    llm = _CountingLLM()
    monkeypatch.setattr(finalizer, "get_llm", lambda: llm)
    settings = get_settings()
    monkeypatch.setattr(settings, "profile_session_idle_timeout_s", 600.0)
    monkeypatch.setattr(settings, "profile_idle_claim_ttl_s", 900.0)
    monkeypatch.setattr(settings, "profile_idle_sweep_batch_size", 10)
    monkeypatch.setattr(settings, "profile_idle_max_concurrency", 2)
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    store = await get_profile_store()
    key = conversation_key("75", "resume")
    await store.append_session_ctx(key, "첫 번째 취향")
    await session_activity.touch_session(75, "resume")

    now = 600.0
    first = await idle_timeout.run_idle_sweep()
    assert first.accepted == 1
    assert await processed_events.get_status("session-end:75:resume") is None

    now = 601.0
    assert await session_activity.touch_session(75, "resume")
    await store.append_session_ctx(key, "복귀 후 두 번째 취향")
    now = 1201.0
    second = await idle_timeout.run_idle_sweep()

    assert second.accepted == 1
    assert llm.delta_calls == 2
    assert await store.get_session_ctx(key) == []


async def test_terminal_end_then_stale_same_session_activity_can_checkpoint_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _LLM()
    monkeypatch.setattr(finalizer, "get_llm", lambda: llm)
    settings = get_settings()
    monkeypatch.setattr(settings, "profile_session_idle_timeout_s", 600.0)
    monkeypatch.setattr(settings, "profile_idle_claim_ttl_s", 900.0)
    monkeypatch.setattr(settings, "profile_idle_sweep_batch_size", 10)
    monkeypatch.setattr(settings, "profile_idle_max_concurrency", 2)
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    store = await get_profile_store()
    key = conversation_key("77", "stale-after-end")
    await store.append_session_ctx(key, "종료 전 발화")
    await session_activity.touch_session(77, "stale-after-end")

    ended = await finalizer.finalize_profile_session(77, "stale-after-end")
    assert ended.status is finalizer.FinalizationStatus.ACCEPTED
    assert await processed_events.get_status("session-end:77:stale-after-end") == "completed"

    now = 1.0
    assert await session_activity.touch_session(77, "stale-after-end")
    assert await processed_events.get_status("session-end:77:stale-after-end") is None
    await store.append_session_ctx(key, "종료 직후 stale ticket 발화")
    now = 601.0

    checkpoint = await idle_timeout.run_idle_sweep()

    assert checkpoint.accepted == 1
    assert await store.get_session_ctx(key) == []


async def test_new_activity_during_terminal_finalizer_invalidates_terminal_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delta_started = asyncio.Event()
    allow_delta = asyncio.Event()

    class _BlockingLLM(_LLM):
        async def complete(self, **kwargs):
            if "델타 추출기" in kwargs["system"]:
                delta_started.set()
                await allow_delta.wait()
            return await super().complete(**kwargs)

    monkeypatch.setattr(finalizer, "get_llm", lambda: _BlockingLLM())
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    store = await get_profile_store()
    key = conversation_key("78", "terminal-race")
    await store.append_session_ctx(key, "종료가 처리할 이전 발화")
    await session_activity.touch_session(78, "terminal-race")
    task = asyncio.create_task(finalizer.finalize_profile_session(78, "terminal-race"))
    await delta_started.wait()

    now = 1.0
    await session_activity.touch_session(78, "terminal-race")
    await store.append_session_ctx(key, "종료 처리 중 들어온 새 발화")
    allow_delta.set()
    result = await task

    assert result.status is finalizer.FinalizationStatus.RETRYABLE
    assert await processed_events.get_status("session-end:78:terminal-race") is None
    row = await session_activity.get_session(78, "terminal-race")
    assert row is not None and row.status == "active"
    assert await store.get_session_ctx(key) == ["종료 처리 중 들어온 새 발화"]


async def test_idle_finalizer_does_not_reserve_live_stream_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = await _expired_claim(monkeypatch, 76, "stream-race")
    finalizer_started = asyncio.Event()
    allow_finalizer = asyncio.Event()

    async def _blocking_finalizer(*args, **kwargs):
        finalizer_started.set()
        await allow_finalizer.wait()
        return finalizer.FinalizationResult(finalizer.FinalizationStatus.ACCEPTED)

    monkeypatch.setattr(idle_timeout, "finalize_profile_session", _blocking_finalizer)
    task = asyncio.create_task(idle_timeout._process_claim(claim, idle_timeout_s=600))
    await finalizer_started.wait()
    stream_key = conversation_key("76", "stream-race")

    assert get_registry().acquire(stream_key)
    get_registry().release(stream_key)
    allow_finalizer.set()
    assert await task == finalizer.FinalizationStatus.ACCEPTED.value


async def test_idle_sweep_enforces_configured_finalizer_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "profile_idle_max_concurrency", 2)
    claims = [ActivityClaim(80 + index, f"s-{index}", f"t-{index}", 0.0) for index in range(5)]
    monkeypatch.setattr(
        session_activity,
        "claim_expired_sessions",
        lambda **kwargs: asyncio.sleep(0, result=claims),
    )
    active = 0
    peak = 0

    async def _tracked_process(claim, *, idle_timeout_s):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return finalizer.FinalizationStatus.ACCEPTED.value

    monkeypatch.setattr(idle_timeout, "_process_claim", _tracked_process)

    result = await idle_timeout.run_idle_sweep()

    assert result.accepted == 5
    assert peak == 2


async def test_idle_sweep_isolates_one_claim_failure_and_releases_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 claim의 DB/registry 예외가 같은 batch 결과·로그를 중단하면 안 된다."""
    settings = get_settings()
    monkeypatch.setattr(settings, "profile_idle_max_concurrency", 2)
    failed = ActivityClaim(85, "failed", "t-failed", 0.0)
    healthy = ActivityClaim(86, "healthy", "t-healthy", 0.0)
    monkeypatch.setattr(
        session_activity,
        "claim_expired_sessions",
        lambda **kwargs: asyncio.sleep(0, result=[failed, healthy]),
    )
    released: list[ActivityClaim] = []

    async def _process(claim, *, idle_timeout_s):
        if claim is failed:
            raise TimeoutError("claim revalidation timeout")
        await asyncio.sleep(0)
        return finalizer.FinalizationStatus.ACCEPTED.value

    async def _release(claim):
        released.append(claim)

    monkeypatch.setattr(idle_timeout, "_process_claim", _process)
    monkeypatch.setattr(idle_timeout, "_release_claim_best_effort", _release)

    result = await idle_timeout.run_idle_sweep()

    assert result.claimed == 2
    assert result.accepted == 1
    assert result.retryable == 1
    assert released == [failed]


async def test_cancelled_idle_finalizer_releases_both_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = await _expired_claim(monkeypatch, 90, "cancel")
    store = await get_profile_store()
    key = conversation_key("90", "cancel")
    await store.append_session_ctx(key, "취소되어도 보존할 발화")
    started = asyncio.Event()

    async def _block_delta(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(finalizer, "generate_session_delta", _block_delta)
    task = asyncio.create_task(
        finalizer.finalize_profile_session(90, "cancel", activity_claim=claim)
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert await store.get_session_ctx(key) == ["취소되어도 보존할 발화"]
    assert await processed_events.get_status("session-end:90:cancel") is None
    row = await session_activity.get_session(90, "cancel")
    assert row is not None and row.status == "active"
