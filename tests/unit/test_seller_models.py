"""판매자 모델 팩토리 테스트 (SPEC-SELLER-001 §8 — 2-tier·temperature·캐시).

실 API 호출 없음 — dummy 키로 인스턴스 속성(model·temperature)만 검증한다.
"""

from __future__ import annotations

import pytest

from app.agents.seller import models as seller_models
from app.agents.seller.models import ROLE_TIER, init_seller_model
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _fresh_cache_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트마다 캐시 초기화 + dummy 키 Settings 주입 — env 오염·실 키 의존 차단."""
    seller_models._cached_model.cache_clear()
    settings = Settings(_env_file=None, anthropic_api_key="test-key")
    monkeypatch.setattr(seller_models, "get_settings", lambda: settings)


def test_role_tier_matches_spec() -> None:
    """SPEC §8 표와 일치 — 라우팅·분류·판정=haiku / 서술·추천=sonnet."""
    assert ROLE_TIER == {
        "supervisor": "haiku",
        "planner": "haiku",
        "worker": "haiku",
        "judge": "haiku",
        "product": "haiku",  # §8 미명시 — 정형 변환이라 Haiku 배정(2-7 확정)
        "report": "sonnet",
        "recommend": "sonnet",
    }


def test_haiku_roles_use_haiku_t0() -> None:
    """supervisor·planner·worker·judge 는 Haiku, temperature=0 (일관성 장치 ①)."""
    expected_id = Settings(_env_file=None).haiku_model_id
    for role in ("supervisor", "planner", "worker", "judge", "product"):
        model = init_seller_model(role)
        assert model.model == expected_id
        assert model.temperature == 0.0


def test_sonnet_roles_use_sonnet_t02() -> None:
    """report·recommend 는 Sonnet, temperature=0.2 (서술 품질)."""
    expected_id = Settings(_env_file=None).sonnet_model_id
    for role in ("report", "recommend"):
        model = init_seller_model(role)
        assert model.model == expected_id
        assert model.temperature == 0.2


def test_settings_override_reflected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings 주입이 팩토리에 반영된다 — 모델ID·temperature 하드코딩이 아님을 강제."""
    custom = Settings(
        _env_file=None,
        anthropic_api_key="test-key",
        sonnet_model_id="claude-sonnet-custom",
        seller_sonnet_temperature=0.5,
    )
    monkeypatch.setattr(seller_models, "get_settings", lambda: custom)
    model = init_seller_model("report")
    assert model.model == "claude-sonnet-custom"
    assert model.temperature == 0.5


def test_same_role_shares_instance() -> None:
    """같은 (모델ID, temperature) 조합은 같은 인스턴스 — 요청마다 재생성하지 않는다."""
    assert init_seller_model("worker") is init_seller_model("worker")
    # supervisor 도 haiku t=0 이라 동일 조합 — 캐시 키가 역할이 아니라 조합임을 확인.
    assert init_seller_model("supervisor") is init_seller_model("worker")


def test_unknown_role_raises() -> None:
    """미등록 역할은 KeyError 즉시 실패 — chart(§12 보류) 등은 등록 후에만 사용."""
    with pytest.raises(KeyError):
        init_seller_model("chart")  # type: ignore[arg-type]
