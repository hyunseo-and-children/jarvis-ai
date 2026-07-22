"""I-17 배치 CLI 러너 — 초기 전체 구축(backfill) 수동 트리거 (이슈 #31).

`uv run python -m app.pipelines.run_batch --full` 로 실행한다. 전체 구축(backfill)은
Google API 호출량·비용이 크고 재시작 시 의도치 않게 재트리거되면 사고로 이어질 수 있어
자동화하지 않고 사람이 명시적으로 1회 실행하기로 결정했다(이슈 #31 논의). 주기 증분 pull 은
scheduler.py(APScheduler)가 앱 기동 시 자동으로 담당한다 — 이 CLI 와는 별개 경로.

--full 없이 실행하면 저장된 커서부터 증분 1회만 수행한다(스케줄러 없이 수동 1회 돌리고 싶을 때).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.pipelines.artifacts_batch import run_artifacts_batch

_log = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="I-17 카탈로그 AI 생성물 배치 러너 (이슈 #31)")
    parser.add_argument(
        "--full",
        action="store_true",
        help="초기 전체 구축(since=0, 원자 교체) - 미지정 시 저장된 커서부터 증분 1회",
    )
    return parser.parse_args(argv)


async def _main(full: bool) -> None:
    result = await run_artifacts_batch(full_rebuild=full)
    _log.info(
        "run_batch 완료: full=%s processed=%d hidden=%d pages=%d cursor=%s",
        full,
        result.processed,
        result.hidden,
        result.pages,
        result.cursor,
    )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    asyncio.run(_main(args.full))


if __name__ == "__main__":
    main()
