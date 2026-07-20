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
import contextlib
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

# get_facts/add_fact 의 asearch 조회 상한 여유치 — 트리밍 직후에도 asearch 가 즉시 cap
# 이하로 수렴한다는 보장이 없어(동시 add_fact 레이스 등) cap 을 그대로 쓰면 경계에서
# 최신 fact 가 잘릴 수 있다. 두 메서드가 각자 다른 매직넘버(1000 / 10_000)를 하드코딩해
# settings.profile_max_facts 와 무관하게 놀던 것을 이 여유치 하나로 통일한다
# (CLAUDE.md "튜너블 하드코딩 금지", PR #47 후속 리뷰).
_FACTS_QUERY_MARGIN = 50

# key(conversation_key)별 asyncio.Lock — append_session_ctx/clear_session_ctx_upto 의
# get→put(read-modify-write) 구간을 직렬화한다. 동일 세션에 연속 발화가 빠르게 들어오면
# lost update 로 앞선 발화가 통째로 유실될 수 있다(RevertStore.add() 와 동일 근거, PR #47 리뷰).
#
# [한계, PR #47 후속 리뷰 — RevertStore._add_locks 와 동일 전제] 이 락도 프로세스 로컬이라
# 다중 인스턴스 배포에서는 직렬화되지 않고(app/core/stream.py 의 ActiveStreamRegistry 와
# 동일 "MVP 단일 인스턴스" 전제), conversation_key 마다 항목이 쌓이기만 하고 제거되지
# 않아 장시간 구동 시 완만한 메모리 누수다 — MVP 규모에서 실질적 위험 낮음으로 판단해
# TTL/LRU 정리는 미구현(reset_profile_store() 는 테스트 격리용으로만 비운다).
_session_locks: dict[str, asyncio.Lock] = {}


def _session_lock(key: str) -> asyncio.Lock:
    lock = _session_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[key] = lock
    return lock


# user_id 별 asyncio.Lock — add_fact() 의 cap 트리밍(asearch→sort→adelete) 구간을 직렬화한다.
#
# [검증 결과, PR #47 후속 리뷰] append_session_ctx 와 달리 이 트리밍은 단일 값을 덮어쓰는
# get→put 이 아니라, 시간에 따라 계속 늘어나는 항목 집합에서 "가장 오래된 초과분만 지우는"
# 연산이다 — 임의 시점의 부분 스냅샷은 항상 그 시점까지 커밋된 항목들의 시간순 앞부분(prefix)
# 이므로, 서로 다른 스냅샷을 본 동시 호출들의 삭제 대상은 항상 서로 부분집합 관계이고
# adelete 는 멱등이라 실제 데이터 유실로 이어지지 않는다(포워스트-인터리브 fake store 와
# 실 Postgres 양쪽으로 재현 시도했으나 락 없이도 cap 을 넘기지 않음을 확인). 즉 이 락은
# 증명된 버그의 수정이 아니라 _session_locks 와의 패턴 일관성을 위한 방어적 비용-제로 조치.
# 한계는 _session_locks 와 동일(프로세스 로컬, user_id 별 무제한 누적) — reset_profile_store()
# 로만 정리.
_fact_locks: dict[str, asyncio.Lock] = {}


def _fact_lock(key: str) -> asyncio.Lock:
    lock = _fact_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _fact_locks[key] = lock
    return lock


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """InMemoryStore 폴백 전용 — 실 임베딩 API 호출 없는 결정론적 벡터(배선만 유지).

    google_api_key 미구성 환경(유닛 테스트·CI·DB 없는 dev)에서 add_fact 가 실제
    Google API 를 호출하면 안 된다 — semantic 인덱스 자체의 유사도 검증은
    tests/integration/test_pg_profile_store.py(fake embed 주입) 가 담당한다.
    """
    dim = get_settings().embedding_dim
    return [[0.0] * dim for _ in texts]


def _pg_index_config() -> dict:
    """pg-profile(AsyncPostgresStore) 전용 semantic 인덱스 — 실 Google 임베딩 API.

    카탈로그와 임베딩 함수·차원 공유(결정 16-A, config 주입).
    """
    settings = get_settings()
    return {"dims": settings.embedding_dim, "embed": embed_texts, "fields": ["fact"]}


def _fallback_index_config() -> dict:
    """InMemoryStore 폴백(테스트 격리·DB 미가용 dev) 전용 — 실 API 호출 없는 fake embed."""
    settings = get_settings()
    return {"dims": settings.embedding_dim, "embed": _fake_embed, "fields": ["fact"]}


@dataclass
class ProfileSummary:
    """압축 프로필 요약 (§5.1 3섹션 마크다운 + 생성 시각)."""

    markdown: str
    generated_at: str  # ISO-8601


class ProfileStore:
    """프로필 스토어 — LangGraph BaseStore(pg-profile) 백엔드(신원 스코프)."""

    def __init__(self, store: BaseStore | None = None) -> None:
        self._store = store or InMemoryStore(index=_fallback_index_config())

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
        limit = get_settings().profile_max_facts + _FACTS_QUERY_MARGIN
        items = await self._store.asearch((_FACTS_NS_ROOT, user_id), limit=limit)
        items.sort(key=lambda it: it.created_at)
        return [it.value["fact"] for it in items]

    async def add_fact(self, user_id: str, fact: str, *, cap: int | None = None) -> None:
        if not fact:
            return
        key = uuid.uuid4().hex
        await self._store.aput((_FACTS_NS_ROOT, user_id), key, {"fact": fact})
        if cap and cap > 0:
            async with _fact_lock(user_id):  # asearch→adelete 구간 직렬화(방어적, 아래 참조)
                items = await self._store.asearch(
                    (_FACTS_NS_ROOT, user_id), limit=cap + _FACTS_QUERY_MARGIN
                )
                if len(items) > cap:
                    items.sort(key=lambda it: it.created_at)
                    for stale in items[: len(items) - cap]:  # 최신 cap 개만 유지(recency-wins)
                        await self._store.adelete((_FACTS_NS_ROOT, user_id), stale.key)

    # ── transient 세션 버퍼 (승격 전 격리, REQ-PROF transient) ──
    async def append_session_ctx(self, key: str, text: str, *, cap: int | None = None) -> None:
        if not text:
            return
        async with _session_lock(key):  # get→put 원자성 보장(lost update 방지)
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
        async with _session_lock(key):  # append_session_ctx 와 동일 key 락으로 직렬화
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
_init_lock = asyncio.Lock()
_pending_cleanup: list[object] = []  # set_store() 가 못 닫은 이전 ctx — _get_store() 진입 시 정리


def set_store(store: BaseStore | None) -> None:
    """store 교체(테스트용) — None 이면 다음 사용 시 재초기화한다.

    기존 `_store_ctx`(실제 연결된 AsyncPostgresStore)가 있으면 정리 대기열에 넣는다.
    이 함수는 sync 라 여기서 직접 await 할 수 없고, `asyncio.get_running_loop()`
    fire-and-forget 태스크 방식은 **실행 중인 루프가 없으면 조용히 스킵**된다 —
    `tests/conftest.py` 의 sync autouse fixture 가 정확히 그 상황이라(이벤트 루프
    시작 전) 실제로는 한 번도 정리가 안 됐었다(app/core/pg_store.py 와 동일 버그,
    PR #46 후속 리뷰). 대신 다음 `_get_store()` 호출(반드시 async 컨텍스트) 시점에
    확실히 정리한다.
    """
    global _store, _store_ctx
    old_ctx = _store_ctx
    _store = store
    _store_ctx = None
    if old_ctx is not None:
        _pending_cleanup.append(old_ctx)


async def _drain_pending_cleanup() -> None:
    while _pending_cleanup:
        ctx = _pending_cleanup.pop()
        with contextlib.suppress(Exception):
            await ctx.__aexit__(None, None, None)


async def _get_store() -> BaseStore:
    """AsyncPostgresStore(pg-profile, pgvector 인덱스) 지연 초기화 — 실패 시 dev 한정 InMemoryStore 폴백.

    락 없는 지연 초기화는 콜드 스타트 시 동시 요청이 커넥션을 중복 생성하는
    레이스가 있다 — `_init_lock` 으로 초기화 블록 전체를 직렬화한다(pg_store.py
    와 동일 패턴, PR #47 리뷰).
    """
    global _store, _store_ctx, _fallback_warned
    await _drain_pending_cleanup()
    async with _init_lock:
        if _store is None:
            settings = get_settings()
            entered_ctx = None
            try:
                from langgraph.store.postgres.aio import AsyncPostgresStore  # noqa: PLC0415

                ctx = AsyncPostgresStore.from_conn_string(
                    settings.profile_db_url, index=_pg_index_config()
                )
                store = await asyncio.wait_for(
                    ctx.__aenter__(), timeout=settings.state_store_connect_timeout_s
                )
                entered_ctx = ctx  # __aenter__ 성공 후에만 __aexit__ 대상(부분 실패 정리용)
                await store.setup()
                _store_ctx = ctx
                _store = store
            except Exception as exc:
                if entered_ctx is not None:
                    # setup() 실패 등 부분 실패 — 이미 연 연결을 닫아 커넥션 누수를 막는다.
                    with contextlib.suppress(Exception):
                        await entered_ctx.__aexit__(type(exc), exc, exc.__traceback__)
                if settings.auth_mode == "jwks":
                    raise  # 운영 — 폴백 금지(프로필이 조용히 증발하면 안 된다)
                if not _fallback_warned:
                    logger.warning(
                        "pg-profile ProfileStore 연결 실패(%s) — InMemoryStore 폴백 "
                        "(dev 전용: 프로세스 재시작 시 프로필 증발)",
                        exc,
                    )
                    _fallback_warned = True
                _store = InMemoryStore(index=_fallback_index_config())
    return _store


async def get_profile_store() -> ProfileStore:
    """프로필 스토어 — pg-profile 연결 백엔드(요청마다 얇은 래퍼 재생성)."""
    return ProfileStore(await _get_store())


def reset_profile_store() -> None:
    """테스트 격리용 — 요약·fact·세션버퍼(InMemoryStore, fake embed) + 멱등 상태(processed_events)를 비운다.

    `_init_lock`·`_session_locks` 도 새로 만든다 — pytest-asyncio 는 테스트 함수마다
    새 이벤트 루프를 쓰는데, 모듈 전역 asyncio.Lock 을 여러 루프에 걸쳐 재사용하면
    이전 루프에 묶인 내부 상태로 다음 테스트에서 락 획득이 영원히 안 풀리는 hang 이
    발생할 수 있다.
    """
    global _init_lock
    set_store(InMemoryStore(index=_fallback_index_config()))
    processed_events.reset()
    _init_lock = asyncio.Lock()
    _session_locks.clear()
    _fact_locks.clear()
