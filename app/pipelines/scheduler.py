"""I-17 배치 주기 증분 pull 스케줄러 (이슈 #31, api-spec §4.8).

APScheduler BackgroundScheduler 는 잡을 자체 스레드 풀에서 실행한다 — FastAPI 메인
이벤트루프(요청 처리)와 완전히 분리돼, 배치가 도는 동안에도 사용자 요청 응답이 막히지 않는다.
잡 함수(_run_incremental_batch)는 동기 콜러블이며, 그 안에서 asyncio.run() 으로 자체 이벤트루프를
새로 열어 비동기 배치(run_artifacts_batch)를 완결한다. app/main.py 의 lifespan 이 앱 기동/종료에
맞춰 start_scheduler()/stop_scheduler() 를 호출한다.

전체 구축(backfill)은 여기서 다루지 않는다 — 사람이 CLI로 명시 트리거한다(run_batch.py, 이슈 #31).
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.pipelines.artifacts_batch import run_artifacts_batch

_log = logging.getLogger(__name__)
_JOB_ID = "i17_incremental_pull"

_scheduler: BackgroundScheduler | None = None


def _run_incremental_batch() -> None:
    """BackgroundScheduler 워커 스레드에서 동기 호출 — 자체 이벤트루프로 증분 배치 1회 완결.

    잡 실패가 스케줄러 프로세스를 죽이면 안 되므로 예외를 삼키고 로그만 남긴다
    (다음 주기에 저장된 커서부터 자연 재개, §4.8).
    """
    try:
        result = asyncio.run(run_artifacts_batch(full_rebuild=False))
        _log.info(
            "scheduler 증분 배치 완료: processed=%d delisted=%d pages=%d cursor=%s",
            result.processed,
            result.delisted,
            result.pages,
            result.cursor,
        )
    except Exception:  # noqa: BLE001 - 잡 실패 격리(다음 주기 자연 재개)
        _log.exception("scheduler 증분 배치 실패 — 다음 주기 재개")


def start_scheduler() -> BackgroundScheduler:
    """스케줄러를 시작하고 인스턴스를 반환한다 (멱등 — 이미 떠 있으면 그대로 반환)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    settings = get_settings()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_incremental_batch,
        IntervalTrigger(seconds=settings.catalog_batch_interval_s),
        id=_JOB_ID,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    """스케줄러를 정지한다 (앱 종료·테스트 격리 공용)."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
