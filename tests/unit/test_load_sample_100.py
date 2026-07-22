"""sample_100 로더의 원본 상품 비보관 계약."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from scripts import load_sample_100


def test_loader_dry_run_requires_only_generated_documents(tmp_path, monkeypatch, capsys):
    documents = tmp_path / "documents.jsonl"
    documents.write_text(
        json.dumps(
            {
                "product_id": 1,
                "search_doc": "AI 생성 검색 문서",
                "embedding": [1.0, 0.0],
                "extras": {"tags": ["여행"]},
                "embed_model": "test-model",
                "embed_dim": 2,
                "embed_task": "RETRIEVAL_DOCUMENT",
                "normalized": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        load_sample_100,
        "get_settings",
        lambda: SimpleNamespace(embedding_dim=2, embedding_model_id="test-model"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "load_sample_100.py",
            "--documents",
            str(documents),
            "--expected-count",
            "1",
            "--dry-run",
        ],
    )

    load_sample_100.main()

    assert "validated 1 documents" in capsys.readouterr().out
