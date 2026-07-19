"""app/agents/seller/tools.py(ToolRuntime 도구 + READ_TOOLS/PRODUCT_TOOLS) 단위 테스트.

DESIGN-SELLER-TOOLS-STAGE1 §6 테스트 목록. 실 Spring 호출 없이 FakeSpringClient 로
브랜드 스코프 주입(IDOR 방지)·degrade 문자열 반환·쓰기/조회 분리를 검증한다.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agents.seller.context import SellerContext
from app.agents.seller.tools import (
    PRODUCT_TOOLS,
    READ_TOOLS,
    get_behavior_events,
    get_funnel,
    get_order_events,
    get_sales_timeseries,
    list_my_products,
    update_product,
)
from app.services import spring_client as spring_client_module
from app.schemas.spring import (
    AccountEventsResult,
    BehaviorEventsResult,
    BehaviorProductRow,
    ChurnResult,
    FunnelResult,
    OrderEventsResult,
    ProductChangeLogResult,
    ProductCreateResult,
    ProductDeleteResult,
    ProductUpdateResult,
    SalesResult,
    SalesSeriesPoint,
    SellerProductList,
)
from app.services.spring_client import SpringUnavailableError

FORBIDDEN_IDENTITY_KEYS = {"sellerId", "brandId", "seller_id", "brand_id"}


class FakeSpringClient:
    """SpringClient 이중(double). 실 HTTP 없이 브랜드 스코프 주입·오류 경로만 검증한다."""

    def __init__(self, *, fail: set[str] | None = None) -> None:
        self.recorded_brand_id: str | None = None
        self.recorded_stats: bool | None = None
        self.recorded_event_args: tuple | None = None
        self.behavior_result = BehaviorEventsResult()  # I-13 기본 빈 응답(3형 공통)
        self.order_events_result = OrderEventsResult(events=[])  # I-14 기본 빈 응답
        self._fail = fail or set()

    def _maybe_fail(self, method: str) -> None:
        if method in self._fail:
            raise SpringUnavailableError(f"Spring 콜백 타임아웃(3.0s): {method}")

    async def get_sales(self, brand_id, from_, to, granularity="daily"):
        self.recorded_brand_id = brand_id
        self._maybe_fail("get_sales")
        return SalesResult(
            series=[
                SalesSeriesPoint(
                    date="2026-07-01",
                    sales=1000,
                    order_count=3,
                    is_anomaly=False,
                    deviation_pct=0.0,
                )
            ]
        )

    async def get_funnel(self, brand_id, from_, to):
        self.recorded_brand_id = brand_id
        self._maybe_fail("get_funnel")
        return FunnelResult(view=100, cart=10, checkout=5, purchase=3)

    async def get_events(
        self, brand_id, from_, to, event_type=None, product_id=None, group_by=None
    ):
        self.recorded_brand_id = brand_id
        self.recorded_event_args = (event_type, product_id, group_by)
        self._maybe_fail("get_events")
        return self.behavior_result  # 기본 빈 응답 — 테스트가 형태별로 교체

    async def get_order_events(
        self, brand_id, from_, to, to_status=None, actor_type=None, group_by=None, stats=None
    ):
        self.recorded_brand_id = brand_id
        self.recorded_stats = stats
        self._maybe_fail("get_order_events")
        return self.order_events_result

    async def get_product_changes(self, brand_id, from_, to, change_type=None, product_id=None):
        self.recorded_brand_id = brand_id
        self._maybe_fail("get_product_changes")
        return ProductChangeLogResult(logs=[])

    async def get_churn(self, brand_id, inactive_days):
        self.recorded_brand_id = brand_id
        self._maybe_fail("get_churn")
        return ChurnResult(churn_rate=5.0, pre_churn_signals=[])

    async def get_account_events(self, event_type=None, from_=None, to=None, group_by=None):
        self._maybe_fail("get_account_events")
        return AccountEventsResult(events=[])

    async def list_products(self, brand_id, status=None, q=None, limit=None, offset=None):
        self.recorded_brand_id = brand_id
        self.recorded_limit = limit  # 기본 limit(Settings) 주입 검증용
        self._maybe_fail("list_products")
        return SellerProductList(rows=[])

    async def create_product(self, brand_id, payload):
        self.recorded_brand_id = brand_id
        self._maybe_fail("create_product")
        return ProductCreateResult(product_id=101, status="ON_SALE")

    async def update_product(self, brand_id, product_id, patch):
        self.recorded_brand_id = brand_id
        self.recorded_patch = patch  # 전 필드 노출(name 등) 전달 검증용
        self._maybe_fail("update_product")
        return ProductUpdateResult(product_id=product_id)

    async def delete_product(self, brand_id, product_id):
        self.recorded_brand_id = brand_id
        self._maybe_fail("delete_product")
        return ProductDeleteResult(product_id=product_id, status="HIDDEN")


class FakeRuntime:
    """ToolRuntime 이중 — 도구 본문은 runtime.context 만 읽으므로 덕 타이핑으로 충분하다."""

    def __init__(self, brand_id: str = "brand-42") -> None:
        self.context = SellerContext(seller_id="seller-1", brand_id=brand_id)


async def _call_runtime_tool(tool: BaseTool, args: dict, fake, brand_id: str = "brand-42"):
    """ToolRuntime 도구를 단위 테스트에서 직접 호출한다.

    에이전트 런타임 없이 원본 코루틴(tool.coroutine)에 FakeRuntime 을 키워드로 넘기고,
    SpringClient 싱글턴을 이중으로 교체했다가 반드시 원복한다.
    """
    spring_client_module.set_spring_client(fake)
    try:
        return await tool.coroutine(runtime=FakeRuntime(brand_id), **args)
    finally:
        spring_client_module.set_spring_client(None)


def test_write_tools_isolated_from_read() -> None:
    """read_tools 에는 create/update/delete 가 없고 product_tools 에만 존재한다."""
    read_names = {t.name for t in READ_TOOLS}
    product_names = {t.name for t in PRODUCT_TOOLS}

    for write_name in ("create_product", "update_product", "delete_product"):
        assert write_name not in read_names
        assert write_name in product_names


def test_no_identity_params_in_any_tool() -> None:
    """모든 도구의 args_schema 에 sellerId/brandId 류 키가 없다(IDOR — 신원 미노출)."""
    all_tools = {t.name: t for t in (*READ_TOOLS, *PRODUCT_TOOLS)}.values()
    for t in all_tools:
        arg_keys = set(t.args.keys())
        assert not (arg_keys & FORBIDDEN_IDENTITY_KEYS), (
            f"{t.name} exposes identity arg: {arg_keys}"
        )
        # ToolRuntime 파라미터는 LLM 스키마에서 은닉되어야 한다(v1 주입 계약).
        assert "runtime" not in arg_keys, f"{t.name} exposes runtime arg"


async def test_context_injects_brand_id() -> None:
    """도구 인자로 brand_id 를 넘기지 않아도 runtime.context 의 brand_id 가 client 에 전달된다."""
    fake = FakeSpringClient()

    await _call_runtime_tool(
        get_sales_timeseries,
        {"from_date": "2026-07-01", "to_date": "2026-07-14"},
        fake,
        brand_id="brand-777",
    )

    assert fake.recorded_brand_id == "brand-777"


async def test_tool_returns_error_string_on_spring_failure() -> None:
    """Spring 실패(SpringUnavailableError) 시 도구는 raise 없이 "Error:" 문자열을 반환한다."""
    fake = FakeSpringClient(fail={"get_sales"})

    result = await _call_runtime_tool(
        get_sales_timeseries, {"from_date": "2026-07-01", "to_date": "2026-07-14"}, fake
    )

    assert result.startswith("Error:")


async def test_tool_returns_error_string_on_timeout() -> None:
    """타임아웃(SpringUnavailableError 로 이미 변환됨)도 "Error:" 문자열로 degrade 된다."""
    fake = FakeSpringClient(fail={"get_funnel"})

    result = await _call_runtime_tool(
        get_funnel, {"from_date": "2026-07-01", "to_date": "2026-07-14"}, fake
    )

    assert result.startswith("Error:")


async def test_sales_tool_summary_includes_reference_period() -> None:
    """매출 조회 도구의 반환 문자열에 기준 기간 고지가 포함된다."""
    result = await _call_runtime_tool(
        get_sales_timeseries,
        {"from_date": "2026-07-01", "to_date": "2026-07-14"},
        FakeSpringClient(),
    )

    assert "기준:" in result
    assert "2026-07-01" in result and "2026-07-14" in result


async def test_search_analysis_guide_is_stub() -> None:
    """내부 NotImplementedError 를 노출하지 않고 degrade 문자열만 반환한다."""
    tool = next(t for t in READ_TOOLS if t.name == "search_analysis_guide")

    result = await tool.ainvoke({"query": "전환율 정의"})

    assert result.startswith("Error:")
    assert "NotImplementedError" not in result


def test_list_my_products_in_both_lists() -> None:
    """list_my_products 는 read_tools·product_tools 양쪽에 모두 존재한다."""
    assert "list_my_products" in {t.name for t in READ_TOOLS}
    assert "list_my_products" in {t.name for t in PRODUCT_TOOLS}


async def test_calculate_tool_handles_division_by_zero() -> None:
    """0 나눗셈(ZeroDivisionError, ArithmeticError 하위)도 raise 없이 degrade 된다(opus 리뷰 M2).

    safe_eval 은 화이트리스트 위반만 ValueError 로 막고, 화이트리스트 안 연산(0 나눗셈 등)의
    파이썬 예외는 그대로 전파되므로 도구가 (ValueError, ArithmeticError, TypeError) 를 모두
    잡아야 한다. 분모 0 은 전환율 계산에서 흔한 입력이라 특히 중요하다.
    """
    tool = next(t for t in READ_TOOLS if t.name == "calculate")

    result = await tool.ainvoke({"expression": "1/0"})

    assert result.startswith("Error:")


async def test_calculate_tool_handles_round_type_error() -> None:
    """round() 인자 오류(TypeError)도 raise 없이 degrade 된다(opus 리뷰 M2 연장)."""
    tool = next(t for t in READ_TOOLS if t.name == "calculate")

    result = await tool.ainvoke({"expression": "round(1, 2, 3)"})

    assert result.startswith("Error:")


async def test_sales_tool_skips_anomaly_detection_for_non_daily_granularity() -> None:
    """granularity 가 daily 가 아니면 이상 감지를 생략한다(opus 리뷰 m5).

    이동평균 window(seller_ma_window, §5)는 "일" 단위를 전제하므로 weekly/monthly 시계열에
    그대로 적용하면 window 정렬이 깨진다 — daily 일 때만 detect_sales_anomalies 를 돈다.
    """

    class SpikySalesClient(FakeSpringClient):
        """이상 감지 임계값(기본 30%)을 확실히 넘는 급증 시계열을 반환하는 이중."""

        async def get_sales(self, brand_id, from_, to, granularity="daily"):
            self.recorded_brand_id = brand_id
            points = [
                SalesSeriesPoint(date=f"2026-07-{day:02d}", sales=100, order_count=1)
                for day in range(1, 8)
            ]
            points.append(SalesSeriesPoint(date="2026-07-08", sales=10000, order_count=50))
            return SalesResult(series=points)

    fake = SpikySalesClient()
    daily_result = await _call_runtime_tool(
        get_sales_timeseries,
        {"from_date": "2026-07-01", "to_date": "2026-07-08", "granularity": "daily"},
        fake,
    )
    weekly_result = await _call_runtime_tool(
        get_sales_timeseries,
        {"from_date": "2026-07-01", "to_date": "2026-07-08", "granularity": "weekly"},
        fake,
    )

    # 상세 포맷 변경(안 1): daily 는 이상 감지 문구(편차율 포함), weekly 는 판정 자체를 생략.
    assert "이상 감지" in daily_result
    assert "이상 감지" not in weekly_result


async def test_get_order_events_tool_passes_stats_through() -> None:
    """도구의 stats 인자가 client.get_order_events 호출로 그대로 전달된다(opus 리뷰 m6)."""
    fake = FakeSpringClient()

    await _call_runtime_tool(
        get_order_events,
        {"from_date": "2026-07-01", "to_date": "2026-07-14", "stats": True},
        fake,
    )

    assert fake.recorded_stats is True


async def test_sales_tool_includes_point_detail_and_caps_output() -> None:
    """시계열 상세 나열(안 1, 2026-07-17 확정) — 포인트별 수치를 포함하되
    seller_summary_max_points 초과분은 "외 N개 포인트 생략" 으로 접는다."""

    class LongSeriesClient(FakeSpringClient):
        """상한(기본 60)을 넘는 90일 시계열을 반환하는 이중."""

        async def get_sales(self, brand_id, from_, to, granularity="daily"):
            self.recorded_brand_id = brand_id
            return SalesResult(
                series=[
                    SalesSeriesPoint(date=f"2026-04-{(d % 30) + 1:02d}", sales=100, order_count=1)
                    for d in range(90)
                ]
            )

    result = await _call_runtime_tool(
        get_sales_timeseries,
        {"from_date": "2026-04-01", "to_date": "2026-06-29"},
        LongSeriesClient(),
    )

    assert "100원/1건" in result  # 포인트 상세가 포함된다
    assert "외 30개 포인트 생략" in result  # 90 - 상한 60 = 30


async def test_order_events_tool_summarizes_kv_with_cap() -> None:
    """I-13/I-14 kv 요약(중간 상세도) — 미확정 필드를 key=value 로 상위 N건 노출."""

    class EventfulClient(FakeSpringClient):
        """상한(기본 5)을 넘는 7건 이벤트를 반환하는 이중."""

        async def get_order_events(
            self, brand_id, from_, to, to_status=None, actor_type=None, group_by=None, stats=None
        ):
            self.recorded_brand_id = brand_id
            self.recorded_stats = stats
            return OrderEventsResult(
                events=[{"toStatus": "CANCELLED", "count": i} for i in range(7)],
                stats={"CANCELLED": 7},
            )

    result = await _call_runtime_tool(
        get_order_events, {"from_date": "2026-07-01", "to_date": "2026-07-14"}, EventfulClient()
    )

    assert "toStatus=CANCELLED" in result  # kv 노출
    assert "외 2건" in result  # 7 - 상한 5 = 2
    assert "CANCELLED=7" in result  # stats dict 노출


async def test_list_products_uses_default_limit_from_settings() -> None:
    """limit 미지정 시 Settings 기본값(seller_list_default_limit)으로 요청한다."""
    from app.core.config import get_settings

    fake = FakeSpringClient()
    await _call_runtime_tool(list_my_products, {}, fake)

    assert fake.recorded_limit == get_settings().seller_list_default_limit


async def test_update_product_exposes_all_schema_fields() -> None:
    """update_product 는 ProductUpdate 전 필드를 인자로 노출한다(2026-07-17 사용자 확정)."""
    fake = FakeSpringClient()
    result = await _call_runtime_tool(
        update_product, {"product_id": 9, "name": "새 이름", "category": "패션"}, fake
    )

    assert "9" in result
    assert fake.recorded_patch.name == "새 이름"  # name 이 스키마까지 전달된다
    assert fake.recorded_patch.category == "패션"
    assert fake.recorded_patch.price is None  # 미지정 필드는 None(부분 수정)


# ── I-13 행동 이벤트 도구 (REALIGN ②-3 — 07/17 확정 명세) ──


async def test_behavior_tool_passes_filters_to_client() -> None:
    """eventType(복수)/productId(숫자)/groupBy 가 client 까지 그대로 전달된다."""
    fake = FakeSpringClient()

    await _call_runtime_tool(
        get_behavior_events,
        {
            "from_date": "2026-07-01",
            "to_date": "2026-07-14",
            "event_type": ["product_view", "add_to_cart"],
            "product_id": 101,
            "group_by": "date",
        },
        fake,
    )

    assert fake.recorded_event_args == (["product_view", "add_to_cart"], 101, "date")


async def test_behavior_tool_summarizes_product_rows_with_authority_note() -> None:
    """groupBy=product — 상품별 카운트 요약 + purchaseComplete 권위 주의 문구."""
    fake = FakeSpringClient()
    fake.behavior_result = BehaviorEventsResult(
        group_by="product",
        rows=[
            BehaviorProductRow(
                product_id=101,
                product_name="에어 러너 2",
                counts={
                    "productView": 1820,
                    "addToCart": 240,
                    "checkoutStart": 96,
                    "purchaseComplete": 61,
                },
                view_to_cart_rate=0.132,
                unique_visitors=1503,
            )
        ],
        total=1,
    )

    result = await _call_runtime_tool(
        get_behavior_events, {"from_date": "2026-07-01", "to_date": "2026-07-14"}, fake
    )

    assert "[101] 에어 러너 2" in result
    assert "조회 1820" in result and "담기 240" in result
    assert "13.2%" in result  # viewToCartRate 백분율 표기
    assert "권위는 매출 조회(I-6)" in result  # 이벤트≠주문 권위(명세 집계 규칙)


async def test_behavior_tool_summarizes_event_type_counts() -> None:
    """groupBy=eventType — counts 합계 요약."""
    fake = FakeSpringClient()
    fake.behavior_result = BehaviorEventsResult(
        group_by="eventType",
        counts={"productView": 8120, "addToCart": 1490},
    )

    result = await _call_runtime_tool(
        get_behavior_events,
        {"from_date": "2026-07-01", "to_date": "2026-07-14", "group_by": "eventType"},
        fake,
    )

    assert "productView=8120" in result and "addToCart=1490" in result


async def test_behavior_tool_caps_date_series_by_settings() -> None:
    """groupBy=date — seller_summary_max_points 초과분은 '외 N일'로 접는다."""
    from app.core.config import get_settings

    cap = get_settings().seller_summary_max_points
    fake = FakeSpringClient()
    fake.behavior_result = BehaviorEventsResult(
        group_by="date",
        series=[{"date": f"2026-07-{d:02d}", "productView": d} for d in range(1, cap + 4)],
    )

    result = await _call_runtime_tool(
        get_behavior_events,
        {"from_date": "2026-07-01", "to_date": "2026-09-30", "group_by": "date"},
        fake,
    )

    assert "외 3일" in result


async def test_behavior_tool_empty_result() -> None:
    """3형 모두 비어 있으면 0건 안내."""
    fake = FakeSpringClient()

    result = await _call_runtime_tool(
        get_behavior_events, {"from_date": "2026-07-01", "to_date": "2026-07-14"}, fake
    )

    assert "행동 이벤트 0건" in result


# ── I-14/I-15 기록 규칙 주의 문구 (REALIGN ②-4 — D32/D34 해석 규칙) ──


async def test_order_events_output_includes_log_rules_note() -> None:
    """전이가 있으면 기록 규칙 주의(완료만 기록·주문 단위 1행)가 함께 나간다."""
    fake = FakeSpringClient()
    fake.order_events_result = OrderEventsResult(
        events=[{"orderId": 5001, "toStatus": "CANCELLED", "actorType": "USER"}]
    )

    result = await _call_runtime_tool(
        get_order_events, {"from_date": "2026-07-01", "to_date": "2026-07-14"}, fake
    )

    assert "구매확정·클레임 신청은 로그에 없음" in result
    assert "아이템 수로 해석 금지" in result


async def test_product_change_logs_output_includes_log_rules_note() -> None:
    """상품 변경 이력 응답에 기록 규칙 주의(재고 차감 미기록·품절=STOCK→0)가 나간다."""
    from app.agents.seller.tools import get_product_change_logs

    fake = FakeSpringClient()

    result = await _call_runtime_tool(
        get_product_change_logs, {"from_date": "2026-07-01", "to_date": "2026-07-14"}, fake
    )

    assert "주문에 의한 재고 차감은 미기록" in result
    assert "new_value 0" in result


def test_worker_prompts_contain_log_interpretation_rules() -> None:
    """워커 프롬프트에 해석 규칙(완료만 기록·이벤트≠주문 권위)이 남아 있다(회귀 방지)."""
    from app.agents.seller.prompts import (
        ABUSE_PROMPT,
        BEHAVIOR_PROMPT,
        CHURN_PROMPT,
        SALES_ANOMALY_PROMPT,
    )

    assert "해석 주의" in SALES_ANOMALY_PROMPT
    assert "완료" in CHURN_PROMPT and "교환" in CHURN_PROMPT
    assert "신청 미기록" in ABUSE_PROMPT
    assert "purchaseComplete" in BEHAVIOR_PROMPT
