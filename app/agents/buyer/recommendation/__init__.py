"""추천 서브그래프 (api-spec v0.15.0, MVP).

파이프라인: dedup 이력 조회 → decompose → search(Spring 위임) → rerank → push.
  - dedup     : spring_client.get_recent_purchases(§4.7) — 결정 14-F (exact 제외·
                소모품 억제·되돌리기 칩). search 와 병렬 호출 가능, 실패 시 dedup 생략(degrade).
  - decompose : Haiku — 구조화 필터 + 키워드 (excludeProductIds 에 dedup 결과 주입)
  - search    : spring_client.search_products (질의 시점 Spring 위임 §4.6, C-15 최우선,
                price/stock+totalCount 반환)
  - rerank    : Sonnet — 프로필 반영 재랭킹 + 근거 생성
  - push      : 최종 랭크 id 를 Spring 에 push (I-21, spring_client.push_recommendations, 경로 B §4.2)

SSE 는 상품 카드를 싣지 않는다 (경로 B) — products.ready({sessionId, listId})만 emit,
표시 필드는 Spring enrich(§4.3). 스트림 수명주기(취소·타임아웃)는 §2.9.

[정정 v0.5.1] AI 생성물(extras·search_doc·임베딩)은 MVP 소속 — I-17 배치(§4.8)로 갱신.
질의 시점 후보 흐름에서의 임베딩 결합(방식1/방식2)은 OPEN — SearchBackend 로 교체 가능 유지.
상품 원본 컬럼 미러는 영구 미채택.
"""
