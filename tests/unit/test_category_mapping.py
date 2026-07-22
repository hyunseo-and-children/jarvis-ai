"""카테고리 매핑(방식 A) never-null 테스트 (이슈 #59).

decompose 추측(raw)을 임베딩으로 실제 DB 카테고리에 보정한다. embed·search·exact 를 주입형
fake 로 대체해 다섯 never-null 분기와 멀티 dedup·상한 절단을 검증한다.
결과는 fan-out leg 용 (canonical, query) 페어 — query 는 그 카테고리의 검색 키워드(§6·§9).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.buyer.recommendation.category_mapping import map_categories
from app.agents.buyer.recommendation.state import CategoryQuery


def _settings(*, top_k: int = 5, fanout_max: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        catalog_db_url="postgresql://x", category_top_k=top_k, category_fanout_max=fanout_max
    )


class _FakeMapper:
    """embed↔search 를 인덱스 인코딩으로 연결해, anchor 텍스트별 최근접을 제어한다."""

    def __init__(self, *, exact: set[str], nearest: dict[str, str], embed_raises: bool = False):
        self._exact = exact
        self._nearest = nearest
        self._embed_raises = embed_raises
        self._embedded: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._embed_raises:
            raise RuntimeError("embed down")
        self._embedded = list(texts)
        return [[float(i)] for i in range(len(texts))]  # vec[0] = 배치 인덱스

    def search(self, vec: list[float], dsn: str, *, k: int) -> list[str]:
        text = self._embedded[int(vec[0])]
        hit = self._nearest.get(text)
        return [hit] if hit else []

    def exact_lookup(self, values, dsn: str) -> set[str]:
        return {v for v in values if v in self._exact}

    async def run(self, queries, utterance="발화", settings=None):
        return await map_categories(
            category_queries=queries,
            utterance=utterance,
            settings=settings or _settings(),
            embed=self.embed,
            search_top_k=self.search,
            exact_lookup=self.exact_lookup,
        )


async def test_exact_match_uses_raw() -> None:
    """raw 가 DB에 exact match → raw 그대로 canonical, query 보존."""
    m = _FakeMapper(exact={"PC부품 > CPU"}, nearest={})
    out = await m.run([CategoryQuery("PC부품 > CPU", "cpu")])
    assert out == [("PC부품 > CPU", "cpu")]


async def test_offlist_uses_nearest() -> None:
    """raw 가 exact 아님 → embed(raw) → 최근접 채택(거리 무관 항상), query 보존."""
    m = _FakeMapper(exact=set(), nearest={"무선 이어폰": "가전 > 이어폰/헤드폰"})
    out = await m.run([CategoryQuery("무선 이어폰", "이어폰")])
    assert out == [("가전 > 이어폰/헤드폰", "이어폰")]


async def test_null_raw_falls_back_to_utterance() -> None:
    """raw==null → embed(발화) → top-1, query(있으면) 보존."""
    m = _FakeMapper(exact=set(), nearest={"집들이 선물 추천": "생활/건강 > 생활용품"})
    out = await m.run([CategoryQuery(None, "집들이 선물")], utterance="집들이 선물 추천")
    assert out == [("생활/건강 > 생활용품", "집들이 선물")]


async def test_empty_queries_normalizes_to_utterance_fallback() -> None:
    """categoryQueries 빈 리스트 → 발화 폴백 1건(query 없음)으로 정규화."""
    m = _FakeMapper(exact=set(), nearest={"유럽여행 준비물": "여행/캠핑 > 여행용품"})
    out = await m.run([], utterance="유럽여행 준비물")
    assert out == [("여행/캠핑 > 여행용품", None)]


async def test_hard_failure_keeps_raw_skips_null() -> None:
    """embed/DB 다운 → raw 있으면 그대로(never-null degrade), null 은 스킵, query 보존."""
    m = _FakeMapper(exact=set(), nearest={}, embed_raises=True)
    out = await m.run([CategoryQuery("PC부품 > CPU", "cpu"), CategoryQuery(None, "뭐")])
    assert out == [("PC부품 > CPU", "cpu")]


async def test_multi_dedup_and_truncate() -> None:
    """서로 다른 raw 가 같은 canonical 로 모이면 dedup(첫 query 유지), fanout_max 로 절단."""
    m = _FakeMapper(
        exact=set(),
        nearest={
            "이어폰": "가전 > 이어폰/헤드폰",
            "무선이어폰": "가전 > 이어폰/헤드폰",
            "TV": "가전 > TV",
        },
    )
    out = await m.run(
        [
            CategoryQuery("이어폰", "이어폰검색"),
            CategoryQuery("무선이어폰", "무선검색"),
            CategoryQuery("TV", "티비검색"),
        ],
        settings=_settings(fanout_max=5),
    )
    # 중복 canonical 합침 — 첫 leg 의 query 유지
    assert out == [("가전 > 이어폰/헤드폰", "이어폰검색"), ("가전 > TV", "티비검색")]
