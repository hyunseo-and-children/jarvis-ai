"""프로필 세션 inactivity bounded sweep (이슈 #79)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.agents.profile import session_activity
from app.agents.profile.finalizer import FinalizationStatus, finalize_profile_session
from app.agents.profile.session_activity import ActivityClaim
from app.core.config import get_settings
from app.core.conversation import conversation_key
from app.core.stream import get_registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdleSweepResult:
    claimed: int = 0
    accepted: int = 0
    duplicate: int = 0
    retryable: int = 0
    skipped: int = 0


async def _release_claim_best_effort(claim: ActivityClaim) -> None:
    try:
        await session_activity.release_claim(claim)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("profile idle skip claim 해제 실패 — lease 만료 후 재시도", exc_info=True)


async def _process_claim(claim: ActivityClaim, *, idle_timeout_s: float) -> str:
    registry = get_registry()
    stream_key = conversation_key(str(claim.user_id), claim.session_id)
    # 실제 라이브 스트림 슬롯을 scheduler가 예약하면 finalizer의 LLM 처리 동안 정상 채팅이
    # false-positive 409를 받는다. 기존 stream만 거르고, 이후 새 활동은 DB touch가 activity
    # claim을 무효화한다. idle finalizer는 fixed processed claim을 완료하지 않고 해제하므로
    # 처리 중/후 같은 sessionId가 재개되어도 다음 checkpoint가 가능하다.
    if registry.is_active(stream_key):
        await _release_claim_best_effort(claim)
        return "skipped"
    if not await session_activity.claim_is_current(claim, idle_timeout_s=idle_timeout_s):
        await _release_claim_best_effort(claim)
        return "skipped"
    outcome = await finalize_profile_session(
        claim.user_id,
        claim.session_id,
        activity_claim=claim,
        terminal=False,
    )
    return outcome.status.value


async def run_idle_sweep() -> IdleSweepResult:
    """만료 후보를 bounded claim하고 제한된 concurrency로 공통 finalizer에 전달한다."""
    settings = get_settings()
    started = time.monotonic()
    claims = await session_activity.claim_expired_sessions(
        idle_timeout_s=settings.profile_session_idle_timeout_s,
        lease_s=settings.profile_idle_claim_ttl_s,
        batch_size=settings.profile_idle_sweep_batch_size,
    )
    semaphore = asyncio.Semaphore(settings.profile_idle_max_concurrency)

    async def _bounded(claim: ActivityClaim) -> str:
        async with semaphore:
            try:
                return await _process_claim(
                    claim,
                    idle_timeout_s=settings.profile_session_idle_timeout_s,
                )
            except Exception:  # noqa: BLE001 - 한 claim 실패를 같은 bounded batch에서 격리
                # claim_is_current 같은 finalizer 바깥 DB 조회가 실패해도 다른 세션은 계속
                # 처리한다. 즉시 해제가 실패하면 token lease가 최종 복구 경계다.
                await _release_claim_best_effort(claim)
                logger.warning("profile idle claim 처리 실패 — retryable로 격리", exc_info=True)
                return FinalizationStatus.RETRYABLE.value

    statuses = await asyncio.gather(*(_bounded(claim) for claim in claims))
    result = IdleSweepResult(
        claimed=len(claims),
        accepted=statuses.count(FinalizationStatus.ACCEPTED.value),
        duplicate=statuses.count(FinalizationStatus.DUPLICATE.value),
        retryable=statuses.count(FinalizationStatus.RETRYABLE.value),
        skipped=statuses.count("skipped"),
    )
    logger.info(
        "profile idle sweep 완료: claimed=%d accepted=%d duplicate=%d retryable=%d "
        "skipped=%d duration_ms=%d",
        result.claimed,
        result.accepted,
        result.duplicate,
        result.retryable,
        result.skipped,
        int((time.monotonic() - started) * 1000),
    )
    return result
