"""app.main lifespan 배선 테스트 (이슈 #31) — 스케줄러 시작/정지 호출만 검증.

TestClient(app)를 `with`로 감싸야 lifespan 이 실제로 발동한다(경험적으로 확인 —
`with` 없이 쓰는 이 저장소의 기존 TestClient 테스트들은 lifespan 영향을 받지 않는다).
start_scheduler/stop_scheduler 는 fake 로 대체해 실제 스케줄러 스레드를 띄우지 않는다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_mod


def test_lifespan_starts_and_stops_scheduler(monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "start_scheduler", lambda: calls.append("start"))
    monkeypatch.setattr(main_mod, "stop_scheduler", lambda: calls.append("stop"))

    with TestClient(main_mod.app) as client:
        assert calls == ["start"]
        resp = client.get("/health")
        assert resp.status_code == 200

    assert calls == ["start", "stop"]
