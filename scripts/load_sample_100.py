"""sample_100/ai의 사전 임베딩 문서를 실제 pg-catalog products 테이블에 적재한다.

Google API를 다시 호출하지 않는다. 문서 벡터의 모델·차원·task·L2 norm을 검증한 뒤
기존 AI 런타임이 읽는 `products` 테이블에 product_id 기준으로 멱등 upsert한다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from pgvector import Vector  # noqa: E402
from pgvector.psycopg import register_vector  # noqa: E402
from psycopg import connect  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def load_documents(
    path: Path, expected_count: int, expected_dim: int, expected_model: str
) -> list[dict[str, Any]]:
    documents = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids = {int(row["product_id"]) for row in documents}
    if len(documents) != expected_count or len(ids) != expected_count:
        raise ValueError(
            f"documents.jsonl은 중복 없이 {expected_count}건이어야 합니다: "
            f"rows={len(documents)}, unique={len(ids)}"
        )

    for row in documents:
        product_id = row["product_id"]
        embedding = row.get("embedding") or []
        if len(embedding) != expected_dim or row.get("embed_dim") != expected_dim:
            raise ValueError(f"product_id={product_id}: embedding 차원이 {expected_dim}이 아닙니다")
        if row.get("embed_model") != expected_model:
            raise ValueError(
                f"product_id={product_id}: embedding 모델이 {expected_model}이 아닙니다"
            )
        if row.get("embed_task") != "RETRIEVAL_DOCUMENT":
            raise ValueError(f"product_id={product_id}: embed_task가 RETRIEVAL_DOCUMENT가 아닙니다")
        if row.get("normalized") is not True:
            raise ValueError(f"product_id={product_id}: normalized=true가 아닙니다")
        norm = math.sqrt(sum(float(value) ** 2 for value in embedding))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-4):
            raise ValueError(f"product_id={product_id}: L2 norm이 1이 아닙니다 ({norm})")
    return documents


def upsert_documents(documents: list[dict[str, Any]]) -> int:
    settings = get_settings()
    with connect(settings.catalog_db_url) as conn:
        register_vector(conn)
        if conn.execute("SELECT to_regclass('public.products')").fetchone()[0] is None:
            raise RuntimeError(
                "pg-catalog products 테이블이 없습니다. docker compose up -d pg-catalog를 먼저 실행하세요"
            )
        with conn.transaction():
            for row in documents:
                product_id = int(row["product_id"])
                conn.execute(
                    """
                    INSERT INTO products
                        (product_id, search_doc, embedding, extras, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (product_id) DO UPDATE SET
                        search_doc = EXCLUDED.search_doc,
                        embedding = EXCLUDED.embedding,
                        extras = EXCLUDED.extras,
                        updated_at = now()
                    """,
                    (
                        product_id,
                        row["search_doc"],
                        Vector(row["embedding"]),
                        Jsonb(row.get("extras") or {}),
                    ),
                )
        ids = [int(row["product_id"]) for row in documents]
        loaded = conn.execute(
            "SELECT count(*) FROM products WHERE product_id = ANY(%s)", (ids,)
        ).fetchone()[0]
    return int(loaded)


def main() -> None:
    workspace = Path(__file__).resolve().parents[2]
    default_bundle = workspace / "sample_100"
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, default=default_bundle / "ai/documents.jsonl")
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # 오프라인 벡터 적재는 인증과 무관 — jwks 의 GOOGLE_API_KEY fail-fast(config §2.3 검증)를
    # 우회한다(§4 — 사전 임베딩 적재는 키 없이 가능).
    os.environ["AUTH_MODE"] = "dev"

    if not args.documents.exists():
        raise SystemExit(
            f"--documents 경로가 없습니다: {args.documents}\n"
            "sample_100 번들(ai/documents.jsonl)은 이 repo에 포함되지 않습니다. "
            "워크스페이스 형제 경로 ../sample_100 에 배치하거나 --documents 로 명시하세요."
        )

    settings = get_settings()
    documents = load_documents(
        args.documents.resolve(),
        expected_count=args.expected_count,
        expected_dim=settings.embedding_dim,
        expected_model=settings.embedding_model_id,
    )
    if args.dry_run:
        print(
            f"validated {len(documents)} documents: model={settings.embedding_model_id}, "
            f"dim={settings.embedding_dim}, task=RETRIEVAL_DOCUMENT, normalized=true"
        )
        return

    loaded = upsert_documents(documents)
    if loaded != len(documents):
        raise RuntimeError(f"적재 후 행 수 불일치: expected={len(documents)}, actual={loaded}")
    print(f"loaded {loaded} embedded documents into pg-catalog.products")


if __name__ == "__main__":
    main()
