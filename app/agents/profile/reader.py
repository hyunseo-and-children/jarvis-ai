"""프로필 리더 (SPEC-PROFILE-001 §6.1).

그래프 진입 시 동기로 profile_summary 를 단일 get 한다 (LLM 호출 0회, REQ-PROF-002).
index.md + 압축 취향 요약만 로드하고 전체 지식 단위 번들은 로드하지 않는다 (결정 4).
게스트/신규 회원은 프로필이 없으므로 None 을 반환한다 (REQ-PROF-003).
"""

from __future__ import annotations

from app.agents.profile.store import get_profile_store


async def read_profile_summary(user_id: str | None) -> dict | None:
    """user_id 의 압축 프로필 요약을 반환한다.

    반환 dict 키: markdown(str), generated_at(ISO-8601 str). 미보유(게스트/신규) 시 None.
    LLM 호출 없음 — 저장소(PostgresStore, pg-profile) 단일 get.
    """
    if not user_id:
        return None
    store = await get_profile_store()
    summary = await store.get_summary(user_id)
    if summary is None:
        return None
    return {"markdown": summary.markdown, "generated_at": summary.generated_at}
