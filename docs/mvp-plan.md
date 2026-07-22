# MVP 구현 계획 (주제별)

MVP 목표: **데모 시나리오 최소셋 동작** — 단일 검색+필터 추천, 동일 질문의 개인화 차이, "담아줘" 장바구니 반영, 판매자 통계 Q&A. 각 주제의 "무엇을 어떻게" 를 정리한다. 계약 세부는 `api-spec §` 참조.

---

## 0. 공통 인프라 (인증 · 설정 · 스트림 운영)

| 항목 | 내용 |
|---|---|
| 목표 | 모든 엔드포인트가 공유하는 인증·설정·SSE 수명주기·관측 기반 |
| 접근 | RS256+JWKS 로컬 검증(무상태), config 주입, SSE 스트림 수명주기 미들웨어 |
| 관련 파일 | `app/core/{auth,config,logging}.py`, `app/api/deps.py`, `app/main.py` |
| 계약 | §2.3 인증 · §2.5 오류 봉투 · §2.8 레이트 리밋 · §2.9 스트림 수명주기 |

- **인증**: JWKS 공개키로 `kid` 매칭 → RS256 서명·`exp`·`iss`·`aud` 검증. 신원은 `sub`+`role`(member/guest/seller). `AUTH_MODE=dev`는 서명 검증 생략(로컬 전용). ✅ 검증됨
- **SSE 수명주기(§2.9)**: 세션당 활성 스트림 1개(초과 시 `409 STREAM_IN_PROGRESS`, in-memory 레지스트리) / 취소 = `request.is_disconnected()` 감지 → LLM 스트림 close + task cancel / 타임아웃 first-token 10s·상한 90s. 📋
- **레이트 리밋(§2.8)**: FastAPI 미들웨어 + in-memory, 토큰 `sub` 스코프, 분당 10·시간당 100(config). 목적 = 남용 차단. 📋
- 주의: 동시 스트림·레이트 리밋 상태는 단일 인스턴스 in-memory 전제 — 다중 인스턴스 확장 시 Redis 이관.

## 1. 구매자 추천 그래프

| 항목 | 내용 |
|---|---|
| 목표 | 자연어 → 근거 있는 추천 목록. 멀티턴 조건 누적·완화 |
| 접근 | intent router → recommendation 서브그래프. 경로 B(SSE에 카드 없음) |
| 관련 파일 | `app/agents/buyer/{graph,recommendation}`, `app/services/{search_service,spring_client}` |
| 계약 | §3.1 SSE · §4.6 검색 위임 · §4.7 이력 · §4.2 목록 push · §4.3 목록 GET |

- **파이프라인**: 이력 조회(dedup) ∥ decompose(Haiku, 구조화 필터+키워드) → search(Spring 위임, §4.6) → rerank(Sonnet, 프로필 반영) → push(§4.2) → SSE `products.ready{sessionId,listId}`.
- **조건 칩**: 추출 필터를 `conditions` 이벤트로 노출, 제거는 다음 턴 `message`에 규약 문자열(`"[조건 제거] priceMax"`)로 재분해 트리거. 규약 문자열 포맷은 LLM 팀 소유(우리가 확정해 FE 통보).
- **degrade**: search 실패 → `SEARCH_FAILED` error(후보 날조 금지) / rerank 실패 → 검색 상위 5~8개, 하드 제약 유지, `done` 종료 / push 실패 → 챗 텍스트 완료 + `products.ready` 생략.
- 주의: 가격 상한 등 정확 제약은 항상 검색 필터로. 후보 흐름(방식1/2)은 OPEN → `SearchBackend`로 교체 가능하게 유지.

## 2. 장바구니 (담기 I-2 · 조회 I-9)

| 항목 | 내용 |
|---|---|
| 목표 | "담아줘" 실행 + 옵션 되물음 + 장바구니 조회. 게스트 지원 |
| 접근 | AI는 의도(상품·옵션·수량)만 확정, 실행은 Spring I-2 위임 |
| 관련 파일 | `app/agents/buyer/cart`, `app/services/spring_client.py`(add_to_cart/get_cart) |
| 계약 | §4.1 담기(I-2) · §4.9 조회(I-9) · §3.1 `action` 이벤트 |

- **담기(I-2)**: `POST /internal/cart/items` + `X-Internal-Token`, 본문 신원(userId|guestId, JWT sub 유래), 단건(묶음은 반복 호출). 게스트 허용. quantity 1~99, 동일 상품·옵션은 Spring이 합산.
- **옵션 되물음**: `400 CART_OPTION_REQUIRED`(options 목록) → 실패 `action` 없이 `token`으로 재질문 → 다음 턴 답을 `optionId`로 해석해 재담기. `CART_OPTION_INVALID`는 1회 재시도 후 `CART_ERROR`.
- **조회(I-9)**: `GET /internal/cart` — "뭐 담겨 있어?" 응답(`token` 텍스트) + 담기 전 기존 보유 확인해 합산 안내. 합산 권위는 Spring, 조회 실패해도 담기는 진행(degrade).
- **결과**: SSE `action`(`CART_ADDED{cartItemId}` / `CART_ADD_FAILED{reason}`). reason = `PRODUCT_NOT_FOUND`/`CART_ERROR`/`OUT_OF_STOCK`(🔴 재고 코드 협의).
- 주의: `GUEST_NOT_ALLOWED` 폐기(결정 8 개정). 신원은 절대 FE 본문 값 신뢰 금지.

## 3. 구매 이력 & dedup

| 항목 | 내용 |
|---|---|
| 목표 | 추천 dedup + 프로필 구매 신호를 질의 시점에 확보 |
| 접근 | 주문 미러/시드 폐기 → `GET /orders/recent` 질의 시점 조회 |
| 관련 파일 | `app/services/spring_client.py`(get_recent_purchases) |
| 계약 | §4.7 (C-6) |

- 응답 3필드(productId·category·purchasedAt). search와 병렬 호출.
- **dedup(결정 14-F, AI-side)**: exact productId 제외 → §4.6 `excludeProductIds`로 전달 + 소모품 카테고리 억제 + 되돌리기 제안 칩(`suggestions`).
- 게스트는 호출 스킵. **degrade**: 실패/타임아웃 시 dedup 없이 추천 진행(막지 않음).
- 주의: `order_seed`는 폐기 예정 — 신규 코드에서 참조 금지.

## 4. 판매자 그래프 (SPEC-SELLER-001 — 분석 · 상품관리 · 일반질문)

| 항목 | 내용 |
|---|---|
| 목표 | 분석 Q&A(멀티에이전트 보고서 + 행동 추천) + 상품 CRUD(전 쓰기 HITL) + 일반질문 즉답 |
| 접근 | supervisor 구조화 출력 라우팅 → 분석 서브그래프(Send 팬아웃 워커 5종·검증 루프) / product_agent / general_agent |
| 관련 파일 | `app/agents/seller/`, `app/api/seller.py`, `spring_client`(집계 7종 I-6~I-16 · CRUD I-9~I-12) |
| 계약 | §3.2 SSE·HITL · §4.4 집계 7종 · §4.5 CRUD 4종 · **[SPEC-SELLER-001](specs/SPEC-SELLER-001.md)** |

- **분석**: planner(이력 반영·기간 정규화·시맨틱 캐시) → 워커 5종(sales_anomaly·conversion·behavior·churn·abuse, Send 팬아웃) → report → verifier(≤3회) → recommend → compose → 분석 이력 저장. 진행 상황 `token` emit(first-token 10s).
- **계산 3층 분담**: Spring=로그 단순 수치 / AI=고도화 계산(임계값 config 주입) / LLM=해석. 🔴 C-13에서 계산 경계표 확정(I-6 초안의 isAnomaly 필드 처리).
- **상품관리**: **모든 쓰기(등록/수정/삭제) HITL** — `draft{draftId, op, changes[]}` → interrupt → FE diff 카드 → 구조화 `confirm{draftId}` → resume → I-10/11/12. 재고는 I-11 통합. 삭제=soft(`HIDDEN`). 추천 적용("N번 적용해줘")도 저장된 구조화 추천 조회 → draft 경유 — 대화 재해석 실행 금지.
- **RAG 기준서**: pg-catalog pgvector — 분석 전 기준서 검색 강제, 코드 판정 번복 금지. 분석 이력은 pg-profile("프로필" 명칭은 구매자 쪽 전용).
- **degrade**: 워커 실패 → 부분 보고서 / 집계 전부 실패 → 사과 token + done / 쓰기 실패 → token 안내 + done / draft TTL 만료 → 재제안.
- 이벤트는 `token`/`draft`/`done`/`error`만. `finishReason`=`stop` 단일. role=seller 없으면 403.
- 주의: **발화 ≠ 동의**(confirm은 구조화 신호만) · 차트 전달은 계약 미정으로 보류 🔴 · I-8(account-events) admin 소유 협의 🔴 · 90s는 목표치(비동기·병렬로 단축, 상세 SPEC §7).

## 5. 프로필 파이프라인 (+ /profile/me)

| 항목 | 내용 |
|---|---|
| 목표 | 대화·구매 이력에서 취향 신호 축적 → 추천 개인화 |
| 접근 | 그래프 진입 시 요약만 read, write는 명시적 종료 또는 10분 비활동 후 sleep-time 병합 |
| 관련 파일 | `app/agents/profile/{reader,builder,gate}`, `app/api/{profile,events}.py`, `app/pipelines/scheduler.py` |
| 계약 | §3.4 `/profile/me` · §3.5 Spring session-end + AI 내부 inactivity timeout |

- **read**: 그래프 진입 시 `index.md` 압축 요약만 로드(전체 번들 금지).
- **승격 게이트(결정 4-A)**: 반복성·현저성·명시성 3조건 통과 시에만 write 후보. "기억해"는 즉시 기록, 일시적 요청("이번엔 비싸도")은 session_context에만.
- **write**: 턴 중 금지 → 세션 종료 델타 생성 → sleep-time 배치 병합(LLM consolidation). 소스 = 대화 + 구매 이력(§4.7).
- **종료 트리거(MVP)**: Spring은 `logout`·`newConversation`만 I-20으로 전달한다. AI는 회원 발화 저장 시 pg-profile의 `last_activity_at`을 갱신하고 기본 60초 sweep으로 10분 비활성 세션을 bounded batch 처리한다. 두 경로는 같은 finalizer·고정 멱등키를 사용하며, 탭 닫기 전용 신호/API는 만들지 않는다(이슈 #79).
- **/profile/me**: 마이페이지 자연어 노출. 게스트·미보유는 `{exists:false, markdown:null}` 정상 200.
- 저장 포맷: OKF 스타일 자연어 위키 + 경량 frontmatter. 저장소 = PostgresStore(별도 DB).

## 6. AI 생성물 배치 (I-8)

| 항목 | 내용 |
|---|---|
| 목표 | 상품 변경을 AI 생성물(extras·search_doc·임베딩)에 반영 |
| 접근 | AI가 당겨오는 pull 배치 — 이벤트/웹훅 없음 |
| 관련 파일 | `spring_client.fetch_product_changes`, `app/pipelines/{enrichment,embedding,seed_loader}` |
| 계약 | §4.8 (I-8, C-4) |

- **흐름**: `GET /products/changes?since={cursor}` (hasMore 루프) → `HIDDEN`은 생성물 삭제/비활성, `ON_SALE`은 enrichment(Haiku, Layer 2 속성·상황 태그) → search_doc 조립 → 임베딩(셀프호스트 1024-dim) → AI Postgres upsert.
- 커서/updatedAt 기반 페이지네이션. **초기 전체 구축도 커서 0부터 같은 배치**로.
- **degrade**: 실패 시 다음 주기 동일 커서부터 재개(자연 회복).
- 주의: 상품 원본 컬럼은 저장하지 않음(생성물만). torch는 `--group embedding`에서만 설치.

## 7. 대화 저장 & 모니터링

| 항목 | 내용 |
|---|---|
| 목표 | 멀티턴 컨텍스트·프로필 트리거 유지 + 운영 관측 |
| 접근 | LangGraph checkpointer(AI Postgres) + 구조화 로그 |
| 관련 파일 | `app/core/logging.py`, chat/seller 스트림 핸들러 |
| 계약 | §6.3 (운영 요구) |

- **대화 저장**: user 메시지 수신 즉시 저장 / assistant 응답 스트리밍 완료 후 저장 / 상태 `COMPLETED`·`FAILED`·`CANCELLED`. 취소·실패 시 부분 텍스트 보존(다음 턴·프로필 스캔 포함).
- **로그 필드**: requestId · userId/role · conversationId · latencyFirstToken/Total · model · promptTokens/completionTokens · errorType · streamStatus.
- **PII**: message 원문은 로그 금지(길이·해시만) — 원문은 대화 저장소에만.
