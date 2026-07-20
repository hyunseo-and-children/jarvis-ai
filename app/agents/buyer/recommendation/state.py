"""추천 서브그래프 내부 상태·헬퍼 (이슈 #2 MVP 슬라이스).

decompose 산출(RouteDecision)·rerank 산출(RerankResult)·conditions 칩 파생을 담는다.
전체 SPEC State(RerankValidation·BundleState·relaxation·sources·priority 등)는
후속(SPEC-RECOMMEND-001 고급기능) — 본 슬라이스는 선형 파이프라인에 필요한 최소만 둔다.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Literal

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from app.core import pg_store
from app.core.llm import LLMError
from app.schemas.chat import ConditionChip
from app.schemas.spring import ProductSearchFilters

_NAMESPACE_ROOT = "buyer_revert"
_CATEGORIES_KEY = "categories"

# key(thread_key)별 asyncio.Lock — RevertStore.add() 의 get→put(read-modify-write) 구간을
# 직렬화한다. 동일 스레드로 겹치는 요청(멀티탭·연속 발화)이 오면 나중 aput 이 앞선 갱신을
# 덮어써 되돌리기 카테고리가 유실될 수 있다(lost update, PR #46 리뷰).
_add_locks: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _add_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _add_locks[key] = lock
    return lock


@dataclass
class CartIntent:
    """decompose 가 추출한 장바구니 의도(이슈 #3). productId 는 직전 추천 문맥에서 해소."""

    product_id: int | None = None
    option_id: int | None = None
    quantity: int = 1


@dataclass
class RouteDecision:
    """decompose(Haiku) 1회 산출 — intent 라우팅 + 병합 필터/의미쿼리/case + 폴백 답변 + 장바구니 의도."""

    intent: Literal["recommend", "cart_add", "cart_view", "general"]
    filters: ProductSearchFilters
    semantic_query: str
    case: int = 2
    reply: str = ""  # intent == general 일 때만 사용자에게 줄 답변
    cart: CartIntent | None = None  # intent == cart_add/cart_view 일 때
    revert_categories: list[str] = field(default_factory=list)  # 소모품 억제 되돌리기(결정 14-F)


@dataclass
class RerankResult:
    """rerank(Sonnet) 산출 — 노출 순서 id + 상품별 근거, 전체 코멘트."""

    ranked: list[tuple[int, str]] = field(default_factory=list)  # (productId, rationale)
    overall_comment: str = ""


def extract_json(text: str) -> dict:
    """LLM 응답 문자열에서 첫 '{'~마지막 '}' 구간의 JSON 객체를 파싱한다(코드펜스 허용).

    파싱 불가/객체 아님이면 LLMError — 상위가 degrade/error 로 처리한다.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError("LLM 응답에서 JSON 을 찾지 못함")
    try:
        obj = json.loads(text[start : end + 1])
    except (ValueError, TypeError) as exc:
        raise LLMError("LLM JSON 파싱 실패") from exc
    if not isinstance(obj, dict):
        raise LLMError("LLM JSON 이 객체가 아님")
    return obj


def build_condition_chips(filters: ProductSearchFilters) -> list[ConditionChip]:
    """병합 필터에서 conditions 칩을 결정론적으로 파생한다(FE 제거 가능, 카드 아님).

    LLM 의 임의 conditions 출력에 의존하지 않고 확정된 필터에서 파생 — 테스트 가능·일관.
    카테고리 칩을 먼저 둔다(api-spec §3.1 (2) 예시 순).
    """
    chips: list[ConditionChip] = []
    if filters.category:
        chips.append(
            ConditionChip(
                field="category", label=f"카테고리 · {filters.category}", value=filters.category
            )
        )
    if filters.price_max is not None:
        chips.append(
            ConditionChip(
                field="priceMax", label=f"{filters.price_max:,}원 이하", value=filters.price_max
            )
        )
    if filters.price_min is not None:
        chips.append(
            ConditionChip(
                field="priceMin", label=f"{filters.price_min:,}원 이상", value=filters.price_min
            )
        )
    if filters.brand:
        chips.append(
            ConditionChip(field="brand", label=" · ".join(filters.brand), value=filters.brand)
        )
    if filters.rating_min is not None:
        chips.append(
            ConditionChip(
                field="ratingMin", label=f"평점 {filters.rating_min}+", value=filters.rating_min
            )
        )
    if filters.keyword:
        chips.append(ConditionChip(field="keyword", label=filters.keyword, value=filters.keyword))
    return chips


class RevertStore:
    """스레드별 소모품 억제 되돌리기 카테고리 집합 — LangGraph BaseStore(pg-profile) 백엔드(신원 스코프 키).

    사용자가 "다시 추천받기"(되돌리기 칩)한 카테고리는 이후 턴에서도 억제하지 않는다(결정 14-F).
    """

    def __init__(self, store: BaseStore | None = None) -> None:
        self._store = store or InMemoryStore()

    async def get(self, key: str) -> set[str]:
        item = await self._store.aget((_NAMESPACE_ROOT, key), _CATEGORIES_KEY)
        return set(item.value[_CATEGORIES_KEY]) if item else set()

    async def add(self, key: str, categories) -> None:
        if not categories:
            return
        async with _lock_for(key):  # get→put 원자성 보장(lost update 방지)
            current = await self.get(key)
            current.update(categories)
            await self._store.aput(
                (_NAMESPACE_ROOT, key), _CATEGORIES_KEY, {_CATEGORIES_KEY: sorted(current)}
            )


async def get_revert_store() -> RevertStore:
    """되돌리기 스토어 — pg-profile 공유 연결 백엔드(요청마다 얇은 래퍼 재생성)."""
    return RevertStore(await pg_store.get_store())


def reset_revert_store() -> None:
    """테스트 격리용 — 공유 pg-profile store(InMemoryStore)를 비운다."""
    pg_store.reset_store()
