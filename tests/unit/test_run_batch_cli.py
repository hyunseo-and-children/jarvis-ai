"""I-17 배치 CLI 러너 테스트 (이슈 #31) — argparse 배선과 run_artifacts_batch 호출만 검증.

실제 배치 실행(run_artifacts_batch)은 test_artifacts_batch.py 소관 — 여기는 CLI 진입점이
올바른 인자로 그 함수를 호출하는지만 확인한다(라이브 Anthropic/Google/Spring 불필요).
"""

from __future__ import annotations

from app.pipelines import run_batch
from app.pipelines.artifacts_batch import BatchResult


def test_parse_args_full_flag_true():
    args = run_batch._parse_args(["--full"])
    assert args.full is True


def test_parse_args_default_full_false():
    args = run_batch._parse_args([])
    assert args.full is False


async def test_main_calls_run_artifacts_batch_with_full_rebuild(monkeypatch):
    calls = []

    async def fake_run_artifacts_batch(*, full_rebuild):
        calls.append(full_rebuild)
        return BatchResult(processed=1, hidden=0, pages=1, cursor="c1")

    monkeypatch.setattr(run_batch, "run_artifacts_batch", fake_run_artifacts_batch)

    await run_batch._main(True)

    assert calls == [True]


async def test_main_incremental_when_not_full(monkeypatch):
    calls = []

    async def fake_run_artifacts_batch(*, full_rebuild):
        calls.append(full_rebuild)
        return BatchResult(processed=0, hidden=0, pages=0, cursor=None)

    monkeypatch.setattr(run_batch, "run_artifacts_batch", fake_run_artifacts_batch)

    await run_batch._main(False)

    assert calls == [False]


def test_cli_entrypoint_invokes_asyncio_run(monkeypatch):
    called = {}

    def fake_run(coro):
        called["ran"] = True
        coro.close()  # "coroutine was never awaited" 경고 방지

    monkeypatch.setattr(run_batch.asyncio, "run", fake_run)

    run_batch.main(["--full"])

    assert called["ran"] is True
