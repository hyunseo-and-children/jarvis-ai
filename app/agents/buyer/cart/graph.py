"""장바구니 서브그래프 스트리밍 (결정 7 / api-spec §4.1 담기·§4.9 조회, 이슈 #3).

담기: (상품·옵션·수량) 의도 확정 → add_to_cart(I-2, 단건) → SSE action.
      옵션 필수(CART_OPTION_REQUIRED)면 실패 action 없이 token 되물음 → pending 저장 →
      다음 턴 사용자 답을 optionId 로 해석해 재담기(§4.1 멀티턴). 담기 전 get_cart(§4.9)로
      기존 보유를 확인해 합산 안내(조회 실패 시에도 담기 진행, degrade).
조회: get_cart(I-18) → token 텍스트 답변(별도 이벤트 없음, §3.1).
게스트 담기 허용(userId|guestId, §4.1) — 신원은 JWT sub 유래(요청 본문 불신).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import ValidationError

from app.agents.buyer._frames import sse
from app.agents.buyer.cart.state import CartStateStore, PendingAdd
from app.agents.buyer.recommendation.state import CartIntent
from app.core.text import _strip_unsafe
from app.schemas.chat import ActionData, DoneData, TokenData
from app.schemas.spring import AddToCartRequest, CartOption
from app.services import spring_client
from app.services.spring_client import (
    CartError,
    CartOptionInvalid,
    CartOptionRequired,
    CartProductNotFound,
    SpringUnavailableError,
)


def cart_identity(identity) -> tuple[int | None, str | None]:
    """신원 → (userId, guestId). 회원=userId(숫자), 게스트=guestId(UUID), 익명(dev 무토큰)=둘 다 None.

    user_id 는 JWT sub 원문 문자열이라 숫자 보장이 없다 — 비숫자면 익명 취급((None, None))해
    상위가 CART_ERROR 로 우아하게 처리하게 한다(미처리 ValueError 로 스트림이 죽지 않게).
    """
    if not identity.is_guest and identity.user_id:
        try:
            return int(identity.user_id), None
        except (ValueError, TypeError):
            return None, None
    if identity.is_guest and identity.subject:
        return None, identity.subject
    return None, None


def _options_text(options: list[CartOption]) -> str:
    """옵션 목록을 되물음 문구로 나열한다 — 추가금(extraPrice)이 있으면 함께 표시."""
    parts: list[str] = []
    for opt in options:
        if not opt.name:
            continue
        # extraPrice 는 api-spec §4.1 상 surcharge(≥0) — 양수만 표시. 0/음수(계약 미정의)는 미표시.
        if opt.extra_price and opt.extra_price > 0:
            parts.append(f"{opt.name}(+{opt.extra_price:,}원)")
        else:
            parts.append(opt.name)
    return _strip_unsafe(" / ".join(parts)) if parts else "옵션"


def _done() -> str:
    return sse("done", DoneData(finish_reason="stop").model_dump(by_alias=True))


async def stream_cart_add(
    *,
    identity,
    cart: CartIntent,
    cart_store: CartStateStore,
    thread_key: str,
    settings,
    allowed_product_ids: set[int] | None = None,
    add_fn=None,
    get_cart_fn=None,
    observer=None,
) -> AsyncIterator[str]:
    """담기 서브그래프. action(CART_ADDED/CART_ADD_FAILED) 또는 옵션 되물음 token 을 낸다."""
    add_fn = add_fn or spring_client.add_to_cart
    get_cart_fn = get_cart_fn or spring_client.get_cart

    user_id, guest_id = cart_identity(identity)
    if user_id is None and guest_id is None:
        yield sse(
            "action",
            ActionData(
                type="CART_ADD_FAILED", message="담기에는 로그인이 필요해요.", reason="CART_ERROR"
            ).model_dump(by_alias=True),
        )
        yield _done()
        return

    # 되물음 진행 중이라도 사용자가 **다른 추천 상품**으로 전환하면 pending 을 버리고 새 담기로 처리한다
    # (옛 상품 옵션 되물음에 갇히지 않게 — decompose 가 전환을 새 productId 로 신호).
    pending = await cart_store.get_pending(thread_key)
    if (
        pending is not None
        and cart.product_id is not None
        and cart.product_id != pending.product_id
        and (allowed_product_ids is None or cart.product_id in allowed_product_ids)
    ):
        await cart_store.clear_pending(thread_key)
        pending = None
    if pending is not None:
        product_id: int | None = pending.product_id
        # 옵션 답변과 함께 수량을 다시 말하면("레드로 5개") 새 수량을 우선한다(기본 1이면 pending 유지).
        # 단 이번 턴이 pending 상품을 겨냥할 때만 — 순수 옵션 답변(productId=None)이거나 같은 상품일 때.
        # (다른/미추천 상품을 가리켜 전환이 성립 안 한 경우의 수량을 옛 상품에 잘못 적용하지 않게.)
        same_target = cart.product_id is None or cart.product_id == pending.product_id
        quantity = cart.quantity if (cart.quantity != 1 and same_target) else pending.quantity
        attempts = pending.attempts
    else:
        product_id = cart.product_id
        quantity = cart.quantity
        attempts = 0
    option_id = cart.option_id

    # 경로 B — SSE에 카드가 없어 문맥으로 상품을 확정한다. 신규 담기는 직전 추천(last_reco)에 있는
    # productId 만 허용(LLM 이 발화 속 임의 숫자를 오추출해 추천 안 된 상품을 담는 것 차단). 되물음
    # 진행(pending) 중이면 이미 검증된 상품이므로 예외.
    unresolved = product_id is None or (
        pending is None
        and allowed_product_ids is not None
        and product_id not in allowed_product_ids
    )
    if unresolved:
        yield sse(
            "token",
            TokenData(text="어떤 상품을 담을까요? 추천을 먼저 받아보시면 담아드릴게요.").model_dump(
                by_alias=True
            ),
        )
        yield _done()
        return

    # 담기 전 기존 보유 확인(안내용, degrade) — 동일 상품·옵션 보유 수량.
    existing = 0
    try:
        cart_view = await get_cart_fn(user_id=user_id, guest_id=guest_id)
        for item in cart_view.items:
            if item.product_id == product_id and (option_id is None or item.option_id == option_id):
                existing += item.quantity
    except SpringUnavailableError:
        pass  # 조회 실패해도 담기는 진행(§4.9)

    try:
        req = AddToCartRequest(
            user_id=user_id,
            guest_id=guest_id,
            product_id=product_id,
            option_id=option_id,
            quantity=quantity,
        )
        result = await add_fn(req)
    except CartOptionRequired as exc:
        # api-spec §4.1 — REQUIRED 는 **상한 없는 되물음 멀티턴**(사용자가 옵션을 아직 안 준 정상 흐름).
        # 각 되물음은 사용자 입력을 요구하므로 서버 무한 루프가 아니다. INVALID 카운터(attempts)는
        # 리셋하지 않고 보존해 사이에 끼어도 INVALID 상한이 유지되게 한다.
        await cart_store.set_pending(
            thread_key,
            PendingAdd(
                product_id=product_id, quantity=quantity, options=exc.options, attempts=attempts
            ),
        )
        yield sse(
            "token",
            TokenData(
                text=f"옵션을 선택해 주세요: {_options_text(exc.options)}. 어떤 걸로 담을까요?"
            ).model_dump(by_alias=True),
        )
        yield _done()
        return
    except CartOptionInvalid as exc:
        # api-spec §4.1 — INVALID 는 재시도 상한(config cart_option_reask_max, 기본 1) 후 CART_ERROR.
        new_attempts = attempts + 1
        if new_attempts > settings.cart_option_reask_max:
            await cart_store.clear_pending(thread_key)
            yield sse(
                "action",
                ActionData(
                    type="CART_ADD_FAILED",
                    message="옵션을 확인하지 못했어요. 다시 시도해 주세요.",
                    reason="CART_ERROR",
                ).model_dump(by_alias=True),
            )
            yield _done()
            return
        await cart_store.set_pending(
            thread_key,
            PendingAdd(
                product_id=product_id, quantity=quantity, options=exc.options, attempts=new_attempts
            ),
        )
        yield sse(
            "token",
            TokenData(
                text=f"그 옵션을 찾지 못했어요. 다시 골라 주세요: {_options_text(exc.options)}"
            ).model_dump(by_alias=True),
        )
        yield _done()
        return
    except CartProductNotFound:
        await cart_store.clear_pending(thread_key)
        yield sse(
            "action",
            ActionData(
                type="CART_ADD_FAILED",
                message="해당 상품을 찾지 못했어요.",
                reason="PRODUCT_NOT_FOUND",
            ).model_dump(by_alias=True),
        )
        yield _done()
        return
    except (CartError, SpringUnavailableError, ValidationError):
        await cart_store.clear_pending(thread_key)
        yield sse(
            "action",
            ActionData(
                type="CART_ADD_FAILED",
                message="장바구니에 담지 못했어요. 잠시 후 다시 시도해 주세요.",
                reason="CART_ERROR",
            ).model_dump(by_alias=True),
        )
        yield _done()
        return

    # 성공 — 되물음 상태 정리 + 합산 안내.
    await cart_store.clear_pending(thread_key)
    if existing > 0:
        message = "이미 담겨 있던 상품이라 수량을 더했어요."
    else:
        message = "장바구니에 담았어요."
    yield sse(
        "action",
        ActionData(type="CART_ADDED", message=message, cart_item_id=result.cart_item_id).model_dump(
            by_alias=True
        ),
    )
    yield _done()


async def stream_cart_view(*, identity, get_cart_fn=None, observer=None) -> AsyncIterator[str]:
    """조회 서브그래프. 장바구니 내용을 token 텍스트로 답한다(§4.9, 별도 이벤트 없음)."""
    get_cart_fn = get_cart_fn or spring_client.get_cart
    user_id, guest_id = cart_identity(identity)
    if user_id is None and guest_id is None:
        yield sse(
            "token",
            TokenData(text="장바구니를 보려면 로그인이 필요해요.").model_dump(by_alias=True),
        )
        yield _done()
        return

    try:
        cart_view = await get_cart_fn(user_id=user_id, guest_id=guest_id)
    except SpringUnavailableError:
        yield sse(
            "token",
            TokenData(text="장바구니를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.").model_dump(
                by_alias=True
            ),
        )
        yield _done()
        return

    if not cart_view.items:
        yield sse("token", TokenData(text="장바구니가 비어 있어요.").model_dump(by_alias=True))
        yield _done()
        return

    lines = []
    for item in cart_view.items:
        product_name = _strip_unsafe(item.product_name or "상품")
        option_name = _strip_unsafe(item.option_name) if item.option_name else ""
        opt = f" ({option_name})" if option_name else ""
        lines.append(f"{product_name}{opt} · {item.quantity}개")
    text = "장바구니에 담긴 상품이에요:\n" + "\n".join(lines)
    yield sse("token", TokenData(text=text).model_dump(by_alias=True))
    yield _done()
