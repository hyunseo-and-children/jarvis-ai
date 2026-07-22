"""repo-local API 정본의 확정 계약이 오래된 OPEN 표기와 다시 충돌하지 않게 한다."""

from pathlib import Path


def test_i20_contract_has_no_stale_open_contract_markers() -> None:
    spec = (Path(__file__).parents[2] / "docs" / "api-spec.md").read_text(encoding="utf-8")
    c8_row = next(line for line in spec.splitlines() if line.startswith("| C-8 |"))
    event_success_rule = next(line for line in spec.splitlines() if "정상 신규·중복 통지는" in line)
    event_auth = spec.split("#### (b) 이벤트 채널", 1)[1].split("### 2.4", 1)[0]
    i20 = spec.split("### 3.5 ", 1)[1].split("### 3.6 ", 1)[0]

    assert "확정" in c8_row
    assert "미확정" not in c8_row
    assert "제안" not in event_success_rule
    assert "🔴" not in event_success_rule
    assert "X-Internal-Token: {SERVICE_TOKEN}" in event_auth
    assert "Authorization: Bearer {SERVICE_TOKEN}" not in event_auth
    assert "UUID 포함" in i20
    assert "최대 64자" in i20
