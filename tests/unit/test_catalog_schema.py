"""pg-catalog에는 I-17 상품 원본의 독립 사본을 보관하지 않는다."""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = _REPO_ROOT / "db/catalog/init/00_products.sql"
_DROP_RAW_COLUMNS = _REPO_ROOT / "db/catalog/migrations/20260722_drop_raw_product_columns.sql"


def test_products_schema_contains_only_generated_artifact_columns():
    schema = _SCHEMA.read_text(encoding="utf-8")
    products_body = schema.split("CREATE TABLE IF NOT EXISTS products (", 1)[1].split(");", 1)[0]

    assert not re.search(r"^\s*(name|category)\s+", products_body, flags=re.MULTILINE)


def test_existing_catalog_has_idempotent_raw_column_removal_migration():
    migration = _DROP_RAW_COLUMNS.read_text(encoding="utf-8")

    assert "DROP COLUMN IF EXISTS name" in migration
    assert "DROP COLUMN IF EXISTS category" in migration
