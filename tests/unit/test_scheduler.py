"""I-17 배치 스케줄러 테스트 (이슈 #31) — BackgroundScheduler 잡 등록·정지만 검증(실제 대기 없음).

_run_incremental_batch()는 BackgroundScheduler 워커 스레드에서 동기로 호출되는 잡 함수라
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


def test_start_scheduler_registers_job_with_configured_interval(monkeypatch):
    settings = Settings(_env_file=None, catalog_batch_interval_s=123.0, google_api_key="test-key")
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    scheduler = sched_mod.start_scheduler()

    job = scheduler.get_job(sched_mod._JOB_ID)
    assert job is not None
    assert job.trigger.interval.total_seconds() == 123.0


def test_start_scheduler_is_idempotent(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key")
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    first = sched_mod.start_scheduler()
    second = sched_mod.start_scheduler()

    assert first is second
    assert len(first.get_jobs()) == 1


def test_stop_scheduler_allows_fresh_restart(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key")
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    first = sched_mod.start_scheduler()
    sched_mod.stop_scheduler()
    second = sched_mod.start_scheduler()

    assert first is not second


def test_start_scheduler_skips_when_google_api_key_missing(monkeypatch):
    """PR #42 리뷰 — dev 모드는 config.py fail-fast(jwks 전용)를 안 타므로, 스케줄러가
    google_api_key 없이 기동되면 5분마다 조용히 EmbeddingError 만 반복하던 원래 문제가
    dev 모드에서 재현된다. 아예 기동하지 않는다."""
    settings = Settings(_env_file=None, google_api_key="")
    monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)

    result = sched_mod.start_scheduler()

    assert result is None


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
