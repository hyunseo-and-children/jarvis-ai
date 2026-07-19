"""프로필 빌더 — 델타 생성 + sleep-time consolidation (SPEC-PROFILE-001 §6.2/6.4, 결정 4-A).

2단 비동기 쓰기:
  (1) 세션 종료 트리거 시 transient 세션 버퍼에서 후보 취향 델타를 LLM(Sonnet) 추출 → 게이트 승격
  (2) sleep-time consolidation: 승격 fact 를 §5.1 3섹션 요약 마크다운으로 재작성(recency-wins)
턴 중에는 write 하지 않고 세션 버퍼만 누적한다(transient 격리). "기억해" hot-path 만 즉시 기록.

LLM 은 주입형(테스트 fake) — 미구성/오류 시 best-effort degrade(프로필 미갱신, 다음 배치가 회수).
프로덕션은 PostgresStore 병합·미처리 스레드 스캔으로 이관(REQ-PROF-050/051).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.profile.gate import should_promote
from app.agents.profile.store import get_profile_store
from app.agents.buyer.recommendation.state import extract_json
from app.core.config import get_settings
from app.core.llm import LLMError

_DELTA_SYSTEM = """당신은 커머스 어시스턴트의 취향 프로필 델타 추출기입니다.
세션 대화(사용자 발화 모음)에서 장기 보관할 만한 취향 신호만 뽑습니다.
반드시 아래 JSON 만 출력하세요(설명·코드펜스 금지):
{ "deltas": [ { "fact": "간결한 취향 서술(한국어)", "salience": 0.0~1.0, "explicit": true|false, "repetitionEma": 0.0~1.0 } ] }
규칙:
- salience=현저성(중요/뚜렷할수록↑), explicit=사용자가 명시적으로 선호를 말함, repetitionEma=반복 정도.
- 일회성 잡담·잡음은 제외. 가격대·브랜드 선호/회피·카테고리·평점 성향 등 재사용 가능한 신호 위주.
- 없으면 {"deltas": []}."""

_CONSOLIDATE_SYSTEM = """당신은 커머스 취향 프로필 요약 작성기입니다.
아래 취향 fact 목록을 사람이 읽는 한국어 마크다운 요약으로 재작성하세요(중복 병합, 최신 우선).
3섹션 구성: (1) 구조화 블록(가격 성향·선호/회피 브랜드·평점·속성) (2) 취향 산문 (3) 최근 맥락.
confidence 수치·내부 메타는 노출하지 마세요. 마크다운만 출력(코드펜스 금지)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_remember(user_id: str, fact: str) -> None:
    """"기억해" hot-path — 명시 명령은 게이트 없이 즉시 승격(REQ-PROF).

    발화 원문을 그대로 저장하되 config 길이 상한으로 절단한다(오탐·남용 시 무제한 누적 방어).
    """
    if not (user_id and fact):
        return
    settings = get_settings()
    cleaned = fact.strip()[: settings.profile_fact_char_cap]
    if cleaned:
        get_profile_store().add_fact(user_id, cleaned, cap=settings.profile_max_facts)


async def generate_session_delta(user_id: str, thread_key: str, *, llm, settings) -> list[str] | None:
    """세션 버퍼(transient)에서 후보 델타를 LLM 추출 → 게이트 승격. 승격된 fact 목록 반환.

    반환: None = degrade(버퍼 없음/LLM 미구성, 버퍼 보존 신호), list = 처리됨(승격 fact, 빈 목록 가능).
    LLMError 는 전파 — 상위가 degrade 처리. 게스트는 호출 안 함(상위 책임).
    """
    store = get_profile_store()
    buffer = store.get_session_ctx(thread_key)
    if not buffer or llm is None:
        return None  # degrade(버퍼 없음/LLM 미구성) — 처리 안 함(상위가 버퍼 보존)
    # LLMError 는 전파 — 상위(events)가 degrade 로 처리해 버퍼를 보존(정상 반려와 구분).
    raw = await llm.complete(system=_DELTA_SYSTEM, user="\n".join(buffer), model=settings.sonnet_model_id, max_tokens=800)
    data = extract_json(raw)
    promoted: list[str] = []
    for delta in data.get("deltas", []) if isinstance(data, dict) else []:
        if not isinstance(delta, dict):
            continue
        fact = str(delta.get("fact") or "").strip()
        if not fact:
            continue
        if should_promote(
            salience=_as_float(delta.get("salience")),
            explicit=bool(delta.get("explicit")),
            repetition_ema=_as_float(delta.get("repetitionEma")),
            threshold=settings.profile_gate_threshold,
        ):
            store.add_fact(user_id, fact, cap=settings.profile_max_facts)
            promoted.append(fact)
    return promoted


async def consolidate(user_id: str, *, llm, settings) -> bool:
    """sleep-time — 승격 fact 를 §5.1 3섹션 요약 마크다운으로 재작성 후 저장. 갱신 여부 반환.

    best-effort — fact 없음/LLM 오류 시 미갱신(False).
    """
    store = get_profile_store()
    facts = store.get_facts(user_id)
    if not facts or llm is None:
        return False
    try:
        raw = await llm.complete(system=_CONSOLIDATE_SYSTEM, user="\n".join(facts), model=settings.sonnet_model_id, max_tokens=1000)
    except LLMError:
        return False
    markdown = (raw or "").strip()[: settings.profile_summary_max_chars]
    if not markdown:
        return False
    store.set_summary(user_id, markdown, _now_iso())
    return True


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
