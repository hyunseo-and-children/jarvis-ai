"""장바구니 서브그래프 스레드 상태 (이슈 #3).

두 가지를 스레드 스코프(신원 스코프 키)로 보관한다 — 인메모리 placeholder:
  - last_reco    : 직전 추천 후보(productId, name) — "그거 담아줘"의 productId 해소 소스(경로 B라
                   SSE엔 카드가 없으므로 AI가 문맥으로 상품을 확정한다).
  - pending_add  : 옵션 되물음 진행 상태(CART_OPTION_REQUIRED/INVALID) — 다음 턴에서 사용자 답을
                   optionId 로 해석해 재담기(§4.1 멀티턴).
프로덕션은 LangGraph 체크포인터로 이관(§6.3, ThreadFilterStore 와 동일 패턴).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.spring import CartOption


@dataclass
class PendingAdd:
    """옵션 되물음 진행 상태. attempts = CART_OPTION_INVALID 재질문 횟수(상한 config)."""

    product_id: int
    quantity: int
    options: list[CartOption] = field(default_factory=list)
    attempts: int = 0


class CartStateStore:
    """스레드별 last_reco + pending_add. 키는 신원 스코프(IDOR 방지)."""

    def __init__(self) -> None:
        self._last_reco: dict[str, list[tuple[int, str]]] = {}
        self._pending: dict[str, PendingAdd] = {}

    def set_last_reco(self, key: str, items: list[tuple[int, str]]) -> None:
        self._last_reco[key] = items

    def get_last_reco(self, key: str) -> list[tuple[int, str]]:
        return self._last_reco.get(key, [])

    def set_pending(self, key: str, pending: PendingAdd) -> None:
        self._pending[key] = pending

    def get_pending(self, key: str) -> PendingAdd | None:
        return self._pending.get(key)

    def clear_pending(self, key: str) -> None:
        self._pending.pop(key, None)

    def clear(self) -> None:
        self._last_reco.clear()
        self._pending.clear()


_cart_store = CartStateStore()


def get_cart_store() -> CartStateStore:
    """장바구니 상태 스토어 싱글턴."""
    return _cart_store


def reset_cart_store() -> None:
    """테스트 격리용 — last_reco·pending 을 비운다."""
    _cart_store.clear()
