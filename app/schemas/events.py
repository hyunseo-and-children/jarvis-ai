"""이벤트 수신 채널 스키마 — session-end 1종만 MVP (api-spec v0.7.0 §3.5).

  - session-end : MVP 유지 — SessionEndEvent {eventId(멱등 키), sessionId, userId, reason(🔴 C-8)}
  - catalog     : [영구 미채택] I-17 pull 배치로 대체 (schemas.spring.ProductChangesPage, §4.8)
  - order       : [영구 미채택] GET /orders/recent 로 대체 (schemas.spring.RecentPurchases, §4.7)

TODO(MVP): SessionEndEvent Pydantic 모델 — 필드·reason 값 집합은 C-8 협의 확정 후 고정.
"""

from __future__ import annotations
