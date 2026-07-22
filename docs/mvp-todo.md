# MVP TODO (주제별 체크리스트)

구현 계획은 [mvp-plan.md](mvp-plan.md). 범례: `[ ]` 미착수 · `[~]` 진행 중 · `[x]` 완료 · 🔴 Spring 협의 선행.

---

## 🚦 착수 우선순위

Spring 협의가 후보 확보의 유일 경로라 **협의 → 그래프 → 운영 규약** 순으로 푼다.

1. **Spring 계약 협의** (병렬 진행) — C-15(검색) → C-6(이력) → C-3/C-16(장바구니) → C-13(I-6) → C-4(I-8)
2. **공통 인프라** (0번) — 인증은 완료, SSE 수명주기·레이트 리밋
3. **구매자 추천 그래프** (1번) — 데모 핵심
4. **장바구니** (2번) — 데모 핵심 "담아줘"
5. **판매자 그래프** (4번), **프로필** (5번), **배치** (6번), **운영** (7번)

---

## 0. 공통 인프라

- [x] RS256+JWKS 인증 · dev 모드 · deps
- [x] config 주입 · 부팅 검증 · 스텁 스트림
- [ ] SSE 동시 스트림 제한 — 세션당 1개, `409 STREAM_IN_PROGRESS`, in-memory 레지스트리 (§2.9a)
- [ ] 요청 취소 — `is_disconnected()` 감지 → LLM 스트림 close + task cancel (§2.9b)
- [ ] 타임아웃 — first-token 10s / 상한 90s / AI→Spring 3s / LLM 30s+1재시도 (§2.9c)
- [ ] 레이트 리밋 미들웨어 — 토큰 스코프, 분당 10·시간당 100 (§2.8)
- [ ] 스트림 전 오류 봉투 코드 전체 (400/401/403/409/429/504) (§2.5)

## 1. 구매자 추천 그래프

- [~] 그래프 스캐폴드(intent router 분기)
- [ ] `search_products` Spring 연결 (§4.6) 🔴 C-15
- [ ] decompose 노드 (Haiku — 구조화 필터 + 키워드)
- [ ] rerank 노드 (Sonnet — 프로필 반영 + 근거 생성)
- [ ] `push_recommendations` → `products.ready` emit (경로 B, §4.2) 🔴 C-9
- [ ] `conditions` 칩 emit + 규약 문자열 재분해 (포맷 확정 후 FE 통보)
- [ ] degrade: SEARCH_FAILED / rerank 폴백 / push 실패 처리
- [ ] 폴백 서브그래프 (무관 질문 · 일반 대화)

## 2. 장바구니 (I-2 · I-9)

- [ ] intent 추출 노드 (상품 · 옵션 · 수량)
- [ ] `add_to_cart` I-2 연결 (§4.1) 🔴 C-3
- [ ] 게스트 담기 허용 (userId|guestId 분기)
- [ ] 옵션 되물음 멀티턴 (`CART_OPTION_REQUIRED` → token 재질문 → 재담기)
- [ ] `get_cart` I-9 연결 — 장바구니 질의 응답 (§4.9) 🔴 C-16
- [ ] 담기 전 기존 보유 조회 → 합산 안내
- [ ] SSE `action` 매핑 (CART_ADDED / CART_ADD_FAILED reason)

## 3. 구매 이력 & dedup

- [ ] `get_recent_purchases` 연결 (§4.7) 🔴 C-6
- [ ] dedup: exact 제외 → `excludeProductIds`
- [ ] 소모품 카테고리 억제 + 되돌리기 `suggestions` 칩
- [ ] 게스트 스킵 · 실패 시 degrade(dedup 없이 진행)

## 4. 판매자 그래프 (SPEC-SELLER-001)

- [~] `/seller/chat` 스캐폴드 (role=seller 403)
- [ ] spring_client 판매자 함수군 — 집계 7종(I-6~I-16) 신설 + CRUD I-9/10/11/12 신설, 구계약 스텁(get_seller_aggregates·get_product_detail) 대체 (§4.4·§4.5) 🔴 C-13/C-14
- [ ] supervisor 구조화 출력 라우팅 (analysis / product / general)
- [ ] general_agent — 읽기 도구 + 계산기 (쓰기 도구 배정 금지)
- [ ] 분석 기준서 문서 작성 → `seller_kb` 인제스트 → `search_analysis_guide` @tool (pg-catalog pgvector)
- [ ] 분석 서브그래프 — planner(이력·기간 정규화) → Send 팬아웃 워커 5종 → report (SPEC §2·§5)
- [ ] AI-측 고도화 계산 모듈 — 이동평균·이상 판정·전환율 비교, 임계값 config 주입 🔴 C-13 계산 경계표
- [ ] report_verifier 검증 루프 (결정론 → LLM 채점, ≤3회)
- [ ] recommend_agent (구조화 RecommendationSet, 읽기 전용) + compose + 진행 상황 `token`
- [ ] 분석 이력 저장·조회 — pg-profile PostgresStore + `analysis_detections` (SPEC §9.1)
- [ ] product_agent — **전 쓰기 HITL**: `draft{draftId}` emit → interrupt → 구조화 `confirm{draftId}` resume → I-10/11/12 (SPEC §6) 🔴 confirm 전송 형식
- [ ] 추천 적용 흐름 — 저장된 recommendations 조회 → draft 경유 (대화 재해석 실행 금지)
- [ ] 가드레일 (scope/PII/출력 검사) + 일관성 회귀 테스트(같은 질문 10회)
- [ ] 시맨틱 캐시 `question_cache`
- [ ] 차트 — **보류** (전달 계약 🔴, SPEC §12)

## 5. 프로필 파이프라인 (+ /profile/me)

- [~] reader/builder/gate 스텁
- [ ] reader — index.md 압축 요약 로드
- [ ] 승격 게이트 (반복성·현저성·명시성 3조건 + "기억해" hot-path)
- [ ] transient 격리 (session_context)
- [ ] 세션 종료 델타 → sleep-time consolidation 병합
- [x] PostgresStore 네임스페이스 (profile/facts — 이슈 #33, pgvector 시맨틱 인덱스는 facts 전용. episodes 는 MVP 미구현·고도화 범위)
- [ ] `GET /profile/me` 라우터 등록 (§3.4)
- [x] `POST /events/session-end` 수신 (§3.5, 멱등 — 이슈 #62/#64, C-8 해소)
- [ ] AI 내부 inactivity timeout 스케줄러 — 회원 발화 활동시각 DB upsert, 기본 10분/60초 bounded sweep, 공통 finalizer·실패 복구 (이슈 #79)

## 6. AI 생성물 배치 (I-8)

- [ ] `fetch_product_changes` 연결 + hasMore 루프 (§4.8) 🔴 C-4
- [ ] 커서 영속화 (다음 주기 since)
- [ ] `HIDDEN` 처리 (생성물 삭제/비활성)
- [ ] enrichment 노드 활성화 (Haiku 배치)
- [ ] build_search_doc + embed_texts 활성화 (torch, --group embedding)
- [ ] AI Postgres upsert + 초기 전체 구축(커서 0)

## 7. 대화 저장 & 모니터링

- [x] 대화 저장 (user 수신 즉시 / assistant 완료 후) — pg-profile `conversation_turns` 일반 테이블(이슈 #33, checkpointer 아님 — 감사·조회용)
- [ ] 저장 상태 COMPLETED/FAILED/CANCELLED + 부분 텍스트 보존
- [ ] 구조화 로그 필드 (requestId·latency 2종·model·tokens·errorType)
- [ ] PII: message 원문 로깅 금지 (길이/해시)

---

## 🔴 Spring 협의 대기 (블로킹)

| 항목 | 계약 | 블로킹 대상 |
|---|---|---|
| C-15 | `POST /products/search` (검색 위임) | 추천 그래프 전체 (최우선) |
| C-6 | `GET /orders/recent` (구매 이력) | dedup · 프로필 구매 소스 |
| C-3 | I-2 잔여 (재고 코드·options 스키마·서비스 토큰) | 장바구니 담기 |
| C-16 | I-9 (장바구니 조회 응답 필드) | 장바구니 조회 |
| C-13 | 집계 7종(I-6~I-16) 응답 스키마 + **계산 경계표**(Spring 수치 vs AI 고도화 계산, SPEC-SELLER-001 §5) | 판매자 분석 워커 전체 |
| C-14 | CRUD 4종(I-9~I-12) 응답 스키마 · HITL confirm 전송 형식 | product_agent 쓰기 |
| C-4 | I-8 (변경분 pull) | AI 생성물 배치 |
| C-9 | 목록 push 응답(listId) | 경로 B |
| C-12 | 목록 GET (Spring 소유) | FE 상품 리스트 |
| C-5 | productId 타입 통일 재통보 | 전 계약 |
| C-1 | role 값 집합 · 토큰 TTL | 인증 |
