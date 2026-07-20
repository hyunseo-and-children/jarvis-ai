"""get_catalog_store() 프로덕션 전환 테스트 (이슈 #31) — 실 pg-catalog 없이 팩토리 배선만 검증.

PgCatalogArtifactStore 클래스 자체를 fake 로 대체해 라이브 DB 연결 없이 "get_catalog_store()가
싱글턴으로 PgCatalogArtifactStore를 생성·캐시하고, reset_catalog_store()가 close() 후 리셋하는지"만
확인한다. PgCatalogArtifactStore 자체 동작은 tests/integration/test_pg_artifact_store.py 소관.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.pipelines import artifact_store as store_mod


class _FakePgStore:
    created_dsns: list[str] = []
    closed: list[bool] = []

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        _FakePgStore.created_dsns.append(dsn)

    def close(self) -> None:
        _FakePgStore.closed.append(True)


def test_get_catalog_store_returns_cached_pg_backed_singleton(monkeypatch):
    _FakePgStore.created_dsns.clear()
    _FakePgStore.closed.clear()
    monkeypatch.setattr("app.pipelines.pg_artifact_store.PgCatalogArtifactStore", _FakePgStore)
    store_mod.reset_catalog_store()

    first = store_mod.get_catalog_store()
    second = store_mod.get_catalog_store()

    assert first is second
    assert isinstance(first, _FakePgStore)
    assert first.dsn == get_settings().catalog_db_url
    assert _FakePgStore.created_dsns == [get_settings().catalog_db_url]  # 1회만 생성(캐시)

    store_mod.reset_catalog_store()
    assert _FakePgStore.closed == [True]
