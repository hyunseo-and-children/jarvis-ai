"""판매자 그래프 모델 팩토리 (SPEC-SELLER-001 §8 — Anthropic 2-tier).

역할→티어 매핑을 코드로 고정한다: supervisor·planner·워커 5종·judge 는
Haiku(t=0, 일관성 장치 ①), report·recommend 는 Sonnet(t=0.2). 모델 ID 와
temperature 는 Settings 단일 출처(haiku_model_id/sonnet_model_id ·
seller_haiku_temperature/seller_sonnet_temperature)이며 하드코딩하지 않는다.

워커 5종은 전부 같은 티어라 역할 "worker" 하나로 묶는다(2026-07-18 확정) —
워커별 모델 차등이 필요해지면 SellerRole 에 세분 역할을 추가하고 ROLE_TIER 에
등록하면 된다. 모델 버전 변경은 일관성 리셋 이벤트로 CHANGELOG 에 기록(§10-①).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import get_settings

SellerRole = Literal["supervisor", "planner", "worker", "judge", "product", "report", "recommend"]
SellerTier = Literal["haiku", "sonnet"]

# SPEC §8 표의 코드화 — 라우팅·분류·정형 분석은 경량(Haiku), 서술·추천은 상위(Sonnet).
# product 는 §8 표에 미명시 — draft 생성은 정형 변환이라 Haiku t=0 배정(2-7, 2026-07-18).
ROLE_TIER: dict[SellerRole, SellerTier] = {
    "supervisor": "haiku",
    "planner": "haiku",
    "worker": "haiku",
    "judge": "haiku",
    "product": "haiku",
    "report": "sonnet",
    "recommend": "sonnet",
}


@lru_cache(maxsize=None)
def _cached_model(model_id: str, temperature: float, api_key: str) -> BaseChatModel:
    """(모델ID, temperature) 조합당 1회만 생성 — 요청마다 클라이언트 재생성 방지.

    모델 인스턴스는 무상태(신원·대화는 호출 인자로만 전달)라 공유해도 안전하다.
    """
    return init_chat_model(f"anthropic:{model_id}", temperature=temperature, api_key=api_key)


def init_seller_model(role: SellerRole) -> BaseChatModel:
    """역할에 배정된 채팅 모델을 반환한다 (SPEC §8).

    Literal 밖 역할은 KeyError 로 즉시 실패 — 신규 역할(예: chart 복원 §12)은
    SellerRole·ROLE_TIER 에 먼저 등록한다. 같은 (모델ID, temperature) 조합은
    같은 인스턴스를 공유한다(lru_cache).
    """
    settings = get_settings()
    tier = ROLE_TIER[role]
    if tier == "haiku":
        model_id = settings.haiku_model_id
        temperature = settings.seller_haiku_temperature
    else:
        model_id = settings.sonnet_model_id
        temperature = settings.seller_sonnet_temperature
    return _cached_model(model_id, temperature, settings.anthropic_api_key)
