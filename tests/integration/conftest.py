"""E2E 스모크 공통 픽스처 (이슈 #35) — Spring stub 설치 + 앱 클라이언트 + 신원 토큰.

`spring_client._client` 를 MockTransport 클라이언트 팩토리로 교체해 **HTTP 경계에서만** 대역을
넣는다(모듈 함수 patch 아님 — URL·헤더·envelope 파싱이 실코드로 검증되게).
전역 인메모리 상태 리셋은 상위 tests/conftest.py 의 autouse 픽스처가 담당한다.
"""

from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services.spring_client import get_recent_purchases as _real_get_recent_purchases
from tests.integration._stubs import ScriptedLLM, SpringStub


@pytest.fixture
def spring(monkeypatch: pytest.MonkeyPatch) -> SpringStub:
    """Spring stub 을 AI→Spring HTTP 경계에 설치한다 (api-spec §1.2 레인 c).

    실 설정(base_url·X-Internal-Token·3s 타임아웃)을 그대로 쓰되 transport 만 Mock 으로 바꾼다.
    """
    import httpx

    import app.services.spring_client as sc

    stub = SpringStub()
    settings = Settings(_env_file=None, internal_api_token="e2e-internal-token")

    def _stub_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=settings.spring_base_url,
            timeout=settings.spring_timeout_s,
            headers={"X-Internal-Token": settings.internal_api_token},
            transport=httpx.MockTransport(stub.handler),
        )

    monkeypatch.setattr(sc, "_client", _stub_client)
    # 상위 conftest 의 autouse `_no_live_recent_purchases` 는 단위테스트가 라이브 Spring 을
    # 건드리지 않게 I-19 를 빈 응답으로 막는다. E2E 는 stub 이 HTTP 경계를 이미 대신하므로
    # 실함수로 되돌려 I-19 역호출까지 전 구간을 검증한다(모듈 임포트 시점에 잡아둔 원본).
    monkeypatch.setattr(sc, "get_recent_purchases", _real_get_recent_purchases)
    return stub


@pytest.fixture
def spring_http(spring: SpringStub):
    """FE→Spring(레인 d) 대역 — 경로 B 종단(CH-5 목록 GET, §4.3) 확인용 동기 클라이언트."""
    import httpx

    with httpx.Client(
        base_url="http://spring.test", transport=httpx.MockTransport(spring.handler)
    ) as client:
        yield client


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """스크립트 LLM 을 그래프·프로필·배치 경로에 주입한다 (라이브 Anthropic 불필요)."""
    import app.agents.buyer.graph as buyer_graph
    import app.api.events as events_api

    scripted = ScriptedLLM()
    monkeypatch.setattr(buyer_graph, "get_llm", lambda: scripted)
    monkeypatch.setattr(events_api, "get_llm", lambda: scripted)
    return scripted


@pytest.fixture
def client() -> TestClient:
    """AI 서버 앱 클라이언트 (기본 dev 인증 모드 — 헤더 없으면 게스트)."""
    with TestClient(app) as test_client:
        yield test_client


def member_token(user_id: str = "42") -> str:
    """dev 모드용 회원 토큰 — 서명 검증 없이 클레임만 읽는다(로컬/CI 전용).

    실서명(RS256/JWKS) 경로는 test_auth_e2e_flow.py 가 jwks 모드로 별도 검증한다(#34 머지분).
    """
    # 32B 이상 더미 키 — dev 모드는 서명을 보지 않지만 짧은 HMAC 키 경고를 피한다.
    return jwt.encode({"sub": user_id, "sub_type": "member"}, "dev-only-not-a-secret-0123456789", algorithm="HS256")


def auth_header(user_id: str = "42") -> dict[str, str]:
    """회원 신원 Authorization 헤더 (신원은 본문이 아니라 토큰 sub 에서만 도출 — §2.3)."""
    return {"Authorization": f"Bearer {member_token(user_id)}"}


def parse_sse(body: str) -> list[dict]:
    """SSE 본문에서 `data:` JSON 이벤트를 순서대로 파싱한다."""
    import json

    return [
        json.loads(line.strip()[len("data:") :].strip())
        for line in body.splitlines()
        if line.strip().startswith("data:")
    ]


def event_types(events: list[dict]) -> list[str]:
    """이벤트 타입 순서 목록 (api-spec §3.1 이벤트명 검증용)."""
    return [e["type"] for e in events]


def first_of(events: list[dict], type_name: str) -> dict | None:
    """지정 타입의 첫 이벤트 data (없으면 None)."""
    return next((e["data"] for e in events if e["type"] == type_name), None)
