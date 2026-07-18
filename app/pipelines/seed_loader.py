"""시드 로더 CLI — 초기 전체 구축은 I-17 배치로 통합 (api-spec §4.8).

[정정 v0.5.1] 별도 시드 적재 경로 불필요 — AI 생성물 초기 전체 구축도 커서 0부터
동일한 I-17 pull 배치(spring_client.fetch_product_changes)로 처리한다(§4.8 복구·초기 구축).
이 CLI 는 로컬 개발에서 배치를 수동 1회 실행하는 진입점으로만 유지한다.

TODO(SPEC-CATALOG-DATA-001 재범위): fetch_product_changes 루프(hasMore) →
enrichment → embedding → upsert 를 호출하는 run_once() 구현.
"""

from __future__ import annotations


def load_seed(seed_dir: str) -> None:
    """[대체] 시드 디렉터리 적재 대신 I-17 배치 1회 실행(run_once)으로 통합 예정 (스텁, §4.8)."""
    raise NotImplementedError("superseded by the §4.8 artifacts batch (I-17) initial full build")
