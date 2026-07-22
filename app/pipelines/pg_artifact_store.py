"""AI 생성물 카탈로그 스토어 — pg-catalog(pgvector) 프로덕션 구현 (이슈 #31, api-spec §4.8).

CatalogArtifactStore(artifact_store.py, 인메모리)와 동일한 메서드 시그니처를 제공하는 동기
구현체 — 호출부(artifacts_batch.py·search_service.py)는 store 를 주입받아 쓰므로 무변경이다.
유닛 테스트는 계속 CatalogArtifactStore(인메모리)를 주입해 pg-catalog 없이도 빠르게 돈다
(tests/conftest.py InMemory 격리 컨벤션, 커밋 5066ecf 와 동일 원칙) — 이 구현체 자체의 테스트는
tests/integration/에 별도로 둔다(@pytest.mark.integration, 실 pg-catalog 필요).

배치 커서는 products 와 별도로 batch_state(단일 행) 테이블에 영속한다(db/catalog/init/00_products.sql).
"""

from __future__ import annotations

from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.pipelines.artifact_store import CatalogArtifact


def _to_list(value: object) -> list[float]:
    """pgvector 조회 결과(Vector | ndarray | list)를 list[float] 로 정규화한다."""
    if hasattr(value, "to_list"):
        return value.to_list()
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)  # type: ignore[arg-type]


def _row_to_artifact(row: tuple) -> CatalogArtifact:
    product_id, search_doc, embedding, extras = row
    return CatalogArtifact(
        product_id=product_id,
        search_doc=search_doc,
        embedding=_to_list(embedding),
        extras=extras or {},
    )


_SELECT_COLS = "product_id, search_doc, embedding, extras"


class PgCatalogArtifactStore:
    """pg-catalog products/batch_state 테이블 기반 스토어. CatalogArtifactStore 와 동일 인터페이스."""

    def __init__(self, dsn: str) -> None:
        self._pool = ConnectionPool(dsn, configure=register_vector, open=True)

    def close(self) -> None:
        self._pool.close()

    def upsert(self, artifact: CatalogArtifact) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO products (product_id, search_doc, embedding, extras, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (product_id) DO UPDATE SET
                    search_doc = EXCLUDED.search_doc,
                    embedding = EXCLUDED.embedding,
                    extras = EXCLUDED.extras,
                    updated_at = now()
                """,  # noqa: S608 - 컬럼 상수만 사용, 사용자 입력 없음
                (
                    artifact.product_id,
                    artifact.search_doc,
                    Vector(artifact.embedding),
                    Jsonb(artifact.extras),
                ),
            )

    def delete(self, product_id: int) -> None:  # HIDDEN — 생성물 제거(유령 상품 방지, §4.8)
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM products WHERE product_id = %s", (product_id,))

    def clear(self) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM products")

    @staticmethod
    def _replace_all(conn, artifacts: list[CatalogArtifact]) -> None:
        conn.execute("DELETE FROM products")
        for artifact in artifacts:
            conn.execute(
                """
                INSERT INTO products (product_id, search_doc, embedding, extras, updated_at)
                VALUES (%s, %s, %s, %s, now())
                """,
                (
                    artifact.product_id,
                    artifact.search_doc,
                    Vector(artifact.embedding),
                    Jsonb(artifact.extras),
                ),
            )

    @staticmethod
    def _set_cursor(conn, cursor: str | None) -> None:
        conn.execute(
            """
            INSERT INTO batch_state (id, cursor) VALUES (1, %s)
            ON CONFLICT (id) DO UPDATE SET cursor = EXCLUDED.cursor
            """,
            (cursor,),
        )

    def replace_all(self, artifacts: list[CatalogArtifact]) -> None:
        """전체 재구축 원자 교체 — 단일 트랜잭션(중간 실패 시 기존 데이터 보존, §4.8)."""
        with self._pool.connection() as conn, conn.transaction():
            self._replace_all(conn, artifacts)

    def replace_all_and_set_cursor(
        self, artifacts: list[CatalogArtifact], cursor: str | None
    ) -> None:
        """전체 생성물과 커서를 하나의 DB 트랜잭션으로 교체한다."""
        with self._pool.connection() as conn, conn.transaction():
            self._replace_all(conn, artifacts)
            self._set_cursor(conn, cursor)

    def get(self, product_id: int) -> CatalogArtifact | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM products WHERE product_id = %s", (product_id,)
            ).fetchone()
        return _row_to_artifact(row) if row else None

    def all(self) -> list[CatalogArtifact]:
        with self._pool.connection() as conn:
            rows = conn.execute(f"SELECT {_SELECT_COLS} FROM products").fetchall()
        return [_row_to_artifact(row) for row in rows]

    def count(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT count(*) FROM products").fetchone()
        return row[0] if row else 0

    def get_cursor(self) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT cursor FROM batch_state WHERE id = 1").fetchone()
        return row[0] if row else None

    def set_cursor(self, cursor: str | None) -> None:
        with self._pool.connection() as conn:
            self._set_cursor(conn, cursor)
