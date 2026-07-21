"""프로필 파이프라인 (이슈 #6, SPEC-PROFILE-001) — reader·gate·builder·엔드포인트·멱등·transient.

LLM(델타·consolidation)은 주입형 fake 로 구동(라이브 Anthropic 불필요). GET /profile/me·
POST /events/session-end 는 TestClient. 저장소는 pg-profile BaseStore(테스트는 InMemoryStore,
conftest reset — 이슈 #33).
"""

from __future__ import annotations

import asyncio
import gc
import json

import jwt
import pytest
from fastapi.testclient import TestClient

from app.agents.profile import processed_events
from app.agents.profile.builder import consolidate, generate_session_delta, record_remember
from app.agents.profile.gate import is_remember_command, should_promote
from app.agents.profile.reader import read_profile_summary
from app.agents.profile.store import ProfileStore, get_profile_store
from app.core.config import get_settings
from app.core.conversation import conversation_key
from app.main import app

client = TestClient(app)


def _member_bearer(sub: str) -> dict:
    token = jwt.encode({"sub": sub}, "test-secret-key-0123456789abcdef", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


class _ProfileLLM:
    """델타 추출(JSON)·consolidation(마크다운)을 system 프롬프트로 분기하는 fake."""

    def __init__(self, deltas=None, summary="# 취향 요약\n- 3~5만원 무선이어폰 선호") -> None:
        self._deltas = (
            deltas
            if deltas is not None
            else [
                {
                    "fact": "3~5만원 무선이어폰 선호",
                    "salience": 0.9,
                    "explicit": True,
                    "repetitionEma": 0.0,
                }
            ]
        )
        self._summary = summary

    async def complete(self, *, system, user, tier, max_tokens=1024, json_output=True):
        if "델타 추출기" in system:
            return json.dumps({"deltas": self._deltas}, ensure_ascii=False)
        return self._summary

    async def stream(self, *, system, user, tier, max_tokens=1024):
        yield "x"


# ─────────── reader ───────────


async def test_reader_none_for_guest_and_missing() -> None:
    assert await read_profile_summary(None) is None
    assert await read_profile_summary("") is None
    assert await read_profile_summary("999") is None  # 미보유


async def test_reader_returns_stored_summary() -> None:
    store = await get_profile_store()
    await store.set_summary("5", "# 요약\n- x", "2026-07-20T00:00:00+00:00")
    got = await read_profile_summary("5")
    assert got and got["markdown"] == "# 요약\n- x" and got["generated_at"].startswith("2026")


# ─────────── gate ───────────


def test_should_promote_gate_rule() -> None:
    # salient AND (explicit OR repeated)
    assert should_promote(salience=0.8, explicit=True, repetition_ema=0.0)  # 명시
    assert should_promote(salience=0.8, explicit=False, repetition_ema=0.7)  # 반복
    assert not should_promote(
        salience=0.8, explicit=False, repetition_ema=0.1
    )  # 현저하나 미반복·비명시
    assert not should_promote(salience=0.2, explicit=True, repetition_ema=0.9)  # 현저성 미달


def test_is_remember_command() -> None:
    assert is_remember_command("이거 기억해줘")
    assert is_remember_command("remember this please")
    assert not is_remember_command("무선 이어폰 추천해줘")
    assert not is_remember_command("저번에 뭐 담았는지 기억해?")  # 질문 오탐 제외
    assert not is_remember_command("이거 기억해두면 좋을까?")
    assert not is_remember_command("어제 일 기억해내려고 했는데 잘 안 되네")  # 비명령 부분매칭 제외
    assert is_remember_command("이거 기억해두세요")
    assert is_remember_command(
        "매운맛 좋아해요, 기억해줘! 다른 것도 추천해줄래?"
    )  # 명령+질문 혼합도 인식
    assert not is_remember_command("이거 안 잊게 기억해줘야 할 것 같아")  # 활용형(줘야) 제외
    assert not is_remember_command("기억해줘도 상관없어")  # 활용형(줘도) 제외


# ─────────── builder (델타·consolidation) ───────────


async def test_generate_session_delta_promotes_via_gate() -> None:
    store = await get_profile_store()
    key = conversation_key("7", "s1")
    await store.append_session_ctx(key, "3만원대 무선 이어폰 위주로 보고 있어요")
    promoted, watermark = await generate_session_delta(
        "7", key, llm=_ProfileLLM(), settings=get_settings()
    )
    assert promoted == ["3~5만원 무선이어폰 선호"]
    assert watermark == 1
    assert "3~5만원 무선이어폰 선호" in await store.get_facts("7")


async def test_generate_session_delta_gate_rejects_low_signal() -> None:
    store = await get_profile_store()
    key = conversation_key("7", "s2")
    await store.append_session_ctx(key, "음")
    llm = _ProfileLLM(
        deltas=[{"fact": "잡담", "salience": 0.1, "explicit": False, "repetitionEma": 0.0}]
    )
    promoted, watermark = await generate_session_delta("7", key, llm=llm, settings=get_settings())
    assert promoted == [] and await store.get_facts("7") == []  # 처리됨(non-None)이나 승격 0
    assert watermark == 1


async def test_clear_session_ctx_upto_preserves_concurrent_append() -> None:
    """LLM 호출(스냅샷~clear 사이)에 새로 추가된 발화는 clear_session_ctx_upto 가 지우지 않는다.

    session-end 처리 중 같은 세션에 새 턴이 들어오는 레이스(§events.session_end) 회귀 테스트.
    """
    store = await get_profile_store()
    key = conversation_key("7", "race")
    await store.append_session_ctx(key, "A")
    promoted, watermark = await generate_session_delta(
        "7", key, llm=_ProfileLLM(), settings=get_settings()
    )
    assert promoted == ["3~5만원 무선이어폰 선호"] and watermark == 1
    # LLM 호출이 끝나고 clear 하기 전, 그 사이에 새 턴이 도착했다고 가정.
    await store.append_session_ctx(key, "B")
    await store.clear_session_ctx_upto(key, watermark)
    assert await store.get_session_ctx(key) == ["B"]  # A(스냅샷분)만 지워지고 B는 보존


async def test_clear_session_ctx_upto_survives_cap_eviction_during_llm_call() -> None:
    """LLM 호출 중 버퍼 상한(cap)을 넘겨 스냅샷 항목 자체가 앞에서 밀려나도, 그 이후 추가분은 보존된다.

    PR #37 리뷰 지적 — count(개수) 기반이면 cap 트리밍으로 위치가 밀린 새 항목을 스냅샷 항목으로
    착각해 잘못 지울 수 있었다. seq 워터마크 기반으로 바꿔 위치와 무관하게 안전하도록 회귀 방지.
    """
    store = await get_profile_store()
    key = conversation_key("7", "cap-race")
    await store.append_session_ctx(key, "A", cap=2)
    promoted, watermark = await generate_session_delta(
        "7", key, llm=_ProfileLLM(), settings=get_settings()
    )
    assert promoted == ["3~5만원 무선이어폰 선호"] and watermark == 1
    # LLM 호출 중 cap(2)을 넘겨 A가 앞에서 밀려나는 상황(둘 다 미분석 상태).
    await store.append_session_ctx(key, "B", cap=2)
    await store.append_session_ctx(key, "C", cap=2)  # len=3 > cap=2 → A 트리밍됨, 버퍼=[B, C]
    await store.clear_session_ctx_upto(key, watermark)
    assert await store.get_session_ctx(key) == ["B", "C"]  # A는 이미 없었고, B/C는 미분석이라 보존


async def test_generate_session_delta_degrades_without_llm_or_buffer() -> None:
    # 버퍼 없음 → None(degrade 신호)
    assert (
        await generate_session_delta(
            "7", conversation_key("7", "empty"), llm=_ProfileLLM(), settings=get_settings()
        )
        is None
    )
    # LLM 미구성 → None
    store = await get_profile_store()
    await store.append_session_ctx(conversation_key("7", "s3"), "x")
    assert (
        await generate_session_delta(
            "7", conversation_key("7", "s3"), llm=None, settings=get_settings()
        )
        is None
    )


async def test_consolidate_writes_summary() -> None:
    store = await get_profile_store()
    await store.add_fact("8", "무선이어폰 선호")
    ok = await consolidate("8", llm=_ProfileLLM(), settings=get_settings())
    assert ok is True
    summary = await store.get_summary("8")
    assert summary and "취향 요약" in summary.markdown and summary.generated_at


async def test_consolidate_degrades_without_facts() -> None:
    assert await consolidate("nofacts", llm=_ProfileLLM(), settings=get_settings()) is False


async def test_record_remember_hot_path() -> None:
    await record_remember("9", "겨울 등산 자주 감")
    store = await get_profile_store()
    assert "겨울 등산 자주 감" in await store.get_facts("9")


async def test_record_remember_caps_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "profile_fact_char_cap", 10)
    await record_remember("cap", "가" * 100)
    store = await get_profile_store()
    facts = await store.get_facts("cap")
    assert len(facts[0]) == 10


async def test_consolidate_respects_char_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "profile_summary_max_chars", 10)
    store = await get_profile_store()
    await store.add_fact("10", "x")
    await consolidate("10", llm=_ProfileLLM(summary="가" * 50), settings=get_settings())
    summary = await store.get_summary("10")
    assert len(summary.markdown) == 10


# ─────────── GET /profile/me ───────────


def test_profile_me_guest_exists_false() -> None:
    r = client.get("/profile/me")  # 무토큰 dev 게스트
    assert r.status_code == 200
    assert r.json()["exists"] is False


def test_profile_me_member_no_profile() -> None:
    r = client.get("/profile/me", headers=_member_bearer("321"))
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False and body["markdown"] is None


async def test_profile_me_member_with_profile_camelcase() -> None:
    store = await get_profile_store()
    await store.set_summary("321", "# 취향\n- 무선이어폰", "2026-07-20T09:00:00+00:00")
    r = client.get("/profile/me", headers=_member_bearer("321"))
    body = r.json()
    assert body["exists"] is True
    assert body["userId"] == "321"  # camelCase
    assert "무선이어폰" in body["markdown"]
    assert body["generatedAt"].startswith("2026")  # camelCase


# ─────────── POST /events/session-end ───────────


async def test_session_end_202_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.events as ev

    monkeypatch.setattr(ev, "get_llm", lambda: _ProfileLLM())
    store = await get_profile_store()
    key = conversation_key("777", "sess-9")
    await store.append_session_ctx(key, "3만원 무선이어폰 찾아줘")
    payload = {"userId": 777, "sessionId": "sess-9", "reason": "logout"}
    r1 = client.post("/events/session-end", json=payload)
    assert r1.status_code == 202 and r1.json()["status"] == "accepted"
    # 프로필 생성됨 + 버퍼 정리됨
    assert await store.get_summary("777") is not None
    assert await store.get_session_ctx(key) == []
    # 멱등 — 같은 (userId, sessionId) 재수신은 duplicate
    r2 = client.post("/events/session-end", json=payload)
    assert r2.status_code == 202 and r2.json()["status"] == "duplicate"


def test_session_end_rejects_missing_identity() -> None:
    """userId·sessionId 누락은 400(§2.5) — 멱등키·프로필 스코프에 필수(이슈 #62)."""
    assert client.post("/events/session-end", json={"sessionId": "s"}).status_code == 400
    assert client.post("/events/session-end", json={"userId": 1}).status_code == 400


def test_session_end_rejects_empty_session_id() -> None:
    """빈 sessionId 는 400 — 필수 불투명 키(§3.5 essential), 퇴화 멱등키/버퍼 키 방어(PR #64 리뷰)."""
    assert (
        client.post("/events/session-end", json={"userId": 1, "sessionId": ""}).status_code == 400
    )


def test_session_end_rejects_userid_out_of_bigint_range() -> None:
    """userId 는 양의 BIGINT 범위만 허용 — 거대 정수 키 남용 방어(int 전환 후에도 유지)."""
    assert (
        client.post("/events/session-end", json={"userId": 0, "sessionId": "s"}).status_code == 400
    )
    assert (
        client.post("/events/session-end", json={"userId": 2**63, "sessionId": "s"}).status_code
        == 400
    )


async def test_session_end_idempotency_scoped_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """멱등키는 (userId, sessionId) 파생 — 같은 sessionId라도 userId가 다르면 서로 중복 아님."""
    import app.api.events as ev

    monkeypatch.setattr(ev, "get_llm", lambda: _ProfileLLM())
    store = await get_profile_store()
    await store.append_session_ctx(conversation_key("111", "shared-sess"), "x")
    await store.append_session_ctx(conversation_key("222", "shared-sess"), "y")
    r1 = client.post("/events/session-end", json={"userId": 111, "sessionId": "shared-sess"})
    r2 = client.post("/events/session-end", json={"userId": 222, "sessionId": "shared-sess"})
    assert r1.json()["status"] == "accepted"
    assert r2.json()["status"] == "accepted"  # 다른 userId → 중복 아님


def test_session_end_service_token_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_mode", "jwks")  # 운영: 서비스 토큰 fail-closed
    monkeypatch.setattr(get_settings(), "internal_api_token", "secret-xyz")
    payload = {"userId": 1, "sessionId": "s"}
    # 토큰 없음 → 401
    assert client.post("/events/session-end", json=payload).status_code == 401
    # 일치 → 202
    r = client.post("/events/session-end", json=payload, headers={"X-Internal-Token": "secret-xyz"})
    assert r.status_code == 202


async def test_session_end_degrades_without_llm() -> None:
    """LLM 미구성이어도 세션 종료는 202(best-effort, 프로필 미갱신)."""
    store = await get_profile_store()
    await store.append_session_ctx(conversation_key("55", "s"), "x")
    r = client.post("/events/session-end", json={"userId": 55, "sessionId": "s"})
    assert r.status_code == 202
    assert await store.get_summary("55") is None  # LLM 없어 미갱신
    # degrade 시 transient 버퍼는 보존(성공 시에만 정리) — 회수 여지
    assert await store.get_session_ctx(conversation_key("55", "s")) != []


# ─────────── e2e (채팅 transient → 세션종료 → 조회) ───────────


async def test_end_to_end_profile_from_chat(monkeypatch: pytest.MonkeyPatch, buyer_fakes) -> None:
    """회원 채팅 → 세션 종료 → 프로필 생성 → GET /profile/me 노출."""
    import app.api.events as ev

    monkeypatch.setattr(ev, "get_llm", lambda: _ProfileLLM())
    hdr = _member_bearer("888")
    # 회원 채팅 1턴 → transient 버퍼 누적
    client.post(
        "/chat",
        json={"sessionId": "e2e", "threadId": "t", "message": "3만원 무선이어폰 추천"},
        headers=hdr,
    )
    store = await get_profile_store()
    assert await store.get_session_ctx(conversation_key("888", "e2e"))  # 버퍼에 쌓임
    # 세션 종료 → 델타·consolidation
    client.post("/events/session-end", json={"userId": 888, "sessionId": "e2e"})
    # 조회
    body = client.get("/profile/me", headers=hdr).json()
    assert body["exists"] is True and "무선이어폰" in body["markdown"]


def test_session_end_rejects_oversized_session_id() -> None:
    """sessionId 길이 상한 초과는 400(불투명 스레드 키·스토어 키 남용 방어)."""
    big = "x" * 100000
    r = client.post("/events/session-end", json={"userId": 1, "sessionId": big})
    assert r.status_code == 400  # 앱이 검증 오류를 400 봉투로 매핑(§2.5)


async def test_session_end_clears_buffer_on_normal_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 이 정상 실행됐으나 게이트가 전부 반려한 경우도 '처리됨'이라 버퍼를 정리한다(무한 보존 방지)."""
    import app.api.events as ev

    low = _ProfileLLM(
        deltas=[{"fact": "잡담", "salience": 0.1, "explicit": False, "repetitionEma": 0.0}]
    )
    monkeypatch.setattr(ev, "get_llm", lambda: low)
    key = conversation_key("66", "s")
    store = await get_profile_store()
    await store.append_session_ctx(key, "음 별로")
    r = client.post("/events/session-end", json={"userId": 66, "sessionId": "s"})
    assert r.status_code == 202
    assert await store.get_session_ctx(key) == []  # 정상 처리 → 버퍼 정리


async def test_add_fact_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """사용자별 fact 개수 상한 — 최신 cap 개만 유지(무제한 누적 방어)."""
    monkeypatch.setattr(get_settings(), "profile_max_facts", 3)
    for i in range(10):
        await record_remember("cap2", f"fact-{i}")
    store = await get_profile_store()
    facts = await store.get_facts("cap2")
    assert len(facts) == 3 and facts == ["fact-7", "fact-8", "fact-9"]


async def test_add_fact_dedup_skips_duplicate_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """동일 텍스트 재승격은 스킵된다(멱등) — session_end 재처리(clear 실패·재전송·다음 배치)로
    같은 델타가 다시 뽑혀도 중복 fact 가 누적되지 않는다(PR #47 후속 리뷰)."""
    monkeypatch.setattr(get_settings(), "profile_max_facts", 5)
    store = await get_profile_store()
    await store.add_fact("dup1", "좋아하는 색은 파랑", cap=5)
    await store.add_fact("dup1", "좋아하는 색은 파랑", cap=5)  # 동일 텍스트 재승격
    await store.add_fact("dup1", "알러지: 땅콩", cap=5)
    facts = await store.get_facts("dup1")
    assert facts.count("좋아하는 색은 파랑") == 1  # 중복 저장 안 됨
    assert set(facts) == {"좋아하는 색은 파랑", "알러지: 땅콩"}


async def test_add_fact_dedup_without_cap() -> None:
    """cap 미지정이어도 동일 텍스트 재승격은 스킵된다 — 호출부가 cap 인자를 실수로 빠뜨려도
    dedup·무제한 누적 방어가 조용히 무력화되지 않는다(PR #47 후속 리뷰)."""
    store = await get_profile_store()
    await store.add_fact("nocap", "탄산수 좋아함")
    await store.add_fact("nocap", "탄산수 좋아함")  # cap 없이 동일 텍스트 재승격
    assert (await store.get_facts("nocap")).count("탄산수 좋아함") == 1


async def test_append_session_ctx_caps_count() -> None:
    """세션 버퍼 개수 상한 — 최신 cap 개만 유지."""
    store = await get_profile_store()
    for i in range(10):
        await store.append_session_ctx("k", f"turn-{i}", cap=3)
    assert await store.get_session_ctx("k") == ["turn-7", "turn-8", "turn-9"]


async def test_session_end_unmarks_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """처리 실패(LLM 오류) 시 멱등키 마킹 해제 + 버퍼 보존 — 재전송이 재처리 가능(멱등은 성공에만)."""
    import app.api.events as ev
    from app.core.llm import LLMError

    class _Raise:
        async def complete(self, **k):
            raise LLMError("transient")

        async def stream(self, **k):
            yield "x"

    monkeypatch.setattr(ev, "get_llm", lambda: _Raise())
    key = conversation_key("44", "s")
    store = await get_profile_store()
    await store.append_session_ctx(key, "취향 신호")
    r = client.post("/events/session-end", json={"userId": 44, "sessionId": "s"})
    assert r.status_code == 202
    # 언마크 → 재전송 시 재처리 (멱등키 = session-end:{userId}:{sessionId})
    assert not await processed_events.seen_event("session-end:44:s")
    assert await store.get_session_ctx(key) != []  # 버퍼 보존


async def test_session_end_returns_202_when_profile_store_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_profile_store() 자체가 실패해도(pg-profile 일시 장애) 500 이 아니라 202(PR #47 후속 리뷰).

    이관 전엔 인메모리 싱글턴이라 이 호출이 실패할 수 없었지만, 운영(jwks)은 pg-profile 연결
    실패 시 폴백 없이 raise 한다 — try 밖에 있으면 일시적 DB 장애만으로 §3.5(항상 202) 위반.
    """
    import app.api.events as ev

    async def _raise() -> None:
        raise RuntimeError("pg-profile 일시 장애")

    monkeypatch.setattr(ev, "get_profile_store", _raise)
    r = client.post("/events/session-end", json={"userId": 44, "sessionId": "s"})
    assert r.status_code == 202 and r.json()["status"] == "accepted"
    assert not await processed_events.seen_event("session-end:44:s")  # 언마크 → 재전송 시 재처리


async def test_session_end_returns_202_and_preserves_buffer_on_store_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pg-profile query deadline도 §3.5 best-effort 경계에서 202 + 버퍼 보존으로 강등한다."""
    import app.api.events as ev

    key = conversation_key("44", "timeout-session")
    store = await get_profile_store()
    await store.append_session_ctx(key, "재시도할 취향 신호")

    async def _timeout(*args, **kwargs):
        raise TimeoutError("pg query deadline")

    monkeypatch.setattr(ev.processed_events, "mark_if_new", _timeout)
    response = client.post(
        "/events/session-end",
        json={"userId": 44, "sessionId": "timeout-session"},
    )

    assert response.status_code == 202
    assert await store.get_session_ctx(key) == ["재시도할 취향 신호"]


def test_internal_token_required_in_jwks_mode() -> None:
    """운영(jwks)에서 internal_api_token 미주입이면 Settings 기동 실패(inbound fail-open 방지)."""
    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(auth_mode="jwks", jwks_url="http://x", pii_hash_pepper="p", internal_api_token="")
    Settings(
        auth_mode="jwks",
        jwks_url="http://x",
        pii_hash_pepper="p",
        internal_api_token="tok",
        google_api_key="k",
    )  # ok


async def test_profile_store_all_operations_have_query_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ProfileStore 공개 I/O가 모두 동일 deadline을 사용한다(이슈 #50)."""

    class _HangStore:
        async def aget(self, *args, **kwargs):
            await asyncio.sleep(10)

        async def aput(self, *args, **kwargs):
            await asyncio.sleep(10)

        async def asearch(self, *args, **kwargs):
            await asyncio.sleep(10)

        async def adelete(self, *args, **kwargs):
            await asyncio.sleep(10)

    monkeypatch.setattr(get_settings(), "state_store_query_timeout_s", 0.01)
    store = ProfileStore(_HangStore())
    operations = [
        lambda: store.get_summary("u"),
        lambda: store.set_summary("u", "m", "t"),
        lambda: store.get_facts("u"),
        lambda: store.add_fact("u", "f"),
        lambda: store.append_session_ctx("k", "x"),
        lambda: store.get_session_ctx("k"),
        lambda: store.get_session_ctx_snapshot("k"),
        lambda: store.clear_session_ctx_upto("k", 1),
    ]
    for operation in operations:
        with pytest.raises(TimeoutError):
            await operation()


def test_profile_lock_registries_release_idle_keys() -> None:
    """유휴 lock key는 GC로 회수되고, 사용 중 lock은 호출자가 참조하는 동안 유지된다."""
    from app.agents.profile import store as profile_store_module

    session_lock = profile_store_module._session_lock("session")
    fact_lock = profile_store_module._fact_lock("user")
    assert len(profile_store_module._session_locks) == 1
    assert len(profile_store_module._fact_locks) == 1

    del session_lock, fact_lock
    gc.collect()

    assert len(profile_store_module._session_locks) == 0
    assert len(profile_store_module._fact_locks) == 0


async def test_processed_events_operations_have_query_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session-end 멱등 테이블의 모든 쿼리가 멈춘 DB에서 유한 시간 내 종료한다."""

    class _HangConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args, **kwargs):
            await asyncio.sleep(10)

    class _HangPool:
        closed = False

        def connection(self):
            return _HangConn()

        async def close(self):
            self.closed = True

    monkeypatch.setattr(get_settings(), "state_store_query_timeout_s", 0.01)
    processed_events.set_pool(_HangPool())
    try:
        operations = [
            lambda: processed_events.mark_if_new("e"),
            lambda: processed_events.seen_event("e"),
            lambda: processed_events.mark_event("e"),
            lambda: processed_events.unmark_event("e"),
        ]
        for operation in operations:
            with pytest.raises(TimeoutError):
                await operation()
    finally:
        processed_events.set_pool(None)
