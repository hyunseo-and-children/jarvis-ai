"""카테고리 매핑(방식 A) canonical-or-null 테스트 (이슈 #59).

decompose 추측(raw)을 임베딩으로 실제 DB 카테고리에 보정한다. embed·search·exact 를 주입형
fake 로 대체해 매핑 분기(exact/raw 최근접/query 앵커/신호 없음→빈 결과·무필터/하드실패degrade)와
멀티 dedup·상한 절단을 검증한다.
결과는 fan-out leg 용 (canonical, query) 페어 — query 는 그 카테고리의 검색 키워드(§6·§9).
"""

from __future__ import annotations

import threading
import time
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


async def test_unmapped_anchor_is_logged(caplog) -> None:
    """임베딩 조회는 정상인데 히트 0건이라 드롭되는 앵커를 관측 로그로 남긴다(PR #73 리뷰 #4).

    categories 미시드·임베딩 결측이면 매 턴 전부 이 분기로 빠져 매핑이 조용히 무력화되는데,
    로그가 없으면 운영 중 감지 불가 — canonical 을 못 낸 앵커를 warning 으로 남긴다.
    """
    m = _FakeMapper(exact=set(), nearest={})  # 모든 앵커 히트 0건
    with caplog.at_level("WARNING"):
        out = await m.run([CategoryQuery("없는카테고리", "q")])
    assert out == []
    assert any(r.msg == "category_unmapped" for r in caplog.records)


async def test_offlist_uses_nearest() -> None:
    """raw 가 exact 아님 → embed(raw) → 최근접 채택(거리 무관 항상), query 보존."""
    m = _FakeMapper(exact=set(), nearest={"무선 이어폰": "가전 > 이어폰/헤드폰"})
    out = await m.run([CategoryQuery("무선 이어폰", "이어폰")])
    assert out == [("가전 > 이어폰/헤드폰", "이어폰")]


async def test_null_raw_uses_leg_query_as_anchor() -> None:
    """raw==null 이면 그 leg 의 query 를 앵커로 embed → top-1(발화 아님), query 보존(PR #73 #17).

    leg 고유 query 가 있으면 발화 전체가 아니라 query 로 임베딩해야 leg 별로 구분된다.
    """
    m = _FakeMapper(exact=set(), nearest={"집들이 선물": "생활/건강 > 생활용품"})
    out = await m.run([CategoryQuery(None, "집들이 선물")], utterance="집들이 선물 추천")
    assert out == [("생활/건강 > 생활용품", "집들이 선물")]


async def test_multi_null_raw_uses_per_leg_query_anchor() -> None:
    """null-raw leg 이 여럿이면 각 leg 의 query 를 앵커로 써서 서로 다른 canonical 로 매핑한다(PR #73 #17).

    발화 전체를 공유 앵커로 쓰면 서로 다른 아이템이 같은 최근접으로 붙어 dedup 으로 fan-out
    폭이 조용히 줄어든다 — leg 고유 query 로 임베딩해 이를 막는다.
    """
    m = _FakeMapper(
        exact=set(),
        nearest={"이어폰": "가전 > 이어폰/헤드폰", "노트북": "컴퓨터 > 노트북"},
    )
    out = await m.run(
        [CategoryQuery(None, "이어폰"), CategoryQuery(None, "노트북")],
        utterance="싼거 추천",
    )
    assert [c for c, _ in out] == [
        "가전 > 이어폰/헤드폰",
        "컴퓨터 > 노트북",
    ]  # 발화 공유로 합쳐지지 않음


async def test_empty_queries_yields_no_category() -> None:
    """categoryQueries 빈 리스트(카테고리 신호 없음) → 빈 결과 → 무필터 검색(카테고리 강제 안 함, PR #73 #22).

    "5만원 이하 아무거나" 같은 category-agnostic 질의를 발화 임베딩으로 엉뚱한 카테고리에 좁히지 않는다.
    """
    m = _FakeMapper(exact=set(), nearest={"유럽여행 준비물": "여행/캠핑 > 여행용품"})
    out = await m.run([], utterance="유럽여행 준비물")
    assert out == []


async def test_null_null_leg_skipped_no_category_forced() -> None:
    """raw·query 모두 없는 leg(신호 없음)는 발화로 강제 매핑하지 않고 스킵한다(PR #73 #22)."""
    m = _FakeMapper(exact=set(), nearest={"싼거 추천": "여행/캠핑 > 여행용품"})
    out = await m.run([CategoryQuery(None, None)], utterance="싼거 추천")
    assert out == []


async def test_hard_failure_degrades_to_empty_not_raw() -> None:
    """embed/DB 다운 → 미검증 raw 를 신뢰하지 않고 빈 legs 로 degrade한다(canonical-or-null, PR #73 #20).

    raw 는 검증이 필요할 만큼 자주 틀린다(이 PR의 전제) — 검증 불가 시 raw 를 보내면 가짜
    categoryName 으로 0건이 날 수 있어, 카테고리 없이(전체) 검색하도록 빈 리스트로 degrade 한다.
    graph 바깥 except(#18)·미매핑(#13)과 동일한 canonical-or-null 불변식.
    """
    m = _FakeMapper(exact=set(), nearest={}, embed_raises=True)
    out = await m.run([CategoryQuery("PC부품 > CPU", "cpu"), CategoryQuery(None, "뭐")])
    assert out == []


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


async def test_search_lookups_run_in_parallel() -> None:
    """need_idx 앵커별 search_top_k 를 병렬 실행한다 — 순차면 동시성 peak 1 (PR #73 리뷰 #3).

    검색 조회를 asyncio.to_thread 로 넘기므로, gather 병렬화 시 여러 조회가 동시에 스레드에서
    돌아 peak concurrency ≥2. 순차 for-loop 면 항상 1이라 이 테스트가 회귀를 잡는다.
    """
    lock = threading.Lock()
    state = {"cur": 0, "peak": 0}

    def _slow_search(vec: list[float], dsn: str, *, k: int) -> list[str]:
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        time.sleep(0.05)  # 겹칠 시간 확보(병렬이면 동시 진입)
        with lock:
            state["cur"] -= 1
        return ["가전 > X"]

    def _embed(texts: list[str]) -> list[list[float]]:
        return [[float(i)] for i in range(len(texts))]

    def _exact(values, dsn: str) -> set[str]:
        return set()

    await map_categories(
        category_queries=[
            CategoryQuery("a", None),
            CategoryQuery("b", None),
            CategoryQuery("c", None),
        ],
        utterance="u",
        settings=_settings(),
        embed=_embed,
        search_top_k=_slow_search,
        exact_lookup=_exact,
    )
    assert state["peak"] >= 2  # 병렬이면 동시 진입 ≥2, 순차면 1
