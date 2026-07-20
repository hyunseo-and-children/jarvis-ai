"""골든셋 비교 하니스 — 방식1(벡터검색) vs 방식2(재정렬) 랭킹 품질 대조 (api-spec §4.8, 이슈 #7).

라이브 Spring/torch 없이 productId 랭킹 수준에서 오프라인 비교한다(착수 방침 2026-07-20).
  방식1: vector_rank(query 임베딩 vs 저장 임베딩)
  방식2: Spring 후보(candidates)를 query 임베딩으로 재정렬
지표: recall@k, 두 방식의 상위-k 겹침(overlap). embed·candidates 는 주입형(오프라인).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from app.pipelines.artifact_store import CatalogArtifactStore
from app.services.search_service import _cosine, vector_rank


@dataclass
class GoldenCase:
    query: str
    relevant_ids: set[int]


@dataclass
class MethodScore:
    method: str
    mean_recall_at_k: float
    per_case: list[float]


@dataclass
class CompareReport:
    k: int
    method1: MethodScore  # 방식1(vector)
    method2: MethodScore  # 방식2(rerank)
    mean_overlap: float   # 두 방식 상위-k 겹침 비율


def recall_at_k(ranked_ids: Sequence[int], relevant: Iterable[int], k: int) -> float:
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    top = set(ranked_ids[:k])
    return len(top & relevant_set) / len(relevant_set)


def _rerank_candidates(
    query_vec: list[float], candidate_ids: Sequence[int], store: CatalogArtifactStore, k: int
) -> list[int]:
    scored = []
    for pid in candidate_ids:
        art = store.get(pid)
        s = _cosine(query_vec, art.embedding) if art and art.embedding else -1.0
        scored.append((s, pid))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [pid for _, pid in scored[:k]]


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def compare_backends(
    cases: Sequence[GoldenCase],
    *,
    store: CatalogArtifactStore,
    embed: Callable[[list[str]], list[list[float]]],
    candidates: Callable[[str], list[int]],
    k: int = 10,
) -> CompareReport:
    """각 케이스에 방식1/방식2 랭킹을 만들어 recall@k·overlap 을 비교한다.

    candidates: query → 방식2용 Spring 후보 productId(오프라인 주입). embed: 텍스트 임베딩(주입).
    """
    r1: list[float] = []
    r2: list[float] = []
    overlaps: list[float] = []
    for case in cases:
        qvec = embed([case.query])[0]
        ranked1 = vector_rank(qvec, store, k=k)
        ranked2 = _rerank_candidates(qvec, candidates(case.query), store, k)
        r1.append(recall_at_k(ranked1, case.relevant_ids, k))
        r2.append(recall_at_k(ranked2, case.relevant_ids, k))
        overlaps.append(len(set(ranked1[:k]) & set(ranked2[:k])) / k if k else 0.0)
    return CompareReport(
        k=k,
        method1=MethodScore("방식1(vector)", _mean(r1), r1),
        method2=MethodScore("방식2(rerank)", _mean(r2), r2),
        mean_overlap=_mean(overlaps),
    )
