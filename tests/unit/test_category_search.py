"""카테고리 top-k 랭킹 로직 테스트 (이슈 #59).

질의 임베딩과 카테고리 임베딩의 코사인으로 상위 k 카테고리를 뽑는 순수 로직만 검증한다
(오프라인 안전, vector_rank 와 동일 패턴). 라이브 pg <=> 조회는 통합 검증 소관.
"""

from __future__ import annotations

from app.pipelines.category_search import rank_categories


def test_returns_top_k_by_cosine_descending() -> None:
    """코사인 유사도 높은 순으로 상위 k 카테고리를 돌려준다."""
    query = [1.0, 0.0]
    candidates = [
        ("정반대", [0.0, 1.0]),  # cos 0
        ("동일", [1.0, 0.0]),  # cos 1
        ("중간", [1.0, 1.0]),  # cos ~0.707
    ]
    assert rank_categories(query, candidates, k=2) == ["동일", "중간"]


def test_k_limits_result_count() -> None:
    """k 개까지만 반환한다."""
    query = [1.0, 0.0]
    candidates = [("A", [1.0, 0.0]), ("B", [0.9, 0.1]), ("C", [0.1, 0.9])]
    assert rank_categories(query, candidates, k=1) == ["A"]


def test_k_larger_than_candidates_returns_all_ranked() -> None:
    """후보보다 k 가 크면 전체를 순위대로 돌려준다."""
    query = [1.0, 0.0]
    candidates = [("멀리", [0.0, 1.0]), ("가까이", [1.0, 0.0])]
    assert rank_categories(query, candidates, k=5) == ["가까이", "멀리"]


def test_excludes_candidates_without_embedding() -> None:
    """임베딩이 비어 있는 후보는 제외한다(아직 임베딩 안 채워진 행 방어)."""
    query = [1.0, 0.0]
    candidates = [("빈임베딩", []), ("정상", [1.0, 0.0])]
    assert rank_categories(query, candidates, k=5) == ["정상"]
