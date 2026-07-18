"""판매자 챗봇 그래프 (api-spec v0.15.0 §3.2 — Batch 1 확대 범위).

MVP 범위 2가지 (결정 20 개정):
  (1) 매출/판매 통계 Q&A — 데이터 원천 = spring_client.get_seller_aggregates
      (I-6 집계 콜백, §4.4, C-13 최우선). sellerId 는 JWT sub, brandId 는 Spring 내부 해소.
  (2) 상세 수정/삭제 draft 흐름 — spring_client.get_product_detail(I-9 목록, §4.5) → LLM 개정안 →
      SSE draft {op, productId, changes:[{field,before,after}]} → FE diff 카드 →
      confirm{draftId} → AI 가 HITL 승인 후 I-11/I-10/I-12 직접 반영. 채팅 발화는 동의가 아니다.

응답 이벤트: token / draft / done / error 만 (products.ready·conditions·action 없음).
done.finishReason 은 "stop" 단일. 비범위(고도화): 리뷰 인사이트.

[대체] 구 order_seed 시드 집계(구 결정 22)·주문 미러 확장(구 결정 20 기본안) 폐기 — I-6 콜백.

TODO(seller graph SPEC): 통계 질의 파싱 → I-6 집계 조회 → token 산문 응답 /
draft intent → I-7 읽기 → 개정안 → draft emit.
"""
