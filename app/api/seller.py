"""판매자 챗봇 엔드포인트 — POST /seller/chat (S-4, api-spec §3.2).

FE 가 Spring 발급 판매자 JWT(role=seller)로 직접 호출한다. 인증 = require_seller
(판매자 스코프·brandId 클레임 검증, 스코프 없으면 403). 신원(sellerId/brandId)은
검증된 JWT 클레임에서만 도출한다 — 요청 본문 신원은 신뢰하지 않는다(IDOR 방지).

MVP 범위(api-spec v0.14.0 §3.2, 결정 20 개정): 통계 Q&A + 상세 수정 draft 흐름.
이벤트는 token / draft / done / error 만 — done.finishReason 은 "stop" 단일.

[4-1b 3분기 배선 + 4-2 HITL 실행] 입구 판정 순서(REALIGN §4 확정):
  ① confirm 코드 선판정(parse_confirm_message, LLM 0회) → _confirm_stream:
     hitl.confirm_draft 가 존재→소유→멱등→TTL 검사 후 resume 실행(I-10/11/12).
  ①.5 추천 적용 선판정(parse_apply_message "N번 적용해줘", LLM 0회) →
     _apply_stream: 이력 recommendations[N-1] → draft 변환(4-3 §6.3).
  ② scope 선차단(check_scope, LLM 0회).
  ③ supervisor 라우팅(route_question — 장애 시 general 폴백은 함수 내부).
분기: analysis → run_analysis_pipeline(emit 큐 중계, 예외 2경우만 사과+error) /
product → draft 검증(validate_draft)·checkpoint 저장(start_draft)·draft emit /
general → 기존 astream 스트림.
스트림 수명주기(409·취소·타임아웃 §2.9 공통)는 구현 TODO — app/api/chat.py 참고.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage

from app.agents.seller.context import SellerContext
from app.agents.seller.history import apply_recommendation
from app.agents.seller.hitl import DraftRecord, confirm_draft, start_draft, validate_draft
from app.agents.seller.middleware import check_scope, mask_output
from app.agents.seller.orchestrator import route_question, run_analysis_pipeline
from app.agents.seller.pipeline import parse_apply_message, parse_confirm_message
from app.agents.seller.schemas import DraftProposal
from app.agents.seller.workers import build_general_agent, build_product_agent
from app.api.deps import require_seller
from app.core.auth import Identity
from app.core.config import get_settings
from app.core.conversation import get_conversation_store
from app.core.errors import get_request_id
from app.core.observability import start_observation
from app.core.stream import open_stream, registry_key
from app.schemas.chat import ChatRequest, DoneData, ErrorData, TokenData
from app.services.spring_client import SpringUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["seller"])

# confirm 실행 중 Spring 장애 안내 — draft 는 interrupt 에 남아 재confirm 가능(4-2).
_CONFIRM_SPRING_DOWN_TOKEN = (
    "죄송합니다. 상품 서버와 통신이 원활하지 않아 반영하지 못했습니다. "
    "초안은 유지되니 잠시 후 같은 승인 요청을 다시 보내주세요."
)

# 분석 파이프라인 예외 2경우(planner 장애·1차 report 실패)의 사과 문구(§7).
_ANALYSIS_APOLOGY_TOKEN = (
    "죄송합니다. 분석 처리 중 문제가 발생해 답변을 완성하지 못했습니다. 잠시 후 다시 시도해 주세요."
)

# 진행 token 큐 종료 신호 — 파이프라인 완료(정상/예외 공통)를 스트림 루프에 알린다.
_PIPELINE_DONE = object()


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, "data": data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _token(text: str) -> str:
    return _sse("token", TokenData(text=text).model_dump(by_alias=True))


def _done() -> str:
    return _sse("done", DoneData(finish_reason="stop").model_dump(by_alias=True))


def _chunk_text(content: object) -> str:
    """AIMessageChunk.content → 텍스트 증분.

    Anthropic 은 str 또는 블록 리스트를 준다 — text 블록만 취하고 tool_use
    블록(도구 호출 인자)은 사용자 스트림에 흘리지 않는다.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


async def _general_stream(request: ChatRequest, identity: Identity) -> AsyncIterator[str]:
    """general_agent astream → token/done (3-7 — SPEC §7 수명주기·degrade).

    - C1(REVIEW-SELLER-STAGE2): build_general_agent 는 **요청마다 재빌드** —
      빌드 시점 today 박제가 장기 실행 서버에서 stale 해지는 것을 방지한다.
    - scope 선차단: 미들웨어(end 점프)가 주입하는 거절 메시지는 astream
      messages 모드에서 모델 청크로 흐르지 않으므로, 코드에서 같은 판정점
      (check_scope)으로 거절 문안을 직접 token emit 한다.
    - 출력 검사(§10-⑥): mask_output 을 청크 단위 적용 — 패턴이 청크 경계에
      걸치면 놓칠 수 있는 한계가 있다(HANDOFF 기록, 4단계 개선 후보).
    - 오류: 스트림 내부 실패는 error 이벤트(LLM_TIMEOUT/INTERNAL) 후 종료(§2.7).
    """
    refusal = check_scope(request.message)
    if refusal:
        yield _token(refusal)
        yield _done()
        return

    try:
        # 빌드도 try 안 — 실패 시 error 이벤트 봉투로 종료(마감 리뷰 M2 반영).
        agent = build_general_agent(today=date.today().isoformat())
        context = SellerContext(
            seller_id=identity.seller_id or "", brand_id=identity.brand_id or ""
        )
        async for item in agent.astream(
            {"messages": [HumanMessage(content=request.message)]},
            context=context,
            stream_mode="messages",
        ):
            message_chunk = item[0] if isinstance(item, tuple) else item
            if not isinstance(message_chunk, AIMessageChunk):
                continue
            text = _chunk_text(message_chunk.content)
            if text:
                yield _token(mask_output(text))
        yield _done()
    except (TimeoutError, asyncio.TimeoutError):
        yield _sse(
            "error",
            ErrorData(code="LLM_TIMEOUT", message="응답 생성이 지연되어 중단됐습니다.").model_dump(
                by_alias=True
            ),
        )
    except Exception:
        logger.exception("판매자 general 스트림 실패 (thread=%s)", request.thread_id)
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="일시적인 오류가 발생했습니다.").model_dump(
                by_alias=True
            ),
        )


async def _analysis_stream(request: ChatRequest, context: SellerContext) -> AsyncIterator[str]:
    """분석 레인 (4-1b) — 파이프라인 emit(진행 token)을 큐로 SSE 에 중계한다.

    - PipelineResult 는 kind 무관 `text→token→done` 단일 계약(3-5 확정).
    - **예외 2경우**(planner 장애·1차 report 실패)만 여기로 전파된다 —
      사과 token 후 error 이벤트로 종료(REVIEW-STAGE3 §5-2 매핑).
    - 진행 token 은 파이프라인 내부 상수 문구라 마스킹 불필요, 최종 text 는
      mask_output 적용(§10-⑥ — 쓰기 직전, 청크 경계 한계는 단일 문자열이라 없음).
    """
    queue: asyncio.Queue[object] = asyncio.Queue()

    async def emit(text: str) -> None:
        await queue.put(text)

    pipeline_task = asyncio.create_task(
        run_analysis_pipeline(request.message, context, today=date.today(), emit=emit)
    )
    # 정상/예외 공통으로 sentinel 을 넣어 진행 token 루프를 반드시 끝낸다.
    pipeline_task.add_done_callback(lambda _t: queue.put_nowait(_PIPELINE_DONE))

    while True:
        item = await queue.get()
        if item is _PIPELINE_DONE:
            break
        yield _token(str(item))

    try:
        result = await pipeline_task
    except (TimeoutError, asyncio.TimeoutError):
        yield _token(_ANALYSIS_APOLOGY_TOKEN)
        yield _sse(
            "error",
            ErrorData(code="LLM_TIMEOUT", message="분석 응답이 지연되어 중단됐습니다.").model_dump(
                by_alias=True
            ),
        )
        return
    except Exception:
        logger.exception("분석 파이프라인 실패 (thread=%s)", request.thread_id)
        yield _token(_ANALYSIS_APOLOGY_TOKEN)
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="일시적인 오류가 발생했습니다.").model_dump(
                by_alias=True
            ),
        )
        return
    yield _token(mask_output(result.text))
    yield _done()


async def _product_stream(request: ChatRequest, context: SellerContext) -> AsyncIterator[str]:
    """product 레인 (4-2 — draft 생성 + checkpoint 저장, 실행은 confirm 스트림).

    product_agent(2-7)로 DraftProposal 을 만들고 validate_draft(코드 선검증 —
    캐스팅·필수 필드·C4)를 통과하면 start_draft 로 checkpoint 에 저장(interrupt
    대기)한 뒤 SSE `draft` 이벤트로 내보낸다. clarification·검증 불성립은
    되묻기 token. 실행은 스트림 2(_confirm_stream — hitl.confirm_draft) 소관.
    check_scope 는 입구 ②에서 이미 수행됨(구조화 레인 코드 경로 — 배정표 준수).
    """
    settings = get_settings()
    try:
        agent = build_product_agent()
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": [HumanMessage(content=request.message)]}, context=context),
            timeout=settings.seller_worker_timeout_s,
        )
        proposal = result.get("structured_response")
        if not isinstance(proposal, DraftProposal):
            raise TypeError("product_agent 가 DraftProposal 을 반환하지 않았다")
    except (TimeoutError, asyncio.TimeoutError):
        yield _sse(
            "error",
            ErrorData(code="LLM_TIMEOUT", message="초안 생성이 지연되어 중단됐습니다.").model_dump(
                by_alias=True
            ),
        )
        return
    except Exception:
        logger.exception("product draft 생성 실패 (thread=%s)", request.thread_id)
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="일시적인 오류가 발생했습니다.").model_dump(
                by_alias=True
            ),
        )
        return

    if proposal.clarification:
        yield _token(mask_output(proposal.clarification))
        yield _done()
        return

    # 코드 선검증(4-2) — 실행 불가능한 draft 는 FE 에 보여주기 전에 되묻는다.
    record, problem = validate_draft(
        proposal, seller_id=context.seller_id, brand_id=context.brand_id
    )
    if record is None:
        yield _token(mask_output(problem or "초안을 만들지 못했습니다. 다시 요청해 주세요."))
        yield _done()
        return

    try:
        await start_draft(record)  # checkpoint 저장 + interrupt 대기(안전장치 ①)
    except Exception:
        logger.exception("draft checkpoint 저장 실패 (thread=%s)", request.thread_id)
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="일시적인 오류가 발생했습니다.").model_dump(
                by_alias=True
            ),
        )
        return

    yield _draft_event(record)
    yield _done()


def _draft_event(record: DraftRecord) -> str:
    """DraftRecord → SSE draft 이벤트 (product 레인·추천 적용 레인 공용, api-spec §3.2)."""
    return _sse(
        "draft",
        {
            "draftId": record.draft_id,
            "op": record.op,
            "productId": record.product_id,  # int | None(create) — F2 숫자 확정
            "changes": [
                {"field": c.field, "before": c.before, "after": c.after} for c in record.changes
            ],
            "summary": mask_output(record.summary),
        },
    )


async def _apply_stream(n: int, request: ChatRequest, context: SellerContext) -> AsyncIterator[str]:
    """추천 적용 레인 (4-3 §6.3 — 입구 ①.5 코드 선판정 후 진입, LLM 0회).

    최신 이력의 recommendations[N-1] 을 코드가 draft 로 변환(대화 재해석 금지) →
    4-2 와 동일하게 checkpoint 저장 후 draft emit — 이후 confirm 흐름 합류.
    불성립(이력 없음·인덱스 불일치·적용 불가 유형·상품 미발견)은 되묻기 token.
    """
    try:
        record, problem = await apply_recommendation(n, context)
    except SpringUnavailableError:
        yield _token(
            "죄송합니다. 상품 정보를 확인하지 못해 추천을 적용할 수 없었습니다. "
            "잠시 후 다시 시도해 주세요."
        )
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="상품 서버 통신에 실패했습니다.").model_dump(
                by_alias=True
            ),
        )
        return
    except Exception:
        logger.exception("추천 적용 처리 실패 (thread=%s, n=%d)", request.thread_id, n)
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="일시적인 오류가 발생했습니다.").model_dump(
                by_alias=True
            ),
        )
        return

    if record is None:
        yield _token(mask_output(problem or "추천을 적용하지 못했습니다. 다시 요청해 주세요."))
        yield _done()
        return

    try:
        await start_draft(record)  # 4-2 재사용 — draftId↔checkpoint 바인딩
    except Exception:
        logger.exception("추천 적용 draft 저장 실패 (thread=%s)", request.thread_id)
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="일시적인 오류가 발생했습니다.").model_dump(
                by_alias=True
            ),
        )
        return

    yield _draft_event(record)
    yield _done()


async def _confirm_stream(draft_id: str, identity: Identity) -> AsyncIterator[str]:
    """confirm 레인 (4-2 스트림 2) — 코드 검사 후 resume 실행, LLM 0회.

    hitl.confirm_draft 가 존재→소유→멱등→TTL 검사를 통과한 경우에만 그래프를
    resume 해 I-10/11/12 를 실행한다. 모든 결과(executed/stale/만료/멱등/미존재)는
    token+done — Spring 장애만 사과 token + error(INTERNAL, draft 유지·재시도 가능).
    """
    try:
        outcome = await confirm_draft(
            draft_id, seller_id=identity.seller_id or "", brand_id=identity.brand_id or ""
        )
    except SpringUnavailableError:
        yield _token(_CONFIRM_SPRING_DOWN_TOKEN)
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="상품 서버 통신에 실패했습니다.").model_dump(
                by_alias=True
            ),
        )
        return
    except Exception:
        logger.exception("confirm 처리 실패 (draftId=%s)", draft_id)
        yield _sse(
            "error",
            ErrorData(code="INTERNAL", message="일시적인 오류가 발생했습니다.").model_dump(
                by_alias=True
            ),
        )
        return
    yield _token(mask_output(outcome.text))
    yield _done()


async def _seller_stream(request: ChatRequest, identity: Identity) -> AsyncIterator[str]:
    """판매자 챗 통합 스트림 (4-1b) — 입구 판정 ①②③ 후 3분기 위임."""
    # ① confirm 코드 선판정 (F3 확정 형식, LLM 0회) → HITL 실행 레인(4-2).
    draft_id = parse_confirm_message(request.message)
    if draft_id:
        async for line in _confirm_stream(draft_id, identity):
            yield line
        return

    # ①.5 추천 적용 코드 선판정 ("N번 적용해줘" 정형 발화, LLM 0회) — 4-3 §6.3.
    apply_n = parse_apply_message(request.message)
    if apply_n is not None:
        apply_context = SellerContext(
            seller_id=identity.seller_id or "", brand_id=identity.brand_id or ""
        )
        async for line in _apply_stream(apply_n, request, apply_context):
            yield line
        return

    # ② scope 선차단 (LLM 0회) — 전 레인 공통 코드 경로.
    refusal = check_scope(request.message)
    if refusal:
        yield _token(refusal)
        yield _done()
        return

    context = SellerContext(seller_id=identity.seller_id or "", brand_id=identity.brand_id or "")

    # ③ supervisor 라우팅 — 장애 시 general 폴백은 route_question 내부(4-1a).
    decision = await route_question(request.message, context)
    logger.info(
        "판매자 라우팅: %s (confidence=%.2f, thread=%s) — %s",
        decision.category,
        decision.confidence,
        request.thread_id,
        decision.reason,
    )

    if decision.category == "analysis":
        async for line in _analysis_stream(request, context):
            yield line
    elif decision.category == "product":
        async for line in _product_stream(request, context):
            yield line
    else:
        async for line in _general_stream(request, identity):
            yield line


@router.post("/seller/chat")
async def seller_chat(
    request: ChatRequest,
    http_request: Request,
    identity: Identity = Depends(require_seller),
) -> StreamingResponse:
    """판매자 챗봇 SSE 스트리밍 (S-4, api-spec §3.2).

    신원(sellerId/brandId)은 require_seller 가 검증된 판매자 JWT 클레임에서
    확보한다(스코프 없으면 403). 4-1b 부터 supervisor 3분기 디스패치가 배선됐다.

    [합류 2026-07-20 rebase] 스트림 수명주기(§2.9)는 팀 공통 래퍼 open_stream 소관 —
    (a) sessionId 당 동시 1스트림(409) (b) 연결 종료 취소 (c) first-token/전체 타임아웃.
    대화 저장·구조화 로그(obs #8)는 start_observation 이 담당한다(chat 과 동일 패턴).
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
        lambda: _seller_stream(request, identity),
        observer=observation,
    )
