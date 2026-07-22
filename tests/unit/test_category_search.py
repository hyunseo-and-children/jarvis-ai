"""카테고리 top-k 랭킹 로직 테스트 (이슈 #59).

질의 임베딩과 카테고리 임베딩의 코사인으로 상위 k 카테고리를 뽑는 순수 로직만 검증한다
(오프라인 안전, vector_rank 와 동일 패턴). 라이브 pg <=> 조회는 통합 검증 소관.
"""

from __future__ import annotations

import pytest

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


def test_get_pool_caches_single_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_pool 은 ConnectionPool 을 한 번만 만들어 재사용한다 — 요청마다 open/close 회피(PR #73 리뷰 #2).

    exact_lookup·search_categories_pg 가 recommend 턴마다 여러 번 호출되는데 매번 풀을 새로
    open/close 하면 연결 수립 비용이 직렬로 쌓인다. 다른 pg 경로와 동일하게 모듈 전역 캐싱한다.
    """
    import psycopg_pool

    from app.pipelines import category_search as cs

    monkeypatch.setattr(cs, "_pool", None)  # 캐시 초기화(테스트 격리)
    created: dict = {"n": 0, "kw": []}

    class _FakePool:
        def __init__(self, dsn: str, **kw) -> None:
            created["n"] += 1
            created["kw"].append(kw)

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _FakePool)

    p1 = cs._get_pool("postgresql://x")
    p2 = cs._get_pool("postgresql://x")
    assert created["n"] == 1  # 두 번 호출해도 풀 생성은 1회(재사용)
    assert p1 is p2
    assert created["kw"][0].get("configure") is not None  # vector 쿼리용 register_vector configure
