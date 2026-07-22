"""[폐기 예정] 주문 시드 서비스 — v0.5.0 에서 대체됨 (api-spec §4.7·§4.4).

[변경 2026-07-15 확정] 주문 미러/시드 노선은 채택하지 않는다:
  - 구매 이력(추천 dedup·프로필 신호) → spring_client.get_recent_purchases
    (GET /orders/recent 질의 시점 조회, api-spec §4.7, C-6)
  - 판매자 통계 → spring_client.get_seller_aggregates
    (I-6 집계 콜백, api-spec §4.4, C-13 — 구 결정 22 의 AI DB 시드안 폐기)

이 모듈과 order_seed 테이블(01_order_seed.sql)은 위 계약 확정 전의 임시 데모용으로만
남아 있으며, Spring 계약(C-6/C-13) 확정 시 삭제한다. 신규 코드에서 참조 금지.
"""

from __future__ import annotations


def recent_purchases(user_id: str, limit: int = 50) -> list[dict]:
    """[폐기 예정] spring_client.get_recent_purchases(§4.7) 로 대체됨. 신규 참조 금지."""
    raise NotImplementedError("order_seed is superseded by GET /orders/recent (api-spec §4.7, C-6)")


def seller_sales_stats(seller_id: str) -> dict:
    """[폐기 예정] spring_client.get_seller_aggregates(I-6, §4.4) 로 대체됨. 신규 참조 금지."""
    raise NotImplementedError(
        "order_seed is superseded by I-6 seller aggregates (api-spec §4.4, C-13)"
    )
