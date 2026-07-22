"""카테고리 top-k 검색 (이슈 #59) — 발화→카테고리 하이브리드의 후보 추출 단계.

질의 임베딩과 `categories` 테이블 임베딩의 코사인 유사도로 상위 k 후보를 뽑는다.
그 소수 후보만 LLM 택일에 넘긴다(LLM 은 여기서 안 부른다).

`rank_categories` 는 순수 랭킹(오프라인 안전, search_service.vector_rank 와 동일 패턴)이라
유닛 테스트로 검증한다. `search_categories_pg` 는 pgvector `<=>` + HNSW 로 DB 에서 직접
top-k 만 조회하는 라이브 경로 — 통합(실 pg-catalog) 검증 소관이다.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.search_service import _cosine


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


def search_categories_pg(query_vec: list[float], dsn: str, *, k: int) -> list[str]:
    """pg-catalog `categories` 에서 코사인 top-k 카테고리를 직접 조회한다(HNSW).

    임베딩 미채움(NULL) 행은 제외한다. `<=>` 는 코사인 거리(작을수록 유사).
    """
    from pgvector import Vector  # noqa: PLC0415 - LAZY import(유닛테스트 pg 의존 회피)
    from pgvector.psycopg import register_vector  # noqa: PLC0415
    from psycopg_pool import ConnectionPool  # noqa: PLC0415

    pool = ConnectionPool(dsn, configure=register_vector, open=True)
    try:
        with pool.connection() as conn:
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
    finally:
        pool.close()
