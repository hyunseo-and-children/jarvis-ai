"""프로필 세션 activity/idle timeout 상태 머신 (이슈 #79, SPEC-PROFILE-001 §6.6)."""

from __future__ import annotations

import asyncio

import pytest

from app.agents.profile import session_activity
from app.agents.profile.session_activity import ActivityClaim
from app.core.config import get_settings
from app.core.conversation import ConversationStore, conversation_key


@pytest.fixture(autouse=True)
def _reset_activity() -> None:
    session_activity.reset()


async def test_touch_upserts_one_row_and_refreshes_db_clock_equivalent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 10.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)

    assert await session_activity.touch_session(7, "s1")
    first = await session_activity.get_session(7, "s1")
    now = 25.0
    assert await session_activity.touch_session(7, "s1")
    second = await session_activity.get_session(7, "s1")

    assert first is not None and first.last_activity_at == 10.0
    assert second is not None and second.last_activity_at == 25.0
    assert second.status == "active"


async def test_idle_boundary_is_inclusive_and_claim_batch_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    for session_id in ("a", "b", "c"):
        await session_activity.touch_session(1, session_id)

    now = 599.999
    assert (
        await session_activity.claim_expired_sessions(
            idle_timeout_s=600,
            lease_s=30,
            batch_size=2,
        )
        == []
    )

    now = 600.0
    claims = await session_activity.claim_expired_sessions(
        idle_timeout_s=600,
        lease_s=30,
        batch_size=2,
    )
    assert [claim.session_id for claim in claims] == ["a", "b"]
    assert all(claim.user_id == 1 for claim in claims)


async def test_new_activity_invalidates_processing_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    await session_activity.touch_session(2, "resume")
    now = 600.0
    [claim] = await session_activity.claim_expired_sessions(
        idle_timeout_s=600,
        lease_s=30,
        batch_size=1,
    )

    now = 601.0
    assert await session_activity.touch_session(2, "resume")

    assert not await session_activity.claim_is_current(
        claim,
        idle_timeout_s=600,
    )
    row = await session_activity.get_session(2, "resume")
    assert row is not None and row.status == "active" and row.claim_token is None


async def test_completed_idle_session_reopens_on_new_member_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    await session_activity.touch_session(3, "closed")
    now = 600.0
    [claim] = await session_activity.claim_expired_sessions(
        idle_timeout_s=600,
        lease_s=30,
        batch_size=1,
    )
    assert await session_activity.complete_session(3, "closed", token=claim.claim_token)

    now = 700.0
    assert await session_activity.touch_session(3, "closed")
    row = await session_activity.get_session(3, "closed")
    assert row is not None and row.status == "active" and row.last_activity_at == 700.0


async def test_expired_processing_claim_is_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    await session_activity.touch_session(4, "crash")
    now = 600.0
    [first] = await session_activity.claim_expired_sessions(
        idle_timeout_s=600,
        lease_s=1,
        batch_size=1,
    )

    now = 601.0
    [second] = await session_activity.claim_expired_sessions(
        idle_timeout_s=600,
        lease_s=30,
        batch_size=1,
    )

    assert second.claim_token != first.claim_token
    assert not await session_activity.release_claim(first)
    assert await session_activity.release_claim(second)


async def test_concurrent_claimers_only_one_owns_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr(session_activity, "_monotonic", lambda: now)
    await session_activity.touch_session(5, "race")
    now = 600.0

    results = await asyncio.gather(
        *(
            session_activity.claim_expired_sessions(
                idle_timeout_s=600,
                lease_s=30,
                batch_size=1,
            )
            for _ in range(5)
        )
    )

    assert sum(len(claims) for claims in results) == 1


async def test_conversation_store_touches_only_member_session() -> None:
    store = ConversationStore()

    await store.save_user_message(
        conversation_key("11", "member-session"),
        "11",
        "member",
        "회원 발화",
        session_id="member-session",
    )
    await store.save_user_message(
        conversation_key("guest-1", "guest-session"),
        "guest-1",
        "guest",
        "게스트 발화",
        session_id="guest-session",
    )
    await store.save_user_message(
        conversation_key("22", "seller-session"),
        "22",
        "seller",
        "판매자 발화",
        session_id="seller-session",
    )

    assert await session_activity.get_session(11, "member-session") is not None
    assert await session_activity.get_session(1, "guest-session") is None
    assert await session_activity.get_session(22, "seller-session") is None


async def test_session_activity_queries_have_application_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """activity standalone 쿼리도 멈춘 DB에서 유한 시간 안에 끝난다."""

    class _HangConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args, **kwargs):
            await asyncio.sleep(10)

    class _HangPool:
        def connection(self):
            return _HangConn()

        async def close(self):
            return None

    monkeypatch.setattr(get_settings(), "state_store_query_timeout_s", 0.01)
    session_activity.set_pool(_HangPool())
    claim = ActivityClaim(1, "s", "token", 0.0)
    try:
        operations = [
            lambda: session_activity.touch_session(1, "s"),
            lambda: session_activity.get_session(1, "s"),
            lambda: session_activity.claim_expired_sessions(
                idle_timeout_s=1,
                lease_s=1,
                batch_size=1,
            ),
            lambda: session_activity.claim_is_current(claim, idle_timeout_s=1),
            lambda: session_activity.complete_session(1, "s"),
            lambda: session_activity.complete_terminal_session(1, "s", observed=None),
            lambda: session_activity.release_claim(claim),
        ]
        for operation in operations:
            with pytest.raises(TimeoutError):
                await operation()
    finally:
        session_activity.set_pool(None)
