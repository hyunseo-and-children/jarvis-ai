"""판매자 챗봇 엔드포인트 — POST /seller/chat (S-4, api-spec §3.2).

FE 가 Spring 발급 판매자 JWT(role=seller)로 직접 호출한다. 인증 = require_seller
(판매자 스코프·brandId 클레임 검증, 스코프 없으면 403). 신원(sellerId/brandId)은
검증된 JWT 클레임에서만 도출한다 — 요청 본문 신원은 신뢰하지 않는다(IDOR 방지).

MVP 범위(api-spec v0.14.0 §3.2, 결정 20 개정): 통계 Q&A + 상세 수정 draft 흐름.
이벤트: meta / token / progress / draft / done / error — done.finishReason 은 "stop" 단일.
  · meta(lane)  : 매 스트림 첫 프레임(FE 화면 전환 레인, 2026-07-22 B).
  · progress    : 분석 진행 상태(로딩 표시, 최종 답변 아님).
  · done(panel) : 우측 패널 조치(replace/keep/refresh) — FE 요구 1~3.

[4-1b 3분기 배선 + 4-2 HITL 실행] 입구 판정 순서(REALIGN §4 확정):
  ① confirm 필드 선판정(request.action=="confirm", LLM 0회) → _confirm_stream:
     hitl.confirm_draft 가 존재→소유→멱등→TTL 검사 후 resume 실행(I-10/11/12).
     [2026-07-22 A-2] 승인은 최상위 action/draftId 구조화 필드로 받는다(발화≠동의).
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
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage
from pydantic.alias_generators import to_camel

from app.agents.seller.context import SellerContext
from app.agents.seller.history import apply_recommendation
from app.agents.seller.hitl import DraftRecord, confirm_draft, start_draft, validate_draft
from app.agents.seller.middleware import check_scope, mask_output
from app.agents.seller.orchestrator import route_question, run_analysis_pipeline
from app.agents.seller.pipeline import parse_apply_message
from app.agents.seller.schemas import DraftProposal
from app.agents.seller.workers import build_general_agent, build_product_agent
from app.api.deps import require_seller
from app.core.auth import Identity
from app.core.config import get_settings
from app.core.conversation import get_conversation_store
from app.core.errors import get_request_id
from app.core.observability import emit_rejection, start_observation
from app.core.stream import open_stream, registry_key
from app.core.text import _strip_unsafe, _strip_unsafe_multiline
from app.schemas.chat import ErrorData, TokenData
from app.schemas.seller import SellerChatRequest
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
    visible = mask_output(_strip_unsafe_multiline(text))
    return _sse("token", TokenData(text=visible).model_dump(by_alias=True))


def _token_chunk(text: str, *, previous_ended_space: bool) -> tuple[str | None, bool]:
    """스트리밍 청크를 정제하되 청크 경계의 정상 공백을 잃지 않는다.

    `_strip_unsafe`의 양끝 strip이 토큰 사이 공백까지 없애지 않도록 센티널로 감싸
    청크 내부 공백을 접고, 직전 청크와 맞닿은 중복 공백만 제거한다.
    """
    framed = _strip_unsafe_multiline(f"\ue000{text}\ue001")
    cleaned = framed[1:-1]
    if previous_ended_space and cleaned.startswith(" "):
        cleaned = cleaned[1:]
    if not cleaned:
        return None, previous_ended_space
    visible = mask_output(cleaned)
    frame = _sse("token", TokenData(text=visible).model_dump(by_alias=True))
    return frame, visible.endswith(" ")


# ── 화면 전환 신호 (FE 계약 B, 2026-07-22 — 판매자 스트림 전용, 구매자 무관) ──────────
#
# 판매자 대시보드는 좌(채팅)/우(패널) 분할이다. 서버가 질문을 분기(analysis/product/
# general/confirm/apply/refused)해도 그 결과를 FE 가 알 수 없어 "우측 패널을 바꿀지"를
# 판단하지 못했다(FE 요구 1~3). 아래 두 신호로 해소한다:
#   · meta(lane)   : 매 스트림 첫 프레임. FE 가 레인을 즉시 알아 로딩 상태를 준비한다.
#   · done(panel)  : 종료 시 패널 조치를 확정한다 — replace(패널 교체)/keep(유지)/refresh(재조회).
# analysis 진행 상태는 최종 답변이 아니므로 token 이 아니라 progress 로 분리한다.

# 레인(meta.lane) — supervisor 3분기 + 코드 선판정 3종.
Lane = Literal["analysis", "product", "general", "confirm", "apply", "refused"]
# 패널 조치(done.panel) — 우측 패널을 어떻게 할지 FE 에 지시.
Panel = Literal["replace", "keep", "refresh"]


def _meta(lane: Lane) -> str:
    """스트림 첫 프레임 — FE 가 우측 패널 처리 레인을 즉시 알도록 한다(요구 1~3)."""
    return _sse("meta", {"lane": lane})


def _progress(text: str) -> str:
    """분석 진행 상태 — 최종 답변이 아니라 로딩 표시용(FE: 임시 텍스트, 답변에서 제외)."""
    return _sse("progress", {"text": text})


def _done(panel: Panel = "keep") -> str:
    """종료 프레임 — finishReason 은 판매자 스트림에서 stop 단일. panel 은 우측 패널 조치.

    구매자 DoneData(app/schemas/chat.py)는 건드리지 않는다 — 판매자 전용 필드라
    여기서 직접 페이로드를 구성한다(camelCase 규약 유지).
    """
    return _sse("done", {"finishReason": "stop", "panel": panel})


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


async def _general_stream(request: SellerChatRequest, identity: Identity) -> AsyncIterator[str]:
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
    # general 은 항상 대화(우측 패널 유지) — 첫 프레임에 레인을 알린다.
    yield _meta("general")
    refusal = check_scope(request.message)
    if refusal:
        yield _token(refusal)
        yield _done("keep")
        return

    try:
        # 빌드도 try 안 — 실패 시 error 이벤트 봉투로 종료(마감 리뷰 M2 반영).
        agent = build_general_agent(today=date.today().isoformat())
        context = SellerContext(
            seller_id=identity.seller_id or "", brand_id=identity.brand_id or ""
        )
        previous_ended_space = False
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
                frame, previous_ended_space = _token_chunk(
                    text, previous_ended_space=previous_ended_space
                )
                if frame is not None:
                    yield frame
        yield _done("keep")
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


async def _analysis_stream(
    request: SellerChatRequest, context: SellerContext
) -> AsyncIterator[str]:
    """분석 레인 (4-1b) — 파이프라인 emit(진행)을 progress 로, 최종 답변을 token 으로 중계.

    - 진행 상태는 최종 답변이 아니므로 `progress` 이벤트로 분리한다(FE: 로딩 표시).
      최종 산출은 단일 `token`(보고서/되묻기/사과 공통) → 이 token 이 우측 패널
      대상인지(replace)·대화인지(keep)는 kind 로 갈린다(아래 panel).
    - 패널: kind=="report" 만 우측 교체(replace) — 되묻기(clarification)·사과(apology)·
      거절(refused)은 대화이므로 유지(keep). (FE 요구 2·3 — "화면 바뀔 질문만" 교체.)
    - **예외 2경우**(planner 장애·1차 report 실패)만 여기로 전파 — 사과 token 후
      error 로 종료(REVIEW-STAGE3 §5-2). error 종료는 패널 유지(done 없음).
    - 진행 문구는 파이프라인 내부 상수라 마스킹 불필요, 최종 text 는 mask_output 적용.
    """
    yield _meta("analysis")
    queue: asyncio.Queue[object] = asyncio.Queue()

    async def emit(text: str) -> None:
        await queue.put(text)

    pipeline_task = asyncio.create_task(
        run_analysis_pipeline(request.message, context, today=date.today(), emit=emit)
    )
    # 정상/예외 공통으로 sentinel 을 넣어 진행 루프를 반드시 끝낸다.
    pipeline_task.add_done_callback(lambda _t: queue.put_nowait(_PIPELINE_DONE))

    while True:
        item = await queue.get()
        if item is _PIPELINE_DONE:
            break
        yield _progress(str(item))

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
    yield _token(result.text)
    # 보고서만 우측 패널 교체, 되묻기·사과·거절은 대화로 유지.
    yield _done("replace" if result.kind == "report" else "keep")


async def _product_stream(request: SellerChatRequest, context: SellerContext) -> AsyncIterator[str]:
    """product 레인 (4-2 — draft 생성 + checkpoint 저장, 실행은 confirm 스트림).

    product_agent(2-7)로 DraftProposal 을 만들고 validate_draft(코드 선검증 —
    캐스팅·필수 필드·C4)를 통과하면 start_draft 로 checkpoint 에 저장(interrupt
    대기)한 뒤 SSE `draft` 이벤트로 내보낸다. clarification·검증 불성립은
    되묻기 token. 실행은 스트림 2(_confirm_stream — hitl.confirm_draft) 소관.
    check_scope 는 입구 ②에서 이미 수행됨(구조화 레인 코드 경로 — 배정표 준수).
    패널: draft 성립 시 우측에 diff 카드(replace), 되묻기·검증 불성립은 대화(keep).
    """
    yield _meta("product")
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
        yield _token(proposal.clarification)
        yield _done("keep")
        return

    # 코드 선검증(4-2) — 실행 불가능한 draft 는 FE 에 보여주기 전에 되묻는다.
    record, problem = validate_draft(
        proposal, seller_id=context.seller_id, brand_id=context.brand_id
    )
    if record is None:
        yield _token(problem or "초안을 만들지 못했습니다. 다시 요청해 주세요.")
        yield _done("keep")
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
    yield _done("replace")  # diff 카드 = 우측 패널 교체


def _draft_event(record: DraftRecord) -> str:
    """DraftRecord → SSE draft 이벤트 (product 레인·추천 적용 레인 공용, api-spec §3.2).

    [C-1 수정 2026-07-22] 와이어의 `changes[].field` 는 **camelCase**(규약 §2.2, api-spec).
    내부 DraftChange.field 는 Spring 쓰기(I-10/11)용 snake_case 로 남기고, 여기서
    나갈 때만 to_camel 로 변환한다 — original_price→originalPrice, image_url→imageUrl,
    stock_quantity→stockQuantity(그 외는 동일). 이 필드는 FE 표시 전용이라 confirm 은
    draftId 만 되보낸다(역변환 불필요).
    """
    return _sse(
        "draft",
        {
            "draftId": record.draft_id,
            "op": record.op,
            "productId": record.product_id,  # int | None(create) — F2 숫자 확정
            "changes": [
                {
                    "field": to_camel(c.field),
                    "before": (
                        mask_output(_strip_unsafe_multiline(c.before))
                        if c.field == "description"
                        else mask_output(_strip_unsafe(c.before))
                    ),
                    "after": (
                        mask_output(_strip_unsafe_multiline(c.after))
                        if c.field == "description"
                        else mask_output(_strip_unsafe(c.after))
                    ),
                }
                for c in record.changes
            ],
            "summary": mask_output(_strip_unsafe(record.summary)),
        },
    )


async def _apply_stream(
    n: int, request: SellerChatRequest, context: SellerContext
) -> AsyncIterator[str]:
    """추천 적용 레인 (4-3 §6.3 — 입구 ①.5 코드 선판정 후 진입, LLM 0회).

    최신 이력의 recommendations[N-1] 을 코드가 draft 로 변환(대화 재해석 금지) →
    4-2 와 동일하게 checkpoint 저장 후 draft emit — 이후 confirm 흐름 합류.
    불성립(이력 없음·인덱스 불일치·적용 불가 유형·상품 미발견)은 되묻기 token.
    패널: draft 성립 시 diff 카드(replace), 불성립은 대화(keep) — product 레인과 동일.
    """
    yield _meta("apply")
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
        yield _token(problem or "추천을 적용하지 못했습니다. 다시 요청해 주세요.")
        yield _done("keep")
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
    yield _done("replace")  # diff 카드 = 우측 패널 교체


async def _confirm_stream(draft_id: str, identity: Identity) -> AsyncIterator[str]:
    """confirm 레인 (4-2 스트림 2) — 코드 검사 후 resume 실행, LLM 0회.

    hitl.confirm_draft 가 존재→소유→멱등→TTL 검사를 통과한 경우에만 그래프를
    resume 해 I-10/11/12 를 실행한다. 모든 결과(executed/stale/만료/멱등/미존재)는
    token+done — Spring 장애만 사과 token + error(INTERNAL, draft 유지·재시도 가능).
    패널: 실제 쓰기가 일어난 executed 만 우측 재조회(refresh) — 그 외(변경 없음)는 유지(keep).
    """
    yield _meta("confirm")
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
    yield _token(outcome.text)
    # 실제 쓰기(executed)만 대시보드·목록 재조회 유발 — 나머지는 변경 없음.
    yield _done("refresh" if outcome.status == "executed" else "keep")


async def _seller_stream(request: SellerChatRequest, identity: Identity) -> AsyncIterator[str]:
    """판매자 챗 통합 스트림 (4-1b) — 입구 판정 ①②③ 후 3분기 위임."""
    # ① confirm 필드 선판정 (A-2 최상위 구조화 필드, LLM 0회) → HITL 실행 레인(4-2).
    # action=="confirm" 이면 draftId 는 스키마 validator 가 보장한다(발화 ≠ 동의 [HARD]).
    if request.action == "confirm":
        async for line in _confirm_stream(request.draft_id or "", identity):
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

    # ② scope 선차단 (LLM 0회) — 전 레인 공통 코드 경로. 도메인 밖 = 대화(패널 유지).
    refusal = check_scope(request.message)
    if refusal:
        yield _meta("refused")
        yield _token(refusal)
        yield _done("keep")
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
    request: SellerChatRequest,
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
    request_id = get_request_id(http_request)
    try:
        store = await get_conversation_store()
    except Exception:
        # chat.py 와 동일 — pg-profile 지연 연결 실패(운영 jwks raise)가 open_stream 안전망 밖이라
        # §6.3 b chat_request 로그(errorType 집계)를 통째로 놓친다. rejection 로그를 남기고 전파한다
        # (PR #48 후속 리뷰).
        emit_rejection(request_id, "INTERNAL", conversationId=request.session_id)
        raise
    observation = start_observation(
        request_id=request_id,
        identity=identity,
        conversation_id=request.session_id,
        message=request.message,
        store=store,
        now=asyncio.get_running_loop().time(),
    )
    return await open_stream(
        http_request,
        registry_key(identity, request.session_id),
        lambda: _seller_stream(request, identity),
        observer=observation,
    )
