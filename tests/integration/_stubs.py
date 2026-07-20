"""E2E 스모크용 Spring stub + 스크립트 LLM (이슈 #35).

AI 서버는 단독 실행이 불가하고 Spring 역호출에 의존한다(api-spec §1.2 레인 c). 이 모듈은
라이브 Spring/Anthropic 없이 **결정적으로** 전 흐름을 돌리기 위한 대역을 제공한다.

설계 원칙 — **경계에서만 대역을 넣는다**:
  - Spring 은 `httpx.MockTransport`(HTTP 경계)로 세운다. `spring_client` 함수를 patch 하지
    않으므로 URL 조립·쿼리 파라미터·`X-Internal-Token` 헤더·응답 envelope 파싱·오류 매핑이
    **실코드 그대로** 검증된다(함수 patch 는 이 계층을 통째로 건너뛰어 계약 회귀를 못 잡는다).
  - LLM 은 주입형 `ScriptedLLM`(프롬프트 시그니처로 5종 호출을 분기).

Spring stub 커버 범위 (api-spec §4):
  I-1  GET  /internal/products/search      (§4.6 후보 검색)
  I-2  POST /internal/cart/items           (§4.1 담기)
  I-18 GET  /internal/cart                 (§4.9 조회)
  I-19 GET  /internal/members/{id}/orders  (§4.7 구매 이력)
  I-21 POST /internal/recommendations      (§4.2 목록 push, 경로 B)
  I-17 GET  /internal/products/changes     (§4.8 변경분 pull)
  CH-5 GET  /api/chat/lists/{listId}       (§4.3 FE→Spring 목록 조회 — 경로 B 종단 확인용)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.llm import LLMError

# ── 기본 카탈로그 (숫자 BIGINT id — §2.6) ──
DEFAULT_CATALOG: list[dict] = [
    {
        "productId": 101,
        "name": "여행용 방수 파우치 L",
        "price": 19000,
        "originalPrice": 25000,
        "categoryName": "여행용품",
        "brandName": "트래블러",
        "rating": 4.6,
        "reviewCount": 320,
    },
    {
        "productId": 102,
        "name": "기내반입 세면도구 파우치",
        "price": 24000,
        "categoryName": "여행용품",
        "brandName": "패커스",
        "rating": 4.3,
        "reviewCount": 158,
    },
    {
        "productId": 103,
        "name": "대용량 캐리어 파우치 3종",
        "price": 31000,
        "categoryName": "여행용품",
        "brandName": "트래블러",
        "rating": 4.1,
        "reviewCount": 87,
    },
]


@dataclass
class SpringStub:
    """Spring 백엔드 대역. 상태(장바구니·push 목록·커서)를 들고 요청을 기록한다.

    실패 주입 플래그로 degrade 경로(§7)를 재현한다 — 검색 5xx·push 5xx·이력 5xx 등.
    """

    catalog: list[dict] = field(default_factory=lambda: [dict(p) for p in DEFAULT_CATALOG])
    orders: list[dict] = field(default_factory=list)
    changes_pages: list[dict] = field(default_factory=list)
    cart_items: list[dict] = field(default_factory=list)
    # listId → productIds (경로 B: push 로 저장 → CH-5 로 조회)
    pushed_lists: dict[str, list[int]] = field(default_factory=dict)
    # 요청 감사 로그 — (method, path, query, headers, body)
    requests: list[dict] = field(default_factory=list)

    # 실패 주입 (degrade 검증용)
    fail_search: bool = False
    fail_purchases: bool = False
    fail_push: bool = False
    fail_cart_add_code: str | None = None  # CART_OPTION_REQUIRED 등
    cart_option_payload: list[dict] = field(default_factory=list)

    # ── 라우팅 ──

    def handler(self, request: httpx.Request) -> httpx.Response:
        """MockTransport 핸들러 — 메서드+경로로 분기한다."""
        path = request.url.path
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode("utf-8", "replace")
        self.requests.append(
            {
                "method": request.method,
                "path": path,
                "query": dict(request.url.params),
                "headers": dict(request.headers),
                "body": body,
            }
        )

        if request.method == "GET" and path == "/internal/products/search":
            return self._search(request)
        if request.method == "POST" and path == "/internal/cart/items":
            return self._cart_add(body)
        if request.method == "GET" and path == "/internal/cart":
            return self._cart_view()
        if request.method == "GET" and path.startswith("/internal/members/"):
            return self._orders()
        if request.method == "POST" and path == "/internal/recommendations":
            return self._push(body)
        if request.method == "GET" and path == "/internal/products/changes":
            return self._changes(request)
        if request.method == "GET" and path.startswith("/api/chat/lists/"):
            return self._list_cards(path.rsplit("/", 1)[-1])
        return httpx.Response(404, json={"success": False, "error": {"code": "NOT_FOUND"}})

    # ── I-1 검색 (§4.6) ──

    def _search(self, request: httpx.Request) -> httpx.Response:
        if self.fail_search:
            return httpx.Response(503, json={"success": False, "error": {"code": "UNAVAILABLE"}})
        params = request.url.params
        items = list(self.catalog)
        # BE I-1 파라미터 의미론 재현 — AI 가 보낸 쿼리가 실제로 반영되는지까지 확인한다.
        if (category := params.get("categoryName")) is not None:
            items = [p for p in items if p.get("categoryName") == category]
        if (brand := params.get("brandName")) is not None:
            items = [p for p in items if p.get("brandName") == brand]
        if (min_price := params.get("minPrice")) is not None:
            items = [p for p in items if p["price"] >= int(min_price)]
        if (max_price := params.get("maxPrice")) is not None:
            items = [p for p in items if p["price"] <= int(max_price)]
        if (size := params.get("size")) is not None:
            items = items[: int(size)]
        return httpx.Response(200, json={"success": True, "data": items})

    # ── I-2 담기 (§4.1) ──

    def _cart_add(self, body: Any) -> httpx.Response:
        if self.fail_cart_add_code == "CART_OPTION_REQUIRED":
            return httpx.Response(
                400,
                json={
                    "success": False,
                    "error": {
                        "code": "CART_OPTION_REQUIRED",
                        "message": "옵션을 선택해주세요",
                        "detail": {"options": self.cart_option_payload},
                    },
                },
            )
        if self.fail_cart_add_code == "PRODUCT_NOT_FOUND":
            return httpx.Response(
                404, json={"success": False, "error": {"code": "PRODUCT_NOT_FOUND"}}
            )
        item = {
            "cartItemId": 9000 + len(self.cart_items) + 1,
            "productId": (body or {}).get("productId"),
            "optionId": (body or {}).get("optionId"),
            "quantity": (body or {}).get("quantity", 1),
            "productName": self._name_of((body or {}).get("productId")),
        }
        self.cart_items.append(item)
        return httpx.Response(200, json={"success": True, "data": {"cartItemId": item["cartItemId"]}})

    def _cart_view(self) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"items": self.cart_items}})

    def _name_of(self, product_id: Any) -> str | None:
        for product in self.catalog:
            if product["productId"] == product_id:
                return product["name"]
        return None

    # ── I-19 구매 이력 (§4.7) ──

    def _orders(self) -> httpx.Response:
        if self.fail_purchases:
            return httpx.Response(503, json={"success": False, "error": {"code": "UNAVAILABLE"}})
        return httpx.Response(200, json={"success": True, "data": {"orders": self.orders}})

    # ── I-21 목록 push (§4.2, 경로 B) ──

    def _push(self, body: Any) -> httpx.Response:
        if self.fail_push:
            return httpx.Response(500, json={"success": False, "error": {"code": "PUSH_FAILED"}})
        payload = body or {}
        self.pushed_lists[str(payload.get("listId"))] = list(payload.get("productIds") or [])
        return httpx.Response(200, json={"success": True, "data": {"listId": payload.get("listId")}})

    # ── CH-5 목록 조회 (§4.3, FE→Spring) ──

    def _list_cards(self, list_id: str) -> httpx.Response:
        """push 된 id 목록을 Spring 이 표시 필드로 enrich 해 돌려주는 경로(표시 권위=Spring)."""
        product_ids = self.pushed_lists.get(list_id)
        if product_ids is None:
            return httpx.Response(404, json={"success": False, "error": {"code": "LIST_NOT_FOUND"}})
        by_id = {p["productId"]: p for p in self.catalog}
        cards = [by_id[pid] for pid in product_ids if pid in by_id]
        return httpx.Response(200, json={"success": True, "data": {"items": cards}})

    # ── I-17 변경분 pull (§4.8) ──

    def _changes(self, request: httpx.Request) -> httpx.Response:
        since = request.url.params.get("since", "0")
        for page in self.changes_pages:
            if str(page.get("since")) == str(since):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {
                            "items": page.get("items", []),
                            "nextCursor": page.get("nextCursor"),
                            "hasMore": page.get("hasMore", False),
                        },
                    },
                )
        return httpx.Response(
            200, json={"success": True, "data": {"items": [], "nextCursor": since, "hasMore": False}}
        )

    # ── 검증 헬퍼 ──

    def requests_to(self, path_prefix: str) -> list[dict]:
        """경로 접두어로 기록된 요청을 추린다."""
        return [r for r in self.requests if r["path"].startswith(path_prefix)]


# ── 스크립트 LLM ──

# enrichment/프로필 시스템 프롬프트의 식별 문구 (app/pipelines/enrichment.py·agents/profile/builder.py)
_ENRICH_MARK = "상품 태깅기"
_DELTA_MARK = "델타 추출기"
_CONSOLIDATE_MARK = "요약 작성기"

DEFAULT_DECOMPOSE = {
    "intent": "recommend",
    "reply": "",
    "case": 2,
    "semanticQuery": "여행용 파우치",
    "filters": {"category": "여행용품", "priceMax": 30000, "keyword": "여행 파우치"},
}

DEFAULT_RERANK = {
    "ranked": [
        {"productId": 102, "rationale": "기내 반입 규격에 맞아요"},
        {"productId": 101, "rationale": "방수라 세면도구에 좋아요"},
    ],
    "overallComment": "여행에 맞는 파우치를 골랐어요",
}

DEFAULT_ENRICH = {"tags": ["여행", "방수", "기내반입"], "attributes": {"소재": "방수 원단"}}

DEFAULT_DELTA = {
    "deltas": [
        {
            "fact": "3만원 이하 여행용품을 선호한다",
            "salience": 0.9,
            "explicit": True,
            "repetitionEma": 0.8,
        }
    ]
}

DEFAULT_PROFILE_MD = "## 취향 요약\n- 3만원 이하 여행용품 선호\n"


class ScriptedLLM:
    """호출 5종(decompose·rerank·enrich·profile delta·consolidate)을 프롬프트로 분기하는 fake.

    tests/_fakes.py 의 FakeLLM 은 모델 id 만 보고 2종을 분기하므로 배치·프로필까지 함께 도는
    E2E 에는 부족하다. 여기서는 **system 프롬프트 시그니처**로 용도를 판정한다.
    실패 주입(*_error)으로 degrade 경로(LLM_UNAVAILABLE·LLM_TIMEOUT·rerank 폴백)를 재현한다.
    """

    def __init__(
        self,
        *,
        decompose: dict | None = None,
        rerank: dict | None = None,
        enrich: dict | None = None,
        delta: dict | None = None,
        profile_markdown: str = DEFAULT_PROFILE_MD,
        decompose_error: bool = False,
        rerank_error: bool = False,
        timeout: bool = False,
    ) -> None:
        self._decompose = DEFAULT_DECOMPOSE if decompose is None else decompose
        self._rerank = DEFAULT_RERANK if rerank is None else rerank
        self._enrich = DEFAULT_ENRICH if enrich is None else enrich
        self._delta = DEFAULT_DELTA if delta is None else delta
        self._profile_markdown = profile_markdown
        self._decompose_error = decompose_error
        self._rerank_error = rerank_error
        self._timeout = timeout
        self.calls: list[tuple[str, str]] = []  # (kind, tier)

    async def complete(self, *, system: str, user: str, tier: str, max_tokens: int = 1024, json_output: bool = True) -> str:
        kind = self._classify(system, tier)
        self.calls.append((kind, tier))
        if kind == "enrich":
            return json.dumps(self._enrich, ensure_ascii=False)
        if kind == "delta":
            return json.dumps(self._delta, ensure_ascii=False)
        if kind == "consolidate":
            return self._profile_markdown
        if kind == "decompose":
            if self._decompose_error:
                raise LLMError("timeout" if self._timeout else "decompose boom")
            return json.dumps(self._decompose, ensure_ascii=False)
        if self._rerank_error:
            raise LLMError("rerank boom")
        return json.dumps(self._rerank, ensure_ascii=False)

    async def stream(self, *, system: str, user: str, tier: str, max_tokens: int = 1024):
        yield "네, 도와드릴게요."

    @staticmethod
    def _classify(system: str, tier: str) -> str:
        """system 프롬프트 시그니처 → 호출 용도. 미상은 tier 로 decompose/rerank 판정."""
        if _ENRICH_MARK in system:
            return "enrich"
        if _DELTA_MARK in system:
            return "delta"
        if _CONSOLIDATE_MARK in system:
            return "consolidate"
        return "decompose" if tier == "fast" else "rerank"

    def calls_of(self, kind: str) -> int:
        """용도별 호출 횟수 — LLM 호출 예산(§llm_call_limit) 확인용."""
        return sum(1 for k, _ in self.calls if k == kind)
