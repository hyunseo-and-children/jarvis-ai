"""app/agents/seller/hitl.py 4-2 HITL 실행 검증 — 실 LLM·PG·HTTP 없음.

InMemorySaver 주입 + 스텁 SpringClient 로 전체 흐름(draft 저장 → interrupt →
confirm resume → 쓰기)과 안전장치 5종(§6.2)을 검증한다. 설계 확정(2026-07-20):
코드 직접 실행 / dev InMemory 폴백 / stale 비교에서 stock 제외.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.seller import hitl
from app.agents.seller.schemas import DraftChange, DraftProposal
from app.schemas.spring import (
    ProductCreateResult,
    ProductDeleteResult,
    ProductUpdateResult,
    SellerProductList,
    SellerProductRow,
)
from app.services.spring_client import SpringUnavailableError, set_spring_client


@pytest.fixture(autouse=True)
def _fresh_checkpointer():
    """테스트마다 격리된 InMemorySaver — PG 연결 시도 자체를 차단한다."""
    hitl.set_checkpointer(InMemorySaver())
    yield
    hitl.set_checkpointer(None)
    set_spring_client(None)


_ROW = SellerProductRow(
    productId=101,
    name="감귤청",
    price=15000,
    originalPrice=18000,
    stockQuantity=100,
    status="ON_SALE",
    description="제주 감귤청입니다.",
)


class _StubSpring:
    """판매자 CRUD 4종만 흉내 — 호출 기록으로 실행 여부·인자를 검증한다."""

    def __init__(self, rows: list[SellerProductRow] | None = None, fail_list: bool = False):
        self.rows = rows if rows is not None else [_ROW]
        self.fail_list = fail_list
        self.calls: list[tuple] = []

    async def list_products(self, brand_id, status=None, q=None, limit=None, offset=None):
        if self.fail_list:
            raise SpringUnavailableError("conn refused")
        self.calls.append(("list", brand_id, offset))
        start = offset or 0
        return SellerProductList(rows=self.rows[start : start + (limit or 20)])

    async def create_product(self, brand_id, payload):
        self.calls.append(("create", brand_id, payload))
        return ProductCreateResult(productId=999, status="ON_SALE")

    async def update_product(self, brand_id, product_id, patch):
        self.calls.append(("update", brand_id, product_id, patch))
        return ProductUpdateResult(productId=product_id)

    async def delete_product(self, brand_id, product_id):
        self.calls.append(("delete", brand_id, product_id))
        return ProductDeleteResult(productId=product_id, status="HIDDEN")

    def write_calls(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("create", "update", "delete")]


def _proposal(**kwargs) -> DraftProposal:
    base = dict(
        op="update",
        product_id=101,
        changes=[DraftChange(field="price", before="15000", after="12900")],
        summary="가격 12,900원으로 인하",
    )
    base.update(kwargs)
    return DraftProposal(**base)


def _record(proposal: DraftProposal | None = None, **kwargs) -> hitl.DraftRecord:
    record, problem = hitl.validate_draft(
        proposal or _proposal(**kwargs), seller_id="7", brand_id="3"
    )
    assert problem is None, problem
    assert record is not None
    return record


# ── validate_draft — 코드 선검증(캐스팅·필수 필드·C4) ───────────────────────────


def test_validate_draft_issues_id_and_identity() -> None:
    """draftId·신원·created_at 은 코드 발급 — LLM 필드가 아니다."""
    record = _record()
    assert record.draft_id
    assert record.seller_id == "7" and record.brand_id == "3"
    assert datetime.fromisoformat(record.created_at).tzinfo is not None  # UTC aware


def test_validate_draft_accepts_comma_and_suffix_numbers() -> None:
    """도구 출력 표기("12,900원")를 옮겨적은 수치도 관용 캐스팅한다."""
    record = _record(changes=[DraftChange(field="price", before="15,000원", after="12,900원")])
    assert record is not None


def test_validate_draft_sanitizes_without_masking_executable_value() -> None:
    """실행 정본은 위험 문자만 제거하고 노출 전용 시크릿 마스킹으로 오염하지 않는다."""
    record = _record(
        changes=[
            DraftChange(
                field="description",
                before="기존 설명",
                after="키 ❤️ sk-abcdefghijklmnop1234 A\ufe0fB\U000e0061",
            )
        ]
    )

    assert record.changes[0].after == "키 ❤️ sk-abcdefghijklmnop1234 AB"
    assert "[민감 정보 차단]" not in record.changes[0].after


def test_validate_draft_rejects_uncastable_int() -> None:
    """정수 불가 수치는 되묻기 — confirm 시점 캐스팅 실패를 선차단한다."""
    record, problem = hitl.validate_draft(
        _proposal(changes=[DraftChange(field="price", before="15000", after="열두배")]),
        seller_id="7",
        brand_id="3",
    )
    assert record is None and "price" in problem


def test_validate_draft_rejects_bad_status() -> None:
    record, problem = hitl.validate_draft(
        _proposal(changes=[DraftChange(field="status", before="ON_SALE", after="SOLD_OUT")]),
        seller_id="7",
        brand_id="3",
    )
    assert record is None and "status" in problem


def test_validate_draft_update_requires_product_id() -> None:
    record, problem = hitl.validate_draft(_proposal(product_id=None), seller_id="7", brand_id="3")
    assert record is None and "상품" in problem


def test_validate_draft_update_requires_changes() -> None:
    record, problem = hitl.validate_draft(_proposal(changes=[]), seller_id="7", brand_id="3")
    assert record is None and problem


def test_validate_draft_create_requires_mandatory_fields() -> None:
    """I-10 필수(name/price/stockQuantity) 누락 create 는 되묻기."""
    record, problem = hitl.validate_draft(
        _proposal(
            op="create",
            product_id=None,
            changes=[DraftChange(field="name", before="", after="한라봉청")],
        ),
        seller_id="7",
        brand_id="3",
    )
    assert record is None
    assert "price" in problem and "stock_quantity" in problem


def test_validate_draft_create_forbids_image_and_status() -> None:
    """C4/D3 — create 는 image_url/status 지정 불가."""
    record, problem = hitl.validate_draft(
        _proposal(
            op="create",
            product_id=None,
            changes=[
                DraftChange(field="name", before="", after="한라봉청"),
                DraftChange(field="price", before="", after="20000"),
                DraftChange(field="stock_quantity", before="", after="50"),
                DraftChange(field="image_url", before="", after="http://x/img.png"),
            ],
        ),
        seller_id="7",
        brand_id="3",
    )
    assert record is None and "이미지" in problem


def test_validate_draft_create_nullifies_product_id() -> None:
    """create 인데 LLM 이 product_id 를 넣어도 관용 — null 로 정규화(F2)."""
    record = _record(
        op="create",
        product_id=777,
        changes=[
            DraftChange(field="name", before="", after="한라봉청"),
            DraftChange(field="price", before="", after="20000"),
            DraftChange(field="stock_quantity", before="", after="50"),
        ],
    )
    assert record.product_id is None


# ── find_stale_changes — S-5 병존 대조(stock 제외) ──────────────────────────────


def test_stale_detects_changed_field() -> None:
    changes = [DraftChange(field="price", before="14000", after="12900")]
    mismatches = hitl.find_stale_changes(_ROW, changes)
    assert mismatches == [("price", "14000", "15000")]


def test_stale_ignores_number_formatting() -> None:
    """ "15,000원" vs 15000 — 표기 차이는 오탐하지 않는다(정수 비교)."""
    changes = [DraftChange(field="price", before="15,000원", after="12900")]
    assert hitl.find_stale_changes(_ROW, changes) == []


def test_stale_exempts_stock_quantity() -> None:
    """stock 은 주문 차감(F6)으로 자연 변동 — 비교 제외(2026-07-20 확정)."""
    changes = [DraftChange(field="stock_quantity", before="90", after="200")]
    assert hitl.find_stale_changes(_ROW, changes) == []


def test_stale_none_field_compares_as_empty() -> None:
    """현재값 None(예: category 미설정) 은 빈 문자열과 동치로 본다."""
    changes = [DraftChange(field="category", before="", after="청류")]
    assert hitl.find_stale_changes(_ROW, changes) == []


# ── 그래프 E2E — draft 저장 → interrupt → confirm resume → 쓰기 ─────────────────


def test_confirm_executes_update_with_draft_args() -> None:
    """confirm 후 실행되는 것은 'FE 에 보여준 draft 그 자체'(안전장치 ①) — 코드 매핑."""
    spring = _StubSpring()
    set_spring_client(spring)
    record = _record()

    async def run():
        await hitl.start_draft(record)
        assert spring.write_calls() == []  # 승인 전 쓰기 0회(발화 ≠ 동의)
        return await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "executed"
    assert "101" in outcome.text
    writes = spring.write_calls()
    assert len(writes) == 1
    op, brand_id, product_id, patch = writes[0]
    assert (op, brand_id, product_id) == ("update", "3", 101)
    assert patch.price == 12900  # draft after 그대로 — LLM 재개입 없음


def test_confirm_is_idempotent() -> None:
    """동일 draftId 재confirm 은 재실행 없이 안내(안전장치 ③ — 더블클릭 방지)."""
    spring = _StubSpring()
    set_spring_client(spring)
    record = _record()

    async def run():
        await hitl.start_draft(record)
        first = await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")
        second = await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")
        return first, second

    first, second = asyncio.run(run())

    assert first.status == "executed"
    assert second.status == "already_done"
    assert len(spring.write_calls()) == 1  # 1회만 실행


def test_confirm_unknown_draft_id() -> None:
    spring = _StubSpring()
    set_spring_client(spring)

    outcome = asyncio.run(hitl.confirm_draft("no-such-draft", seller_id="7", brand_id="3"))

    assert outcome.status == "not_found"
    assert spring.write_calls() == []


def test_confirm_brand_mismatch_hides_existence() -> None:
    """타 판매자의 draftId 추측 confirm — 미존재와 동일 문구(존재 비노출) + 실행 0회."""
    spring = _StubSpring()
    set_spring_client(spring)
    record = _record()

    async def run():
        await hitl.start_draft(record)
        return await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="999")

    outcome = asyncio.run(run())

    assert outcome.status == "not_found"
    assert outcome.text == hitl._NOT_FOUND_TEXT
    assert spring.write_calls() == []


def test_confirm_seller_mismatch_same_brand_blocks() -> None:
    """같은 브랜드(brand=3)라도 타 판매자(seller=8)의 draftId confirm 은 차단 — brand 만이
    아니라 seller 까지 대조한다(리뷰 반영: draft 소유권 IDOR 방지)."""
    spring = _StubSpring()
    set_spring_client(spring)
    record = _record()  # seller_id=7, brand_id=3

    async def run():
        await hitl.start_draft(record)
        return await hitl.confirm_draft(record.draft_id, seller_id="8", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "not_found"
    assert outcome.text == hitl._NOT_FOUND_TEXT
    assert spring.write_calls() == []


def test_confirm_concurrent_executes_once() -> None:
    """동시 confirm 2건(안전장치 ③ 보강) — check-then-act 를 draftId 락으로 직렬화해
    상품 쓰기가 정확히 1회만 실행된다(중복 실행 방지)."""
    spring = _StubSpring()
    set_spring_client(spring)
    record = _record()

    async def run():
        await hitl.start_draft(record)
        return await asyncio.gather(
            hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3"),
            hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3"),
        )

    outcomes = asyncio.run(run())

    statuses = sorted(o.status for o in outcomes)
    assert statuses == ["already_done", "executed"]
    assert len(spring.write_calls()) == 1  # 정확히 1회 실행


def test_confirm_expired_draft_blocks_execution() -> None:
    """TTL(안전장치 ⑤) — 만료 draft confirm 은 실행 없이 만료 안내."""
    spring = _StubSpring()
    set_spring_client(spring)
    record = _record()
    stale_created = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    expired = record.model_copy(update={"created_at": stale_created})

    async def run():
        await hitl.start_draft(expired)
        return await hitl.confirm_draft(expired.draft_id, seller_id="7", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "expired"
    assert "만료" in outcome.text
    assert spring.write_calls() == []


def test_confirm_stale_price_blocks_and_asks_again() -> None:
    """S-5 병존(F7) — before 불일치는 실행 중단 + 현재값 안내(되묻기)."""
    changed_row = _ROW.model_copy(update={"price": 13000})  # FE 직접 수정이 선행된 상황
    spring = _StubSpring(rows=[changed_row])
    set_spring_client(spring)
    record = _record()

    async def run():
        await hitl.start_draft(record)
        return await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "stale"
    assert "13000" in outcome.text  # 현재값 안내
    assert spring.write_calls() == []


def test_confirm_stock_drift_executes_with_note() -> None:
    """stock 자연 변동은 실행을 막지 않되 결과 안내에 현재값을 표기한다."""
    drifted = _ROW.model_copy(update={"stock_quantity": 97})  # 주문 3건 차감
    spring = _StubSpring(rows=[drifted])
    set_spring_client(spring)
    record = _record(
        changes=[DraftChange(field="stock_quantity", before="100", after="200")],
        summary="재고 200건으로 보충",
    )

    async def run():
        await hitl.start_draft(record)
        return await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "executed"
    assert "97" in outcome.text  # 변동 사실 표기
    assert spring.write_calls()[0][3].stock_quantity == 200


def test_confirm_missing_product_is_stale() -> None:
    """I-9 재조회에서 상품 미발견(삭제 등) — 실행 중단 + 되묻기."""
    spring = _StubSpring(rows=[])
    set_spring_client(spring)
    record = _record()

    async def run():
        await hitl.start_draft(record)
        return await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "stale"
    assert spring.write_calls() == []


def test_confirm_delete_maps_to_i12() -> None:
    """delete draft → I-12 soft delete — 결과에 HIDDEN 명시(물리 삭제 아님)."""
    spring = _StubSpring()
    set_spring_client(spring)
    record = _record(
        op="delete",
        changes=[DraftChange(field="status", before="ON_SALE", after="HIDDEN")],
        summary="상품 숨김",
    )

    async def run():
        await hitl.start_draft(record)
        return await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "executed"
    assert "HIDDEN" in outcome.text
    assert spring.write_calls() == [("delete", "3", 101)]


def test_confirm_create_maps_to_i10_without_image() -> None:
    """create draft → I-10 — C4/D3: imageUrl 미전송(BE 기본값 처리 가정)."""
    spring = _StubSpring()
    set_spring_client(spring)
    record = _record(
        op="create",
        product_id=None,
        changes=[
            DraftChange(field="name", before="", after="한라봉청"),
            DraftChange(field="price", before="", after="20,000"),
            DraftChange(field="stock_quantity", before="", after="50"),
            DraftChange(field="description", before="", after="제주 한라봉청"),
        ],
        summary="한라봉청 신규 등록",
    )

    async def run():
        await hitl.start_draft(record)
        return await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "executed" and "999" in outcome.text
    payload = spring.write_calls()[0][2]
    assert (payload.name, payload.price, payload.stock_quantity) == ("한라봉청", 20000, 50)
    assert payload.image_url is None
    assert spring.calls[0][0] == "create"  # create 는 I-9 재조회(stale) 생략


def test_confirm_spring_down_keeps_draft_retryable() -> None:
    """Spring 장애 시 예외 전파 — checkpoint 는 interrupt 에 남아 재confirm 가능."""
    spring = _StubSpring(fail_list=True)
    set_spring_client(spring)
    record = _record()

    async def run():
        await hitl.start_draft(record)
        with pytest.raises(SpringUnavailableError):
            await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")
        spring.fail_list = False  # 복구 후 재시도
        return await hitl.confirm_draft(record.draft_id, seller_id="7", brand_id="3")

    outcome = asyncio.run(run())

    assert outcome.status == "executed"
    assert len(spring.write_calls()) == 1


def test_find_product_paginates_until_found() -> None:
    """I-9 productId 필터 부재 — 페이지 순회로 대상 행을 찾는다."""
    filler = [_ROW.model_copy(update={"product_id": i, "name": f"상품{i}"}) for i in range(1, 41)]
    spring = _StubSpring(rows=[*filler, _ROW.model_copy(update={"product_id": 500})])
    set_spring_client(spring)

    row = asyncio.run(hitl._find_product("3", 500))

    assert row is not None and row.product_id == 500
    assert len([c for c in spring.calls if c[0] == "list"]) == 3  # 20건 × 3페이지
