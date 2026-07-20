"""장바구니 서브그래프 스레드 상태 (이슈 #3, 이슈 #33 — pg-profile BaseStore 이관).

두 가지를 스레드 스코프(신원 스코프 키)로 보관한다 — LangGraph BaseStore(pg-profile) 백엔드:
  - last_reco    : 직전 추천 후보의 productId — "그거 담아줘"의 productId 해소 소스(경로 B라
                   SSE엔 카드가 없으므로 AI가 문맥으로 상품을 확정한다). 상품명은 pg-profile 에
                   저장하지 않고 프로세스 로컬 휘발성 캐시에만 둔다(아래 _last_reco_names 참조).
  - pending_add  : 옵션 되물음 진행 상태(CART_OPTION_REQUIRED/INVALID) — 다음 턴에서 사용자 답을
                   optionId 로 해석해 재담기(§4.1 멀티턴).
프로덕션은 app.core.pg_store 공유 연결(pg-profile), 테스트는 기본 InMemoryStore()(무인자 생성자)
또는 명시 주입 — app/agents/seller/history.py 와 동일한 BaseStore 이관 패턴(§6.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from app.core import pg_store
from app.schemas.spring import CartOption

_NAMESPACE_ROOT = "buyer_cart"
_LAST_RECO_KEY = "last_reco"
_PENDING_KEY = "pending"

# last_reco 의 상품명(product.name 원본 컬럼 사본)은 pg-profile 에 저장하지 않는다 — CLAUDE.md
# "AI Postgres 에는 AI 생성물만 저장, 상품 원본 컬럼 사본 금지"(PR #46 후속 리뷰). pg-profile 엔
# productId(참조 식별자)만 영속하고, 이름은 decompose 프롬프트 문맥용으로만 쓰이므로 이 프로세스
# 로컬 휘발성 캐시(thread key → {productId: name})에 둔다. 같은 프로세스 수명 동안에는 이름
# disambiguation("파란 니트 담아줘")이 정상 동작하고, 재시작(캐시 소실) 후에는 pid 만 pg-profile
# 에서 복원되어 이름은 "" 로 degrade 한다("그거 담아줘" pid 해소는 계속 작동).
# [한계] 스레드 키마다 누적되며 TTL/LRU 정리는 미구현(MVP 단일 인스턴스 전제, _session_locks 와
# 동일) — 다중 인스턴스에선 추천/담기가 다른 인스턴스에 걸리면 이름만 캐시 미스로 "" degrade.
# reset_cart_store() 는 테스트 격리용으로만 비운다.
_last_reco_names: dict[str, dict[int, str]] = {}


@dataclass
class PendingAdd:
    """옵션 되물음 진행 상태. attempts = CART_OPTION_INVALID 재질문 횟수(상한 config)."""

    product_id: int
    quantity: int
    options: list[CartOption] = field(default_factory=list)
    attempts: int = 0


class CartStateStore:
    """스레드별 last_reco + pending_add. 키는 신원 스코프(IDOR 방지)."""

    def __init__(self, store: BaseStore | None = None) -> None:
        self._store = store or InMemoryStore()

    def _ns(self, key: str) -> tuple[str, str]:
        return (_NAMESPACE_ROOT, key)

    async def set_last_reco(self, key: str, items: list[tuple[int, str]]) -> None:
        # pg-profile 엔 productId 만 영속(상품명 사본 금지). 이름은 휘발성 캐시에만 둔다.
        await self._store.aput(
            self._ns(key), _LAST_RECO_KEY, {"product_ids": [pid for pid, _ in items]}
        )
        _last_reco_names[key] = {pid: name for pid, name in items}

    async def get_last_reco(self, key: str) -> list[tuple[int, str]]:
        item = await self._store.aget(self._ns(key), _LAST_RECO_KEY)
        if not item:
            return []
        names = _last_reco_names.get(key, {})  # 재시작·타 인스턴스면 미스 → 이름 "" degrade
        return [(pid, names.get(pid, "")) for pid in item.value["product_ids"]]

    async def set_pending(self, key: str, pending: PendingAdd) -> None:
        await self._store.aput(
            self._ns(key),
            _PENDING_KEY,
            {
                "product_id": pending.product_id,
                "quantity": pending.quantity,
                "options": [o.model_dump() for o in pending.options],
                "attempts": pending.attempts,
            },
        )

    async def get_pending(self, key: str) -> PendingAdd | None:
        item = await self._store.aget(self._ns(key), _PENDING_KEY)
        if not item:
            return None
        value = item.value
        return PendingAdd(
            product_id=value["product_id"],
            quantity=value["quantity"],
            options=[CartOption.model_validate(o) for o in value["options"]],
            attempts=value["attempts"],
        )

    async def clear_pending(self, key: str) -> None:
        await self._store.adelete(self._ns(key), _PENDING_KEY)


async def get_cart_store() -> CartStateStore:
    """장바구니 상태 스토어 — pg-profile 공유 연결 백엔드(요청마다 얇은 래퍼 재생성)."""
    return CartStateStore(await pg_store.get_store())


def reset_cart_store() -> None:
    """테스트 격리용 — 공유 pg-profile store(InMemoryStore)와 휘발성 이름 캐시를 비운다."""
    pg_store.reset_store()
    _last_reco_names.clear()
