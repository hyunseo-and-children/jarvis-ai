"""Spring I-20과 AI inactivity timeout이 공유하는 프로필 세션 finalizer (이슈 #79)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Awaitable, Callable

from app.agents.profile import processed_events, session_activity
from app.agents.profile.builder import ConsolidationResult, consolidate, generate_session_delta
from app.agents.profile.session_activity import ActivityClaim, SessionActivity
from app.agents.profile.store import ProfileStore, get_profile_store
from app.core.config import Settings, get_settings
from app.core.conversation import conversation_key
from app.core.llm import LLMClient, get_llm

logger = logging.getLogger(__name__)


class FinalizationStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    RETRYABLE = "retryable"


@dataclass(frozen=True)
class FinalizationResult:
    status: FinalizationStatus


async def release_processed_claim_best_effort(
    event_id: str,
    token: str,
    *,
    log: logging.Logger | None = None,
) -> None:
    """취소 중에도 processed-event claim 해제를 마치고 DB 실패는 lease 복구에 맡긴다."""
    target_log = log or logger
    release_task = asyncio.create_task(processed_events.release_claim(event_id, token))
    try:
        await asyncio.shield(release_task)
    except asyncio.CancelledError:
        task = asyncio.current_task()
        outer_cancelled = task is not None and task.cancelling() > 0
        try:
            await release_task
        except BaseException:  # stale cleanup result 회수; 실제 outer cancellation은 아래서 재전파
            pass
        if outer_cancelled:
            raise
        target_log.warning("session-end claim 해제 task 취소 — lease 만료 후 재시도")
    except Exception:
        target_log.warning("session-end claim 해제 실패 — lease 만료 후 재시도", exc_info=True)


async def _release_activity_claim_best_effort(
    claim: ActivityClaim,
    *,
    log: logging.Logger,
) -> None:
    try:
        await session_activity.release_claim(claim)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("profile idle activity claim 해제 실패 — lease 만료 후 재시도", exc_info=True)


async def _complete_activity_best_effort(
    user_id: int,
    session_id: str,
    claim: ActivityClaim | None,
    *,
    log: logging.Logger,
) -> bool:
    try:
        completed = await session_activity.complete_session(
            user_id,
            session_id,
            token=claim.claim_token if claim is not None else None,
        )
        if not completed:
            log.warning("profile session activity 완료 ownership 상실 — 다음 sweep이 복구")
        return completed
    except asyncio.CancelledError:
        raise
    except Exception:
        # 호출자가 retryable로 집계하고 finally에서 activity/processed claim을 해제한다.
        log.warning("profile session activity 완료 기록 실패 — 재시도 필요", exc_info=True)
        return False


async def _complete_terminal_activity_best_effort(
    user_id: int,
    session_id: str,
    observed: SessionActivity | None,
    *,
    log: logging.Logger,
) -> bool:
    try:
        completed = await session_activity.complete_terminal_session(
            user_id,
            session_id,
            observed=observed,
        )
        if not completed:
            log.info("session-end 처리 중 새 activity 감지 — terminal 완료 취소")
        return completed
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("profile terminal activity 완료 기록 실패 — 재시도 필요", exc_info=True)
        return False


async def finalize_profile_session(
    user_id: str | int,
    session_id: str,
    *,
    activity_claim: ActivityClaim | None = None,
    terminal: bool = True,
    settings: Settings | None = None,
    store_factory: Callable[[], Awaitable[ProfileStore]] | None = None,
    llm_factory: Callable[[], LLMClient | None] | None = None,
    log: logging.Logger | None = None,
) -> FinalizationResult:
    """한 세션 버퍼를 실패 안전 멱등 lifecycle로 처리한다.

    외부 I-20은 ``terminal=True``로 fixed dedup을 완료한다. idle scheduler는
    ``terminal=False``로 같은 claim을 세션 단위 mutex로만 쓰고 성공 뒤 해제하여, 같은
    sessionId가 재활동하면 다음 idle checkpoint를 다시 처리할 수 있게 한다.
    CancelledError만 호출자에게 재전파한다.
    """
    target_log = log or logger
    resolved_settings = settings or get_settings()
    numeric_user_id = int(user_id)
    user_key = str(numeric_user_id)
    key = conversation_key(user_key, session_id)
    dedup_key = processed_events.session_end_event_id(numeric_user_id, session_id)
    processed_token: str | None = None
    processed_completed = False
    activity_completed = False
    terminal_activity: SessionActivity | None = None

    try:
        processed_token = await processed_events.claim_event(
            dedup_key,
            lease_s=resolved_settings.session_end_claim_ttl_s,
        )
        if processed_token is None:
            return FinalizationResult(FinalizationStatus.DUPLICATE)

        if terminal:
            terminal_activity = await session_activity.get_session(numeric_user_id, session_id)
            if not await processed_events.claim_is_current(dedup_key, processed_token):
                return FinalizationResult(FinalizationStatus.RETRYABLE)

        factory = store_factory or get_profile_store
        store = await factory()
        buffer, _ = await store.get_session_ctx_snapshot(key)
        if buffer:
            resolved_llm = (llm_factory or get_llm)()
            result = await generate_session_delta(
                user_key,
                key,
                llm=resolved_llm,
                settings=resolved_settings,
            )
            if result is None:
                return FinalizationResult(FinalizationStatus.RETRYABLE)
            _, watermark = result
            consolidation = await consolidate(
                user_key,
                llm=resolved_llm,
                settings=resolved_settings,
            )
            if consolidation is ConsolidationResult.FAILED:
                return FinalizationResult(FinalizationStatus.RETRYABLE)
            # 처리 중 추가된 새 발화(seq > watermark)는 보존한다.
            await store.clear_session_ctx_upto(key, watermark)

        if terminal:
            activity_completed = await _complete_terminal_activity_best_effort(
                numeric_user_id,
                session_id,
                terminal_activity,
                log=target_log,
            )
            if not activity_completed:
                return FinalizationResult(FinalizationStatus.RETRYABLE)
            processed_completed = await processed_events.complete_claim(dedup_key, processed_token)
            if not processed_completed:
                raise RuntimeError("session-end claim ownership lost")
        else:
            activity_completed = await _complete_activity_best_effort(
                numeric_user_id,
                session_id,
                activity_claim,
                log=target_log,
            )
            if not activity_completed:
                return FinalizationResult(FinalizationStatus.RETRYABLE)
        return FinalizationResult(FinalizationStatus.ACCEPTED)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - I-20 best-effort 및 idle 재시도 경계
        target_log.warning("session-end 내부 처리 실패 — 202 degrade", exc_info=True)
        return FinalizationResult(FinalizationStatus.RETRYABLE)
    finally:
        if processed_token is not None and not processed_completed:
            await release_processed_claim_best_effort(dedup_key, processed_token, log=target_log)
        if activity_claim is not None and not activity_completed:
            await _release_activity_claim_best_effort(activity_claim, log=target_log)
