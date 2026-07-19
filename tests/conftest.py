"""테스트 공통 — 인프라 전역 상태(레이트 리밋·활성 스트림 레지스트리)를 테스트마다 격리."""

from __future__ import annotations

import pytest

from app.agents.buyer.cart.state import reset_cart_store
from app.agents.buyer.graph import reset_thread_store
from app.core.conversation import reset_store
from app.core.ratelimit import reset_limiter
from app.core.stream import get_registry


@pytest.fixture(autouse=True)
def _reset_infra_state():
    """각 테스트 전후로 인메모리 카운터·레지스트리를 비워 테스트 간 누수를 막는다."""
    reset_limiter()
    reset_store()
    reset_thread_store()
    reset_cart_store()
    get_registry()._active.clear()
    yield
    reset_limiter()
    reset_store()
    reset_thread_store()
    reset_cart_store()
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
