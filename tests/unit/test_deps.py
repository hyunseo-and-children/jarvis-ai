"""require_seller 의존성 단위 테스트 — 판매자 스코프 + brandId 클레임 강제 (api-spec §2.3/§3.2)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import deps
from app.core.auth import Identity


def _patch_identity(monkeypatch: pytest.MonkeyPatch, identity: Identity) -> None:
    """get_identity 를 고정 Identity 반환으로 패치."""
    monkeypatch.setattr(deps, "get_identity", lambda authorization=None: identity)


def test_require_seller_accepts_seller_with_brand_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """seller_id + brand_id 있는 토큰 → 통과."""
    ident = Identity(user_id="7", is_guest=False, seller_id="7", brand_id="brand-99")
    _patch_identity(monkeypatch, ident)
    assert deps.require_seller(authorization="Bearer x") is ident


def test_require_seller_rejects_missing_brand_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """seller 인데 brandId 클레임 없으면 403 (§2.3 필수, 본문 우회 방지)."""
    ident = Identity(user_id="7", is_guest=False, seller_id="7", brand_id=None)
    _patch_identity(monkeypatch, ident)
    with pytest.raises(HTTPException) as exc:
        deps.require_seller(authorization="Bearer x")
    assert exc.value.status_code == 403


def test_require_seller_rejects_non_seller(monkeypatch: pytest.MonkeyPatch) -> None:
    """seller 스코프 없는 토큰 → 403."""
    ident = Identity(user_id="42", is_guest=False, seller_id=None, brand_id=None)
    _patch_identity(monkeypatch, ident)
    with pytest.raises(HTTPException) as exc:
        deps.require_seller(authorization="Bearer x")
    assert exc.value.status_code == 403
