from app.pipelines.artifact_store import CatalogArtifact


def test_artifact_provenance_defaults_none():
    a = CatalogArtifact(product_id=1, search_doc="d", embedding=[0.0])
    assert a.embed_model is None and a.embed_dim is None
    assert a.embed_task is None and a.normalized is None


def test_artifact_carries_provenance():
    a = CatalogArtifact(
        product_id=1,
        search_doc="d",
        embedding=[0.0],
        embed_model="gemini-embedding-001",
        embed_dim=1536,
        embed_task="RETRIEVAL_DOCUMENT",
        normalized=True,
    )
    assert a.embed_model == "gemini-embedding-001"
    assert a.embed_dim == 1536 and a.embed_task == "RETRIEVAL_DOCUMENT"
    assert a.normalized is True
