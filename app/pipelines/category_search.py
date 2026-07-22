"""카테고리 top-k 검색 (이슈 #59) — 발화→카테고리 하이브리드의 후보 추출 단계.

질의 임베딩과 `categories` 테이블 임베딩의 코사인 유사도로 상위 k 후보를 뽑는다.
그 소수 후보만 LLM 택일에 넘긴다(LLM 은 여기서 안 부른다).

`rank_categories` 는 순수 랭킹(오프라인 안전, search_service.vector_rank 와 동일 패턴)이라
유닛 테스트로 검증한다. `search_categories_pg` 는 pgvector `<=>` + HNSW 로 DB 에서 직접
top-k 만 조회하는 라이브 경로 — 통합(실 pg-catalog) 검증 소관이다.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from app.services.search_service import _cosine

_pool = None  # 모듈 전역 ConnectionPool (lazy 생성·프로세스 수명 재사용)
_pool_lock = threading.Lock()


def _get_pool(dsn: str):
    """모듈 전역 ConnectionPool 을 lazy 하게 한 번만 만들어 재사용한다(요청마다 open/close 회피).

    exact_lookup·search_categories_pg 가 recommend 턴마다 여러 번 호출되는데 매번 풀을 새로
    open/close 하면 연결 수립 비용이 직렬로 쌓인다(PR #73 리뷰). pg_artifact_store·
    processed_events 등 다른 pg 경로와 동일한 모듈 캐싱 패턴이다. vector 쿼리를 위해
    register_vector 를 configure 로 걸어 exact_lookup(비-vector)과 단일 풀을 공유한다.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from pgvector.psycopg import register_vector  # noqa: PLC0415 - LAZY(유닛 pg 의존 회피)
                from psycopg_pool import ConnectionPool  # noqa: PLC0415

                _pool = ConnectionPool(dsn, configure=register_vector, open=True)
    return _pool


def rank_categories(
    query_vec: list[float],
    candidates: Sequence[tuple[str, list[float]]],
    *,
    k: int,
) -> list[str]:
    """질의 임베딩과 코사인 유사도 높은 순으로 상위 k 카테고리(문자열)를 돌려준다.

    임베딩이 비어 있는 후보(_cosine → -1.0)는 최하위로 밀려 사실상 제외된다.
    """
    scored = [(_cosine(query_vec, emb), cat) for cat, emb in candidates if emb]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [cat for _, cat in scored[:k]]


def exact_lookup(values: Sequence[str], dsn: str) -> set[str]:
    """`categories` 테이블에 그대로 존재하는 값들의 집합을 1왕복으로 조회한다(이슈 #59).

    decompose 추측이 이미 canonical(실재 카테고리)인지 판정한다 — 임베딩 보정 앞단의 빠른 길.
    빈 입력은 즉시 빈 집합(불필요한 조회 회피).
    """
    vals = [v for v in values if v]
    if not vals:
        return set()
    with _get_pool(dsn).connection() as conn:
        rows = conn.execute(
            "SELECT category FROM categories WHERE category = ANY(%s)",
            (vals,),
        ).fetchall()
    return {row[0] for row in rows}


def search_categories_pg(query_vec: list[float], dsn: str, *, k: int) -> list[str]:
    """pg-catalog `categories` 에서 코사인 top-k 카테고리를 직접 조회한다(HNSW).

    임베딩 미채움(NULL) 행은 제외한다. `<=>` 는 코사인 거리(작을수록 유사).
    """
    from pgvector import Vector  # noqa: PLC0415 - LAZY import(유닛테스트 pg 의존 회피)

    with _get_pool(dsn).connection() as conn:
        rows = conn.execute(
            """
            SELECT category
            FROM categories
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s
            LIMIT %s
            """,  # noqa: S608 - 컬럼 상수만 사용, 파라미터 바인딩
            (Vector(query_vec), k),
        ).fetchall()
    return [row[0] for row in rows]
