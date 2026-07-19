"""구매자 챗봇 엔드포인트 — POST /chat (SSE 스트리밍, FE 직접).

MVP 스캐폴드: 실제 추천 서브그래프(SPEC-RECOMMEND-001) 연결 전 스텁을 스트리밍한다.
SSE 이벤트명·필드는 api-spec v0.7.0 §3.1(CH-2 명명, camelCase)과 일치.
SSE 는 상품 카드를 싣지 않는다 (경로 B). 스텁 순서:
    token → conditions → products.ready({sessionId, listId}) → done({finishReason:"stop"}).

TODO(v0.7.0 §2.9 스트림 수명주기 — 미구현):
  - 동시 스트림 제한: sessionId 당 활성 스트림 1개, 초과 시 409 STREAM_IN_PROGRESS(§2.5 봉투).
    in-memory 레지스트리(MVP 단일 인스턴스 전제 — 다중 인스턴스 시 Redis 이관).
  - 취소 = 연결 종료: 이벤트 전송 사이 request.is_disconnected() 폴링 → 감지 즉시
    LLM 스트림 close(토큰 비용 차단) + LangGraph task 취소. 별도 취소 엔드포인트 없음.
  - 타임아웃(config): first-token 10s(초과 시 504/UPSTREAM_TIMEOUT 또는 in-stream error),
    스트림 전체 상한 90s(done 강제 종료).
  - 레이트 리밋(§2.8): FastAPI 미들웨어 + in-memory, 분당 10/시간당 100(config), 429.
TODO(§6.3 운영 요구 — 미구현): 대화 저장(user 수신 즉시 / assistant 완료 후,
  COMPLETED|FAILED|CANCELLED, 부분 텍스트 보존) + 구조화 로그(requestId·first-token/total
  latency·model·tokens·errorType, message 원문 로깅 금지).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_identity
from app.core.auth import Identity
from app.core.conversation import get_conversation_store
from app.core.errors import get_request_id
from app.core.observability import start_observation
from app.core.stream import open_stream, registry_key
from app.schemas.chat import (
    ChatRequest,
    ConditionChip,
    ConditionsData,
    DoneData,
    ProductsReadyData,
    TokenData,
)

router = APIRouter(tags=["chat"])


def _sse(event_type: str, data: dict) -> str:
    """SSE `data:` 프레임 1줄 직렬화. 각 이벤트는 {type, data} JSON 이다 (api-spec §3.1)."""
    payload = {"type": event_type, "data": data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stub_stream(request: ChatRequest, identity: Identity) -> AsyncIterator[str]:
    """추천 파이프라인 연결 전 스텁 스트림 (api-spec §3.1 이벤트 순서 준수, 카드 없음).

    TODO(SPEC-RECOMMEND-001): buyer graph 진입 → 프로필 read → intent router →
    recommendation 서브그래프(decompose→Spring 검색(§4.2)→rerank→목록 push(§4.3)) 연결.
    push 성공 후에만 products.ready 를 emit 한다.
    """
    # (1) token — 근거/코멘트 토큰 증분
    yield _sse("token", TokenData(text="(stub) 추천 파이프라인 연결 전입니다.").model_dump(by_alias=True))

    # (2) conditions — 추출 필터 조건 칩 (FE 제거 가능). 최소 예시 1건.
    conditions = ConditionsData(
        chips=[ConditionChip(field="category", label="예시 카테고리", value="예시/카테고리")]
    )
    yield _sse("conditions", conditions.model_dump(by_alias=True))

    # (3) products.ready — 목록 push 성공 상관관계 키 (카드 없음). listId 는 스텁 고정값.
    ready = ProductsReadyData(session_id=request.session_id, list_id="stub-list-1")
    yield _sse("products.ready", ready.model_dump(by_alias=True))

    # (4) done — 정상 종료
    yield _sse("done", DoneData(finish_reason="stop").model_dump(by_alias=True))


@router.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    identity: Identity = Depends(get_identity),
) -> StreamingResponse:
    """구매자 챗봇 SSE 스트리밍 (api-spec §3.1).

    스트림 수명주기(§2.9 동시 스트림 409·취소·전체/first-token 타임아웃)는
    open_stream 이 감싼다. 레이트 리밋(§2.8)·오류 봉투(§2.5)는 app.main 미들웨어·핸들러.
    """
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
        lambda: _stub_stream(request, identity),
        observer=observation,
    )
