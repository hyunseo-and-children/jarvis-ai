"""추천 그래프 테스트용 fake LLM / 검색 백엔드 / push (이슈 #2).

라이브 Anthropic·Spring 없이 그래프를 결정론적으로 구동한다. FakeLLM 은 모델 id 로
decompose(haiku) vs rerank(sonnet) 를 구분해 사전 정의 JSON 을 돌려준다.
"""

from __future__ import annotations

import json

from app.core.llm import LLMError
from app.schemas.spring import ProductSearchResult, SpringProduct

DEFAULT_DECOMPOSE = {
    "intent": "recommend",
    "reply": "",
    "case": 2,
    "semanticQuery": "무선 이어폰",
    "filters": {"category": "무선이어폰", "priceMax": 50000, "keyword": "무선 이어폰"},
}

DEFAULT_RERANK = {
    "ranked": [
        {"productId": 101, "rationale": "가성비가 좋아요"},
        {"productId": 102, "rationale": "음질이 우수해요"},
    ],
    "overallComment": "요청 조건에 맞는 추천이에요",
}

DEFAULT_PRODUCTS = [
    SpringProduct(product_id=101, name="이어폰A", price=39000, rating=4.5, category="무선이어폰", brand="BrandX"),
    SpringProduct(product_id=102, name="이어폰B", price=48000, rating=4.2, category="무선이어폰", brand="BrandY"),
    SpringProduct(product_id=103, name="이어폰C", price=29000, rating=3.9, category="무선이어폰", brand="BrandZ"),
]


class FakeLLM:
    """모델 id 로 decompose/rerank 를 분기하는 fake. error 플래그로 실패도 주입."""

    def __init__(
        self,
        *,
        decompose: dict | None = None,
        rerank: dict | None = None,
        decompose_error: bool = False,
        rerank_error: bool = False,
        timeout: bool = False,
    ) -> None:
        self._decompose = DEFAULT_DECOMPOSE if decompose is None else decompose
        self._rerank = DEFAULT_RERANK if rerank is None else rerank
        self._decompose_error = decompose_error
        self._rerank_error = rerank_error
        self._timeout = timeout
        self.calls: list[tuple[str, str]] = []  # (model, user) 기록

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1024) -> str:
        self.calls.append((model, user))
        if "haiku" in model:
            if self._decompose_error:
                raise LLMError("timeout" if self._timeout else "decompose boom")
            return json.dumps(self._decompose, ensure_ascii=False)
        if self._rerank_error:
            raise LLMError("rerank boom")
        return json.dumps(self._rerank, ensure_ascii=False)

    async def stream(self, *, system: str, user: str, model: str, max_tokens: int = 1024):
        yield "x"


class FakeBackend:
    """search_service.SearchBackend 대체 — 고정 상품을 돌려준다."""

    def __init__(self, products: list[SpringProduct] | None = None) -> None:
        self._products = DEFAULT_PRODUCTS if products is None else products

    async def search(self, filters) -> ProductSearchResult:
        return ProductSearchResult(products=list(self._products), total_count=len(self._products))


async def fake_push(push) -> bool:
    """항상 성공하는 push."""
    return True
