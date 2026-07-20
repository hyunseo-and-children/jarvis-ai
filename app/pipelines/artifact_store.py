"""AI 생성물 카탈로그 스토어 — 인메모리 placeholder (api-spec §4.8, I-17 배치).

프로덕션은 pg-catalog(pgvector, config.catalog_db_url)로 이관한다. MVP 는 전역 인메모리로 동작만
재현한다 — 다른 스토어(ProfileStore·CartStateStore)와 동일 패턴. AI 생성물(extras·search_doc·임베딩)만
보관하고 상품 원본 컬럼 사본은 두지 않는다(CLAUDE.md). 배치 커서도 여기 영속한다
(자연 복구 — 페이지 처리 성공 후에만 전진, §4.8).
"""

from __future__ import annotations

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
        self._items.pop(product_id, None)  # DELISTED — 생성물 제거(유령 상품 방지, §4.8)

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


_store: CatalogArtifactStore | None = None


def get_catalog_store() -> CatalogArtifactStore:
    """전역 스토어 싱글턴 (MVP placeholder)."""
    global _store
    if _store is None:
        _store = CatalogArtifactStore()
    return _store


def reset_catalog_store() -> None:
    """테스트 격리용 리셋."""
    global _store
    _store = None
