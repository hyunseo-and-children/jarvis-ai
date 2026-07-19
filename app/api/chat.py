"""구매자 챗봇 엔드포인트 — POST /chat (SSE 스트리밍, FE 직접).

buyer 그래프(SPEC-RECOMMEND-001)를 open_stream 으로 감싸 스트리밍한다. SSE 이벤트명·필드는
api-spec §3.1(camelCase)과 일치하며, 상품 카드는 싣지 않는다(경로 B) —
products.ready 는 {sessionId, listId} 상관키만 나른다.

스트림 수명주기(§2.9 동시 스트림 409·취소·전체/first-token 타임아웃)는 open_stream 이,
레이트 리밋(§2.8)·오류 봉투(§2.5)는 app.main 미들웨어·핸들러가 담당한다. 대화 저장·구조화
로그(§6.3)는 observation(start_observation)이 open_stream 훅으로 붙는다.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agents.buyer.graph import run_buyer_turn
from app.api.deps import get_identity
from app.core.auth import Identity
from app.core.conversation import get_conversation_store
from app.core.errors import get_request_id
from app.core.observability import start_observation
from app.core.stream import open_stream, registry_key
from app.schemas.chat import ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    identity: Identity = Depends(get_identity),
) -> StreamingResponse:
    """구매자 챗봇 SSE 스트리밍 (api-spec §3.1)."""
    observation = start_observation(
        request_id=get_request_id(http_request),
        identity=identity,
        conversation_id=request.session_id,
        message=request.message,
        store=get_conversation_store(),
        now=asyncio.get_running_loop().time(),
    )
    return await open_stream(
        http_request,
        registry_key(identity, request.session_id),
        lambda: run_buyer_turn(request, identity, observer=observation),
        observer=observation,
    )
