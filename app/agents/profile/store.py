"""프로필 저장소 — LangGraph PostgresStore(BaseStore) + pgvector 이관 (SPEC-PROFILE-001 §5.3, 이슈 #33).

네임스페이스(결정 16, §5.3): profile(요약) · facts(승격된 장기 fact, semantic 인덱스) ·
session_ctx(transient 세션 버퍼, 격리). fact 는 1개 = store item 1개로 저장해(REQ-PROF-070)
BaseStore 의 semantic 인덱스가 fact 단위로 실제 동작하게 한다 — 임베딩은 카탈로그 파이프라인과
모델 공유(app.pipelines.embedding.embed_texts, Google gemini-embedding-001 / config.embedding_dim,
결정 16-A: 인스턴스는 카탈로그와 별도[pg-profile]). session-end 멱등(processed eventId)은
get→put 두 단계가 원자적이지 않아 이 스토어가 아니라 전용 테이블(processed_events.py)이 맡는다.

dev 폴백은 app/agents/seller/history.py 와 동일 규약(InMemoryStore + 경고 1회), 운영(jwks)은
폴백 금지 — 재시작 시 프로필이 조용히 증발하면 안 된다.

보관:
  - summary       : namespace ("profile", user_id) key "summary" → 압축 프로필 요약(markdown, generated_at)
  - facts         : namespace ("facts", user_id) key=fact별 uuid → 승격된 장기 fact(semantic 인덱스 대상)
  - session_ctx   : namespace ("session_ctx", conversation_key) key "buffer" → transient 후보 버퍼(승격 전, 격리)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from app.agents.profile import processed_events
from app.core.config import get_settings
from app.pipelines.embedding import embed_texts

logger = logging.getLogger(__name__)

_PROFILE_NS_ROOT = "profile"
_FACTS_NS_ROOT = "facts"
_SESSION_NS_ROOT = "session_ctx"
_SUMMARY_KEY = "summary"
_SESSION_KEY = "buffer"


def _index_config() -> dict:
    """facts semantic 인덱스 설정 — 카탈로그와 임베딩 함수·차원 공유(결정 16-A, config 주입)."""
    settings = get_settings()
    return {"dims": settings.embedding_dim, "embed": embed_texts, "fields": ["fact"]}


@dataclass
class ProfileSummary:
    """압축 프로필 요약 (§5.1 3섹션 마크다운 + 생성 시각)."""

    markdown: str
    generated_at: str  # ISO-8601


class ProfileStore:
    """프로필 스토어 — LangGraph BaseStore(pg-profile) 백엔드(신원 스코프)."""

    def __init__(self, store: BaseStore | None = None) -> None:
        self._store = store or InMemoryStore(index=_index_config())

    # ── 요약 (reader·GET·consolidation) ──
    async def get_summary(self, user_id: str) -> ProfileSummary | None:
        item = await self._store.aget((_PROFILE_NS_ROOT, user_id), _SUMMARY_KEY)
        if not item:
            return None
        return ProfileSummary(
            markdown=item.value["markdown"], generated_at=item.value["generated_at"]
        )

    async def set_summary(self, user_id: str, markdown: str, generated_at: str) -> None:
        await self._store.aput(
            (_PROFILE_NS_ROOT, user_id),
            _SUMMARY_KEY,
            {"markdown": markdown, "generated_at": generated_at},
            index=False,  # 요약 전문은 semantic 인덱스 대상이 아니다(REQ-PROF-071 — facts 전용)
        )

    # ── 장기 fact (승격 결과·consolidation 입력) — fact 1개 = store item 1개(semantic 인덱스) ──
    async def get_facts(self, user_id: str) -> list[str]:
        items = await self._store.asearch((_FACTS_NS_ROOT, user_id), limit=1000)
        items.sort(key=lambda it: it.created_at)
        return [it.value["fact"] for it in items]

    async def add_fact(self, user_id: str, fact: str, *, cap: int | None = None) -> None:
        if not fact:
            return
        key = uuid.uuid4().hex
        await self._store.aput((_FACTS_NS_ROOT, user_id), key, {"fact": fact})
        if cap and cap > 0:
            items = await self._store.asearch((_FACTS_NS_ROOT, user_id), limit=10_000)
            if len(items) > cap:
                items.sort(key=lambda it: it.created_at)
                for stale in items[: len(items) - cap]:  # 최신 cap 개만 유지(recency-wins)
                    await self._store.adelete((_FACTS_NS_ROOT, user_id), stale.key)

    # ── transient 세션 버퍼 (승격 전 격리, REQ-PROF transient) ──
    async def append_session_ctx(self, key: str, text: str, *, cap: int | None = None) -> None:
        if not text:
            return
        item = await self._store.aget((_SESSION_NS_ROOT, key), _SESSION_KEY)
        value = item.value if item else {"items": [], "next_seq": 0}
        seq = value["next_seq"] + 1
        buf: list[list] = value["items"]
        buf.append([seq, text])
        if cap and cap > 0 and len(buf) > cap:
            del buf[: len(buf) - cap]  # 최신 cap 개만 유지(무제한 누적 방어)
        await self._store.aput(
            (_SESSION_NS_ROOT, key), _SESSION_KEY, {"items": buf, "next_seq": seq}, index=False
        )

    async def get_session_ctx(self, key: str) -> list[str]:
        item = await self._store.aget((_SESSION_NS_ROOT, key), _SESSION_KEY)
        return [text for _, text in item.value["items"]] if item else []

    async def get_session_ctx_snapshot(self, key: str) -> tuple[list[str], int]:
        """(발화 목록, 스냅샷 워터마크 seq) 반환 — 워터마크는 clear_session_ctx_upto 인자로 그대로 넘긴다."""
        item = await self._store.aget((_SESSION_NS_ROOT, key), _SESSION_KEY)
        if not item:
            return [], 0
        buf = item.value["items"]
        return [text for _, text in buf], (buf[-1][0] if buf else 0)

    async def clear_session_ctx_upto(self, key: str, watermark: int) -> None:
        """watermark(seq) 이하 항목만 제거 — cap 트리밍으로 스냅샷 항목이 먼저 밀려나 있어도,
        그 사이 새로 추가된 항목(seq > watermark)은 위치와 무관하게 항상 보존된다."""
        item = await self._store.aget((_SESSION_NS_ROOT, key), _SESSION_KEY)
        if not item:
            return
        remaining = [[seq, text] for seq, text in item.value["items"] if seq > watermark]
        if remaining:
            await self._store.aput(
                (_SESSION_NS_ROOT, key),
                _SESSION_KEY,
                {"items": remaining, "next_seq": item.value["next_seq"]},
                index=False,
            )
        else:
            await self._store.adelete((_SESSION_NS_ROOT, key), _SESSION_KEY)


_store: BaseStore | None = None
_store_ctx: object | None = None  # AsyncPostgresStore cm — 앱 수명 동안 GC 방지
_fallback_warned = False


def set_store(store: BaseStore | None) -> None:
    """store 교체(테스트용) — None 이면 다음 사용 시 재초기화한다."""
    global _store, _store_ctx
    _store = store
    _store_ctx = None


async def _get_store() -> BaseStore:
    """AsyncPostgresStore(pg-profile, pgvector 인덱스) 지연 초기화 — 실패 시 dev 한정 InMemoryStore 폴백."""
    global _store, _store_ctx, _fallback_warned
    if _store is None:
        settings = get_settings()
        index_config = _index_config()
        try:
            from langgraph.store.postgres.aio import AsyncPostgresStore  # noqa: PLC0415

            ctx = AsyncPostgresStore.from_conn_string(settings.profile_db_url, index=index_config)
            store = await asyncio.wait_for(
                ctx.__aenter__(), timeout=settings.state_store_connect_timeout_s
            )
            await store.setup()
            _store_ctx = ctx
            _store = store
        except Exception as exc:
            if settings.auth_mode == "jwks":
                raise  # 운영 — 폴백 금지(프로필이 조용히 증발하면 안 된다)
            if not _fallback_warned:
                logger.warning(
                    "pg-profile ProfileStore 연결 실패(%s) — InMemoryStore 폴백 "
                    "(dev 전용: 프로세스 재시작 시 프로필 증발)",
                    exc,
                )
                _fallback_warned = True
            _store = InMemoryStore(index=index_config)
    return _store


async def get_profile_store() -> ProfileStore:
    """프로필 스토어 — pg-profile 연결 백엔드(요청마다 얇은 래퍼 재생성)."""
    return ProfileStore(await _get_store())


def reset_profile_store() -> None:
    """테스트 격리용 — 요약·fact·세션버퍼(InMemoryStore) + 멱등 상태(processed_events)를 비운다."""
    set_store(InMemoryStore(index=_index_config()))
    processed_events.reset()
