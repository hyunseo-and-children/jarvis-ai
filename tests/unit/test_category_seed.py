"""카테고리 시드 페어링 로직 테스트 (이슈 #59).

leaf 목록을 임베딩(주입형)해 (category, vector) 목록으로 짝짓는 순수 로직만 검증한다.
DB upsert(pg-catalog)는 통합 테스트 소관(@pytest.mark.integration).
"""

from __future__ import annotations

from app.pipelines.category_seed import embed_categories


def test_pairs_each_leaf_with_its_vector() -> None:
    """leaf 순서 그대로 임베딩 벡터와 1:1로 짝짓는다."""
    leaves = ["가전 > TV", "PC부품 > CPU"]

    def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[float(i)] for i, _ in enumerate(texts)]

    rows = embed_categories(leaves, fake_embed)
    assert rows == [("가전 > TV", [0.0]), ("PC부품 > CPU", [1.0])]


def test_deduplicates_preserving_order() -> None:
    """중복 leaf 는 한 번만 임베딩·수록한다(순서 보존)."""
    seen: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        seen.append(texts)
        return [[0.0] for _ in texts]

    rows = embed_categories(["A", "B", "A"], fake_embed)
    assert [c for c, _ in rows] == ["A", "B"]
    assert seen == [["A", "B"]]  # 중복 제거 후 한 번만 임베딩 호출


def test_empty_leaves_skip_embed() -> None:
    """빈 입력이면 임베딩을 호출하지 않고 빈 목록을 돌려준다."""
    called = False

    def fake_embed(texts: list[str]) -> list[list[float]]:
        nonlocal called
        called = True
        return []

    assert embed_categories([], fake_embed) == []
    assert called is False
