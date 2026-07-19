"""유닛 테스트 공통 픽스처 — 판매자 영속 백엔드를 InMemory 로 격리한다.

4-2(hitl checkpointer)·4-3(history store)은 미주입 시 pg-profile 접속을 시도한다
(실패 시 dev 폴백). 유닛 테스트는 환경(PG 가동 여부)에 절대 의존하면 안 되므로
전 테스트에 InMemory 백엔드를 자동 주입하고 종료 시 초기화한다 — 로컬에 PG 가
떠 있어도 유닛 테스트가 실 DB 에 쓰는 사고를 구조로 차단한다.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.agents.seller import history, hitl


@pytest.fixture(autouse=True)
def _isolate_seller_persistence():
    hitl.set_checkpointer(InMemorySaver())
    history.set_store(InMemoryStore())
    yield
    hitl.set_checkpointer(None)
    history.set_store(None)
