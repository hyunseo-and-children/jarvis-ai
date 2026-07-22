"""decompose 카테고리 추출 파싱 테스트 (이슈 #59, 방식 A).

decompose 가 `categoryQueries: [{category, query}]` 를 `RouteDecision.category_queries`
(list[CategoryQuery])로 파싱하는지 검증한다. 실제 매핑(임베딩 보정)은 그래프 단계 소관.
"""

from __future__ import annotations

import json

from app.agents.buyer.recommendation.decompose import decompose


class _FakeLLM:
    """지정 raw JSON 문자열을 fast tier 에서 돌려주는 최소 LLM."""

    def __init__(self, raw: str) -> None:
        self._raw = raw

    async def complete(
        self, *, system: str, user: str, tier: str, max_tokens: int = 1024, json_output: bool = True
    ) -> str:
        return self._raw

    async def stream(self, *, system: str, user: str, tier: str, max_tokens: int = 1024):
        yield "x"


def _raw(**over) -> str:
    base = {"intent": "recommend", "reply": "", "semanticQuery": "q", "filters": {}}
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


async def _run(raw: str, **kw):
    return await decompose(
        _FakeLLM(raw), query="발화", prior_filters=None, profile_summary=None, tier="fast", **kw
    )


async def test_parses_single_category_query() -> None:
    """단일 카테고리 추측 → category_queries 길이 1, raw/query 매핑."""
    d = await _run(
        _raw(categoryQueries=[{"category": "가전 > 이어폰/헤드폰", "query": "무선 이어폰"}])
    )
    assert len(d.category_queries) == 1
    assert d.category_queries[0].raw_category == "가전 > 이어폰/헤드폰"
    assert d.category_queries[0].query == "무선 이어폰"


async def test_parses_multiple_category_queries() -> None:
    """상황형 → 여러 카테고리 추출."""
    d = await _run(
        _raw(
            categoryQueries=[
                {"category": "여행/캠핑 > 여행용품", "query": "여행 자물쇠"},
                {"category": "가전 > 어댑터", "query": "여행용 어댑터"},
            ]
        )
    )
    assert [c.raw_category for c in d.category_queries] == ["여행/캠핑 > 여행용품", "가전 > 어댑터"]


async def test_missing_category_queries_yields_empty() -> None:
    """categoryQueries 누락 → 빈 리스트(그래프에서 발화 폴백)."""
    d = await _run(_raw())
    assert d.category_queries == []


async def test_null_category_allowed() -> None:
    """category=null 추측 허용(그래프에서 발화 폴백으로 흡수)."""
    d = await _run(_raw(categoryQueries=[{"category": None, "query": "집들이 선물"}]))
    assert len(d.category_queries) == 1
    assert d.category_queries[0].raw_category is None
    assert d.category_queries[0].query == "집들이 선물"


async def test_truncates_to_fanout_max() -> None:
    """category_fanout_max 로 추출 개수를 절단한다(하드코딩 금지)."""
    many = [{"category": f"c{i} > m{i}", "query": f"q{i}"} for i in range(10)]
    d = await _run(_raw(categoryQueries=many), category_fanout_max=3)
    assert len(d.category_queries) == 3
