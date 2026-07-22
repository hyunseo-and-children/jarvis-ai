"""I-17 증분 pull + 프로필 inactivity 스케줄러 (이슈 #31/#79).

APScheduler AsyncIOScheduler는 FastAPI lifespan의 event loop에 결합한다. async 프로필 idle
job은 loop-bound pg-profile pool과 활성 스트림 registry를 같은 loop에서 안전하게 사용한다.
동기 I-17 job은 기본 executor에서 실행되고 내부 ``asyncio.run()``으로 독립 배치를 완결한다.

전체 구축(backfill)은 여기서 다루지 않는다 — 사람이 CLI로 명시 트리거한다(run_batch.py, 이슈 #31).

[MVP 단일 인스턴스 전제, PR #42 리뷰] AsyncIOScheduler는 프로세스 로컬 스케줄러라 분산
락·리더 선출이 없다 — 다중 인스턴스(uvicorn --workers, k8s replica 등)로 배포하면 인스턴스마다
독립적으로 같은 배치가 동시 실행돼 Google API 호출이 인스턴스 수만큼 배가된다. 이 리포는 아직
단일 인스턴스 배포만 지원한다(app/core/ratelimit.py·app/core/stream.py 와 동일 전제 — 두 곳 다
"다중 인스턴스 확장 시 Redis 이관" 문서화만 해두고 구현은 안 함). 다중 인스턴스 확장 시 이
스케줄러도 같은 방식(예: Redis 분산 락으로 리더만 잡 실행)으로 이관해야 한다.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.agents.profile.idle_timeout import run_idle_sweep
from app.core.config import get_settings
from app.pipelines.artifacts_batch import run_artifacts_batch

_log = logging.getLogger(__name__)
_I17_JOB_ID = "i17_incremental_pull"
_PROFILE_IDLE_JOB_ID = "profile_idle_timeout"
# 하위 호환 — 기존 내부 테스트/운영 도구가 참조하던 이름.
_JOB_ID = _I17_JOB_ID

_scheduler: AsyncIOScheduler | None = None


def _run_incremental_batch() -> None:
    """AsyncIO scheduler 기본 executor에서 동기 호출 — 자체 이벤트루프로 증분 배치 1회 완결.

    잡 실패가 스케줄러 프로세스를 죽이면 안 되므로 예외를 삼키고 로그만 남긴다
    (다음 주기에 저장된 커서부터 자연 재개, §4.8).
    """
    try:
        result = asyncio.run(run_artifacts_batch(full_rebuild=False))
        _log.info(
            "scheduler 증분 배치 완료: processed=%d hidden=%d pages=%d cursor=%s",
            result.processed,
            result.hidden,
            result.pages,
            result.cursor,
        )
    except Exception:  # noqa: BLE001 - 잡 실패 격리(다음 주기 자연 재개)
        _log.exception("scheduler 증분 배치 실패 — 다음 주기 재개")


async def _run_profile_idle_sweep() -> None:
    """FastAPI event loop에서 inactivity sweep 1회를 실행하고 실패를 다음 tick으로 격리한다."""
    try:
        await run_idle_sweep()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - job 실패 격리, activity lease가 다음 sweep 복구
        _log.exception("profile idle sweep 실패 — 다음 주기 재개")


def start_scheduler() -> AsyncIOScheduler:
    """스케줄러를 시작하고 인스턴스를 반환한다 (멱등 — 이미 떠 있으면 그대로 반환).

    프로필 idle job은 provider 키와 무관하게 항상 등록한다. GOOGLE_API_KEY가 없으면 I-17
    job만 생략한다. 호출은 FastAPI lifespan 등 실행 중인 event loop 안에서 해야 한다.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    settings = get_settings()
    scheduler = AsyncIOScheduler(event_loop=asyncio.get_running_loop())
    scheduler.add_job(
        _run_profile_idle_sweep,
        IntervalTrigger(seconds=settings.profile_idle_sweep_interval_s),
        id=_PROFILE_IDLE_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if settings.google_api_key:
        scheduler.add_job(
            _run_incremental_batch,
            IntervalTrigger(seconds=settings.catalog_batch_interval_s),
            id=_I17_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    else:
        _log.warning(
            "GOOGLE_API_KEY 미설정 — I-17 job만 비활성화합니다 (profile idle job은 계속 실행됩니다)"
        )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    """스케줄러를 정지한다 (앱 종료·테스트 격리 공용)."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except RuntimeError:
            # 테스트/비정상 종료에서 scheduler가 묶였던 loop가 먼저 닫힌 경우.
            pass
        _scheduler = None
