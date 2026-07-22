"""장바구니 서브그래프 (결정 7 / api-spec v0.15.0 §4.1·§4.9).

흐름:
  담기   : "담아줘" → (상품, 옵션, 수량) 의도 확정 → spring_client.add_to_cart(I-2, 단건)
           — 묶음은 상품별 반복 호출. 결과는 SSE action(CART_ADDED/CART_ADD_FAILED).
  되물음 : 옵션 필수 상품인데 optionId 없음 → I-2 가 400 CART_OPTION_REQUIRED(options 목록)
           → 실패 action 없이 token 으로 "어떤 색상으로 담을까요?" 재질문 → 다음 턴에서
           사용자 답을 optionId 로 해석해 재담기 (멀티턴, §4.1).
  조회   : "장바구니에 뭐 있어?" → spring_client.get_cart(I-18) → token 텍스트 답변.
           담기 전 조회로 기존 보유 확인 → 합산 안내("이미 담겨 있어 N개로 늘렸어요").
           합산 실행 권위는 Spring — 조회 실패 시에도 담기는 진행(degrade, §4.9).

AI 서버는 커머스 DB 에 직접 write 하지 않는다 — 담기 실행·검증은 Spring 소관.
[변경 v0.6.0] 게스트 담기 허용(BE 02 D30, 결정 8 개정) — 구 게스트 선차단/가입 유도 폐기.
로그인 유도는 결제 시점 FE 몫.

TODO: intent 추출 노드(상품/옵션/수량) + 되물음 상태 관리 + add_to_cart/get_cart 호출
      + 응답 분기 (PRODUCT_NOT_FOUND/STOCK_INSUFFICIENT/CART_ERROR, api-spec §4.1).
"""
