"""이벤트 수신 채널 스키마 — session-end 1종만 MVP (api-spec v0.15.17 §3.5).

  - session-end : MVP 유지 — SessionEndEvent {userId(number), sessionId, reason} · 멱등키=(userId,sessionId) 파생(§2.7, 이슈 #62)
  - catalog     : [영구 미채택] I-17 pull 배치로 대체 (schemas.spring.ProductChangesPage, §4.8)
  - order       : [영구 미채택] GET /orders/recent 로 대체 (schemas.spring.RecentPurchases, §4.7)

SessionEndEvent 계약은 Spring PR #24 실측과 이슈 #62에서 확정됐다. 실제 모델은
``app.schemas.profile.SessionEndEvent``가 소유한다.
"""

from __future__ import annotations
