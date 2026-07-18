"""SSE 스트림 수명주기 (api-spec §2.9) — 동시 스트림 레지스트리 + 취소/타임아웃.

`POST /chat`·`POST /seller/chat` 공통. 엔드포인트의 내부 이벤트 제너레이터를
`open_stream()` 으로 감싸면:
  (a) 동시 스트림 제한 — sessionId 당 1개, 초과 시 409 STREAM_IN_PROGRESS(§2.9 a)
  (b) 취소 — 연결 종료 시 Starlette 가 응답 task 를 취소 → finally 로 정리(레지스트리
      해제 + 내부 제너레이터 aclose 로 LLM 스트림/그래프 task 전파). 제너레이터가 유휴일
      때는 is_disconnected() 폴링으로도 조기 감지(§2.9 b)
  (c) 타임아웃 — first-token 상한 초과 시 스트림 전 504, 전체 상한 초과 시 done(stop) 절단(§2.9 c)

MVP 단일 인스턴스 전제. 다중 인스턴스 확장 시 레지스트리를 Redis 로 이관한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.auth import Identity
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.chat import DoneData, ErrorData

logger = get_logger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # 리버스 프록시 버퍼링 비활성 (api-spec §2.4)
}


class ActiveStreamRegistry:
    """인메모리 활성 스트림 레지스트리 (§2.9 a). acquire/release 는 await 를 끼지 않아
    이벤트 루프 상에서 원자적이다 (check-then-add 사이 선점 없음)."""

    def __init__(self) -> None:
        self._active: set[str] = set()

    def acquire(self, session_id: str) -> bool:
        """활성 등록. 이미 활성이면 False (호출자가 409 로 거절)."""
        if session_id in self._active:
            return False
        self._active.add(session_id)
        return True

    def release(self, session_id: str) -> None:
        """활성 해제 (중복 해제 무해)."""
        self._active.discard(session_id)

    def is_active(self, session_id: str) -> bool:
        return session_id in self._active


_registry = ActiveStreamRegistry()


def get_registry() -> ActiveStreamRegistry:
    """활성 스트림 레지스트리 싱글턴."""
    return _registry


def registry_key(identity: Identity, session_id: str) -> str:
    """동시성 레지스트리 키를 **인증된 신원**에 묶는다 (§2.9 a IDOR 방지).

    키를 본문 session_id 만으로 쓰면 다른 사용자가 남의 session_id 를 body 에 실어
    동시성 슬롯을 선점(피해자에 409 유발)할 수 있다. owner(판매자/회원 식별자, 게스트는
    "guest")를 접두어로 붙여 사용자 간 슬롯 침범을 막는다.
    """
    # subject(검증된 sub)는 게스트 UUID 포함 모든 역할에 보존된다(auth.Identity). 이걸로
    # 키를 묶어야 게스트끼리도 서로의 슬롯을 침범하지 못한다. 신원 없음(dev 무토큰)은 "anon".
    owner = identity.subject or "anon"
    return f"{owner}:{session_id}"


def _done_stop_frame() -> str:
    """전체 상한 초과 시의 정상 절단 done 프레임 (finishReason stop)."""
    import json

    payload = {"type": "done", "data": DoneData(finish_reason="stop").model_dump(by_alias=True)}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_frame(code: str, message: str) -> str:
    """스트림 시작 후 오류의 in-stream `error` 프레임 (api-spec §3.1, §2.9 c)."""
    import json

    payload = {"type": "error", "data": ErrorData(code=code, message=message).model_dump(by_alias=True)}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def open_stream(
    request: Request,
    session_id: str,
    inner_factory: Callable[[], AsyncIterator[str]],
) -> StreamingResponse:
    """내부 이벤트 제너레이터를 §2.9 수명주기로 감싼 StreamingResponse 를 만든다.

    스트림 시작 전 실패는 예외로 던져 §2.5 봉투로 나간다:
      - 409 STREAM_IN_PROGRESS: 동일 세션 활성 스트림 존재
      - 504 UPSTREAM_TIMEOUT: first-token 상한 초과 (첫 이벤트 도착 전)
    """
    settings = get_settings()
    registry = get_registry()

    if not registry.acquire(session_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "STREAM_IN_PROGRESS", "message": "동일 세션에 진행 중인 스트림이 있습니다"},
        )

    loop = asyncio.get_event_loop()
    start = loop.time()
    agen = inner_factory()

    poll = settings.stream_disconnect_poll_s
    deadline = start + settings.stream_total_timeout_s
    ft_deadline = start + settings.stream_first_token_timeout_s

    async def _empty_stream() -> AsyncIterator[str]:
        return
        yield  # pragma: no cover - 빈 스트림(제너레이터화용 unreachable)

    # (c) first-token 상한 — 첫 이벤트 도착 전이므로 아직 200 헤더 전. 초과 시 504(§2.5).
    # (b) 이 대기 구간에서도 disconnect 를 폴링한다 — 첫 이벤트 전에 클라이언트가 떠나면
    #     상류 LLM 비용·레지스트리 슬롯을 first-token 상한(기본 10s)까지 붙들지 않는다.
    # wait_for 로 __anext__ 를 취소하면 제너레이터가 손상되므로 task 폴링을 쓴다(_wrapped 동일).
    # 스트림 반환 전 실패는 _wrapped finally 가 안 도므로 여기서 해제하고, 정상/빈 스트림만
    # _wrapped finally 한 곳에서 해제한다(이중 해제 레이스 방지).
    ft_task = asyncio.ensure_future(agen.__anext__())

    async def _abort_prestream() -> None:
        registry.release(session_id)
        if not ft_task.done():
            ft_task.cancel()
        await asyncio.gather(ft_task, return_exceptions=True)
        await agen.aclose()

    first: str | None = None
    while True:
        remaining = ft_deadline - loop.time()
        if remaining <= 0:
            await _abort_prestream()
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={"code": "UPSTREAM_TIMEOUT", "message": "상류(LLM) 응답 지연"},
            )
        completed, _ = await asyncio.wait({ft_task}, timeout=min(remaining, poll))
        if ft_task in completed:
            try:
                first = ft_task.result()
            except StopAsyncIteration:
                first = None  # 빈 스트림 — 해제는 _wrapped finally 에서.
            except BaseException:
                # 첫 프레임 전 상류 오류(LLM/Spring 등)·취소 — 누수 방지 후 전파.
                await _abort_prestream()
                raise
            break
        if await request.is_disconnected():
            # (b) 첫 이벤트 전 연결 종료 — 정리 후 즉시 종료(소비될 응답 없음).
            logger.info("stream cancelled before first token session=%s", session_id)
            await _abort_prestream()
            return StreamingResponse(
                _empty_stream(),
                media_type="text/event-stream; charset=utf-8",
                headers=_SSE_HEADERS,
            )

    async def _wrapped() -> AsyncIterator[str]:
        # 다음 이벤트 대기는 지속 task 로 폴링한다 — wait_for 로 __anext__ 를 취소하면
        # 제너레이터가 망가지므로(다음 호출이 StopAsyncIteration) asyncio.wait 로 완료만 관찰한다.
        next_task: asyncio.Task | None = None
        try:
            if first is not None:
                yield first
            next_task = asyncio.ensure_future(agen.__anext__())
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    # (c) 전체 상한 초과 — done(stop)으로 정상 절단.
                    logger.info("stream total cap reached session=%s", session_id)
                    yield _done_stop_frame()
                    break
                completed, _ = await asyncio.wait({next_task}, timeout=min(remaining, poll))
                if next_task not in completed:
                    # 아직 이벤트 없음(제너레이터 유휴) — (b) 연결 종료 조기 감지.
                    if await request.is_disconnected():
                        logger.info("stream cancelled by client disconnect session=%s", session_id)
                        break
                    continue  # 같은 task 를 계속 기다린다(제너레이터 보존)
                try:
                    item = next_task.result()
                except StopAsyncIteration:
                    break
                except Exception:
                    # (c) 첫 이벤트 후(=200 전송 후) 상류 오류 — 계약상 in-stream error 로
                    #     마무리해야 한다(연결만 끊기지 않게, §2.9 c/§3.1). 기본 INTERNAL.
                    #     실제 그래프는 필요 시 자체 error(LLM_UNAVAILABLE 등)를 먼저 emit 가능.
                    logger.exception("in-stream error session=%s", session_id)
                    yield _error_frame("INTERNAL", "처리 중 오류가 발생했습니다")
                    break
                yield item
                next_task = asyncio.ensure_future(agen.__anext__())
        finally:
            # (b) 취소·상한·정상 종료 공통 정리: 대기 중 task 취소 → 내부 제너레이터 close
            #     (LLM 스트림/그래프 task 로 취소 전파) → 레지스트리 해제.
            #     취소 턴의 CANCELLED 저장·부분텍스트 보존은 이슈 #8(대화 저장) 소관.
            if next_task is not None and not next_task.done():
                next_task.cancel()
                await asyncio.gather(next_task, return_exceptions=True)
            await agen.aclose()
            registry.release(session_id)

    return StreamingResponse(
        _wrapped(),
        media_type="text/event-stream; charset=utf-8",
        headers=_SSE_HEADERS,
    )
