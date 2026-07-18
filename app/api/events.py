"""이벤트 수신 엔드포인트 — session-end 1종만 MVP (api-spec v0.15.0 §3.5).

[변경 v0.5.0 확정] Spring → AI 이벤트는 POST /events/session-end 하나만 MVP 유지:
  - session-end : 세션 종료 통지 → 프로필 델타 추출 트리거 (best-effort·멱등, C-8 🔴)
                  유실돼도 정합성은 대화 저장소 스캔이 회수한다.
  - catalog     : [영구 미채택] 카탈로그 이벤트/웹훅 없음 — AI 생성물 갱신은
                  I-17 pull 배치(spring_client.fetch_product_changes, §4.8)로 대체.
  - order       : [영구 미채택] 주문 알림/미러 없음 — 구매 이력은 질의 시점 조회
                  (GET /orders/recent, §4.7)로 대체.

TODO(MVP): APIRouter + POST /events/session-end + verify_service_token(레인 b) +
eventId 멱등 처리(§2.7, 202 Accepted) → main.py 라우터 등록.
"""

from __future__ import annotations
