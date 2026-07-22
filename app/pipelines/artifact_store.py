"""AI 생성물 카탈로그 스토어 (api-spec §4.8, I-17 배치).

CatalogArtifact·CatalogArtifactStore(인메모리)·ArtifactStore(공유 계약)를 정의한다.
[2026-07-20 이슈 #31] 프로덕션 진입점 get_catalog_store()는 pg-catalog(pgvector)로 이관 완료 —
PgCatalogArtifactStore(pg_artifact_store.py)를 반환한다. CatalogArtifactStore(인메모리)는
테스트 주입용(격리, tests/conftest.py InMemory 컨벤션과 동일 원칙)과 full_rebuild 임시 버퍼로
계속 쓰인다. AI 생성물(extras·search_doc·임베딩)만 보관하고 상품 원본 컬럼 사본은 두지 않는다(CLAUDE.md).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class CatalogArtifact:
    """상품 1건의 AI 생성물 (원본 컬럼 사본 아님). name·category 는 후보 표시/디버깅용 최소 보조."""

    product_id: int
    search_doc: str
    embedding: list[float]
    extras: dict = field(default_factory=dict)
    name: str | None = None
    category: str | None = None


@runtime_checkable
class ArtifactStore(Protocol):
    """CatalogArtifactStore(인메모리)·PgCatalogArtifactStore(pg-catalog) 공유 계약 (이슈 #31).

    인터페이스가 바뀌면 양쪽 구현체를 함께 고쳐야 한다 — 타입체커가 시그니처 드리프트를 잡아준다.
    """

    def upsert(self, artifact: CatalogArtifact) -> None: ...
    def delete(self, product_id: int) -> None: ...
    def clear(self) -> None: ...
    def replace_all(self, artifacts: list[CatalogArtifact]) -> None: ...
    def get(self, product_id: int) -> CatalogArtifact | None: ...
    def all(self) -> list[CatalogArtifact]: ...
    def count(self) -> int: ...
    def get_cursor(self) -> str | None: ...
    def set_cursor(self, cursor: str | None) -> None: ...


class CatalogArtifactStore:
    """AI 생성물 인메모리 스토어 (productId 키) + 배치 커서."""

    def __init__(self) -> None:
        self._items: dict[int, CatalogArtifact] = {}
        self._cursor: str | None = None

    def upsert(self, artifact: CatalogArtifact) -> None:
        self._items[artifact.product_id] = artifact

    def delete(self, product_id: int) -> None:
        self._items.pop(product_id, None)  # HIDDEN — 생성물 제거(유령 상품 방지, §4.8)

    def clear(self) -> None:
        self._items.clear()

    def replace_all(self, artifacts: list[CatalogArtifact]) -> None:
        """전체 재구축 원자 교체 — 성공한 임시 결과로 한 번에 스왑(중간 실패 시 기존 데이터 보존)."""
        self._items = {a.product_id: a for a in artifacts}

    def get(self, product_id: int) -> CatalogArtifact | None:
        return self._items.get(product_id)

    def all(self) -> list[CatalogArtifact]:
        return list(self._items.values())

    def count(self) -> int:
        return len(self._items)

    def get_cursor(self) -> str | None:
        return self._cursor

    def set_cursor(self, cursor: str | None) -> None:
        self._cursor = cursor


_store: ArtifactStore | None = None
_store_lock = threading.Lock()


def get_catalog_store() -> ArtifactStore:
    """전역 스토어 싱글턴 — pg-catalog(PgCatalogArtifactStore) 반환 (이슈 #31).

    pg_artifact_store 를 함수 내부에서 LAZY import 한다(artifact_store.py → pg_artifact_store.py
    → artifact_store.py 순환 임포트 회피). 테스트는 store 를 직접 주입해 이 경로를 타지 않는다.

    이중 확인 락(double-checked locking) — 스케줄러(별도 OS 스레드)와 요청 처리(이벤트루프)가
    최초 호출에 동시 진입하면 락 없이는 커넥션 풀이 중복 생성되고 하나가 샌다(PR #42 리뷰).
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from app.core.config import get_settings  # noqa: PLC0415
                from app.pipelines.pg_artifact_store import PgCatalogArtifactStore  # noqa: PLC0415

                _store = PgCatalogArtifactStore(get_settings().catalog_db_url)
    return _store


def reset_catalog_store() -> None:
    """테스트 격리용 리셋 — 연결 풀이 있으면 정리 후 캐시를 비운다."""
    global _store
    if _store is not None and hasattr(_store, "close"):
        _store.close()
    _store = None
