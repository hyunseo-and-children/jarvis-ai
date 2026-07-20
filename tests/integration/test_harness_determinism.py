"""하니스 결정성 회귀 가드 (PR #41·#43 리뷰 반영).

E2E 스모크의 존재 이유는 "어느 환경에서도 같은 결과"다. 인증 레인이 앰비언트 `.env`/환경변수
(`AUTH_MODE=jwks` 등)에 흔들리면 흐름을 태워보기도 전에 401 로 무너진다 — 그 회귀를 막는다.

**타이밍이 핵심**: 테스트 본문에서 `monkeypatch.setenv` 를 하면 이미 `client`→`dev_settings`
가 만들어진 뒤라 아무것도 검증하지 못한다(PR #43 리뷰 지적 — 그 형태의 가드는 핀을 제거해도
그대로 통과했다). 오염은 **픽스처 해석보다 먼저** 걸려야 하므로, `dev_settings` 보다 앞서
해석되는 픽스처(`ambient_jwks_env`)에서 환경을 오염시키고 그 사실을 자기검증한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.integration.conftest import auth_header, event_types, parse_sse

BODY = {"sessionId": "sess-det", "threadId": "th-det", "message": "여행용 파우치 추천해줘"}


@pytest.fixture
def ambient_jwks_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """앱 설정이 만들어지기 **전에** 앰비언트 환경을 jwks 로 오염시킨다(실패 조건 재현)."""
    monkeypatch.setenv("AUTH_MODE", "jwks")
    monkeypatch.setenv("JWKS_URL", "https://spring.example/.well-known/jwks.json")
    monkeypatch.setenv("JWT_SCOPE", "chat:stream")
    monkeypatch.setenv("PII_HASH_PEPPER", "ambient-pepper")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "ambient-token")
    # 자기검증 — 이 시점에 앰비언트를 읽으면 실제로 jwks 가 나온다. 이 단언이 없으면
    # "오염이 걸리지 않아서 통과한" 공허한 가드와 구분되지 않는다.
    assert Settings().auth_mode == "jwks"


@pytest.fixture
def polluted_client(ambient_jwks_env: None, client: TestClient) -> TestClient:
    """앰비언트가 jwks 로 오염된 상태에서 생성된 앱 클라이언트.

    인자 순서가 곧 해석 순서 — `ambient_jwks_env`(오염) → `client`(→ `dev_settings` 핀).
    """
    return client


def test_dev_pin_wins_over_polluted_ambient_env(polluted_client: TestClient) -> None:
    """앰비언트가 jwks 여도 하니스 인증 레인은 dev 로 고정된다 (핀 제거 시 실패하는 가드)."""
    import app.core.ratelimit as ratelimit
    from app.api import deps

    assert deps.get_settings().auth_mode == "dev"
    assert ratelimit.get_settings().auth_mode == "dev"


def test_buyer_flow_survives_ambient_jwks_env(polluted_client, spring, llm) -> None:
    """`AUTH_MODE=jwks` 환경에서도 기본 구매자 스모크가 그대로 완주한다.

    핀이 풀리면 dev 전용 HS256 하니스 토큰이 RS256 서명 검증에 걸려 401 이 된다.
    """
    resp = polluted_client.post("/chat", json=BODY, headers=auth_header())

    assert resp.status_code == 200
    assert event_types(parse_sse(resp.text))[-1] == "done"


def test_session_end_survives_ambient_jwks_env(polluted_client, spring, llm) -> None:
    """세션 종료 통지도 마찬가지 — jwks 였다면 서비스 토큰 누락으로 401 이 됐을 경로(§3.5)."""
    polluted_client.post("/chat", json=BODY, headers=auth_header())

    resp = polluted_client.post(
        "/events/session-end",
        json={"eventId": "evt-det", "userId": "42", "sessionId": "sess-det"},
    )

    assert resp.status_code == 202
