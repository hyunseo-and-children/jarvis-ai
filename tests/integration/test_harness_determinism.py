"""하니스 결정성 회귀 가드 (PR #41 리뷰 반영).

E2E 스모크의 존재 이유는 "어느 환경에서도 같은 결과"다. 인증 레인이 앰비언트 `.env`/환경변수
(`AUTH_MODE=jwks` 등)에 흔들리면 흐름을 태워보기도 전에 401 로 무너진다 — 그 회귀를 막는다.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from tests.integration.conftest import auth_header, event_types, parse_sse

BODY = {"sessionId": "sess-det", "threadId": "th-det", "message": "여행용 파우치 추천해줘"}


def test_dev_settings_pin_is_independent_of_ambient_env(
    monkeypatch: pytest.MonkeyPatch, dev_settings: Settings
) -> None:
    """앰비언트 환경변수가 jwks 여도 하니스 인증 레인은 dev 로 고정된다."""
    monkeypatch.setenv("AUTH_MODE", "jwks")
    monkeypatch.setenv("JWKS_URL", "https://spring.example/.well-known/jwks.json")

    from app.api import deps

    assert deps.get_settings().auth_mode == "dev"
    assert dev_settings.auth_mode == "dev"


def test_buyer_flow_survives_ambient_jwks_env(
    monkeypatch: pytest.MonkeyPatch, client, spring, llm
) -> None:
    """`AUTH_MODE=jwks` 가 환경에 있어도 기본 구매자 스모크는 그대로 완주한다."""
    monkeypatch.setenv("AUTH_MODE", "jwks")
    monkeypatch.setenv("JWKS_URL", "https://spring.example/.well-known/jwks.json")

    resp = client.post("/chat", json=BODY, headers=auth_header())

    assert resp.status_code == 200, "dev 핀이 풀리면 HS256 하니스 토큰이 401 로 걸린다"
    assert event_types(parse_sse(resp.text))[-1] == "done"


def test_session_end_survives_ambient_jwks_env(
    monkeypatch: pytest.MonkeyPatch, client, spring, llm
) -> None:
    """세션 종료 통지도 마찬가지 — jwks 였다면 서비스 토큰 누락으로 401 이 됐을 경로."""
    monkeypatch.setenv("AUTH_MODE", "jwks")

    client.post("/chat", json=BODY, headers=auth_header())
    resp = client.post(
        "/events/session-end",
        json={"eventId": "evt-det", "userId": "42", "sessionId": "sess-det"},
    )

    assert resp.status_code == 202
