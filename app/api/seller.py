"""판매자 챗봇 엔드포인트 — POST /seller/chat (SSE 스트리밍, FE 직접).

MVP 범위(api-spec v0.15.0 §3.2, 결정 20 개정): 통계 Q&A + 상세 수정 draft 흐름.
이벤트는 token / draft / done / error 만 — done.finishReason 은 "stop" 단일.
판매자 스코프(role==seller) 없는 토큰은 require_seller 의존성이 403 으로 거부한다.

[변경 v0.4.0+] 데이터 소스 = Spring I-6 집계 콜백(spring_client.get_seller_aggregates,
§4.4, C-13) — 구 order_seed 시드 폐기. draft 는 I-9 목록 읽기(§4.5) → SSE draft → confirm →
AI 가 HITL 승인 후 I-11 등 직접 반영. 스트림 수명주기(409·취소·타임아웃)는 §2.9 공통
— 구현 TODO 는 app/api/chat.py 참고.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import require_seller
from app.core.auth import Identity
from app.schemas.chat import ChatRequest, DoneData, TokenData

router = APIRouter(tags=["seller"])


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, "data": data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stub_stream(request: ChatRequest, identity: Identity) -> AsyncIterator[str]:
    """판매자 스텁 스트림 (token → done).

    TODO(seller graph SPEC): (1) 통계 Q&A — I-6 집계 콜백(get_seller_aggregates, §4.4) 연결.
    (2) draft 흐름 — I-9 목록 읽기(get_product_detail, §4.5) → LLM 개정안 → draft 이벤트
    {productId, changes:[{field,before,after}]} emit (§3.2).
    """
    yield _sse(
        "token",
        TokenData(text="(stub) 판매자 통계 Q&A 파이프라인 연결 전입니다.").model_dump(by_alias=True),
    )
    yield _sse("done", DoneData(finish_reason="stop").model_dump(by_alias=True))


@router.post("/seller/chat")
async def seller_chat(
    request: ChatRequest,
    identity: Identity = Depends(require_seller),
) -> StreamingResponse:
    """판매자 챗봇 SSE 스트리밍 (api-spec §3.2). role==seller 필수."""
    return StreamingResponse(
        _stub_stream(request, identity),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
