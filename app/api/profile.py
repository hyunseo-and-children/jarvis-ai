"""프로필 조회 엔드포인트 — GET /profile/me (MVP, api-spec §3.4).

마이페이지 프로필 자연어 마크다운 passthrough(결정 4-A, SPEC-PROFILE-001 §6.9). 조회 대상은
JWT sub 에서 도출(경로에 userId 없음 — IDOR 방지, 결정 19). 게스트·프로필 미보유는
{exists:false, markdown:null} 정상 200(오류 아님, REQ-PROF-081). 편집 PUT 은 고도화(EX-P3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agents.profile.reader import read_profile_summary
from app.api.deps import get_identity
from app.core.auth import Identity
from app.schemas.profile import ProfileView

router = APIRouter(tags=["profile"])


@router.get("/profile/me")
def get_profile_me(identity: Identity = Depends(get_identity)) -> ProfileView:
    """토큰 소유자 본인의 프로필 요약 마크다운 (§3.4)."""
    # 게스트/무신원 → 개인화 프로필 없음(정상 200).
    if identity.is_guest or not identity.user_id:
        return ProfileView(user_id=identity.subject or "", exists=False)
    summary = read_profile_summary(identity.user_id)
    if summary is None:
        return ProfileView(user_id=identity.user_id, exists=False)
    return ProfileView(
        user_id=identity.user_id,
        exists=True,
        markdown=summary["markdown"],
        generated_at=summary["generated_at"],
    )
