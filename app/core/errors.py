"""스트림 전(前) 오류 봉투 (api-spec §2.5) + requestId 부여.

비스트리밍 응답·SSE 스트림 시작 전 거부(인증·검증·409·429·504 등)의 오류는
{"error": {code, message, requestId}} 봉투로 낸다. 스트림 **내부** 오류는 §3.1
in-stream `error` 이벤트로 별개다(이 모듈 소관 아님).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"

# api-spec §2.5 [확정] 스트림 전 상태 코드 매핑. detail dict 의 code 가 우선한다.
_STATUS_CODE_MAP: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "TOKEN_INVALID",
    403: "FORBIDDEN",
    409: "STREAM_IN_PROGRESS",
    429: "RATE_LIMITED",
    500: "INTERNAL",
    504: "UPSTREAM_TIMEOUT",
}
_DEFAULT_MESSAGE: dict[int, str] = {
    400: "요청 본문/파라미터 오류",
    401: "인증 실패",
    403: "권한 없음",
    409: "동일 세션에 진행 중인 스트림이 있습니다",
    429: "요청이 너무 많습니다",
    500: "서버 내부 오류",
    504: "상류(LLM/Spring) 응답 지연",
}


def new_request_id() -> str:
    """추적용 요청 식별자 생성 (로그 상관관계)."""
    return uuid.uuid4().hex


def get_request_id(request: Request) -> str:
    """요청 컨텍스트의 requestId 를 반환한다 (미들웨어 미적용 시 즉석 생성)."""
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = new_request_id()
        request.state.request_id = rid
    return rid


def error_envelope(code: str, message: str, request_id: str) -> dict[str, Any]:
    """§2.5 오류 봉투 dict."""
    return {"error": {"code": code, "message": message, "requestId": request_id}}


def _resolve(status_code: int, detail: Any) -> tuple[str, str]:
    """(status, detail) → (code, 안전 메시지). detail 의 code/message 가 매핑을 덮어쓴다."""
    code = _STATUS_CODE_MAP.get(status_code, "ERROR")
    message = _DEFAULT_MESSAGE.get(status_code, "오류가 발생했습니다")
    if status_code >= 500:
        # 5xx 는 detail(내부 오류 메시지/PII 가능)을 무시하고 항상 고정 안전 메시지를 쓴다.
        return code, message
    if isinstance(detail, dict):
        code = str(detail.get("code", code))
        message = str(detail.get("message", message))
    elif isinstance(detail, str) and detail:
        message = detail
    return code, message


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTPException → §2.5 봉투. 내부 스택/PII 미포함, requestId 부여."""
    code, message = _resolve(exc.status_code, exc.detail)
    rid = get_request_id(request)
    if exc.status_code >= 500:
        logger.error("unhandled http error status=%s code=%s rid=%s", exc.status_code, code, rid)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(code, message, rid),
        headers={REQUEST_ID_HEADER: rid},
    )


async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """요청 본문/파라미터 검증 실패 → 400 BAD_REQUEST 봉투 (상세 필드는 미노출)."""
    rid = get_request_id(request)
    return JSONResponse(
        status_code=400,
        content=error_envelope("BAD_REQUEST", _DEFAULT_MESSAGE[400], rid),
        headers={REQUEST_ID_HEADER: rid},
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """라우터/그래프에서 처리되지 않은 예외(500) → §2.5 봉투. 내부 스택/PII 미노출."""
    rid = get_request_id(request)
    logger.exception("unhandled exception rid=%s", rid)
    return JSONResponse(
        status_code=500,
        content=error_envelope("INTERNAL", "서버 내부 오류", rid),
        headers={REQUEST_ID_HEADER: rid},
    )


async def request_context_middleware(request: Request, call_next: Any) -> Any:
    """요청마다 requestId 를 부여하고 응답 헤더로 노출한다 (로그 상관관계)."""
    rid = new_request_id()
    request.state.request_id = rid
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = rid
    return response


def install_error_handling(app: FastAPI) -> None:
    """예외 핸들러 + requestId 미들웨어를 앱에 등록한다."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    app.middleware("http")(request_context_middleware)
