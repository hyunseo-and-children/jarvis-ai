"""테스트 공통 — 인프라 전역 상태(레이트 리밋·활성 스트림 레지스트리)를 테스트마다 격리."""

from __future__ import annotations

import os
import sys

import pytest

# 로컬 통합용 .env가 단위/통합 테스트의 인증·외부 provider를 오염시키지 않게 한다.
# 단, 실 키가 필요한 smoke(마커 명시 선택)는 그대로 둔다 — 여기서 지우면 실행 불가(리뷰 반영).
if not ("smoke" in " ".join(sys.argv) and "not smoke" not in " ".join(sys.argv)):
    os.environ["AUTH_MODE"] = "dev"
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["GOOGLE_API_KEY"] = ""

from app.agents.buyer.cart.state import reset_cart_store
from app.agents.buyer.graph import reset_thread_store
from app.agents.buyer.recommendation.state import reset_revert_store
from app.agents.profile.store import reset_profile_store
from app.core.conversation import reset_store
from app.core.ratelimit import reset_limiter
from app.core.stream import get_registry
from app.pipelines.artifact_store import reset_catalog_store


@pytest.fixture(autouse=True)
def _reset_infra_state():
    """각 테스트 전후로 인메모리 카운터·레지스트리를 비워 테스트 간 누수를 막는다.

    reset_catalog_store(): get_catalog_store() 싱글턴은 이제 pg-catalog 연결 풀을 여는데,
    유닛 테스트는 항상 store 를 직접 주입해 이 경로를 타지 않는다 — 혹시 실수로 호출됐을
    커넥션 풀이 다음 테스트로 새지 않게 방어적으로 리셋한다(이슈 #31).
    """
    reset_limiter()
    reset_store()
    reset_thread_store()
    reset_cart_store()
    reset_revert_store()
    reset_profile_store()
    reset_catalog_store()
    get_registry()._active.clear()
    yield
    reset_limiter()
    reset_store()
    reset_thread_store()
    reset_cart_store()
    reset_revert_store()
    reset_profile_store()
    reset_catalog_store()
    get_registry()._active.clear()


@pytest.fixture
def buyer_fakes(monkeypatch):
    """/chat 을 실 buyer 그래프 + fake LLM/검색/push 로 구동한다(라이브 의존 없이 해피패스)."""
    import app.agents.buyer.graph as bg
    import app.services.search_service as ss
    import app.services.spring_client as sc
    from tests._fakes import FakeBackend, FakeLLM, fake_push

    llm = FakeLLM()
    monkeypatch.setattr(bg, "get_llm", lambda: llm)
    monkeypatch.setattr(ss, "default_backend", FakeBackend())
    monkeypatch.setattr(sc, "push_recommendations", fake_push)
    return llm


@pytest.fixture(autouse=True)
def _no_live_recent_purchases(monkeypatch):
    """구매이력 조회 기본값을 빈 응답으로 — 단위테스트가 라이브 Spring 을 건드리지 않게.
    dedup 동작 검증 테스트는 get_recent_purchases 를 명시적으로 재패치한다.
    """
    from app.schemas.spring import RecentPurchases

    async def _empty(user_id, status=None):
        return RecentPurchases()

    monkeypatch.setattr("app.services.spring_client.get_recent_purchases", _empty)
