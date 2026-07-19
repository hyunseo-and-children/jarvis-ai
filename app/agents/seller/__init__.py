"""판매자 챗봇 그래프 (api-spec v0.15.x §3.2, DESIGN-SELLER-TOOLS-STAGE1).

MVP 범위 2가지 (결정 20 개정, v0.9.0/v0.13.0 반영):
  (1) 매출/판매 통계 Q&A — 데이터 원천은 SpringClient 조회 7종(app/services/spring_client.py,
      §4.4) 을 app/agents/seller/tools.py 의 @tool 로 감싼 것이다. sellerId/brandId 는
      요청 본문이 아니라 검증된 판매자 JWT 클레임(Identity.brand_id)에서만 얻어
      ToolRuntime[SellerContext] 로 요청마다 주입한다(IDOR 방지, §2.6).
  (2) 상세 수정 draft 흐름 — I-9 자사 상품 목록 조회(list_my_products, §4.5)로 `before` 를
      확보 → LLM 개정안 → SSE draft {productId, changes:[{field,before,after}]} → 판매자
      승인(HITL) → AI 가 SpringClient.update_product 등(I-10/11/12, §4.5)을 **직접** 호출해
      반영한다. [폐기] 구 "FE가 판매자 JWT 로 Spring S-3 PATCH 실행" 모델(v0.4.0)은
      v0.9.0 에서 대체됐다 — S-3 는 FE 대시보드용 별개 조회 엔드포인트다(AI 쓰기 경로 아님).

응답 이벤트: token / draft / done / error 만 (products.ready·conditions·action 없음).
done.finishReason 은 "stop" 단일. 비범위(고도화): 리뷰 인사이트.

[대체] 구 order_seed 시드 집계(구 결정 22)·주문 미러 확장(구 결정 20 기본안) 폐기 —
SpringClient 콜백(§4.4/§4.5)으로 대체.

TODO(seller graph SPEC, 2단계 이후): 통계 질의 파싱 → READ_TOOLS 조회 도구 호출 →
token 산문 응답 / draft intent → list_my_products(before) → 개정안 → draft emit → 승인 후
쓰기 도구 호출. 본 패키지의 1단계(Tool 계층) 산출물은 calc.py(순수 계산)·tools.py(@tool
클로저 팩토리)이며, 그래프/서브에이전트 배선은 이 파일의 범위 밖(2단계 이후)이다.
"""
