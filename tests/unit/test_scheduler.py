"""I-17 + 프로필 idle 스케줄러 테스트 (이슈 #31/#79, 실제 대기 없음).

_run_incremental_batch()는 executor 워커 스레드에서 동기로 호출되는 잡 함수라
(내부에서 asyncio.run 으로 자체 이벤트루프를 새로 연다) 여기 테스트도 동기(def)로 호출한다 —
이미 실행 중인 이벤트루프 안에서 asyncio.run()을 부르면 RuntimeError 가 나기 때문에
async def 테스트로 감싸면 안 된다.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.pipelines import scheduler as sched_mod
from app.pipelines.artifacts_batch import BatchResult


@pytest.fixture(autouse=True)
def _reset_scheduler():
    sched_mod.stop_scheduler()
    yield
    sched_mod.stop_scheduler()


async def test_start_scheduler_registers_both_jobs_with_configured_intervals(monkeypatch):
    settings = Settings(
        _env_file=None,
        catalog_batch_interval_s=123.0,
        profile_idle_sweep_interval_s=17.0,
        google_api_key="test-key",
    )
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    scheduler = sched_mod.start_scheduler()

    i17 = scheduler.get_job(sched_mod._I17_JOB_ID)
    idle = scheduler.get_job(sched_mod._PROFILE_IDLE_JOB_ID)
    assert i17 is not None and i17.trigger.interval.total_seconds() == 123.0
    assert idle is not None and idle.trigger.interval.total_seconds() == 17.0


async def test_start_scheduler_is_idempotent(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key")
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    first = sched_mod.start_scheduler()
    second = sched_mod.start_scheduler()

    assert first is second
    assert len(first.get_jobs()) == 2


async def test_stop_scheduler_allows_fresh_restart(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key")
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    first = sched_mod.start_scheduler()
    sched_mod.stop_scheduler()
    second = sched_mod.start_scheduler()

    assert first is not second


async def test_missing_google_key_skips_only_i17_but_keeps_idle_job(monkeypatch):
    """이슈 #79 — I-17 provider 조건이 프로필 inactivity 회수까지 끄면 안 된다."""
    settings = Settings(_env_file=None, google_api_key="")
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    scheduler = sched_mod.start_scheduler()

    assert scheduler.get_job(sched_mod._I17_JOB_ID) is None
    assert scheduler.get_job(sched_mod._PROFILE_IDLE_JOB_ID) is not None


async def test_idle_job_prevents_overlap_and_coalesces_missed_ticks(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="")
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    scheduler = sched_mod.start_scheduler()

    idle = scheduler.get_job(sched_mod._PROFILE_IDLE_JOB_ID)
    assert idle.max_instances == 1
    assert idle.coalesce is True


def test_run_incremental_batch_calls_run_artifacts_batch_with_full_rebuild_false(monkeypatch):
    calls = []

    async def fake_run_artifacts_batch(*, full_rebuild):
        calls.append(full_rebuild)
        return BatchResult(processed=1, hidden=0, pages=1, cursor="c1")

    monkeypatch.setattr(sched_mod, "run_artifacts_batch", fake_run_artifacts_batch)

    sched_mod._run_incremental_batch()

    assert calls == [False]


def test_run_incremental_batch_swallows_exceptions(monkeypatch):
    async def fake_run_artifacts_batch(*, full_rebuild):
        raise RuntimeError("boom")

    monkeypatch.setattr(sched_mod, "run_artifacts_batch", fake_run_artifacts_batch)

    sched_mod._run_incremental_batch()  # 예외가 전파되지 않으면 통과(스케줄러 프로세스 보호)


async def test_run_profile_idle_sweep_uses_current_event_loop(monkeypatch):
    loops = []

    async def fake_run_idle_sweep():
        import asyncio

        loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(sched_mod, "run_idle_sweep", fake_run_idle_sweep)

    await sched_mod._run_profile_idle_sweep()

    import asyncio

    assert loops == [asyncio.get_running_loop()]


async def test_run_profile_idle_sweep_swallows_exceptions(monkeypatch):
    async def fake_run_idle_sweep():
        raise RuntimeError("boom")

    monkeypatch.setattr(sched_mod, "run_idle_sweep", fake_run_idle_sweep)

    await sched_mod._run_profile_idle_sweep()
