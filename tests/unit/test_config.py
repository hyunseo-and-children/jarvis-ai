from app.core.config import Settings


def test_embedding_provenance_defaults():
    s = Settings(_env_file=None)
    assert s.embedding_task_document == "RETRIEVAL_DOCUMENT"
    assert s.embedding_task_query == "RETRIEVAL_QUERY"
    assert s.embedding_normalized is True
