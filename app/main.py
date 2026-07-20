"""FastAPI 애플리케이션 엔트리포인트.

CORS 미들웨어(오리진은 설정 주입), MVP 라우터(chat/seller), GET /health 를 구성한다.
FE 가 AI 서버를 다른 오리진에서 직접 호출하므로 CORS 가 앞단으로 이동했다 (api-spec §2.7 / C-11).

[변경 2026-07-15] MVP 표면은 /chat, /seller/chat, /health 로 축소.
  - [TODO MVP] GET /profile/me(§3.4)·POST /events/session-end(§3.5) 라우터 등록 필요 —
    현재 플레이스홀더(app/api/{profile,events}.py). catalog/order 이벤트는 영구 미채택.
  - [완료] §2.9 스트림 수명주기(app/core/stream.py)·§2.8 레이트 리밋(app/core/ratelimit.py)·§2.5 오류 봉투(app/core/errors.py).

[추가 2026-07-20, 이슈 #31] lifespan 에서 I-17 배치 스케줄러(app/pipelines/scheduler.py)를
기동/종료한다 — TestClient(app) 를 `with` 없이 쓰는 기존 테스트들은 lifespan 이 발동하지 않아
영향이 없다(경험적으로 확인).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# [이슈 #33] Windows 기본 ProactorEventLoop 는 psycopg async 연결을 지원하지 않아
# AsyncPostgresStore/AsyncPostgresSaver(pg-profile, seller history.py·hitl.py 포함)가
# 조용히 실패 → dev 폴백(InMemoryStore/Saver)으로 전락한다(docs/lessons.md 2026-07-20).
# uvicorn 이 이벤트 루프를 만들기 전에(모듈 임포트 시점) 정책을 교체해야 한다.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.api import chat, events, profile, seller
from app.core.config import get_settings
from app.core.errors import install_error_handling
from app.core.logging import configure_logging
from app.core.ratelimit import rate_limit_middleware
from app.pipelines.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 기동 시 I-17 증분 배치 스케줄러를 시작하고, 종료 시 정지한다 (이슈 #31)."""
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


def create_app() -> FastAPI:
    """FastAPI 앱을 생성·구성해 반환한다 (앱 팩토리)."""
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title="Jarvis AI Server",
        version="0.1.0",
        description="agentic commerce AI server (FastAPI + LangGraph)",
        lifespan=_lifespan,
    )

    # 오류 봉투(§2.5) 예외 핸들러 + requestId 미들웨어. 스트림 전 거부/검증 실패를
    # {"error":{code,message,requestId}} 봉투로 낸다.
    install_error_handling(app)

    # 레이트 리밋(§2.8, 채팅 전송 경로) — requestId 미들웨어보다 안쪽에 둔다.
    app.middleware("http")(rate_limit_middleware)

    # CORS 는 최외곽(가장 마지막 등록)에 둬 오류·429 응답에도 헤더가 실리게 한다 (api-spec §2.7).
    # Authorization 헤더 사용 → 브라우저 preflight(OPTIONS) 발생.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # MVP 라우터: 사용자 대면 chat / seller 만 등록한다.
    app.include_router(chat.router)
    app.include_router(seller.router)
    app.include_router(profile.router)
    app.include_router(events.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        """헬스 체크. 컨테이너 healthcheck·부팅 확인용."""
        return {"status": "ok"}

    return app


app = create_app()
