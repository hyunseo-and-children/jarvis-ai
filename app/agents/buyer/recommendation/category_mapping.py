"""카테고리 매핑 오케스트레이션 (이슈 #59, 방식 A — LLM 추측 → 임베딩 보정).

decompose 가 추측한 카테고리(raw)들을 실제 DB 카테고리(canonical)로 보정한다. LLM 재호출
없이 exact match → 임베딩 최근접 → 발화 폴백 순으로 처리하며, **never-null**(정상 흐름은 항상
canonical 을 낸다). embed·search·exact 는 주입형 seam 이라 유닛테스트가 pg·API 없이 돈다.

블로킹 호출(embed_texts·pgvector 조회·exact SELECT)은 asyncio.to_thread 로 감싸 이벤트 루프를
막지 않는다(search_service 와 동일 패턴).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence

from app.agents.buyer.recommendation.state import CategoryQuery
from app.pipelines.category_search import exact_lookup as _exact_lookup
from app.pipelines.category_search import search_categories_pg as _search_top_k
from app.pipelines.embedding import embed_texts as _embed_texts

logger = logging.getLogger(__name__)

EmbedFn = Callable[[list[str]], list[list[float]]]
SearchFn = Callable[..., list[str]]
ExactFn = Callable[[Sequence[str], str], set[str]]


def _dedup_truncate(
    legs: list[tuple[str, str | None]], fanout_max: int
) -> list[tuple[str, str | None]]:
    """canonical 기준 순서보존 dedup(첫 query 유지) 후 fanout_max 로 절단."""
    seen: set[str] = set()
    out: list[tuple[str, str | None]] = []
    for cat, query in legs:
        if cat not in seen:
            seen.add(cat)
            out.append((cat, query))
    return out[:fanout_max]


async def map_categories(
    *,
    category_queries: Sequence[CategoryQuery],
    utterance: str,
    settings,
    embed: EmbedFn = _embed_texts,
    search_top_k: SearchFn = _search_top_k,
    exact_lookup: ExactFn = _exact_lookup,
) -> list[tuple[str, str | None]]:
    """decompose 추측들을 canonical (category, query) leg 리스트로 보정한다(never-null).

    각 leg 의 query 는 그 카테고리 전용 검색 키워드(fan-out leg keyword, §6·§9) — 매핑 전
    추측(raw)이 어디로 보정되든 원 추측의 query 를 그대로 이어 붙인다.
    per-category 규칙(우선순위): (1) raw 가 exact match → raw. (2) raw 있으나 exact 아님 →
    embed(raw) 최근접. (3) raw==null → embed(발화) top-1 폴백. (4) 빈 리스트 → 발화 폴백 1건.
    (5) 하드 실패(embed/DB 예외) → raw 그대로(있으면)·null 스킵.
    """
    dsn = settings.catalog_db_url
    k = settings.category_top_k
    fanout_max = settings.category_fanout_max
    queries = list(category_queries) or [CategoryQuery(None, None)]  # 빈 리스트 → 발화 폴백
    raws = [q.raw_category for q in queries]
    qtexts = [q.query for q in queries]  # leg keyword 로 이어 붙일 원 추측 query

    try:
        non_null = [r for r in raws if r]
        exact = await asyncio.to_thread(exact_lookup, non_null, dsn) if non_null else set()
        # 보정(임베딩) 필요한 것: exact 가 아닌 raw(=raw 앵커) 또는 null(=발화 앵커)
        need_idx = [i for i, r in enumerate(raws) if not (r and r in exact)]
        anchors = [raws[i] or utterance for i in need_idx]
        vecs = await asyncio.to_thread(embed, anchors) if anchors else []
        # 앵커별 최근접 조회를 병렬 실행 — 카테고리 여러 개(상황형 질의)일수록 직렬 지연이
        # leg 수만큼 쌓이므로 gather 로 동시 실행한다(순서 보존 → need_idx 매핑 유지, §6).
        hit_lists = await asyncio.gather(
            *(asyncio.to_thread(search_top_k, vecs[j], dsn, k=k) for j in range(len(need_idx)))
        )
        nearest: dict[int, str | None] = {
            need_idx[j]: (hits[0] if hits else None) for j, hits in enumerate(hit_lists)
        }
    except Exception as exc:  # noqa: BLE001 - 하드 실패는 사유 무관 degrade(never-null 유지)
        logger.warning("category_hard_fail", extra={"reason": str(exc)})
        return _dedup_truncate([(r, qtexts[i]) for i, r in enumerate(raws) if r], fanout_max)

    result: list[tuple[str, str | None]] = []
    for i, r in enumerate(raws):
        if r and r in exact:
            logger.info("category_mapped", extra={"raw": r, "canonical": r})
            result.append((r, qtexts[i]))
            continue
        canonical = nearest.get(i)
        if canonical:
            event = "category_repaired" if r else "category_fallback_top1"
            logger.info(event, extra={"raw": r, "canonical": canonical})
            result.append((canonical, qtexts[i]))
        else:
            # 임베딩 조회는 정상 완료됐지만 히트 0건 → canonical 없이 드롭. never-null 정책상
            # 드문 상태(categories 미시드·임베딩 결측 등)라 조용히 넘기지 않고 관측 가능하게
            # 남긴다 — 매 턴 전부 이 분기면 카테고리 매핑이 사실상 무력화된 신호(PR #73 리뷰).
            logger.warning("category_unmapped", extra={"raw": r})
    return _dedup_truncate(result, fanout_max)
