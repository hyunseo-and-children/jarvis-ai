from types import SimpleNamespace

import pytest

from app.schemas.spring import SellerProductList
from scripts import check_spring_connection


@pytest.mark.asyncio
async def test_check_connection_reports_returned_row_count(monkeypatch, capsys):
    class StubSpringClient:
        async def list_products(self, **kwargs):
            assert kwargs == {"brand_id": "9", "limit": 1, "offset": 0}
            return SellerProductList(rows=[])

    monkeypatch.setattr(
        check_spring_connection,
        "get_settings",
        lambda: SimpleNamespace(
            internal_api_token="configured",
            spring_base_url="https://spring.example",
        ),
    )
    monkeypatch.setattr(
        check_spring_connection,
        "get_spring_client",
        lambda: StubSpringClient(),
    )

    result = await check_spring_connection.check_connection("9")

    assert result == 0
    assert "returned=0" in capsys.readouterr().out
