"""카테고리 사전 시드 (이슈 #59) — leaf 임베딩 후 pg-catalog `categories` 테이블 채움.

발화→카테고리 매핑(임베딩 top-k + LLM 택일 하이브리드)의 준비 단계. 카테고리 leaf 를
미리 임베딩해 DB 에 넣어두면 런타임엔 top-k 검색만 하면 된다.

임베딩은 주입형(embed 콜러블) — 프로덕션 재현은 `embed_texts`(gemini-embedding-001),
테스트/오프라인 재적재는 캐시·페이크를 주입한다(app.pipelines.embedding 과 동일 패턴).
런타임 top-k 검색은 별도 모듈 소관이며, 여기는 1회 빌드 진입점이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

EmbedFn = Callable[[list[str]], list[list[float]]]


def embed_categories(leaves: Sequence[str], embed: EmbedFn) -> list[tuple[str, list[float]]]:
    """중복 제거(순서 보존) 후 임베딩해 (category, vector) 목록을 만든다.

    빈 입력이면 임베딩을 호출하지 않는다(불필요한 API 호출 회피).
    """
    unique = list(dict.fromkeys(leaves))
    if not unique:
        return []
    vectors = embed(unique)
    return list(zip(unique, vectors, strict=True))


def load_leaves(source_path: str) -> list[str]:
    """`categories.json`(문자열 배열)에서 leaf 목록을 로드한다. 비문자열은 거부."""
    data = json.loads(Path(source_path).read_bytes().decode("utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError(f"카테고리 사전은 문자열 배열이어야 함: {source_path}")
    return data


def upsert_categories(dsn: str, rows: Sequence[tuple[str, list[float]]], model: str) -> int:
    """(category, vector) 목록을 categories 테이블에 upsert 한다(단일 트랜잭션).

    category UNIQUE 충돌 시 임베딩·모델·시각을 갱신한다(재시드 멱등). 채운 행 수를 돌려준다.
    """
    from pgvector import Vector  # noqa: PLC0415 - LAZY import(pg 미설치 환경 유닛테스트 회피)
    from pgvector.psycopg import register_vector  # noqa: PLC0415
    from psycopg_pool import ConnectionPool  # noqa: PLC0415

    pool = ConnectionPool(dsn, configure=register_vector, open=True)
    try:
        with pool.connection() as conn, conn.transaction():
            for category, vector in rows:
                conn.execute(
                    """
                    INSERT INTO categories (category, embedding, embedding_model, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (category) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        embedding_model = EXCLUDED.embedding_model,
                        updated_at = now()
                    """,  # noqa: S608 - 컬럼 상수만 사용, 사용자 입력 없음
                    (category, Vector(vector), model),
                )
        return len(rows)
    finally:
        pool.close()


def seed_from_file(source_path: str, dsn: str, embed: EmbedFn, model: str) -> int:
    """소스 파일 → 임베딩 → categories upsert 를 한 번에 수행한다(1회 빌드)."""
    rows = embed_categories(load_leaves(source_path), embed)
    return upsert_categories(dsn, rows, model)
