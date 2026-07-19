"""require_seller_internal 검증 (REALIGN-SELLER-20260719 D1 — Spring 패스스루 인증).

의존성 함수를 직접 호출한다(HTTP 서버 없음). 검증 항목:
  - 서비스 토큰 일치 → Identity(메아리 신원) 반환
  - 토큰 결손/불일치 → 401 UNAUTHORIZED
  - 신원 헤더 결손 → 400 BAD_REQUEST (권한 아님 — Spring 호출 오류)
  - service_token 미설정(dev) → 검증 스킵하되 신원 헤더는 여전히 필수
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import deps
from app.core.config import Settings

_TOKEN = "internal-secret-token"


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    """deps.get_settings 를 테스트 전용 Settings 로 치환한다 (_env_file 미사용)."""
    settings = Settings(_env_file=None, **overrides)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)


def test_valid_token_and_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """토큰 일치 + 신원 헤더 → Identity 로 메아리 신원이 그대로 실린다."""
    _patch_settings(monkeypatch, service_token=_TOKEN)

    identity = deps.require_seller_internal(
        x_internal_token=_TOKEN, x_seller_id="7", x_brand_id="3"
    )

    assert identity.seller_id == "7"
    assert identity.brand_id == "3"
    assert identity.user_id is None and identity.is_guest is False


@pytest.mark.parametrize("bad_token", [None, "", "wrong-token"])
def test_invalid_token_401(monkeypatch: pytest.MonkeyPatch, bad_token: str | None) -> None:
    """토큰 결손/불일치 → 401 UNAUTHORIZED (api-spec §2.4 매핑)."""
    _patch_settings(monkeypatch, service_token=_TOKEN)

    with pytest.raises(HTTPException) as exc_info:
        deps.require_seller_internal(x_internal_token=bad_token, x_seller_id="7", x_brand_id="3")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    ("seller_id", "brand_id"),
    [(None, "3"), ("7", None), (None, None), ("", "3")],
)
def test_missing_identity_headers_400(
    monkeypatch: pytest.MonkeyPatch, seller_id: str | None, brand_id: str | None
) -> None:
    """신원 헤더 결손 → 400 — Spring 쪽 호출 오류이지 권한 문제(403)가 아니다."""
    _patch_settings(monkeypatch, service_token=_TOKEN)

    with pytest.raises(HTTPException) as exc_info:
        deps.require_seller_internal(
            x_internal_token=_TOKEN, x_seller_id=seller_id, x_brand_id=brand_id
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "BAD_REQUEST"


def test_dev_skip_without_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """service_token 미설정(dev) → 토큰 검증 스킵, 신원 헤더만으로 통과."""
    _patch_settings(monkeypatch, service_token=None)

    identity = deps.require_seller_internal(x_internal_token=None, x_seller_id="7", x_brand_id="3")

    assert identity.seller_id == "7"


def test_dev_skip_still_requires_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev 스킵이어도 신원 헤더 결손은 400 — 신원 없는 스트림은 성립 불가."""
    _patch_settings(monkeypatch, service_token=None)

    with pytest.raises(HTTPException) as exc_info:
        deps.require_seller_internal(x_internal_token=None, x_seller_id=None, x_brand_id=None)

    assert exc_info.value.status_code == 400
