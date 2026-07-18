# 자비스 AI 에이전트 서버 — API 명세서

> ⚠️ **동기화 사본(mirror)** — 이 파일의 **정본(single source of truth)은 기획 저장소**
> `project-planning/my-project/docs/api-spec.md` 다. 계약을 바꾸려면 정본을 먼저 개정하고
> 이 사본을 같은 버전으로 동기화한다. 사본과 정본이 어긋나면 **정본이 우선**한다.
> 동기화: **v0.15.4 기준 2026-07-18**.

| 항목 | 값 |
|---|---|
| 문서 버전 | v0.15.4 |
| 작성일 | 2026-07-14 (v0.15.4 개정 2026-07-18) |
| 상태 | draft |
| 대상 독자 | Spring 백엔드 팀, React 프론트엔드(FE) 팀 |
| 소유 | AI 에이전트 서버 팀 |

> 본 문서는 **인터페이스 계약(interface contract)** 이다. 사용자 대면 엔드포인트의 동작·불변식은 소유 SPEC(`SPEC-RECOMMEND-001`, `SPEC-PROFILE-001`)에서 확정되며, 본 문서는 그 계약을 외부(Spring/FE) 관점에서 정리한다. 이벤트 채널(`/events/*`)의 HTTP 계약은 **본 문서가 단일 소스(single source of truth)** 로 소유한다(product.md 결정 21).
>
> **[v0.5.0 개정 — 2026-07-15 사용자 최종 확정]** 본 개정은 v0.4.0 Batch 2(카탈로그 미러 + 배치 동기화)를 **되돌려**, **후보 검색 = 질의 시점 Spring 위임(`POST /products/search`)** 을 **프로젝트 전 범위의 유일·영구 후보 확보 경로**로 확정한다. **[v0.5.1 정정 — 용어 확정]** 채택하지 않는 것은 **상품 원본 컬럼의 AI측 사본**(가격·재고·상품명 등 필터 컬럼 복제)이다. **AI 생성물 — extras(추론 태그)·search_doc·임베딩 벡터 — 은 AI Postgres에 저장·유지**하며(결정 3 Layer 2/3·결정 6 존속), 상품 변경 반영은 **AI가 요청하는 pull 배치**(§4.8)로 갱신한다. 이는 v0.4.0의 provenance 노트가 폐기했던 검색 위임 노선을 **최종 채택**하는 것이며, 이미 boot-verified 구현 스캐폴드(`~/projet/hk-final`, jarvis-ai, FastAPI+LangGraph)가 이 노선 위에 존재하고 사용자가 이를 구현 기준으로 비준했다.
> - **핵심 변경**: 후보 확보가 "AI 자기 검색 인덱스(미러)"에서 "질의 시점 Spring `POST /products/search` 위임"(신규 §4.6)으로 **영구 전환**된다. 상품 원본 컬럼의 사본(미러)은 두지 않는다. **[v0.5.1 정정]** AI 생성물(extras·search_doc·임베딩)은 유지하며 bulk export pull 배치(§4.8, C-4 부활)로 갱신한다. 질의 시점 후보 흐름에서 AI 임베딩과 Spring 검색의 결합 방식은 **OPEN**(§4.8 말미 — 두 방식 병행 검토).
> - **[이벤트 최종 — 2026-07-15 사용자 확정]** `POST /events/session-end`(세션 종료 통지)만 **MVP에 유지**된다. **주문 알림(구 `POST /events/order`)·주문 미러는 채택하지 않는다** — 검색이 질의 시점 위임으로 확정되면서 구매 이력도 **추천 직전 질의 시점 조회(`GET /internal/members/{id}/orders`, §4.7)** 로 확보한다(결정 14-F 동작 요구는 불변, 데이터 획득 방식만 교체). **병행 PRD 초안 라인은 모든 이벤트를 고도화로 옮겼으나, 본 계약은 session-end 유지 한 지점에서 PRD와 갈라진다** — PRD의 events-scope를 **바로잡아야 하며**(§8 항목 6), 본 문서는 PRD를 조용히 따르지 않는다.
> - **Batch 1(판매자 확장)은 v0.4.0 그대로 유지**: `POST /seller/chat` = 통계 Q&A(원천 = Spring 집계 I-6 질의 시점 콜백, C-7 해소) + 상세 수정 draft 흐름(I-7 읽기 → LLM 개정안 → SSE `draft` → FE diff 카드 → FE가 Spring `S-3` PATCH로 반영, FE↔Spring 전제).
> - **[v0.6.0 개정 — 2026-07-15 사용자 확정, BE "챗봇 장바구니 담기(I-2)" 문서 채택]** 장바구니 계약을 BE 팀 I-2 문서 기준으로 재작성한다(§4.1) — **게스트 담기 허용**(02 D30, 결정 8 개정 필요 §8 항목 7), **`POST /internal/cart/items` + `X-Internal-Token` 서비스 토큰 + 본문 신원(userId/guestId, AI-검증 JWT `sub` 유래)**, **`optionId` 필수 옵션 되물음 멀티턴**(400 `CART_OPTION_REQUIRED` + options 목록 → LLM 재질문), 동일 상품·옵션 기존 존재 시 **Spring이 quantity 합산**. **장바구니 조회(§4.9, C-16 신설)** 추가 — "장바구니에 뭐 있어?" 질의 응답 + 담기 시 기존 보유 안내.
> - **[v0.7.0 개정 — 2026-07-15 사용자 확정, 스트림 운영 규약]** SSE 스트림 수명주기 규약 신설(§2.9) — **동시 스트림 제한(세션당 1개, `409 STREAM_IN_PROGRESS`)**, **취소 = 클라이언트 연결 종료**(FE `AbortController` → AI가 disconnect 감지 시 LLM 스트림 즉시 중단), **타임아웃 기준표**(first-token 10s / 스트림 상한 90s / AI→Spring 3s / LLM 30s+1재시도), **레이트 리밋 값·소유 확정**(FastAPI 미들웨어 + in-memory, 분당·시간당 상한 config). 대화 저장(COMPLETED/FAILED/CANCELLED)·로그/모니터링 필드는 운영 요구로 부록 §6.3에 등재.
>
> **[v0.3.0 명명 기준 — 유지]** FE/BE 팀의 챗 API 문서("추천 챗봇 CH-2")를 **명명 기준(naming baseline)** 으로 채택한다. 구매자 SSE 이벤트명은 `token`/`conditions`/`action`/`products.ready`/`done`/`error`를 쓰고(구 `text.delta`/`products` 폐기), 모든 페이로드 필드는 **camelCase**로 표기한다. 이 변경으로 `SPEC-RECOMMEND-001` §5.3과의 정렬이 깨지므로 해당 SPEC의 **동기화 개정(sync amendment)** 이 후속으로 필요하다(§7). 본 문서는 SPEC을 편집하지 않고 후속 항목으로만 등록한다.
>
> **[provenance — 노선 확정]** v0.4.0은 검색 위임 노선을 "미비준 병렬 초안"으로 폐기하고 미러+배치를 채택했으나, v0.5.0은 사용자 최종 확정으로 **검색 위임 노선을 유일·영구 비준 노선으로 채택**한다(구현 기준 = `~/projet/hk-final` 스캐폴드). v0.4.0 Batch 2(미러+배치·카탈로그 벡터 검색)는 **채택하지 않기로 확정**되어 문서에서 제거된다. 병행 PRD 라인 대비 유일하게 다른 점은 session-end가 MVP에 남는다는 것이다(주문 알림은 §4.7 질의 시점 조회로 대체). 상세는 §6.2 변경 이력 참고.
>
> **표기 규약**
> - 🔴 **협의 필요**: Spring/FE 팀과 계약 확정이 필요한 미해결 항목. 이 표시가 붙은 스키마는 본 문서에서 **제안(초안)** 으로만 제시한다.
> - **제안(초안)**: 어느 계약에서도 아직 확정하지 않은 형태(상관관계 키·목록 push 스키마·bulk export API·I-6/I-7 계약 등)를 본 문서가 초안으로 제안하는 것. 최종 확정 전까지 변경될 수 있다.
> - **확정안 반영**: 소유 SPEC 또는 팀 세션에서 확정된 결정을 본 문서에 반영한 것. Spring/FE 수용 전까지 🔴가 병기될 수 있다.

---

## 1. 개요 (Overview)

### 1.1 목적

자비스 AI 에이전트 서버가 제공/요구하는 HTTP API 표면을 Spring 백엔드 팀·React FE 팀과 공유하기 위한 계약 문서다. AI 서버는 자연어 상품 추천·프로필·판매자 통계/상세 수정 보조 응답을 담당하고, 커머스 트랜잭션(장바구니 저장·결제·회원)과 **상품 표시 UI(우측 상품 패널)·상품 원본 데이터**는 Spring이 소유한다.

### 1.2 호출 방향 원칙 (Call Direction)

FE가 사용자 대면 API에 대해 **AI 서버를 직접 호출**하고(결정 19), AI 서버는 **후보 검색(질의 시점 Spring 위임)·구매 이력 조회·장바구니·최종 목록 push·판매자 집계 조회(I-6)·상세 읽기(I-7)** 를 위해 Spring을 역호출한다. Spring → AI 이벤트 레인은 **`/events/session-end` 1종만** 유지된다(§3.5) — 주문 알림은 채택하지 않는다(§3.6·§4.7). **v0.5.0에서 AI 카탈로그 사본(미러)·bulk export 배치 레인은 채택하지 않기로 확정**되어 표면에서 제거된다 — 후보 확보는 미러 조회가 아니라 **질의 시점 `POST /products/search`**(§4.6)이며, 이는 프로젝트 전 범위의 유일 후보 경로다.

| 레인 | 방향 | 호출 | 인증 | 근거 |
|---|---|---|---|---|
| (a) 사용자 대면 | **FE → AI (직접)** | `POST /chat`, `POST /seller/chat`, `GET /profile/me` | 사용자 JWT (§2.3 a) | 결정 19 |
| (b) 이벤트 | **Spring → AI** | `POST /events/session-end` | 서비스 간 토큰 (§2.3 b) | 결정 12/16/21 |
| (c) 역방향(질의 시점) | **AI → Spring** | 후보 검색(I-1, §4.6), 구매 이력 조회(I-19, §4.7), **주문상태 요약(I-4 — CH-2 흡수, v0.15.2)**, 장바구니 담기(I-2, §4.1)·조회(I-18, §4.9), 추천 목록 push(I-21, §4.2), 판매자 집계(I-6/7/13/14/15/16, §4.4), 상품 CRUD(I-9/10/11/12, §4.5), 생성물 배치(I-17, §4.8·고도화) | **전부 서비스 토큰(internal, `X-Internal-Token`) + 본문/쿼리 신원**(AI가 JWT `sub`에서 도출) — BE 실측 정합(v0.13.0) | 결정 7 / 경로 B / BE DB 정합 |
| (d) 전제 계약 | **FE → Spring** | 세션+스트림 티켓 발급(CH-1)·티켓 재발급(CH-1b), 추천 목록 GET(§4.3), (판매자 FE 직접 상품편집 — AI 표면 밖) | Spring 소관 | 결정 19 / 경로 B / v0.12.0 |

- 레인 (a): 사용자(회원·게스트·판매자)의 요청. 신원은 **토큰 클레임**에서 추출한다(§2.3, §2.6). AI는 사용자 요청 본문의 식별자를 신뢰하지 않는다.
- 레인 (b): Spring → AI 이벤트는 **세션 종료 통지(`/events/session-end`, 프로필 조기 트리거) 1건**이다. 주문 알림은 채택하지 않는다 — 구매 이력은 질의 시점 조회(§4.7)로 확보하며, 카탈로그 변경 이벤트도 존재하지 않는다(사본 없음).
- 레인 (c): AI → Spring 질의 시점 역방향이 **7건**이다 — (1) **후보 검색(`POST /products/search`, §4.6, v0.5.0 신규)** — 추천 후보를 질의 시점에 Spring에 위임(가장 중요한 신규 계약, 검색 품질이 추천 품질을 좌우), (2) **구매 이력 조회(`GET /internal/members/{id}/orders`, §4.7, v0.5.0 신규)** — dedup(exact 제외·소모품 억제·되돌리기 칩)과 프로필 구매 소스의 입력, (3) 장바구니 담기(I-2, §4.1, `X-Internal-Token` 서비스 토큰 — v0.6.0에서 BE 문서 기준으로 전환), (4) **장바구니 조회(§4.9, v0.6.0 신규)** — 장바구니 질의 응답·기존 보유 안내, (5) 추천 목록 push(§4.2, 경로 B), (6) 판매자 집계 조회(I-6, §4.4) — 판매자 통계 답변 원천, (7) 상세 읽기(I-7, §4.5) — draft 흐름의 현재 상세 조회.
- 레인 (d): FE ↔ Spring 전제 계약(Spring 소유). (1) **세션+스트림 티켓 발급(CH-1, `POST /api/chat/sessions`)** — 응답에 `sessionId`(TTL 10분 sliding) + 첫 `streamTicket`(RS256, TTL 30~60s). (2) **스트림 티켓 재발급(CH-1b, 제안 `POST /api/chat/tickets`)** — 세션 유지한 채 새 티켓만 발급(2번째 메시지·`401` 시). **CH-1 재호출은 새 세션(맥락 단절)이라 티켓 재발급에 쓸 수 없어 별도 경로가 필요**하다. (3) 추천 목록 GET(§4.3). (4) 판매자가 FE에서 직접 상품을 편집하는 경로(AI 표면 밖). ※ 구 "draft 적용 = FE가 S-3 PATCH"는 **폐기** — 채팅 경로 쓰기는 AI 직접(§3.2), `S-3`은 자사 상품 목록 조회(=I-9)다.

> **[HARD] 후보 확보 = 질의 시점 Spring 검색(v0.5.0, 유일·영구)**: 구매자 추천 후보는 **질의 시점에 Spring `POST /products/search`(§4.6)를 위임 호출**하여 확보한다. 상품 원본 컬럼의 AI측 사본은 두지 않는다. **[v0.5.1]** AI 생성물(extras·search_doc·임베딩)은 AI Postgres에 저장하며(§4.8), 질의 시점에 AI 임베딩과 Spring 검색을 어떻게 결합할지는 OPEN(§4.8 말미)이다. rerank(profile_summary 반영)는 여전히 AI 경계에서 수행한다.
>
> **[HARD] 표시 경로 = 경로 B(불변)**: 상품 목록은 SSE에 싣지 않는다. AI가 최종 랭크 목록을 Spring에 push(§4.2)하면 Spring이 표시 필드를 enrich하여 저장하고(§4.3), FE가 이를 GET한다. **표시 권위 = Spring**(결정 9-B, AI는 표시 필드 미보유). §4.6 검색 응답의 price는 rerank·예산 검증(AI-side)용이며 우측 패널 표시가는 여전히 경로 B로 채운다. 단방향 원칙의 AI→Spring 역방향 예외 증가는 product.md 신규 결정 레코드가 명문화한다(§8 항목 3).

### 1.3 MVP 범위 요약

MVP(개발 가동 목표 2026-07-19)에 포함되는 API 표면:

- **아키텍처**: 사용자 대면 API(`/chat`·`/seller/chat`·`GET /profile/me`)는 **FE → AI 직접 호출**(사용자 JWT), **후보 확보는 질의 시점 Spring 검색 위임(`POST /products/search`, §4.6)**, **상품 목록 표시는 경로 B**(AI → Spring push → FE ← Spring GET)로 분리된다(§1.2). 상품 원본 컬럼 사본은 없음, AI 생성물(extras·search_doc·임베딩)은 pull 배치로 유지(§4.8, v0.5.1 정정).
- **추천 agent** — `POST /chat`(SSE 스트리밍, 상품 추천 서브그래프 포함). 소유: `SPEC-RECOMMEND-001`. 후보는 §4.6 Spring 검색으로 확보, rerank는 AI-side. SSE 스트림은 상품 카드를 싣지 않고 `products.ready` 상관관계 키만 emit한다.
- **후보 검색 위임** — `POST /products/search`(§4.6, AI → Spring 질의 시점). decompose 산출 구조화 필터로 Spring 카탈로그를 검색하고, rerank·예산 검증에 필요한 후보 필드(price 포함)를 돌려받는다. **가장 중요한 신규 Spring 계약**.
- **장바구니 서브그래프** — `POST /chat` 내부 흐름. 실제 담기는 AI → Spring 장바구니 API 호출(I-2, §4.1, 단건 — 묶음은 반복 호출). **게스트도 담기 가능**(v0.6.0). 옵션 필수 상품은 `CART_OPTION_REQUIRED` 응답의 options 목록으로 **되물음 멀티턴**을 수행하고, 담기 전/질의 시 장바구니 **조회**(§4.9)로 기존 보유·수량 합산을 안내한다. 결과는 SSE `action` 이벤트로 반영.
- **프로필 조회** — `GET /profile/me`(마이페이지, 토큰 소유자 본인). 소유: `SPEC-PROFILE-001`.
- **판매자 agent** — `POST /seller/chat`. (a) **매출/판매 통계 Q&A**(원천 = Spring 집계 I-6 콜백, C-7 해소) **+ (b) 상세 수정 draft 흐름**(I-7 읽기 → `draft` 이벤트 → FE 반영). 리뷰 인사이트는 **비범위(MVP 제외)**.
- **이벤트 채널** — `POST /events/session-end`(세션 종료 통지)만 MVP 유지. 주문 알림은 채택하지 않음 — 구매 이력은 **질의 시점 조회(`GET /internal/members/{id}/orders`, §4.7)** 로 대체(사용자 명시 결정 — 병행 PRD 라인과는 session-end 유지 지점에서 갈라짐, §8 항목 6).

> **판매자 agent 범위(Batch 1)**: 판매자 agent는 원래 고도화(~7/31) 범위였으나 2026-07-14 세션에서 최소 범위(통계 Q&A)로 MVP에 편입되었고(product.md 결정 20), 2026-07-15 세션에서 **상세 수정 draft 흐름까지 MVP로 확대**되었다(§8 결정 20 개정 항목). 리뷰 인사이트(측면별 감성)는 계속 고도화.
>
> **[v0.5.0] 시맨틱 검색 caveat(정직 명시)**: Case 3(상황 기반) 추천 품질은 **Spring 검색(`POST /products/search`) + LLM decompose(쇼핑리스트 분해)** 로 달성한다. **AI 측 시맨틱(임베딩) 인덱스는 도입하지 않는다** — 따라서 상황 태그·의미 유사도 기반 검색은 Spring 카탈로그의 키워드/필터 검색 능력 한도 안에서만 동작한다. 이 caveat로 `SPEC-RECOMMEND-001`의 검색 도구(search-tool) 절이 개정 대상이 된다(§7).

---

## 2. 공통 규약 (Common Conventions)

### 2.1 Base URL

```
{AI_SERVER_BASE_URL}
```

- 배포 환경별 실제 값은 인프라 설정으로 주입한다(플레이스홀더). 예: `https://ai.jarvis.internal`.
- 모든 §3 경로는 이 base URL에 상대적이다.

### 2.2 명명 규약 (Naming Convention)

**[HARD] 본 문서의 모든 JSON 필드명·SSE 이벤트명은 FE/BE 팀 챗 API 문서("추천 챗봇 CH-2")를 명명 기준으로 채택한다.**

- **구매자 SSE 이벤트명**: `token` / `conditions` / `action` / `products.ready` / `done` / `error`(§3.1). 구 v0.2.0/`SPEC-RECOMMEND-001` §5.3의 `text.delta`·`products`는 **폐기**한다.
- **판매자 SSE 이벤트명**: `token` / `draft` / `done` / `error`(§3.2). `products.ready`·`conditions`·`suggestions`·`budget`·`action`은 판매자 스트림에서 **emit하지 않는다**.
- **필드명**: 모든 페이로드는 **camelCase**(`sessionId`·`threadId`·`productId`·`finishReason`·`relaxationNotice`·`verifiedSum`·`withinBudget`·`droppedItems`·`feasibilityNotice`·`cartItemId`…). 구 snake_case는 전 계약에서 폐기한다.
- **일관성**: `/events/*`(§3.5~3.6)와 `GET /profile/me`(§3.4)도 **camelCase로 통일**할 것을 제안한다(제안(초안)). `SPEC-PROFILE-001` §5.4의 `ProfileViewResponse` 필드 역시 camelCase 정렬 대상이다(§7 후속 개정).

> **SPEC 정렬 깨짐 명시 🔴**: 이 명명 채택으로 `SPEC-RECOMMEND-001` §5.3(snake_case + `text.delta`/`products` 이벤트)과 정렬이 깨진다. 의미론은 보존하되 이름만 바뀌므로, **SPEC §5.3의 동기화 개정**이 후속으로 필요하다(§7).

### 2.3 인증 (Authentication) — 확정안 반영(RS256/JWKS·401 규약), Spring 수용 전 🔴

인증은 **호출자 유형에 따라 2종**으로 나뉜다.

#### (a) 사용자 대면 API — 사용자 JWT (레인 a)

`POST /chat`, `POST /seller/chat`, `GET /profile/me`에 적용한다.

```
Authorization: Bearer {STREAM_TICKET}   ← Spring이 스트림 단위로 발급한 단명 JWT (로그인 AT가 아님)
```

- **[개정 v0.10.0] SSE에 쓰는 토큰 = 스트림 단명 티켓** — 로그인 AT(전권 토큰)를 SSE에 직접 싣지 않는다. Spring이 **채팅 진입 시 신원을 확인하고 스트림 단위로 단명 JWT(RS256, TTL 30~60초)를 발급**하며, FE는 이 티켓으로 AI 서버에 SSE 연결한다. ("JWKS 검토 후 제안" 최종안 채택.)
  - **발급 흐름**: `FE → Spring`(회원=AT / 게스트=`guest_id` 쿠키) → `Spring`(신원 확인 후 스트림 티켓 발급, RS256) → `FE → AI`(티켓으로 SSE) → `AI`(JWKS 검증 후 스트리밍). **첫 티켓**은 **CH-1**(세션 발급, `POST /api/chat/sessions`) 응답에 얹어 추가 왕복이 없다(응답에 `sessionId` + `streamTicket`).
  - **[중요] 티켓 재발급 경로 필요** — 스트림 티켓 TTL(30~60초)이 세션 TTL(10분 sliding)보다 **훨씬 짧아**, CH-1 1회로는 첫 스트림만 커버된다. 2번째 메시지부터는 **세션을 유지한 채 티켓만 재발급**하는 별도 경로(가칭 **CH-1b `POST /api/chat/tickets`**)가 필요하다 — CH-1 재호출은 새 세션(맥락 단절)이라 쓸 수 없다. Spring 소유 전제 계약(§1.2 레인 d, 🔴 C-1).
  - **채택 이유**: (1) **게스트 커버** — 게스트는 로그인 AT가 없으므로 Spring이 `guest_id` 쿠키를 확인해 동일 경로로 티켓 발급(`sub_type: guest`). (2) **전권 AT 비노출** — SSE 쿼리스트링/헤더에는 30~60초짜리 읽기 전용 티켓만 나가 유출 시 피해가 "스트림 1회 연결"로 한정. (3) **aud 규율** — 로그인 AT는 Spring 전용, FastAPI용 `aud`는 티켓에만. (4) **발급 = 인증 관문** — 모든 SSE 연결이 스트림마다 Spring 신원 검증을 1회 통과.
- **[확정] 서명·검증 = RS256 + JWKS** — Spring이 **JWKS 엔드포인트**(`GET /.well-known/jwks.json`)를 노출하고, AI 서버가 JWKS 공개키를 **fetch·캐시하여 로컬 검증**한다(RS256, `kid`로 키 선택). **`kid` miss 시에만 refetch**하며, 요청마다 Spring에 왕복하지 않는다(FastAPI 기동 시 Spring이 잠깐 죽어 있어도 캐시로 동작).
- **[확정] 스트림 티켓 필수 클레임**:
  - `sub` — 사용자/판매자/게스트 식별자(숫자 id를 문자열로, §2.5·§2.6).
  - `sub_type` — `member` | `guest`. (구 `role`을 대체/보완 — 회원/게스트 구분. **판매자 role·`brandId` 표현 방식은 🔴 확인**, 아래 참고.)
  - `iss` — 발급자, 제안값 `"jarvis-spring-auth"`.
  - `aud` — 대상, 제안값 `"jarvis-fastapi-ai"`. **AI는 `aud`를 검증**한다(토큰 혼용 방지 — 로그인 AT는 이 aud가 없어 SSE에 못 씀).
  - `scope` — 제안값 `"chat:stream"`. **AI는 `scope`를 검증**한다.
  - `exp` — 발급 후 **30~60초**. (완전 1회용은 아니며 짧은 TTL로 근사 — Redis는 Spring 전용 결정 유지, stateless 검증.)
  - **판매자(`/seller/chat`)**: `role == "seller"` 판정 + **`brandId` 클레임이 티켓에 실린다 — [확정]**. 집계·CRUD 역호출(§4.4·§4.5)의 `{brandId}` path에 이 값을 쓴다. AI는 `brandId`를 **요청 본문에서 받지 않고 검증된 티켓 클레임에서만** 얻는다(userId와 동일 원칙 — IDOR 방지, 판매자가 남의 brandId로 조회 불가, §2.6). 다만 클레임 예시가 구매자(`sub_type`) 기준이라 **정확한 클레임 이름·`role` 병행 표현(형식)만 🔴 BE 확인**(C-1). AI는 신원을 **오직 토큰 클레임에서만** 추출한다(요청 본문 금지, §2.5·§3.1·§3.2).
  - 검증 항목: **signature / exp / iss / aud / scope**.
- **[확정] 401 통일 규약**: 토큰이 **없음/무효/만료**이면 AI 서버는 항상 **`401`** 을 반환한다.
  - `code == "TOKEN_EXPIRED"` — `exp` 경과.
  - `code == "TOKEN_INVALID"` — 서명 불일치·형식 오류·누락.
  - **FE 반응**: `401` 수신 → Spring **티켓 재발급 경로(CH-1b, §1.2 레인 d)** 에서 새 스트림 티켓 발급 → 새 티켓으로 원 요청을 **1회 재시도**(§2.5·§6.1). (티켓 TTL이 짧지만, 스트림 시작 전 만료 시의 재발급 흐름이며 — 이미 열린 스트림은 티켓 만료로 끊지 않는다, §2.5.)
- **[확정] `403` 규약**: `/seller/chat`는 `role == "seller"`를 요구한다. 판매자 스코프가 없는 토큰의 호출은 **`403 FORBIDDEN`**.
- **[폐기] `CHAT_SESSION_EXPIRED`(FE/BE 문서의 `400`) 폐기**: `sessionId`에는 만료 의미가 **없다**(§2.6). 인증 실패는 모두 `401`(TOKEN_*)로 통일한다.

#### (b) 이벤트 채널 — 서비스 간 토큰 (레인 b)

`POST /events/session-end`(Spring → AI, §3.5)에 적용한다. (v0.5.0에서 주문 알림·카탈로그 배치는 채택하지 않으므로 해당 인증 항목은 없다.)

```
Authorization: Bearer {SERVICE_TOKEN}
```

- 서비스 간(service-to-service) 토큰. 발급·회전 주체, 만료 정책, mTLS 병용 여부는 🔴 협의(§5 C-1).
- 사용자 JWT와 **별개의 자격 증명**이다 — 이벤트 채널은 사용자 신원이 아니라 서비스 신원을 검증한다.
- **[개정 v0.13.0] AI → Spring 역호출은 전 구간 동일 레인** — BE DB 실측(`internal` 그룹 전부 `서비스 토큰`)에 맞춰 **`X-Internal-Token` 서비스 토큰 + 본문/쿼리에 신원**(AI가 검증한 JWT `sub`에서 도출)으로 통일한다. 구 "사용자/판매자 JWT 포워딩" 제안(후보 검색·판매자·구매 이력)은 **폐기** — 장바구니 I-2 패턴이 표준. **IDOR 안전**: 본문 신원은 사용자 입력이 아니라 AI가 검증 토큰에서 도출한 값이다(§2.6).

### 2.4 Content-Type

| 방향 | Content-Type |
|---|---|
| 요청 본문(JSON) | `application/json; charset=utf-8` |
| 일반 JSON 응답 | `application/json; charset=utf-8` |
| SSE 스트리밍 응답(`/chat`, `/seller/chat`) | `text/event-stream; charset=utf-8` |

- SSE 응답 시 **FastAPI 앞단 리버스 프록시**는 **응답 버퍼링을 비활성화**해야 토큰 단위 스트리밍이 유지된다. FE가 AI 서버를 직접 호출하므로 chat 스트림에 대한 **Spring 중계 버퍼링 이슈는 해당하지 않는다**(§1.2).

### 2.5 스트림 전(前) 오류 봉투 (Pre-stream Error Envelope) — 확정안 반영, Spring 수용 전 🔴

비스트리밍 응답 및 **SSE 스트림이 시작되기 전** 거부(인증·요청 검증 등)의 오류 봉투다. (스트림 **내부** 오류는 §3.1/§3.2의 `error` 이벤트로 별도 전달되며 아래 봉투와 다르다.)

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "requestId": "string"
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `error.code` | string | 기계 판독용 오류 코드(아래 상태 매핑) |
| `error.message` | string | 사람이 읽는 안전한 메시지(내부 스택/PII 미포함) |
| `error.requestId` | string | 추적용 요청 식별자(로그 상관관계) |

**[확정] 스트림 전 상태 코드 매핑**:

| HTTP | `code` | 의미 |
|---|---|---|
| `400` | `BAD_REQUEST` | 요청 본문/파라미터 오류 |
| `401` | `TOKEN_EXPIRED` / `TOKEN_INVALID` | 인증 실패(§2.3 a) |
| `403` | `FORBIDDEN` | 권한 없음(예: 판매자 스코프 없이 `/seller/chat`) |
| `409` | `STREAM_IN_PROGRESS` | **[v0.7.0]** 동일 `sessionId`에 활성 스트림 존재(§2.9) — FE는 진행 중 스트림 종료 후 재시도 |
| `429` | `RATE_LIMITED` | 레이트 리밋 초과(§2.8) |
| `504` | `UPSTREAM_TIMEOUT` | **[v0.7.0]** 스트림 시작 전 상류(LLM/Spring) 타임아웃(§2.9 기준표) |

- `404`/`503`/`500` 등 그 외 상태는 필요 시 동일 봉투로 확장한다(제안). 정확한 코드 목록은 Spring 협의로 조정될 수 있다. 🔴
- 이 표가 **스트림 전 오류 코드의 통합 목록**이다(v0.7.0 — 4번 항목). 스트림 **내부** 오류는 §3.1 in-stream `error` 4종(+타임아웃, §2.9)으로 별도.

#### 401 토큰 만료 재발급 흐름

- 사용자 JWT가 없음/무효/만료이면 AI 서버는 `401`(TOKEN_EXPIRED 또는 TOKEN_INVALID)을 반환한다(레인 a).
- FE 재시도 흐름: `401` 수신 → FE가 **Spring에 토큰 재발급 요청** → 새 토큰으로 원 요청 **1회 재시도**. AI 서버는 재발급에 관여하지 않는다(§6.1).
- **SSE 인증은 연결 시작 시점에 검증**한다(제안). 스트림 진행 중 스트림 티켓(TTL 30~60초)이 만료되어도 **활성 스트림을 끊지 않는다**(스트림 자체가 LLM 응답 1회 분량) — 만료는 다음 연결에서 `401`로 나타나며, FE가 그 시점에 새 티켓 발급·재연결한다(§2.3). 확정 전 제안(초안)이다. 🔴

### 2.6 식별자 규약 (Identifiers) — 확정, 양팀 통보 필요

**[HARD, 개정 v0.15.3] `productId` = 숫자(BIGINT), DB 스키마 기준** — **[사용자 확정 2026-07-18]** 상품/옵션/장바구니/주문 원본 id는 전부 **`BIGINT` 숫자**다(product/product_option/cart_item/order 테이블). AI 계약(§4 internal + SSE `draft`)은 **숫자 id를 그대로** 쓴다. 구 "경계별 문자열 정규화·전 구간 문자열" 규칙은 **폐기** — 그냥 스키마 타입을 따른다. **게스트 id(`guestId`)만 UUID 문자열**(guest.id CHAR(36)). 구매자 SSE는 상품 카드/productId를 싣지 않으므로(경로 B, `products.ready`=listId만) 경계 변환 이슈 자체가 없다.

> **[✅ 정렬 완료 v0.15.3]** 코드(`schemas/spring.py`·`chat.py`)의 상품/옵션/장바구니/주문 id를 `int`(BIGINT)로, `guest_id`를 `str`(UUID)로 반영. CLAUDE.md "전 구간 string" 규칙도 개정. BE I-17 예시가 문자열 productId를 보이나 DDL은 BIGINT라 스키마 기준 `int`(BE 표기 불일치 — 통보 대상).

**사용자/게스트/판매자 식별자 = 숫자 id(numeric)** — Spring이 발급하며(게스트도 Spring이 숫자 id 부여), JWT `sub` 클레임에 **문자열화하여** 담는다. `role`(§2.3 a)로 회원/게스트/판매자를 구분한다.

**`sellerId` = JWT `sub`(role=seller)에서 도출 · `brandId` = JWT `brandId` 클레임에서 도출** — AI는 판매자 역호출(§4.4·§4.5)에 필요한 `sellerId`·`brandId`를 **모두 검증된 판매자 JWT 클레임에서만** 얻는다. **`brandId`를 요청 본문·사용자 발화에서 받지 않는다**(IDOR 방지 — 판매자가 남의 `brandId`로 조회 불가). RS256 서명이라 클레임 위조 불가. **[개정 v0.8.0]** 구 "AI는 brandId를 알지 못한다(Spring 내부 해소)"에서 "JWT 클레임에서만 획득"으로 완화 — BE 집계 API가 `{brandId}` path를 요구함에 따름. `brandId` 클레임 발급은 Spring 계약(🔴 C-1).

**`sessionId` = Spring 발급(CH-1 `POST /api/chat/sessions`)** — AI는 이를 **만료 의미 없는 불투명 스레드 키**로만 취급한다(AI에게 세션 만료·`CHAT_SESSION_EXPIRED` 개념 없음, §2.3 a). CH-1은 `sessionId`(BE Redis, TTL 10분 sliding) 외에 **첫 스트림 티켓**(§2.3 a)도 함께 반환하며, 이후 티켓 재발급은 CH-1b(§1.2 레인 d)가 담당한다.

> 사용자/판매자 식별자 타입(숫자)·클레임 키는 Spring 회원 스키마 소유다 — 세부는 🔴 협의(§5 C-10).

### 2.7 이벤트 엔드포인트 멱등성 규약 (Idempotency for Event Endpoints)

`/events/*` 엔드포인트(§3.5~3.6)는 통지 채널이므로 **멱등(idempotent)** 이어야 한다.

- 각 이벤트는 **멱등 키**를 담는다(제안 필드명: `eventId`). 동일 `eventId`의 재전송은 중복 처리되지 않는다.
- 통지는 **best-effort** 이며, 유실되어도 AI 서버의 정합성은 통지에 의존하지 않는다(세션 종료: 대화 스캔이 회수 / 주문: 일 1회 보정이 회수). 상세는 각 엔드포인트 항목 참고.
- 성공 응답은 처리 여부와 무관하게 `202 Accepted`(수신 확인)를 기본으로 제안한다. 🔴

### 2.8 CORS 및 레이트 리밋 (CORS & Rate Limiting) — 🔴 협의 필요

FE가 AI 서버(FastAPI)를 **다른 오리진에서 직접 호출**하므로 브라우저 CORS·남용 방어가 AI 서버 앞단으로 이동한다.

- **CORS**: AI 서버는 FE 오리진에 대해 CORS 헤더를 서빙해야 한다. 허용 오리진 목록은 🔴 협의(§5 C-11). `Authorization` 헤더를 사용하므로 브라우저 **preflight(OPTIONS)** 가 발생한다 — AI 서버는 preflight에 `Access-Control-Allow-Headers: Authorization` 등으로 응답해야 한다.
- **레이트 리밋(레인 a)**: 게스트도 토큰(익명 JWT)을 지참하므로 **토큰 스코프 기반 레이트 리밋**이 가능하다(§2.5). 초과 시 `429 RATE_LIMITED`(§2.5).
- **[v0.7.0 확정] 목적·소유·값**: 목적은 정밀 과금 통제가 아니라 **무분별한 남용 차단**(2026-07-15 사용자). **MVP 소유 = FastAPI 미들웨어 + in-memory 카운터**(단일 인스턴스 전제 — 다중 인스턴스 확장 시 Redis 이관, §2.9 동시 스트림 레지스트리와 동일 단서). 상한은 **config 기본값 제안**: 채팅 메시지(POST /chat·/seller/chat) **분당 10회 / 시간당 100회**(토큰 `sub` 스코프, 게스트 동일). 값 자체는 운영 조정 대상이며 계약 사항은 "429 + 토큰 스코프"뿐이다.
- **잔여 🔴(C-11)**: 허용 오리진 목록만 남음.

### 2.9 SSE 스트림 수명주기 — 동시 스트림·취소·타임아웃 [v0.7.0 신설]

`POST /chat`·`POST /seller/chat` 공통 규약이다.

#### (a) 동시 스트림 제한 — 세션당 1개

- 동일 `sessionId`에 활성 스트림이 있는 상태에서 새 요청이 오면 **`409 STREAM_IN_PROGRESS`**(§2.5 봉투)로 거절한다(기존 스트림은 유지 — last-wins 아님, 2026-07-15 확정).
- **FE 1차 방어**: 스트리밍 중 입력창 비활성화. 409는 서버 측 백스톱(탭 중복·재전송 대비).
- 구현: 인프로세스 활성 스트림 레지스트리(**MVP 단일 인스턴스 전제** — 결정 8의 무상태 원칙과의 긴장은 "요청 간 사용자 상태 없음" 의미로 한정 해석하고, 다중 인스턴스 확장 시 Redis로 이관).

#### (b) 요청 취소 — 취소 신호 = 연결 종료 (별도 취소 엔드포인트 없음)

- FE: `AbortController.abort()` → fetch 연결 종료. 이것이 유일한 취소 인터페이스다.
- AI 서버: SSE 제너레이터가 이벤트 전송 사이마다 disconnect를 감지(`request.is_disconnected()` 폴링)하고, 감지 즉시 **진행 중인 LLM 스트림을 close**(토큰 비용 차단)하며 LangGraph 실행 task를 취소한다.
- 취소된 턴의 대화 저장은 `CANCELLED` 상태 + **부분 생성 텍스트 보존**(§6.3) — 다음 턴 컨텍스트·프로필 스캔에 포함된다.

#### (c) 타임아웃 기준표 — 제한값 확정(config 기본값)

| 구간 | 기준값 | 초과 시 동작 |
|---|---|---|
| FE→AI **first-token**(스트림 첫 이벤트까지) | **10s** | 스트림 시작 전이면 `504 UPSTREAM_TIMEOUT`(§2.5), 시작 후면 in-stream `error` 후 종료 |
| FE→AI **스트림 전체 상한** | **90s** | `done`(finishReason `stop`) 강제 종료 + 저장 상태 `FAILED` 아님(정상 절단) |
| AI→Spring 콜백(§4.1/§4.4~4.7/§4.9) | **3s**(BE I-2 문서 기준으로 통일) | 각 계약의 degrade 규칙(조회 생략·담기 `CART_ERROR`·dedup 생략 등) |
| AI→LLM 단일 호출 | **30s + 1회 재시도** | 재시도 실패 시 in-stream `error`(`LLM_UNAVAILABLE` 계열) |

- 값은 config 기본값이며 운영 조정 가능. **계약 사항은 초과 시 동작**(어떤 오류가 어느 채널로 오는가)이다.

---

## 3. AI 서버 제공 API

> **호출자 구분**: §3.1~3.4(사용자 대면)은 **FE → AI 직접 호출**(사용자 JWT, 레인 a). §3.5~3.6(`/events/*`)은 **Spring → AI 서버 간 호출**(서비스 토큰, 레인 b). §1.2 참고.

### 3.1 `POST /ai/chat` — 구매자 챗봇 (SSE 스트리밍, FE 직접)

구매자의 자연어 질의를 받아 상품 추천/장바구니/상품 질문/**주문상태 문의** 등을 SSE로 스트리밍 응답한다. **[v0.15.2] 주문상태 Q&A(I-4)를 CH-2에 흡수** — 별도 CS 챗봇 없음. 관리자 CS 문의(CH-3·I-5·AD-1/2·M-9)는 **post-MVP**. 소유: `SPEC-RECOMMEND-001`(추천 서브그래프), 상위 구매자 그래프 SPEC(라우팅).

> **[경로 정합 v0.15.0]** FE-대면 경로는 **`{AI_SERVER}/chat`**(BE DB 07/17 실측 — 구 `/ai/chat` 표기 정정, `{AI_SERVER}` 접두어로 AI 서버 직접 호출임을 명시, 인증=스트림 티켓 필요). 본 문서 다른 위치의 `POST /chat`·`POST /ai/chat` 표기는 이 경로로 읽는다. (판매자는 `{AI_SERVER}/seller/chat`.)

#### 요청 (Request)

```json
{
  "sessionId": "string",
  "threadId": "string",
  "message": "string"
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `sessionId` | string | 예 | Spring 발급 세션 식별자(불투명 스레드 키, 만료 없음, §2.6). 세션 종료 통지(§3.5)와 상관관계 |
| `threadId` | string | 예 | 대화 스레드 식별자. 멀티턴 필터 누적·프로필 델타 워터마크 대상 |
| `message` | string | 예 | 현재 턴 사용자 원문 질의 |

> **[보안] `userId`는 요청 본문에 없다** — 사용자 식별자·역할은 `Authorization` 헤더의 JWT 클레임(`sub`/`role`)에서만 추출한다(사칭 방지, §2.3 a·§2.5).

#### 응답 (Response) — `text/event-stream`

SSE로 스트리밍한다. 표준 `EventSource`는 GET 전용이므로 FE는 **fetch 스트리밍(ReadableStream)** 으로 소비한다(§6.1). 이벤트명은 `token`/`conditions`/`action`/`products.ready`/`done`/`error`를 쓴다. **상품 카드는 SSE로 오지 않는다**(경로 B, §3.3·§4.2·§4.3).

**(1) `token`** — 근거/코멘트 토큰 증분 (0회 이상).

```json
{ "type": "token", "data": { "text": "이 케이스는 방수라서" } }
```

**(2) `conditions`** — 추출된 필터 조건을 FE 제거 가능한 칩으로 전달 (0~1회)

```json
{
  "type": "conditions",
  "data": {
    "chips": [
      { "field": "priceMax", "label": "5만원 이하", "value": 50000 },
      { "field": "category", "label": "여행용품", "value": "여행용품/보안용품" }
    ]
  }
}
```

- FE는 각 칩을 제거 가능한 형태로 노출한다. **칩 제거는 왕복(round-trip)** — 다음 턴의 `message`에 **규약 문자열**(예: `"[조건 제거] priceMax"`)로 실어 재분해를 트리거한다.
- **규약 문자열 정확 포맷은 🔴 협의(LLM 팀 소유)**.

**(3) `action`** — 장바구니 담기 결과 (0회 이상). §4.1(I-2)과 연동.

```json
{
  "type": "action",
  "data": { "type": "CART_ADDED", "message": "여행용 방수 파우치를 장바구니에 담았어요.", "cartItemId": "55" }
}
```

실패 예:

```json
{
  "type": "action",
  "data": { "type": "CART_ADD_FAILED", "message": "해당 상품을 찾지 못했어요.", "reason": "PRODUCT_NOT_FOUND" }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `type` | `"CART_ADDED"` \| `"CART_ADD_FAILED"` | 담기 성공/실패 |
| `message` | string | 사용자 노출 안전 문구 |
| `cartItemId` | string \| 없음 | 성공 시 담긴 항목 식별자(I-2 응답 `data.cartItemId`) |
| `reason` | string \| 없음 | 실패 시 사유 코드(§4.1) |

- **`reason` 허용값(v0.6.0 재편)**: `PRODUCT_NOT_FOUND` / `CART_ERROR` / `OUT_OF_STOCK`(BE I-2 문서에 재고 코드 부재 — 🔴 협의, §4.1). ~~`GUEST_NOT_ALLOWED`~~는 **폐기** — 게스트도 담기 허용(v0.6.0, 결정 8 개정 필요 §8 항목 7).
- **옵션 되물음은 `action` 실패가 아니다** — I-2가 `400 CART_OPTION_REQUIRED`(options 목록 포함)를 반환하면 AI는 실패 `action`을 emit하지 않고 **`token` 텍스트로 옵션을 되묻는 멀티턴**으로 이어간다(§4.1). 사용자가 옵션을 답하면 `optionId`를 해석해 재담기한다.
- **장바구니 조회 응답("장바구니에 뭐 있어?")도 별도 이벤트 없이 `token` 텍스트**로 답한다(§4.9).

**(4) `products.ready`** — AI가 추천 목록을 Spring에 push한 뒤 emit (정확히 1회, 성공 시).

```json
{ "type": "products.ready", "data": { "sessionId": "sess-771", "listId": "list-4471" } }
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `sessionId` | string | 상관관계 키(요청과 동일) |
| `listId` | string | FastAPI 생성 목록 식별자(§4.2 I-21) — reason 포함 카드는 CH-5로 조회(§4.3) |

- **상관관계 키 형태는 제안(초안)** 🔴 — 정확한 키는 Spring 목록 push/GET 계약(§4.2/§4.3) 확정과 함께 정한다.
- FE는 `products.ready` 수신 시 §4.3 목록 GET으로 Spring이 표시 필드를 채운 목록을 조회해 우측 상품 패널을 렌더한다(§6.1).
- **push 실패 시**: `products.ready`는 emit되지 **않는다.** 챗 텍스트는 정상 완료되고 지연 안내가 포함되며, 스트림은 `error`가 아니라 **`done`** 으로 종료한다(§3.3).

**(5) `done`** — 정상 종료

```json
{ "type": "done", "data": { "finishReason": "stop" } }
```

- `finishReason`: `"stop"`(정상 완료) \| `"zero_result"`(0건). **0건은 오류가 아니라 정상 종료**이며 FE는 우측 패널을 빈 상태(empty state)로 전환한다.
- 재랭킹(rerank) 실패 또는 목록 push 실패도 `error`가 아니라 `done`으로 종료한다(degrade 정책, §3.3).

**(6) `error`** — 오류 종료 (스트림 내부)

```json
{ "type": "error", "data": { "code": "LLM_TIMEOUT", "message": "일시적으로 응답이 지연됐어요." } }
```

- **스트림 내부 `error.code` 허용값(4종)**: `LLM_TIMEOUT` / `LLM_UNAVAILABLE` / `SEARCH_FAILED` / `INTERNAL`.
- **단계별 상세는 서버 로그 전용** — decompose/rerank 등 스테이지 단위 실패 코드는 사용자 스트림에 노출하지 않는다. rerank 실패는 검색 상위로 degrade 후 `done`으로 종료한다(하드 제약 유지).

#### MVP 추가 페이로드 — SSE 측 탑재 (구매자)

FE/BE 문서에 없으나 MVP에 필요한 아래 3종은 **모두 구매자 SSE 측에 실린다**(표시 필드는 Spring, 추천 로직 산출물은 AI 경계 유지):

- **`suggestions`(제안 칩)** — 0건 완화 제안 + 구매 이력 되돌리기(결정 14-D/14-F). 전용 이벤트 `suggestions`로 emit(제안(초안)).

```json
{
  "type": "suggestions",
  "data": {
    "chips": [
      { "label": "6만원대까지 볼까요?", "relaxation": { "field": "priceMax", "value": 65000 }, "estCount": 12 },
      { "label": "소금은 최근 구매 — 다시 추천받기", "revert": { "category": "조미료" }, "estCount": 8 }
    ]
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `label` | string | 칩 문구 |
| `relaxation.field` | string | 완화 대상 필드 |
| `relaxation.value` | any | 제안 값 |
| `revert.category` | string | 구매 이력 억제 되돌리기 대상 카테고리(결정 14-F) |
| `estCount` | int | 완화/재포함 적용 시 예상 결과 수(COUNT). `estCount == 0`인 칩은 제외 |

- **`relaxationNotice`(자동 완화 투명 안내)** — 0건 자동 완화 적용 시 안내(결정 14-D). 안내 산문은 `token`으로 스트리밍하고, 기계 판독 플래그가 필요하면 `done.data.relaxationNotice: string | null`로 병기(제안(초안)).
- **총액 예산 요약(BudgetSummary)** — Case 3 총액 예산(결정 14-A). 전용 SSE 이벤트 `budget`(제안(초안)):

```json
{
  "type": "budget",
  "data": { "totalBudget": 50000, "verifiedSum": 47800, "withinBudget": true, "droppedItems": [], "feasibilityNotice": null }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `totalBudget` | int | 묶음 총액 상한 |
| `verifiedSum` | int | 코드가 **인덱스 price**로 결정론 합산한 값(LLM 산수 아님) |
| `withinBudget` | bool | `verifiedSum <= totalBudget` |
| `droppedItems` | string[] | 예산 초과 제외 아이템 label |
| `feasibilityNotice` | string \| null | 부분 충족 안내 |

> **[주의] `verifiedSum`은 검색 응답(§4.6) 가격 기준**이다(질의 시점 Spring 가격이라 신선함). 다만 경로 B에서 표시 가격은 Spring이 목록 GET 시점에 다시 채우므로(§4.3), 검색~표시 사이 가격 변경 시 SSE `budget`과 우측 패널 표시가가 순간 괴리할 수 있다(SPEC-RECOMMEND-001 OPEN-11). 예산 표시 정책은 🔴 기획·Spring 협의(§8 항목 2).

#### 이벤트 순서 계약

정상 흐름(추천): `conditions`(0~1회) → `token`(0회 이상) + `suggestions`/`budget`(해당 시) → `products.ready`(성공 시 정확히 1회) → `done`(1회). 장바구니 흐름: `token`/`action` → `done`. `products.ready`는 목록 push 성공 이후에만 나타난다.

#### 오류/degrade 동작 (참고)

- `search` 실패: `SEARCH_FAILED` `error` 이벤트, 후보 날조 없음.
- `rerank` 실패 또는 출력 검증 실패: 검색 상위 5~8개로 degrade, 하드 제약(예: `priceMax`) 유지, `error`가 아닌 `done` 종료.
- 목록 push(§4.2) 실패: 챗 텍스트 정상 완료 + 지연 안내 + `done`(no `products.ready`)(§3.3).
- LLM 타임아웃/불가용: `LLM_TIMEOUT`/`LLM_UNAVAILABLE` `error` (기준값·재시도는 §2.9 c).
- 스트림 중 소비자 abort(HTTP 연결 종료): 진행 중 LLM 호출 취소 — 취소 의미론·부분 텍스트 저장은 §2.9 b·§6.3.
- 동시 스트림·타임아웃 수명주기 전반은 **§2.9**(v0.7.0, `/seller/chat` 공통).

### 3.2 `POST /seller/chat` — 판매자 챗봇 (SSE 스트리밍, FE 직접) — [v0.4.0 확대, Batch 1]

입점 판매자의 (a) 매출/판매 통계 자연어 질문과 (b) 상품 상세 수정 요청을 처리한다. **MVP 범위 확대**: 통계 Q&A **+ 상세 수정 draft 흐름**. 소유 SPEC은 별도(판매자 그래프 SPEC, 미작성).

> **인증**: `role == "seller"` 필수. 판매자 스코프가 없는 토큰의 호출은 `403 FORBIDDEN`(§2.3 a).
>
> **응답 형식**: `/chat`과 일관성을 위해 **SSE 스트리밍**. 이벤트는 `token`/`draft`/`done`/`error`만 쓴다 — `products.ready`·`conditions`·`suggestions`·`budget`·`action`은 판매자 스트림에서 **emit하지 않는다**(§2.2). `done.finishReason`은 `"stop"` **하나뿐**이다(`zero_result` 없음).

#### 요청 (Request) — 제안(초안)

```json
{
  "sessionId": "string",
  "threadId": "string",
  "message": "string"
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `sessionId` | string | 예 | 세션 식별자(불투명 스레드 키) |
| `threadId` | string | 예 | 대화 스레드 식별자 |
| `message` | string | 예 | 통계 질문("이번 주 매출 어때?") 또는 상세 수정 요청("이 상품 설명 더 매력적으로 바꿔줘") |

> **[보안] `sellerId`·`brandId`는 요청 본문에 없다** — 판매자 식별자는 JWT `sub`·`brandId` 클레임(+`role == "seller"`)에서만 추출한다(사칭 방지, §2.3 a). AI는 `brandId`를 **검증된 토큰에서** 얻어 집계 역호출(§4.4)의 `{brandId}` path에 쓴다 — 사용자 입력 brandId는 신뢰하지 않는다(§2.6).

#### 응답 (Response) — `text/event-stream`

**(1) 통계 Q&A 흐름**: `token`(답변 토큰) → `done` → (오류 시 `error`). 통계 수치는 MVP에서 `token` 산문으로 응답한다(구조화 `stats` 이벤트는 판매자 SPEC에서 확정). 원천은 **I-6 집계 콜백**(§4.4·아래 데이터 소스).

**(2) 상품 수정/등록/삭제 흐름 — [확정 v0.11.0]**: 판매자 `product_agent`가 상품 쓰기를 **AI가 Spring internal API로 직접 수행**하되, **모든 쓰기는 HITL 승인 게이트를 통과**한다. 채팅 경로 쓰기는 AI가, FE에서 직접 편집하는 경로는 FE↔Spring(AI 표면 밖)이 담당한다.

**2-스트림(interrupt/resume) 흐름** — SSE 1스트림 = 응답 1회(§2.9)라 승인 대기를 한 연결에 물지 않고 끊고-재개한다:
```
[스트림 1 · 제안]  token(근거) → draft{draftId, op, changes} → (LangGraph interrupt, 상태 checkpointer 저장) → done
                   FE: diff 카드 + [적용]/[취소]
[스트림 2 · 승인·실행]  FE가 confirm{draftId} 전송 → 그래프 resume → AI가 I-10/I-11/I-12 호출(§4.5) → token(결과) → done
```
- **읽기(before)**: 대상 확인은 **I-9 자사 상품 목록**(§4.5)으로 조회.
- **HITL 안전장치 [HARD]**:
  1. **승인은 `draftId`에 바인딩** — confirm은 그 draft를 참조해 **보여준 diff == 실행하는 쓰기**를 보장(다중 draft·불일치 방지). 상태 checkpointer가 실제 변경분을 보유.
  2. **명시 액션만 승인** — confirm은 **구조화 신호**(`{action:"confirm", draftId}`)여야 하며 자유 텍스트는 승인 아님(발화 ≠ 동의).
  3. **멱등성** — 동일 `draftId` 재전송은 1회만 실행(더블클릭 방지).
  4. **Spring 소유권이 하드 게이트** — HITL 우회해도 Spring이 `brandId`(JWT)로 상품 귀속 검증(§4.5). HITL은 사람-안전, Spring authz는 최종 방어.
  5. **대기 TTL** — 미승인 draft는 N분 후 만료(checkpoint TTL) — 지연 승인 방지.
- **삭제(I-12)는 필수 HITL** — soft delete(`status=HIDDEN`, 물리 삭제 없음). HITL(그래프) + soft delete(데이터) 이중 방어. (MVP는 전 쓰기 단순 `[적용]` 확인, 삭제만 문구 강조 권장.)
- **`draftId`는 선택적 권장** — 제안이 항상 하나·즉시 승인이면 checkpointer만으로도 동작하나, 다중 draft·멱등 대비로 부여를 권장.
- **HITL 승인 이벤트명·confirm 전송 형식(별도 요청 vs 특수 message)은 🔴 판매자 SPEC/BE 확정** (S-2 interrupt/resume 계열).

**`draft`** — 상세 수정 개정안 (정확히 1회)

```json
{
  "type": "draft",
  "data": {
    "draftId": "draft-8f21",
    "op": "update",
    "productId": "P-10293",
    "changes": [
      { "field": "description", "before": "여행용 방수 파우치입니다.", "after": "우천·수영장에도 안심인 IPX8 방수 파우치. 여권·전자기기를 완벽 보호합니다." },
      { "field": "name", "before": "방수 파우치", "after": "여행용 IPX8 방수 파우치" }
    ]
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `draftId` | string | 이 제안의 식별자. 승인(confirm)이 이 값을 참조해 **보여준 것 == 실행하는 것**을 보장하고 다중 draft·중복 승인을 구분한다(아래 HITL) |
| `op` | `"update"` \| `"create"` \| `"delete"` | 실행할 쓰기 종류(I-11/I-10/I-12 매핑) |
| `productId` | number | 대상 상품 식별자(숫자 BIGINT, §2.6). `create`는 없을 수 있음 |
| `changes` | array | 필드별 변경 제안 배열 |
| `changes[].field` | string | 수정 대상 필드(예: `name`·`description`·`price`·`stockQuantity`·`status`) |
| `changes[].before` | string | I-9(목록)로 읽은 현재 값 |
| `changes[].after` | string | LLM 생성 개정안 |

- **FE 렌더링**: FE는 `draft`를 **diff 카드**로 렌더하고 `[적용]`/`[취소]` 버튼을 노출한다(승인 UI).
- **[HARD, 개정 v0.9.0] 반영은 AI가 Spring internal API로 직접 수행** — 판매자 승인(HITL) 후 AI가 **I-11 PATCH(수정)/I-10 POST(등록)/I-12 DELETE(삭제)**(§4.5, `X-Internal-Token`+`{brandId}`)를 호출한다. **구 "FE가 본인 JWT로 S-3 PATCH" 모델은 폐기** — [BE 실측 정정 v0.13.0] **S-3 = `GET /api/seller/products`(SELLER, FE→Spring)** 로 **판매자 본인 FE 대시보드용** 목록이고, AI가 쓰는 목록은 **I-9 `GET /internal/seller/{brandId}/products`(서비스 토큰, AI→Spring)** 로 **별개**다(둘 다 조회, 레인만 다름). 즉 S-3는 PATCH가 아니며 I-9와 동일 엔드포인트도 아니다. 채팅 경로의 쓰기는 AI가 internal API(I-11 등)로 수행한다. (판매자가 **FE에서 직접** 상품을 편집하는 경로는 FE↔Spring 별개, AI 표면 밖.) 반영 결과는 `token`으로 안내.
- **[HARD] 대화 발화는 동의로 취급하지 않는다** — 채팅의 모호한 발화("응 바꿔")는 승인이 아니다. 반영의 유일한 경로는 **HITL 명시 승인**(아래).

#### 데이터 소스 계약 — [v0.4.0 해소, C-7 RESOLVED, Batch 1]

**[확정]** 판매자 통계 답변의 원천은 **Spring의 판매자 집계 API(I-6)를 질의 시점에 콜백**하는 것이다(§4.4). AI는 **원시 로그를 제공받지 않고 집계값만** 조회한다. 이로써 **C-7이 해소**되며, **구 결정 20 기본안(주문 미러의 `sellerId`·금액 확장)은 폐기**된다.

- **원천 = 집계 API 콜백**: AI가 JWT 클레임의 `brandId`로 `GET /internal/seller/{brandId}/sales`(매출 시계열) 등 집계 API를 호출해 집계값을 받고 LLM으로 자연어 답변한다. **[개정 v0.8.0]** 구 "sellerId만 넘기고 Spring이 내부 해소"에서 "brandId 클레임으로 `{brandId}` path 호출"로 변경(§2.6·§4.4). 판매자 집계는 단일 API가 아니라 **5종**(매출·퍼널·행동·이탈·계정, §4.4)이다.
- **주문 데이터 접근은 조회로 통일(C-6)**: 주문 미러는 존재하지 않는다(§3.6 삭제) — 추천 dedup(결정 14-F)·프로필(결정 16)은 **질의 시점 구매 이력 조회(§4.7)** 를 사용하고, 판매자 통계는 I-6 콜백(§4.4)을 사용한다.

> **MVP 비범위(명시)**: 리뷰 인사이트(측면별 감성)는 판매자 agent MVP에 **포함하지 않는다**(고도화). 본 엔드포인트는 판매 통계 Q&A + 상세 수정 draft만 다룬다.

### 3.3 상품 목록 경로 B (Product List — Path B)

**[HARD] 구매자 SSE 스트림은 상품 카드를 싣지 않는다.** 상품 목록은 아래 경로 B로 전달된다(후보는 질의 시점 Spring 검색 §4.6에서 확보):

```
[0] AI: decompose → Spring POST /products/search 위임 조회(§4.6) → 후보 목록(price 포함) → rerank(profile_summary)
[1] AI: rerank 완료 → listId 생성 → 최종 id 목록 push (AI → Spring, I-21 §4.2)
        POST {SPRING_BASE_URL}/internal/recommendations { sessionId, listId, productIds:[Top5 숫자] }
[2] Spring: productIds를 Redis에 listId 키로 TTL 저장 + 표시 필드(price·imageUrl·reviewCount 등) enrich
[3] AI: 콜백 성공 → SSE `products.ready`({ sessionId, listId }) emit (reason은 콜백에 포함돼 CH-5로 전달)
[4] FE: `products.ready` 수신 → 카드 GET (FE → Spring, CH-5 §4.3) → 우측 상품 패널 렌더
```

**설계 근거(rationale)**: 우측 상품 패널은 **Spring이 서빙하는 UI**다. **표시 권위는 Spring**에 남고, AI는 표시 필드(가격·이미지·리뷰수·재고)를 보유·전달하지 않는다(결정 9-B 유지·강화). AI가 전달하는 것은 **추천 로직의 산출물**(어떤 상품을, 어떤 순위로, 왜)뿐이다. §4.6 검색이 돌려주는 price는 **rerank·예산 검증(AI-side)용 질의 시점 값**이며, 우측 패널 표시가는 여전히 Spring이 목록 GET(§4.3)에서 채운다.

**push 실패 처리**: 목록 push(§4.2)가 실패하면 — 챗 텍스트는 정상 완료되고 지연 안내를 포함하며, 스트림은 `error`가 아니라 **`done`** 으로 종료하고 `products.ready`는 emit하지 않는다.

> **[point 조회 폐기]** 구 v0.2.0 "상품 point 조회 API"는 **완전 삭제**된다. 표시 필드는 소비자 point 조회가 아니라 **Spring이 목록 enrich 시점에 채운다**(§4.3). product.md 신규 결정 레코드 필요(§8 항목 1).

### 3.4 `GET /profile/me` — 마이페이지 프로필 조회 (FE 직접)

마이페이지 표시용으로 **토큰 소유자 본인의** 사람이 읽는 프로필 마크다운을 반환한다(자연어 마크다운 passthrough). 소유: `SPEC-PROFILE-001` §5.4/§6.9. MVP는 **조회(GET)만** 제공하며 편집(PUT)은 고도화 범위다.

> **[보안] 경로에서 `{userId}` 제거 — `GET /profile/me`**: `GET /profile/{userId}`는 **IDOR** 위험이 있어, 조회 대상 신원을 **토큰 클레임(`sub`)에서 도출**하는 `GET /profile/me`를 채택한다(결정 19).
> - **SPEC 동기화 필요 🔴**: `SPEC-PROFILE-001` §5.4/§6.9는 `GET /profile/{user_id}`로 정의되어 있으므로 `/me` 채택 및 camelCase 정렬(§2.2)에 맞춘 **동기화 개정**이 필요하다(§7).

#### 요청 (Request)

```
GET /profile/me
```

- 경로 파라미터 없음. 조회 대상은 `Authorization` 헤더 JWT의 `sub` 클레임에서 도출한다.
- 게스트 토큰(`role == "guest"`): 프로필이 없으므로 `exists = false` 정상 응답.

#### 응답 (Response) — `application/json` (camelCase 정렬 제안)

```json
{
  "userId": "string",
  "exists": true,
  "markdown": "# 취향 요약\n- 3~5만원대 무선 이어폰 선호\n...",
  "generatedAt": "2026-07-13T21:04:00Z"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `userId` | string | 요청 대상 식별자(토큰 `sub` 도출) |
| `exists` | bool | 프로필 존재 여부. 게스트·신규 회원 `false` |
| `markdown` | string \| null | 사람이 읽는 프로필 마크다운. 미존재 시 `null` |
| `generatedAt` | string \| null | 요약 생성 시각(ISO-8601). 미존재 시 `null` |

- 게스트/프로필 미보유: `exists = false`, `markdown = null`을 **오류가 아닌 정상 응답(200)** 으로 반환한다(SPEC-PROFILE-001 REQ-PROF-081).
- **PUT 미제공**: 프로필 편집은 고도화 범위(SPEC-PROFILE-001 EX-P3).

### 3.5 `POST {AI_SERVER}/events/session-end` (I-20) — 세션 종료 통지 (Spring → AI, best-effort, 멱등, 본 문서 소유)

Spring이 세션 종료(로그아웃·탭 종료·비활동 10분·새 대화 시작)를 감지해 프로필 파이프라인 **조기 트리거**로 전달한다(결정 12/16). HTTP 계약은 본 문서 소유(결정 21), 수신 동작은 `SPEC-PROFILE-001`.

> **[경로/방향 정합 v0.15.0]** BE DB 07/17에서 I-20 경로가 **`{AI_SERVER}/events/session-end`**로 등재됨 — 이는 **AI 서버가 호스팅하는 inbound 엔드포인트**(Spring→AI, checkpointer thread_id 리셋)임을 뜻한다. 우리 `app/api/events.py`가 수신 측으로 이미 배치돼 정합. AI가 Spring을 호출하는 역방향(§4)이 아니다.

#### 요청 (Request) — 제안(초안, camelCase 정렬)

```json
{
  "eventId": "sess-end-5521",
  "userId": "string",
  "sessionId": "string",
  "endedAt": "2026-07-14T09:40:00Z",
  "reason": "inactivityTimeout"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `eventId` | string | 멱등 키(§2.7) |
| `userId` | string | 세션 소유 회원 식별자 |
| `sessionId` | string | 종료된 세션 식별자 |
| `endedAt` | string | 종료 시각(ISO-8601) |
| `reason` | string | 종료 사유(`logout` \| `tabClose` \| `inactivityTimeout` \| `newConversation`) |

- **best-effort**: 통지 유실 시 다음 sleep-time 배치의 미처리 스레드 스캔이 회수(SPEC-PROFILE-001 REQ-PROF-050/051).
- **멱등**: 중복 수신도 델타·프로필 중복 처리 안 함(REQ-PROF-051).
- 응답: `202 Accepted`(제안). 필드·`reason` 값 집합은 🔴 협의(§5 C-8).

### 3.6 (삭제) 주문 이벤트 — 채택하지 않음 [v0.5.0]

**[v0.5.0 삭제]** 구 `POST /events/order`(주문 이벤트 미러)는 **채택하지 않는다**(2026-07-15 사용자 확정). 검색이 질의 시점 Spring 위임(§4.6)으로 확정되면서 구매 이력도 **추천 직전 질의 시점 조회(`GET /internal/members/{id}/orders`, §4.7)** 로 확보한다 — 알림 수신도, 미러 테이블도 없다. 결정 14-F의 동작 요구(exact `productId` 제외·소모품 카테고리 억제·되돌리기 칩)는 **불변**이며 데이터 획득 방식만 교체된다. 프로필 파이프라인의 구매 소스도 sleep-time 배치가 동일 API(§4.7)를 조회한다(SPEC-PROFILE-001 개정 필요, §7.2).

> **[v0.5.0] 카탈로그 동기화 채널 없음**: AI 카탈로그 사본(미러)을 채택하지 않으므로 카탈로그 변경 이벤트 채널도, 배치 폴링도 **존재하지 않는다**(2026-07-15 확정, §4.6 말미). Spring → AI 이벤트는 §3.5(`/events/session-end`) 하나만 남는다.

---

## 4. AI 서버 ↔ Spring 역방향/전제 계약

AI → Spring 질의 시점 역방향이 **7건**이다 — **후보 검색(`POST /products/search`, §4.6)**, **구매 이력 조회(`GET /internal/members/{id}/orders`, §4.7)**, 장바구니 담기(I-2, §4.1)·**조회(§4.9, v0.6.0 신설)**, 최종 목록 push(§4.2), 판매자 집계 조회(I-6, §4.4), 상세 읽기(I-7, §4.5). 여기에 FE ↔ Spring 전제 계약(목록 GET §4.3)이 더해진다. **v0.5.0에서 카탈로그 bulk export 배치 계약은 채택하지 않으므로 존재하지 않는다**(§4.6 말미). 아래는 **제안 계약(초안)** 이며, 실제 엔드포인트·인증·오류 코드는 🔴 Spring 팀 협의로 확정한다.

### 4.1 장바구니 담기 API (I-2, 결정 7) — BE 문서 채택 [v0.6.0]

**BE 팀 "챗봇 장바구니 담기"(No. I-2) 문서를 계약 기준으로 채택**한다. AI 서버는 "담아줘" 자연어에서 (상품, 옵션, 수량) 의도만 확정하고, 담기 실행·검증은 Spring에 위임한다(결정 7 유지). 구 v0.3.0 제안(JWT 포워딩 + `items[]` 다건)은 **폐기**한다.

#### AI → Spring 요청 (I-2 확정)

```
POST {SPRING_BASE_URL}/internal/cart/items
X-Internal-Token: {서비스 토큰}   ← internal 그룹, 타임아웃 권장 3s
```

```json
{ "userId": 123, "guestId": null, "productId": "1", "optionId": null, "quantity": 1 }
```

| 요청 필드 | 타입 | 설명 |
|---|---|---|
| `userId` / `guestId` | number / string \| null | **둘 중 하나** — 챗 요청의 메아리(`userId`=숫자, `guestId`=UUID 문자열, §2.6). AI가 신원을 만들지 않고 **AI-검증 JWT `sub`에서 도출**해 전달한다(FE 본문 값 사용 금지, §2.3) |
| `productId` | number | 담을 상품 식별자(숫자 BIGINT, §2.6) |
| `optionId` | number \| null | 상품 옵션. 옵션 필수 상품인데 null이면 `400 CART_OPTION_REQUIRED`(아래) |
| `quantity` | int | **1~99, 합산 포함** — 동일 상품·옵션이 이미 있으면 **Spring이 수량 합산**(입구가 달라도 같은 CartService 검증) |

- **단건 계약** — Case 3 묶음 담기는 상품별로 **반복 호출**한다(항목별 성공/실패가 자연 분리되므로 SSE `action`도 항목별 emit).
- **게스트 담기 허용** — `role == "guest"`여도 `guestId`로 담기 성공(BE 02 D30, 2026-07-10 개정 — 기존 403 유도 폐기). **로그인 유도는 결제 시점 FE 몫.** 구 AI-side 차단(`GUEST_NOT_ALLOWED`)은 폐기 — **결정 8 개정 필요(§8 항목 7)**.
- **합산 안내(v0.6.0)**: 담기 전 §4.9 조회로 동일 상품·옵션 기존 보유를 확인하면 "이미 담겨 있어 N개로 늘렸어요"처럼 안내할 수 있다. **합산의 권위는 Spring**(조회는 안내용 — 조회 실패 시에도 담기는 진행).
- **부수효과**: `CART_ADD(via: chat)` 이벤트는 BE가 적재(AI 무관).

#### 성공 응답 — 200

```json
{ "success": true, "data": { "cartItemId": 55 } }
```

`cartItemId`는 SSE `action`(`CART_ADDED`)에 사용한다(§3.1).

#### 실패 응답 — I-2 오류 코드와 AI 동작 매핑

| HTTP | I-2 `code` | 조건 | AI 동작 |
|---|---|---|---|
| 400 | `CART_OPTION_REQUIRED` | 옵션 필수인데 `optionId` 없음 — **응답에 options 목록 포함**(포함 형식 🔴 구현 시 확정) | **되물음 멀티턴**: 실패 `action` 없이 `token`으로 "어떤 색상으로 담을까요?" 재질문 → 다음 턴에서 사용자 답을 `optionId`로 해석해 재담기 |
| 400 | `CART_OPTION_INVALID` | 옵션이 해당 상품 소속 아님 | AI가 `optionId` 해석 오류 — options 목록 재확인 후 **되물음 재시도**(1회), 반복 실패 시 `action` `CART_ERROR` |
| 404 | `PRODUCT_NOT_FOUND` | 없는 상품 | `action` `CART_ADD_FAILED` + `reason: "PRODUCT_NOT_FOUND"` |
| 401 | `INTERNAL_TOKEN_INVALID` | 서비스 토큰 없음/불일치 | 운영 오류 — 사용자에게는 `action` `CART_ERROR`로 안내, 서버 로그/알림 |

> **🔴 잔여 협의(C-3)**: (1) **[07/17 갱신] 재고 실재화** — ERD v2에서 `product.stock_quantity`(시드 100) 추가로 품절이 실제 발생 가능 → I-2에 `OUT_OF_STOCK`(재고 부족) 오류 코드 추가를 BE에 요청(현재 I-2 문서에 부재). (2) `CART_OPTION_REQUIRED` 응답의 **options 목록 스키마**(optionId + 표시명 필수 — LLM 되물음 문구 생성용). (3) `productId` 타입(§2.6 경계 정책, C-5). (4) 서비스 토큰(`X-Internal-Token`) 발급·교환 방식.

### 4.2 추천 목록 전달 API (I-21 `POST /internal/recommendations`) — [BE DB 등재 v0.15.0, 스키마 🔴]

rerank 완료 후 AI가 **최종 랭크 상품 id(Top5)만** Spring에 POST한다. Spring이 Redis에 TTL 저장하고 표시 필드를 enrich하며, FE가 **CH-5**(§4.3)로 카드를 조회한다. **[07/17 BE 신설]** 합의된 추천 흐름 6번("FastAPI가 최종 추천 상품 ID만 Spring에 전달")의 실제 API.

#### AI → Spring 요청 (I-21)

```
POST {SPRING_BASE_URL}/internal/recommendations
X-Internal-Token: {서비스 토큰}   ← internal 그룹, 3s
```

```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "listId": "list-4471",
  "productIds": [101, 205, 552, 88, 13],
  "reasons": [
    { "productId": 101, "reason": "방수 등급이 높아 우천 시에도 안전합니다." },
    { "productId": 205, "reason": "가벼워 휴대가 편합니다." }
  ]
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `sessionId` | string(UUID) | 상관관계 키(`products.ready`와 상관) |
| `listId` | string | **FastAPI가 생성**해 넘기는 목록 식별자 — Spring이 Redis에 이 키로 TTL 저장, FE가 CH-5로 조회 |
| `productIds` | number[] | 최종 랭크 상품 id(Top5). **순서 유지 = 렌더 순서**(리랭킹 순서). 숫자 id(§2.6 internal) |
| `reasons` | array | **[역제안 v0.15.2] 상품별 추천 근거** `{productId(숫자), reason}` — productId로 키잉(순서 권위는 `productIds`). Spring이 Redis 저장 → **CH-5 카드에 `reason` echo**(§4.3). 구 BE 제안(reason=SSE)에 대한 역제안 🔴 |

- **[변경 07/17] payload = id 배열만** — 구 §4.2 `groups[{title,category,items[{productId,rank,reason}]}]` 구조는 **폐기**. 묶음 제목·순위·근거는 콜백에 싣지 않는다.
- **`listId`는 FastAPI 생성** — 구 "Spring이 listId 발급" 가정 폐기. **TTL = 10분(config, 세션 TTL 이하) 제안** — FE가 products.ready 직후 CH-5 조회하므로 짧아도 됨, 🔴 확정.
- **[역제안 v0.15.2] `reason`은 이 콜백에 포함** — `reasons[{productId, reason}]`를 Spring이 Redis 저장 → **CH-5 카드에 echo**(§4.3)해 FE에 전달. 구 BE 07/17 제안(reason=SSE·콜백 불포함)에 대한 역제안 — SSE(`products.ready`)는 상관키만 유지, 경로 B 일관·FE join 불필요. BE 확정 🔴.
- **규약**: FastAPI는 이 콜백이 **성공한 뒤에만** SSE `products.ready`({sessionId, listId})를 발행한다 — 콜백 실패 시 미발행(FE가 빈 목록 조회 방지, §3.3).
- **인증**: `X-Internal-Token` 서비스 토큰.
- 🔴 협의(C-9): `listId` 형식·TTL·재조회 정책, `reason` SSE 전달 최종 확정.

### 4.3 추천 목록/카드 조회 (CH-5 `GET /api/chat/lists/{listId}`, FE ↔ Spring 전제 계약) — [BE DB 등재 v0.15.0, 스키마 OPEN]

FE가 `products.ready` 수신 후 Spring에서 **표시 필드가 채워진** 추천 카드를 조회한다. **[07/17 BE 신설] 구 P-7을 대체**하는 CH-5. **이 계약은 FE ↔ Spring 간이며 AI 서버는 관여하지 않는다**(레인 d). AI는 I-21(§4.2)로 **id만** 넘기고, 카드 표시 필드는 Spring이 채운다.

#### FE → Spring 요청 (CH-5)

```
GET {SPRING_BASE_URL}/api/chat/lists/{listId}   ← BE DB(CH-5). listId = I-21에서 FastAPI가 넘긴 값
```

- Spring이 I-21로 받은 `productIds`를 자기 DB에서 enrich(name·price·image·reviewCount 등)해 **순서 유지 카드**로 반환.
- **표시 권위 = Spring**: `price`·`originalPrice`·`imageUrl`·`reviewCount`·`availability`를 Spring이 채운다(결정 9-B).
- **카드 응답 스키마는 FE ↔ Spring이 확정**(LLM 사안 아님). **[역제안 v0.15.2] 카드 항목에 `reason` 포함** — Spring이 I-21에서 받은 값을 echo(§4.2)해 FE는 카드+이유를 한 번에 받는다. 스키마 OPEN 🔴(§5 C-12).

### 4.4 판매자 집계 조회 API (I-6, query-time) — 🔴 제안(초안) [v0.4.0 신설, Batch 1]

판매자 통계 답변의 원천. AI가 질의 시점에 이 API를 호출해 **집계값만** 받는다(원시 로그 미제공). C-7 해소의 핵심 계약이다.

#### AI → Spring 요청 (제안)

**[개정 v0.8.0]** BE 문서 기준으로 판매자 집계는 **단일 API가 아니라 `brandId` 스코프 집계 5종**이다(전부 `internal`·`X-Internal-Token`·3s). `brandId`는 검증된 판매자 JWT 클레임에서 얻는다(§2.3·§2.6). **전체 5종 반영·I-number 정합은 #9로 진행** — 아래는 대표(매출 시계열).

```
GET {SPRING_BASE_URL}/internal/seller/{brandId}/sales?from={d}&to={d}&granularity={g}
X-Internal-Token: {서비스 토큰}
```

**판매자 조회/집계 7종 (BE 실제 No., 전부 GET·internal·`X-Internal-Token`)**:

| BE No. | 경로 | 내용 · 쿼리 | 소비 서브에이전트 |
|---|---|---|---|
| I-6 | `/internal/seller/{brandId}/sales` | 매출 시계열 · `from`/`to`(필수)·`granularity`(daily/weekly/monthly/summary). `series[{date,sales,orderCount,isAnomaly,deviationPct}]` | sales_anomaly·conversion·general·recommend·chart |
| I-7 | `/internal/seller/{brandId}/funnel` | 구매전환 퍼널 4단(view→cart→checkout→purchase) · `from`/`to` | conversion·behavior·chart |
| I-13 | `/internal/seller/{brandId}/events` | 행동 이벤트 집계(`behavior_events`) · `from`/`to`·`eventType`(product_view/add_to_cart/checkout_start/purchase_complete)·`productId`·`groupBy`(product/eventType/date). `rows[{productId,counts{4종},viewToCartRate,uniqueVisitors}]` — **LLM팀 본문 재작성 반영(v0.15.1)** | behavior·conversion |
| I-16 | `/internal/seller/{brandId}/churn` | 이탈 코호트 · `inactiveDays`. `churnRate`·`preChurnSignals` | churn |
| I-14 | `/internal/seller/{brandId}/order-events` | 주문 상태 전이/조회(`order_status_logs`) · `toStatus`(8종 복수)·`actorType`·`from`/`to`·`stats`·`groupBy` | sales_anomaly·conversion·churn·abuse·general |
| I-15 | `/internal/seller/{brandId}/product-changes` | 상품 변경 이력(`product_change_logs`) · `changeType`(PRICE/STOCK/STATUS)·`productId`·`from`/`to` | sales_anomaly·churn·recommend |
| I-8 | `/internal/account-events` | 계정/보안 이벤트 집계 **(전역·브랜드 스코프 아님, admin 소유 🔴)** · `eventType`·`from`/`to`·`groupBy` | abuse·churn |

- **`brandId` = JWT 클레임** — AI는 사용자 입력이 아니라 검증 토큰에서 얻어 `{brandId}` path에 쓴다(IDOR 방지, §2.6). 전역 I-8은 brandId 무관.
- **집계/이력값만** 반환(원시 로그 아님). AI는 LLM으로 자연어 답변.
- ⚠️ **혼동 주의**: BE I-15 `product-changes`(판매자 감사 로그)는 C-4 `products/changes`(AI 생성물 배치 pull, §4.8)와 **다르다**. BE I-14 `order-events`(판매자 주문 이벤트)는 C-6 `orders/recent`(구매자 이력, §4.7)와 **다르다**.
- 응답 스키마·전역 I-8 소유(admin)·I-number 정합은 🔴 협의(§5 C-13, #9).

### 4.5 판매자 상품 CRUD API (I-9/I-10/I-11/I-12) — [개정 v0.9.0, BE 문서 채택]

**[개정]** 구 §4.5(I-7 상세 읽기 + FE S-3 PATCH 반영)를 폐기하고, BE 판매자 상품 관리 4종을 채택한다. **AI(`product_agent`)가 Spring internal API로 읽기·쓰기를 직접 수행**하며, 쓰기는 §3.2 HITL 승인 게이트를 거친다. 전부 `internal`·`X-Internal-Token`·`{brandId}`(JWT 클레임)·3s.

| BE No. | Method · 경로 | 용도 | 비고 |
|---|---|---|---|
| I-9 | GET `/internal/seller/{brandId}/products` | 자사 상품 목록 조회 · `status`(ON_SALE/HIDDEN)·`q`·`limit`/`offset` | draft의 `before` 소스(구 I-7 상세 읽기 대체). `rows[{productId,name,price,originalPrice,stockQuantity,status,displayedSalesCount,category,description,imageUrl}]` |
| I-10 | POST `/internal/seller/{brandId}/products` | 상품 등록 · Body `name`·`price`(≤`originalPrice`)·`stockQuantity`(≥0) 필수 | 201 `{productId,status:"ON_SALE"}`. 신규 등록은 변경 이력 미기록 |
| I-11 | PATCH `/internal/seller/{brandId}/products/{productId}` | 상품 수정(가격·설명·상태·재고 통합) · Body 바꿀 필드만 | 재고도 이 API(별도 재고 API 없음). 변경 시 `product_change_logs`(PRICE/STOCK/STATUS) |
| I-12 | DELETE `/internal/seller/{brandId}/products/{productId}` | 상품 삭제(soft) · Body 없음 | **HITL 승인 후에만 실행**. 물리 삭제 없음 — `status=HIDDEN` 전환. 200 `{productId,status:"HIDDEN"}` |

- **쓰기는 `product_agent` 전용** — 쓰기 도구는 이 서브에이전트에만 배정(다른 서브에이전트는 읽기만).
- **소유권 검증은 Spring**: `brandId`(JWT 클레임 유래)로 판매자가 자기 상품만 다루도록 검증. AI는 신원을 요청 본문에서 받지 않는다.
- **status는 `ON_SALE`|`HIDDEN` 2종** (물리 삭제 없음).
- 정확한 응답 스키마·`categoryId`/`attributes` 스키마·HITL 이벤트 계약은 🔴 협의(§5 C-14, #9). "DB 논의 필요"(삭제 PDF)는 BE 내부 사안.

### 4.6 후보 검색 위임 API (I-1 `GET /internal/products/search`, query-time) — [BE 실측 정합 v0.13.0, 착수 전 최우선]

**[v0.5.0 — 가장 중요한 신규 Spring 계약]** 구매자 추천 후보를 **질의 시점에 Spring에 위임 검색**한다. AI는 사용자 원문을 decompose하여 구조화 필터를 만들고, 이 API로 Spring 카탈로그를 검색해 rerank·예산 검증에 필요한 후보를 돌려받는다. **검색 품질이 곧 추천 품질을 좌우**하므로 착수 전 최우선 협의 대상(C-15)이다. AI 카탈로그 사본·벡터 인덱스가 없으므로 **이 API가 유일·영구 후보 확보 경로**다.

#### AI → Spring 요청 (제안)

```
GET {SPRING_BASE_URL}/internal/products/search   ← BE 실측(I-1). 인증 서비스 토큰
X-Internal-Token: {서비스 토큰}
```

> **⚠️ GET vs POST — 우리 역제안 필요 🔴**: BE는 I-1을 **GET**으로 정의했으나, 우리 decompose 산출물은 **구조화 필터 + `excludeProductIds` 배열 + 키워드**로 복잡해 GET 쿼리스트링에 싣기 부적합하다(배열·중첩). **POST 바디 또는 GET+구조화 쿼리 규약**을 BE에 역제안한다. 아래 필드 집합은 전송 방식과 무관하게 유지. 신원(userId/guestId)은 서비스 토큰 레인이라 **쿼리/바디에 AI가 도출해 전달**(§2.6).

```json
{
  "category": "여행용품/보안용품",
  "priceMin": null,
  "priceMax": 50000,
  "brand": ["샘소나이트"],
  "ratingMin": 4.0,
  "keyword": "방수 파우치",
  "excludeProductIds": ["P-10001", "P-10044"],
  "sort": null,
  "limit": 30
}
```

| 요청 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `category` | string \| null | 아니오 | decompose가 추출한 카테고리 |
| `priceMin` / `priceMax` | int \| null | 아니오 | 예산·가격 필터. 질의 시점 값이므로 **가격 필터는 항상 최신(freshness)** |
| `brand` | string[] \| null | 아니오 | 브랜드 필터(다중 허용) |
| `ratingMin` | number \| null | 아니오 | 최소 평점 필터 |
| `keyword` | string \| null | 아니오 | 자유 키워드/쿼리 문자열(MVP는 Spring DB 텍스트 검색) |
| `excludeProductIds` | string[] | 아니오(기본 `[]`) | **dedup 정확 제외** — 최근 구매 등(결정 14-F). 게스트는 빈 배열 |
| `sort` | string \| null | 아니오 | 정렬 힌트(예: `priceAsc`·`ratingDesc`). 값 집합 🔴 |
| `limit` | int | 예 | 반환 상한(topK). 기본 30 제안 |

- **필드 집합 🔴**: 위 요청·아래 응답 필드 집합은 `~/projet/hk-final` 스캐폴드 구현과 정렬한 **제안(초안)** 이며 Spring 수용 전까지 🔴다(§5 C-15).
- **dedup = `excludeProductIds` 요청 파라미터(제안)**: 정확 제외 dedup은 **이 요청의 `excludeProductIds`로 전달**하는 것을 제안한다(스캐폴드 채택안). **대안**: Spring이 전체를 반환하고 AI가 사후 필터(post-filter)하는 방식도 가능하나, 후보 한도(`limit`) 안에서의 낭비를 줄이기 위해 **요청 파라미터 제외를 기본안**으로 한다. 최종 확정은 🔴(§5 C-15).

#### AI가 받는 응답 (제안)

```json
{
  "products": [
    {
      "productId": "P-10293",
      "name": "여행용 방수 파우치",
      "price": 47800,
      "listPrice": 52000,
      "stock": 12,
      "category": "여행용품/보안용품",
      "brand": "샘소나이트",
      "rating": 4.6,
      "mainImage": "https://..."
    }
  ],
  "totalCount": 37
}
```

| 응답 필드 | 타입 | 설명 |
|---|---|---|
| `products[]` | array | 후보 배열(rerank 입력) |
| `products[].productId` | number | 후보 식별자(숫자 BIGINT, §2.6) |
| `products[].name` | string | 상품명(rerank·근거 생성용) |
| `products[].price` | int | **질의 시점 가격** — 예산 검증(`verifiedSum`, §3.1 budget)·하드 제약(`priceMax`) 판정에 사용 |
| `products[].category` / `brand` / `rating` | string/string/number | rerank 신호·필터 검증용 |
| `products[].attributes` | object \| 없음 | (선택) 추가 속성. 구조 미확정 🔴(C-5) |
| `totalCount` | int | 필터 매칭 총 건수 — **완화 칩 `estCount`(COUNT, §3.1 suggestions) 산정용** |

- **🔴 필드 집합**: 위 후보 필드 집합(특히 rerank·예산 검증이 요구하는 `price`·`rating`·`brand`)은 🔴 확정 대상이다(§5 C-15).
- **freshness(신선도) — 트레이드오프 없음**: 질의 시점 검색이므로 **가격·재고 필터는 항상 최신**이다. v0.4.0 미러 배치의 "필터 경계 오류(stale price로 인한 오포함/오제외)" 트레이드오프는 **본 구조에서 소멸**한다. (표시가는 여전히 경로 B로 Spring이 목록 GET에서 채운다, §4.3.)
- **rerank는 AI-side**: 이 API는 후보만 반환하고, profile_summary 반영 rerank·근거 생성은 AI 경계에서 수행한다(하드 제약 유지).

#### [v0.5.0 확정] 채택하지 않는 것 (Not Adopted)

**[v0.5.1 정정] 채택하지 않는 것은 상품 원본 컬럼의 AI측 사본(미러)이다** — 가격·재고·상품명 등 필터/표시 컬럼을 AI DB에 복제하지 않으며, 카탈로그 소유는 Spring/MySQL 단일 원본이다. 반면 **AI 생성물(extras·search_doc·임베딩 벡터)은 AI Postgres에 저장·유지**한다(결정 3 Layer 2/3·결정 6 존속) — 갱신은 §4.8 pull 배치. 질의 시점 후보 확보에서 AI 임베딩과 §4.6 Spring 검색의 결합 방식은 §4.8 말미 OPEN.

### 4.7 구매 이력 조회 API (I-19 `GET /internal/members/{id}/orders`, query-time) — [BE 본문 재작성 v0.15.0]

구 주문 이벤트 미러(§3.6 삭제)를 대체한다. 추천 흐름이 **search 직전**(decompose와 병렬 가능)에 호출해 최근 구매를 확보하고, **결정 14-F 판단은 AI-side**에서 수행한다 — exact `productId` 제외 + 소모품 카테고리 억제 + 되돌리기 제안 칩(suggestions) 생성 → 제외 목록을 §4.6 `excludeProductIds`로 전달. 프로필 sleep-time 배치도 동일 API를 구매 소스로 조회한다. **게스트는 호출을 스킵**한다(이력 없음, 결정 8).

#### AI → Spring 요청 (I-19, BE 본문 재작성 07/17)

```
GET {SPRING_BASE_URL}/internal/members/{id}/orders?status={enum}   ← {id}=memberId(AI가 JWT sub 도출)
X-Internal-Token: {서비스 토큰}
```

- `status`(선택, **단일 값만** — 다중 미지원) enum: `ORDERED | SHIPPING | DELIVERED | CONFIRMED | CANCELLED | RETURNED`(아이템 상태, **교환 없음 — 07/17 제거**). 없으면 전체.

#### AI가 받는 응답 (I-19, BE 본문)

```json
{
  "success": true,
  "data": {
    "orders": [
      {
        "orderId": 1023,
        "orderedAt": "2026-07-10T14:23:00",
        "representativeStatus": "DELIVERED",
        "items": [
          { "orderItemId": 2001, "productId": 552, "productName": "무선 키보드", "optionName": "블랙", "quantity": 1, "price": 29000, "status": "DELIVERED" }
        ],
        "itemsTotal": 29000,
        "shippingFee": 0,
        "totalAmount": 29000
      }
    ]
  }
}
```

- **id는 전부 숫자(BIGINT), 필드 camelCase** — 다른 internal API(I-2·I-18)와 동일 규칙(§2.6).
- **`shippingFee`는 항상 0**(MVP 배송비 없음 확정) — `totalAmount` = 상품 스냅샷 합. 배송비 도입 시 재개정.
- `representativeStatus` = 대표 상태 파생 enum **8종**(`PENDING/PAYMENT_FAILED/CLAIM_IN_PROGRESS/CONFIRMED/COMPLETED/ORDERED/SHIPPING/DELIVERED`), 표시 문구는 FE 매핑(한국어 문자열 폐기, 07/17 FE 요청).
- **⚠️ [dedup 갭 — 미해소] items에 `category` 필드 없음** — 결정 14-F의 소모품 **카테고리 억제**는 I-19만으로 불가. **exact `productId` 제외**는 가능. 카테고리 dedup이 필요하면 별도 소스(I-1 검색 결과 category 또는 BE에 category 추가 요청) 필요 🔴.
- **지연 가드**: §4.6 검색과 병렬 호출 가능. 실패/타임아웃 시 **dedup 없이 추천 진행**(degrade).
- 실패: `400 ORDER_INVALID_PARAM`(status enum 위반) / `401 INTERNAL_TOKEN_INVALID` / `404 MEMBER_NOT_FOUND`.
- 경로·파라미터 수용은 🔴 협의(§5 C-6) — 07/17 BE 확인질문("이대로 가도 되는지").

### 4.8 AI 생성물 갱신 배치 (bulk export pull) — 🔴 제안(초안) [v0.5.1 신설]

AI Postgres의 **AI 생성물(extras·search_doc·임베딩 벡터, `productId` 키)** 을 상품 변경에 맞춰 갱신하는 배치. **AI가 요청하는 pull 방식**으로 확정 — Spring 주기 push는 기각(스케줄러·재시도·버퍼링 부담이 Spring으로 넘어가고, 유실 시 결국 pull 보정이 또 필요).

```
GET {SPRING_BASE_URL}/internal/products/changes?since={cursor}&limit={n}   ← BE 실측(I-17)
Authorization: 서비스 간 토큰(§2.3 b 계열) 🔴
```

#### AI가 받는 응답 (제안 — I-8 초안과 동일)

```json
{
  "items": [
    { "productId": "1", "status": "ACTIVE", "updatedAt": "2026-07-15T10:00:00Z", "name": "여행용 방수 파우치", "description": "…", "category": "여행용품", "attributes": { } }
  ],
  "nextCursor": "opaque-cursor-123",
  "hasMore": true
}
```

| 필드 | 설명 |
|---|---|
| `items[].status` | `ACTIVE` \| **`DELISTED`** — **판매 중지/삭제 상품 필수** — 없으면 AI 생성물(임베딩)이 유령 상품을 계속 추천 후보로 유지 |
| `items[]` 콘텐츠 필드 | enrichment·search_doc 조립 입력(name/description/category/attributes). **AI는 이 값을 저장하지 않고 산출물 생성에만 사용** |
| `nextCursor` | 다음 페이지 시작점(불투명 커서). AI가 저장했다가 다음 주기의 `since`로 사용 |
| `hasMore` | `true`면 같은 주기 안에서 `nextCursor`로 즉시 재요청(따라잡기), `false`면 이번 주기 종료 |

- **흐름**: AI 배치 잡이 주기적으로 변경분 조회(커서 기반, `hasMore` 루프로 페이지 소진) → `DELISTED`는 AI 생성물 삭제/비활성 → 나머지는 enrichment(Haiku, Layer 2 속성·상황 태그 추출) → `search_doc` 조립 → 임베딩(셀프호스트 한국어 모델, 결정 6) → AI Postgres upsert. **상품 원본 컬럼은 저장하지 않는다** — 산출물만 저장.
- **복구·초기 구축**: 실패 시 다음 주기에 동일 커서부터 재개(자연 회복). 초기 전체 구축도 커서 0부터 같은 API로 처리.
- **hk-final 매핑**: `app/pipelines/enrichment.py`·`embedding.py` 스텁을 활성화(“고도화 post-MVP” 주석은 “본 배치” 로 정정), torch는 embedding 의존성 그룹 설치.
- **[07/17 Q4 초안]** 커서 = "수정시각+id" **수용**(우리는 불투명 문자열로만 취급, 파싱 안 함). `attributes` = 카테고리별 자유 dict(현행 유지). **리뷰 텍스트는 MVP 제외**(search_doc 리뷰 결합은 고도화). 페이지 크기·주기는 config. **🔴 선결: 이 배치(enrichment·임베딩)의 MVP/post-MVP 스코프** — config.py(post-MVP) vs 파이프라인 주석(MVP) 모순 미해소.

> **[OPEN — 질의 시점 후보 흐름]** AI 임베딩과 §4.6 Spring 검색의 결합 방식은 미확정 — **두 방식 병행 검토**(2026-07-15 사용자): **방식 1** AI 벡터 검색으로 상위 N개 `productId` 확보 → Spring에 id 제약 조회로 가격·재고 필터+상세 획득(§4.6에 id 제약 변형 필요 여지 🔴) / **방식 2** Spring 검색이 후보 확보 → AI 임베딩은 시맨틱 재정렬·상황 매칭 보조. hk-final `SearchBackend` 인터페이스로 두 방식 모두 교체 가능하게 구현하고, 골든셋/실측으로 확정한다.

### 4.9 장바구니 조회 API (I-18 `GET /internal/cart`) — [BE 실측 정합 v0.13.0]

담기(I-2, §4.1)의 짝이 되는 **조회 계약**. 두 용도로 사용한다(2026-07-15 사용자 확정):

1. **장바구니 질의 응답** — "장바구니에 뭐 있어?" 발화 시 조회 후 `token` 텍스트로 답변한다(별도 SSE 이벤트 없음, §3.1).
2. **담기 시 기존 보유 안내** — 담기 전 동일 상품·옵션 보유를 확인해 "이미 담겨 있어 N개로 늘렸어요"류 안내를 생성한다. **수량 합산의 실행 권위는 Spring**(I-2가 합산 처리) — 조회는 안내용이며, **조회 실패 시에도 담기는 진행**한다(degrade).

#### AI → Spring 요청 (제안)

```
GET {SPRING_BASE_URL}/internal/cart?userId={id}   또는 ?guestId={id}
X-Internal-Token: {서비스 토큰}   ← I-2와 동일 인증 레인
```

#### AI가 받는 응답 (제안)

```json
{
  "success": true,
  "data": {
    "items": [
      { "cartItemId": 55, "productId": "1", "productName": "여행용 방수 파우치", "optionId": "OP-3", "optionName": "블루", "quantity": 2, "price": 12900 }
    ]
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `items[].cartItemId` | number | 장바구니 항목 식별자(I-2 응답과 동일 체계) |
| `items[].productId` | number | 상품 식별자(숫자 BIGINT, §2.6) |
| `items[].productName` / `optionName` | string | **챗 답변 문장 생성에 필수** — id만으로는 자연어 답변 불가 🔴 |
| `items[].optionId` | string \| null | 옵션 |
| `items[].quantity` | int | 현재 수량 |
| `items[].price` | number \| 없음 | 표시가(선택 — 총액 안내용, Spring 표시 권위 유지) |

- **빈 장바구니는 `items: []` 정상 200**(오류 아님). 실패 코드 제안: `400 CART_QUERY_INVALID`(userId/guestId 둘 다 없거나 둘 다 존재) / `401 INTERNAL_TOKEN_INVALID`(I-2와 동일).

> **🔴 협의(C-16)**: 경로·쿼리 파라미터(userId/guestId), 응답 필드(특히 `productName`/`optionName` 포함 여부), 페이징 필요 여부(MVP는 전량 반환 가정), I-2와 동일한 `X-Internal-Token` 인증 수용.

---

## 5. 협의 필요 항목 요약표 (🔴 Consolidated Open Items)

Spring/FE 팀과 확정이 필요한 항목을 통합한다. 각 항목은 본 문서에서 **제안(초안)** 또는 **확정안 반영(수용 전 🔴)** 으로 제시된다.

**[v0.6.0] 착수 전 필수(최우선)**: **C-15(`POST /products/search` 후보 검색 — 최우선, 유일 후보 경로)** · C-6(구매 이력 조회) · C-3(장바구니 담기 I-2 잔여) + C-16(장바구니 조회) · C-13(I-6 집계) · C-1 잔여(role 값·TTL).

| # | 항목 | 현재 상태 | 소유/근거 | 상태 |
|---|---|---|---|---|
| C-1 | **인증(auth)** | **확정**: RS256 + JWKS(Spring JWKS 노출, AI 로컬 검증·kid miss refetch), `401` 통일, `/seller/chat` role=seller(403)(§2.3). **[v0.10.0] SSE = 스트림 단명 티켓**(로그인 AT 아님, TTL 30~60s, CH-1 발급) — 클레임 `sub`+`sub_type`(member/guest)+`iss`+`aud`+`scope`+`exp`, 검증 signature/exp/iss/aud/scope. **[v0.8.0·확정] `brandId` = 판매자 티켓 클레임**(body 금지, `{brandId}` path용 — userId와 동일 IDOR 원칙, §2.6). **[v0.12.0] CH-1 티켓 발급 + 재발급(CH-1b)** — CH-1 응답에 `streamTicket` 추가, 세션 유지 티켓 재발급 경로 신설 필요(TTL 30~60s ≪ 세션 10분, §1.2 레인 d) | 결정 19 / JWKS 검토 후 제안 | 🔴 잔여 — **iss/aud/scope 값·TTL·판매자 티켓 클레임 형식·CH-1b 경로/응답(Spring 소유)** |
| C-2 | **스트림 전 오류 봉투** | **확정안 반영**: `error.{code,message,requestId}` + 상태 매핑(`400`/`401`/`403`/`429`)(§2.5) | 본 문서 제안 | 🔴 Spring 수용 전 |
| C-3 | **[v0.6.0 재작성] 장바구니 담기 API(I-2)** | **BE 문서 채택**: `POST /internal/cart/items` 단건 + `X-Internal-Token` + 본문 신원(JWT `sub` 유래) + `optionId` + quantity 1~99 합산 + **게스트 담기 허용**. 옵션 되물음 멀티턴(`CART_OPTION_REQUIRED` options 목록)(§4.1) | BE I-2 문서 / 결정 7 / 결정 8 개정(§8 항목 7) | 🔴 잔여 — **재고(OUT_OF_STOCK) 코드 부재·options 목록 스키마·서비스 토큰 발급** (productId 타입은 숫자 확정 v0.15.3) |
| C-4 | **[BE 실측 v0.13.0] AI 생성물 갱신 배치 = I-17** | `GET /internal/products/changes`(§4.8, BE DB "상품 정보 Batch") — AI 생성물(extras·search_doc·임베딩) 갱신 pull. 서비스 토큰. 상품 원본 사본 없음 | I-17 / BE DB | 🔴 커서·페이지·주기 |
| C-5 | **[개정 v0.15.4] `productId` 타입 & `attributes`** | **[BE 확정] 원본 id = 숫자(BIGINT)**. internal(I-1/2/19/21) = 숫자 그대로. **구매자 SSE는 productId 미탑재(경로 B)라 경계 변환 자체가 없음** — 구 "SSE/FE 문자열 정규화" 규칙 폐기(§2.6). `attributes` JSON 구조만 미확정 | 결정 9-B / BE ERD v2 | ✅ 코드 int 정렬 완료(v0.15.3) · 🔴 attributes 구조만 |
| C-6 | **[본문 확정 07/17] 구매 이력 = I-19** | `GET /internal/members/{id}/orders`(§4.7) 본문 재작성 — camelCase·숫자 id·`shippingFee` 0·`representativeStatus` 8종·item status 6종. **`category` 없음**(카테고리 dedup 불가, productId dedup만). dedup(14-F)·프로필 소스 공용, 게스트 스킵 | I-19 / BE 본문 | 🟢 대체로 확정(BE "이대로 가도 되는지" 확인) · 🔴 category 갭 |
| C-7 | **판매자 판매 데이터 소스** | **[해소]** 원천 = **Spring 집계 API(I-6) 질의 시점 콜백**(§3.2·§4.4). 구 기본안(주문 미러 sellerId·금액 확장) 폐기 | 결정 20 개정/Batch 1 | ✅ **해소** — 계약 세부는 C-13으로 이관 |
| C-8 | **세션 종료 통지 = I-20** | `POST /events/session-end`(§3.5, BE DB 등재) best-effort·멱등. MVP 유지 | I-20 / BE DB | 🔴 미확정 — 필드·`reason` 값 집합 |
| C-9 | **[BE 신설 07/17] 추천 push = I-21** | `POST /internal/recommendations` `{sessionId, listId, productIds[Top5 숫자]}`(§4.2). **listId=FastAPI 생성**, reason=SSE 직접, 콜백 성공 후 products.ready. 구 groups 구조 폐기 | I-21 / BE DB | 🔴 listId TTL·형식·reason SSE 최종(LLM 확정질문 2) |
| C-10 | **식별자 = 토큰 클레임** | **확정(숫자 사용자 id)**: 사용자/게스트/판매자 = 숫자 id, JWT `sub`에 문자열화. `role` enum 구분(§2.6). **양팀 통보 필요** | 결정 8/19 / 2026-07-14 세션 확정 | 🔴 미확정 — 클레임 키·id 타입 세부 |
| C-11 | **[v0.7.0 축소] CORS 허용 오리진** | 레이트 리밋은 **확정**(FastAPI 미들웨어 + in-memory, 분당 10/시간당 100 config, §2.8) — 협의 잔여는 **FE 허용 오리진 목록**뿐 | 결정 19 / v0.7.0 확정 | 🔴 잔여 — 허용 오리진(FE 통보) |
| C-12 | **[BE 신설 07/17] 카드 조회 = CH-5** | `GET /api/chat/lists/{listId}`(§4.3, 구 P-7 대체) — Spring이 표시 필드 enrich·서빙, FE↔Spring. AI 미관여 | CH-5 / BE DB | 🔴 카드 응답 스키마(FE↔Spring 소유, LLM 사안 아님) |
| C-13 | **[재정의 v0.8.0] 판매자 집계 API 5종** | BE 문서 채택 — `GET /internal/seller/{brandId}/{sales\|funnel\|events\|churn}` + 전역 `/internal/account-events`. `X-Internal-Token`, `brandId`=JWT 클레임(§2.6). 구 단일 `/seller/aggregates` 제안 폐기. 통계 답변 원천(§4.4) | BE 문서 / #9 | 🔴 **최우선** — 응답 스키마(**I-13은 LLM팀 재작성 반영 완료**, 나머지 4종 잔여)·전역 I-8 admin 소유·I-number 정합 |
| C-14 | **[재정의 v0.9.0] 판매자 상품 CRUD API 4종** | BE 문서 채택 — I-9 목록/I-10 등록/I-11 수정/I-12 삭제(soft,HITL). `internal`·`X-Internal-Token`·`{brandId}`. **AI 직접 쓰기**(구 "I-7 읽기 + FE S-3 PATCH" 폐기), 쓰기는 HITL 승인(§3.2·§4.5) | BE 문서 / #9 | 🔴 미확정 — 응답 스키마·attributes 스키마·HITL 이벤트 계약 |
| C-15 | **후보 검색 위임 = I-1** | BE 실측 `GET /internal/products/search`(§4.6, "상품 검색 LLM 콜백"), **서비스 토큰**. decompose 구조화 필터 + `excludeProductIds` + 키워드, 응답(`price`·`rating`·`brand`)·`totalCount`. **유일·영구 후보 경로** | I-1 / BE DB | 🔴 **최우선** — **GET vs POST 역제안**(복잡 필터)·응답 필드 |
| C-16 | **장바구니 조회 = I-18** | BE 실측 `GET /internal/cart`(§4.9, "챗봇 장바구니 조회"), 서비스 토큰. 질의 응답 + 담기 시 기존 보유·합산 안내. `productName`/`optionName` 포함 필요(챗 답변 생성용) | I-18 / BE DB | 🔴 응답 필드(`productName`/`optionName`) |

> 참고(v0.5.0): **C-15 신설**(후보 검색 — 유일 경로·최우선). **C-4 폐기**(카탈로그 동기화 자체 없음). **C-6 재정의**(주문 알림/미러 → 질의 시점 구매 이력 조회 §4.7). **C-7 해소**(I-6 콜백, 세부는 C-13). C-1/C-2/C-3은 확정안 반영이나 Spring 수용 전까지 🔴 잔여를 유지한다.
> 참고(v0.6.0): **C-3 재작성**(BE I-2 문서 채택 — 구 JWT 포워딩/`items[]` 제안 폐기, 게스트 담기 허용). **C-16 신설**(장바구니 조회).

### 5.1 07/17 BE 확인질문 — LLM팀 답변 필요 (Q1~Q9)

BE "API·ERD 변경 정리(07/17)" Part 2가 **우리(LLM팀)에게 확정을 요청**한 9건. `우리 답(제안)`은 대부분 **기존 계약과 일치**해 즉답 가능, 일부만 팀 결정 필요.

| Q | 질문 | 우리 답(제안) | 상태 |
|---|---|---|---|
| Q1 | 세션 종료 sessionId **UUID 수용**?(현재 I-20이 `S-` 형식 강제) | ✅ **예** — CH-1이 UUID 발급, `S-` 이유 없음. BE가 UUID 수용하도록 I-20 정규식 수정 | 즉답 |
| Q2 | **I-21** 스키마·`listId` TTL·`reason` 전달 | ✅ 스키마 수용. listId=우리 생성·TTL 10분(config). **[역제안] reason을 I-21 콜백에 포함→CH-5로 전달**(§4.2·§4.3, BE의 SSE안 대신) — BE 확정 대기 🔴 | 초안(역제안) |
| Q3 | **[적용]** = `{action:"confirm", draftId}` 확정? | ✅ **예** — §3.2 HITL 설계와 동일 | 즉답 |
| Q4 | **I-17** 커서·`attributes`·리뷰 텍스트 | ✅ 초안: 커서 "수정시각+id" 수용(불투명 취급)·attributes 자유 dict·리뷰 MVP 제외(§4.8). 🔴 선결: I-17 배치 MVP/post-MVP 스코프 | 초안 |
| Q5 | **I-13** 본문 재작성(I-5 내용 복붙이던 것) | ✅ **LLM팀이 직접 재작성해 Notion 반영**(§4.4 I-13, v0.15.1) — BE 검토만 | 해소 |
| Q6 | **CH-3**(CS 챗) 라우팅 | ✅ **관리자 CS 문의(CH-3·I-5·AD-1/2·M-9) 전부 post-MVP**. **주문상태 Q&A(I-4)는 구매자 챗(CH-2)에 흡수** — 별도 CS챗 없음 | 해소 |
| Q7 | 게스트 담기 실패 **3종·차단 없음** 최종? | ✅ **예** — `ActionData` 이미 3종(OUT_OF_STOCK/PRODUCT_NOT_FOUND/CART_ERROR), GUEST_NOT_ALLOWED 폐기(§3.1·§4.1) | 즉답 |
| Q8 | 판매자 챗 주소 `{AI_SERVER}/seller/chat`(별도) vs `/chat` 채널 | `{AI_SERVER}/seller/chat`(S-4, 별도 주소) 최종 — 채널 구분 아님(§3.2) | 즉답 |
| Q9 | 챗봇 담기 `add_to_cart` 이벤트 누가 쏘나 | I-2 문서상 **BE가 서버측 `CART_ADD(via:chat)` 적재**(§4.1) — FE 추가 발사 불필요 | 즉답 |

> **[v0.15.2 갱신]** Q5(I-13 재작성)·Q6(관리자 문의 MVP 제외)로 **해소**. Q2·Q4는 초안 반영 — 🔴 선결 2건: **Q2 reason 적재 형식(FE 조율)**, **Q4 I-17 배치 MVP/post-MVP 스코프**(config vs 파이프라인 모순). 나머지(Q1·Q3·Q7·Q8·Q9)는 확정 회신 가능.

---

## 6. 부록 (Appendix)

### 6.1 FE 소비 노트 — fetch 스트리밍 + 경로 B (FE 직접 호출)

표준 `EventSource` API는 **GET 전용**이므로, `POST /chat`·`POST /seller/chat`의 SSE 응답은 FE에서 **`fetch` + `ReadableStream`** 으로 소비한다. FE는 **AI 서버(FastAPI)를 직접 호출**하며 `Authorization` 헤더에 사용자 JWT를 실어 보낸다(§2.3 a).

**구매자(`/chat`) 흐름**

1. `fetch(url, { method: "POST", headers: { Authorization: "Bearer {USER_JWT}", "Content-Type": "application/json" }, body })` 후 `response.body.getReader()`로 스트림을 읽고 `data:` 라인을 직접 파싱한다.
2. 각 이벤트(`token`/`conditions`/`action`/`suggestions`/`budget`/`products.ready`/`done`/`error`)로 디스패치한다.
3. **`token` → 챗 렌더**, **`products.ready` → 우측 패널**(§4.3 Spring 목록 GET), **`action` → 담기 결과 토스트**, **`conditions` → 제거 가능 조건 칩**, **`done.finishReason == "zero_result"` → 빈 상태**.
4. **`401` 재발급 흐름**: 요청 시작 시 `401`(TOKEN_EXPIRED/TOKEN_INVALID)을 받으면 → Spring 토큰 재발급 → 새 JWT로 원 요청 **1회 재시도**(§2.5).

**판매자(`/seller/chat`) 흐름**

5. 이벤트는 `token`/`draft`/`done`/`error`만 디스패치한다.
6. **`draft` → diff 카드**: `changes[]`의 필드별 before/after를 diff 카드로 렌더하고 `[적용]`/`[취소]`를 노출한다. **`[적용]` 시 FE는 `{action:"confirm", draftId}`를 판매자 챗(S-4)으로 보내고, AI가 HITL resume으로 Spring internal API(I-11 등)를 호출해 반영**한다(§3.2). 채팅 발화만으로는 반영되지 않는다. (판매자가 챗봇이 아니라 **화면에서 직접** 상품을 고칠 때는 별도 경로 **S-5 `PATCH /api/seller/products/{id}`**(FE→Spring, AI 표면 밖, 07/17 신설)를 쓴다 — 챗봇 수정(I-11)과 병존.)

- **버퍼링 주의**: FE 직접 호출이므로 Spring 중계 버퍼링 이슈는 없다. 남는 주의점은 **FastAPI 앞단 리버스 프록시**의 응답 버퍼링 비활성화뿐이다(§2.4).
- **[v0.7.0] 취소·동시 스트림**: 사용자가 응답 중단 시 `AbortController.abort()`로 연결을 끊는다(별도 취소 API 없음, §2.9 b). 스트리밍 중에는 입력창을 비활성화한다 — 중복 전송 시 서버가 `409 STREAM_IN_PROGRESS`를 반환한다(§2.9 a). `504 UPSTREAM_TIMEOUT`(스트림 전)·in-stream `error`(스트림 중) 구분은 §2.9 c.

### 6.2 버전 관리 / 변경 이력 규약

본 문서는 **semver 유사(major.minor.patch)** 로 버전을 매긴다.

- **major**: 하위 호환을 깨는 계약 변경(필드 제거·의미 변경·엔드포인트 삭제).
- **minor**: 하위 호환 유지 추가(신규 엔드포인트·선택 필드·🔴 항목 확정).
- **patch**: 오탈자·설명 보강.
- 소유 SPEC(`SPEC-RECOMMEND-001`, `SPEC-PROFILE-001`)의 계약이 개정되면 본 문서를 동기화하고 변경 이력을 남긴다. `/events/*` HTTP 계약은 본 문서가 소유하므로(결정 21), 그 개정은 본 문서 버전 증가로 반영한다.

#### 변경 이력 (Change Log)

| 버전 | 날짜 | 변경 |
|---|---|---|
| v0.15.4 | 2026-07-18 | **v0.15.3 stale 참조 정리(팀원 PRD/SPEC-CART 검토 중 발견).** (1) 필드표 `productId`/`optionId` `string`→`number`, `guestId`=UUID 문자열 명시(§4.1·§4.5·§4.6·§4.7). (2) **C-5** 'SSE/FE 경계 문자열 정규화' 서술 폐기 — 경로 B, 코드 int 정렬 완료(v0.15.3). (3) **I-19 구매이력 경로 통일**: `GET /orders/recent` → `GET /internal/members/{id}/orders`(§4.7 BE 실측). (4) **CLAUDE.md(정본·미러)** 버전 v0.7.0→v0.15.4, 인증레인 정정(전 internal `X-Internal-Token`), cart 조회 I-9→I-18. 계약 변경 없음. |
| v0.15.3 | 2026-07-18 | **productId·id 타입 = DB 스키마 기준 BIGINT 확정(사용자).** §2.6 개정: 상품/옵션/장바구니/주문 id = **숫자(BIGINT)**, **게스트 id만 UUID 문자열**(guest.id CHAR(36)). 구 "경계별 문자열 정규화·전 구간 문자열" 규칙 폐기. **CLAUDE.md(정본·hk-final) "productId 전 구간 string" 규칙도 개정.** 코드 반영: `schemas/spring.py`·`chat.py`의 상품/옵션/장바구니/주문 id → `int`, `guest_id`·`get_cart` → `str`(UUID), dedup 비교 타입 정합(SpringProduct·excludeProductIds → int). BE I-17 예시가 문자열 productId를 보이나 DDL은 BIGINT — 스키마 기준 int(BE 표기 불일치 통보 대상). (Claude PR 리뷰가 CLAUDE.md 불일치를 지적해 해소.) |
| v0.15.2 | 2026-07-18 | **BE 확인질문 Q2·Q4·Q6 초안 반영(결정 대기).** (1) **Q6 해소**: 관리자 CS 문의(CH-3·I-5·AD-1/2·M-9) 전부 **post-MVP**. **주문상태 Q&A(I-4)는 구매자 챗(CH-2)에 흡수** — 별도 CS챗 없음(§1.2 레인 c·§3.1). (2) **Q2 초안(역제안)**: 추천 `reason`을 **I-21 콜백에 포함**(`reasons[{productId, reason}]`)해 Spring이 CH-5 카드에 echo(§4.2·§4.3) — BE 07/17 제안(reason=SSE)에 대한 역제안. 경로 B 일관·FE join 불필요, SSE(`products.ready`)는 상관키만. `listId` TTL 10분(config). BE 확정 🔴. (3) **Q4 초안**(§4.8): 커서 "수정시각+id" 수용(불투명 취급)·`attributes` 자유 dict·리뷰 텍스트 MVP 제외. 🔴 선결: I-17 배치 MVP/post-MVP 스코프(config vs 파이프라인 모순). §5.1 Q2/Q4/Q6 갱신. |
| v0.15.1 | 2026-07-18 | **I-13 행동 이벤트 조회/집계 본문 확정 — LLM팀 재작성 + BE Notion 반영.** BE 확인질문 Q5 해소: I-13(`GET /internal/seller/{brandId}/events`) 페이지에 I-5(문의 접수) 내용이 복붙돼 있던 것을 LLM팀이 `behavior_events` 기반 스펙으로 재작성해 **Notion 페이지에 직접 반영**. 요청(`from`/`to`·`eventType` 4종·`productId`·`groupBy`) + 응답 3형태(product/eventType/date, `counts`·`viewToCartRate`·`uniqueVisitors`) + 집계 규칙(판매자 스코프=`product→brand.seller_id`, camelCase, `client_event_id` 중복 배제, purchaseComplete는 행동 맥락용·매출 권위는 I-6/I-14). §4.4 I-13 행·§5.1 Q5·C-13 갱신. 판매자 집계 나머지 4종 응답 스키마는 C-13 잔여. |
| v0.15.0 | 2026-07-17 | **BE 07/17 API·ERD 개정 반영(확정분) — Notion "📡 API 명세서" 실측 + "API·ERD 변경 정리(07/17)".** (1) **I-21 `POST /internal/recommendations` 신설**(§4.2 재작성, C-9 확정) — 추천 목록 push가 `{sessionId, listId, productIds[Top5]}`로 확정. **`listId`는 FastAPI 생성**(구 Spring 생성 가정 폐기), `reason`은 SSE 직접(콜백 불포함), **콜백 성공 후에만 `products.ready`**. 구 groups/items/reason 구조 폐기. (2) **CH-5 `GET /api/chat/lists/{listId}` 신설**(§4.3 재작성, C-12) — 추천 카드 조회(구 P-7 대체), FE↔Spring, 카드 스키마 FE/LLM OPEN. (3) **I-19 본문 재작성**(§4.7) — camelCase·숫자 id·`shippingFee` 항상 0·`representativeStatus` enum 8종·item `status` 6종(교환 제거)·**`category` 필드 없음**(dedup은 `productId` 기준). (4) **CH-2 경로 = `{AI_SERVER}/chat`**(오타 수정), **I-20 = `{AI_SERVER}/events/session-end`**(Spring→AI inbound — 우리가 호스팅하는 엔드포인트임을 명확화). (5) **[BE DB] `productId` = 숫자(BIGINT) 확정** — internal(AI↔Spring) 계약은 숫자, SSE/FE 경계서 문자열 정규화(§2.6 개정). (6) **[BE DB/ERD] `product` +`stock_quantity`(시드 100)** — I-2 `OUT_OF_STOCK` 실재화(§4.1), `user_event`→`behavior_events`(+guest_id +client_event_id), `order_status_logs`·`product_change_logs`·`account_event_logs` 신설, **배송비 0원·교환 제거(주문상태 11→9종)**. (7) 신규 FE/Spring 엔드포인트 **E-1**(`POST /api/events` 행동 수집)·**CH-6**(`POST /api/chat/seller/sessions`)·**S-5**(`PATCH /api/seller/products/{id}` 판매자 직접 수정 — 챗봇 I-11과 병존) 등재. (8) **LLM팀 확인질문 9개**(§5 말미 표) — 세션 UUID 수용·I-21 확정·[적용] 형식·I-17 커서·I-13 재작성·CS챗 라우팅·게스트 담기·판매자챗 주소·담기 이벤트. |
| v0.14.0 | 2026-07-16 | **구매 이력 = I-19, 세션 종료 = I-20 — BE DB No. 채번 확정(사용자 승인 후 Notion 수정).** (1) **구매 이력 조회 = I-19 `GET /internal/members/{id}/orders`**(BE DB "구매 이력 목록" 행에 No.·그룹·Method·경로 채움) — §4.7·C-6·레인 c 정합, 구 `/orders/recent` 제안 폐기. `{id}`=userId(AI 도출), 서비스 토큰. I-4(주문 상태 요약)와 별개. (2) **세션 종료 = I-20 `POST /events/session-end`**(BE DB 행에 No. 채움) — §3.5·C-8 정합. Notion BE DB 실제 수정(사용자 승인). |
| v0.13.0 | 2026-07-16 | **BE API 명세 DB(Notion) 실측 정합 — 인증 레인 서비스 토큰 통일 + 실제 I-number/경로 — 사용자 확정.** (1) **[BREAKING] AI→Spring 역호출 전 구간 `X-Internal-Token` 서비스 토큰 + 본문/쿼리 신원**(AI가 JWT `sub` 도출)으로 통일 — 구 "사용자/판매자 JWT 포워딩"(후보 검색·구매 이력) 폐기. BE `internal` 그룹이 전부 서비스 토큰이라 정합(IDOR는 AI 도출 신원으로 유지). (2) **실제 BE 번호/경로 반영**: 후보 검색 = **I-1 `GET /internal/products/search`**(구 `POST /products/search`), 생성물 배치 = **I-17 `GET /internal/products/changes`**, 장바구니 조회 = **I-18 `GET /internal/cart`**. (3) **구매자 챗 경로 = `POST /ai/chat`**(구 `/chat`, BE DB 실측·인증 필요). (4) **S-3 정정**: `GET /api/seller/products`(SELLER·FE용) ∥ I-9(internal·AI용) **별개** — 구 "S-3=I-9" 오기 정정. (5) **후보 검색 GET vs POST 역제안 🔴**: 복잡 필터(배열·중첩)라 POST 바디 역제안. (6) **C-6 구매 이력**: BE DB 미등재 → AI팀 신규 요청 필요(I-4는 주문 상태 요약). C-4/C-15/C-16 = I-17/I-1/I-18로 확정. |
| v0.12.0 | 2026-07-16 | **CH-1 스트림 티켓 발급 + 재발급 경로 명시 — 사용자 확정.** 스트림 티켓 발급을 전제 계약(§1.2 레인 d)에 구체화: (1) **CH-1**(`POST /api/chat/sessions`) 응답에 `sessionId`(10분 sliding) + **첫 `streamTicket`**(RS256, TTL 30~60s) 반환. 신원은 회원 AT/게스트 쿠키로 확인(`sub_type`). (2) **[중요] 티켓 재발급 경로 신설 필요(CH-1b `POST /api/chat/tickets` 제안)** — 티켓 TTL(30~60s) ≪ 세션 TTL(10분)이라 CH-1 1회로는 첫 스트림만 커버, 2번째 메시지부터는 세션 유지한 채 티켓만 재발급해야 하며 **CH-1 재호출은 새 세션(맥락 단절)이라 못 씀**. (3) `401` 재발급 흐름을 CH-1b로 명확화. (4) 레인 d에서 구 "draft 적용=FE S-3 PATCH" 제거(v0.11.0 정합). CH-1/CH-1b는 Spring 소유 — 경로·응답 🔴 C-1. |
| v0.11.0 | 2026-07-16 | **판매자 쓰기 모델·HITL 계약 확정(쟁점 B) — 사용자 확정.** (1) **채팅 경로 쓰기 = AI가 internal API(I-10/11/12) 직접 수행 + HITL 승인 게이트**(v0.9.0 확정). 판매자가 FE에서 직접 편집하는 경로는 FE↔Spring 별개(AI 표면 밖). (2) **`S-3` = 자사 상품 목록 조회(=I-9, 읽기)** 로 명확화 — 구 S-4 문서의 "S-3 PATCH"는 오표기, brandId=JWT 클레임(userId와 동일 IDOR 원칙, 쟁점 A 확정). (3) **HITL 2-스트림 계약**: 스트림1 `draft{draftId,op,changes}` → LangGraph interrupt → done / 스트림2 `confirm{draftId}` → resume → I-11 등 실행 → done. (4) **HITL 안전장치 5종**: draftId 바인딩·명시 액션만·멱등성·Spring 소유권 하드게이트·대기 TTL. 삭제 필수 HITL + soft delete(HIDDEN). (5) `draft` 이벤트에 `draftId`·`op` 추가. 승인 이벤트명·confirm 형식은 🔴 판매자 SPEC. |
| v0.10.0 | 2026-07-16 | **SSE 인증 = 스트림 단명 티켓("JWKS 검토 후 제안" 채택) — 사용자 확정.** SSE에 로그인 AT를 직접 싣지 않고, Spring이 채팅 진입 시 신원 확인 후 **스트림 단위 단명 JWT(RS256, TTL 30~60초)** 를 발급(CH-1에 얹음). 게스트는 `guest_id` 쿠키로 동일 발급(`sub_type: guest`). 클레임 재편: `sub`+`sub_type`(member/guest)+`iss`(jarvis-spring-auth)+`aud`(jarvis-fastapi-ai)+`scope`(chat:stream)+`exp`. 검증에 **`aud`·`scope` 추가**(토큰 혼용 방지). JWKS는 `kid` miss 시 refetch. 판매자 티켓의 role/brandId 표현은 🔴 확인. §2.3·§2.5·C-1 개정. (JWKS 코어(RS256/kid/엔드포인트)는 기존 반영분 유지.) |
| v0.9.0 | 2026-07-16 | **판매자 BE internal API 배치 전면 반영(11종 PDF) — 사용자 확정.** (1) **판매자 조회/집계 7종**(§4.4): I-6 sales·I-7 funnel·I-13 events·I-16 churn·I-14 order-events·I-15 product-changes(brandId path) + I-8 account-events(전역). (2) **판매자 상품 CRUD 4종**(§4.5, C-14 재정의): I-9 목록·I-10 등록·I-11 수정·I-12 삭제(soft=HIDDEN). 구 "I-7 상세 읽기 + FE S-3 PATCH" **폐기**. (3) **[BREAKING] 판매자 쓰기 모델 전환**(§3.2): "FE가 본인 JWT로 S-3 PATCH 반영(AI 표면 밖)" → **"AI(`product_agent`)가 Spring internal API로 직접 쓰기 + 파괴적 작업은 HITL interrupt/resume 승인 게이트"**. "대화 발화 ≠ 동의" 원칙은 유지(HITL로 구현). soft delete(status=HIDDEN). (4) 전부 `internal`·`X-Internal-Token`·`{brandId}`(JWT 클레임). (5) **혼동 주의**: BE I-15 product-changes ≠ C-4 products/changes(생성물 배치), BE I-14 order-events ≠ C-6 orders/recent(구매자 이력). (6) 판매자 서브에이전트 다수화(sales_anomaly·conversion·behavior·churn·abuse·general·recommend·chart·product). **결정 20 개정 필요**(§8 항목 8). 응답 스키마·I-number 정합·HITL 이벤트는 #9. |
| v0.8.0 | 2026-07-16 | **판매자 `brandId` = JWT 클레임 + 집계 API 5종(BE 문서) — 사용자 확정.** (1) **§2.3 클레임에 `brandId` 추가**(role=seller 시 필수). (2) **§2.6 `brandId 미보유` 원칙 개정** — "AI는 brandId를 알지 못한다(Spring 내부 해소)"에서 **"brandId를 요청 본문에서 받지 않는다 — 검증된 판매자 JWT 클레임에서만 획득"** 으로 완화(IDOR 방지 취지 유지, RS256 위조 불가). BE 집계 API가 `{brandId}` path를 요구함에 따른 정합. (3) **§4.4 재정의** — 판매자 집계는 단일 `/seller/aggregates`(폐기)가 아니라 **brandId 스코프 집계 5종**: I-6 `sales`·I-7 `funnel`·I-13 `events`·I-16 `churn`(brandId path) + I-8 `account-events`(전역·admin). 전부 `internal`·`X-Internal-Token`. (4) **C-13 재정의**(5종), **C-1**에 brandId 클레임 발급 협의 추가. (5) **BE I-number ≠ 기존 임의 I-number**(BE I-7=funnel vs 기존 I-7=상세) — 정합은 #9. 상품 CRUD·주문 PDF 반영은 후속. |
| v0.7.0 | 2026-07-15 | **스트림 운영 규약 신설 — 사용자 확정(7개 항목).** (1) **§2.9 신설**: 동시 스트림 세션당 1개(`409 STREAM_IN_PROGRESS`, 기존 스트림 유지 — 409 거절안 채택), 취소 = 연결 종료(FE `AbortController` → disconnect 감지 → **LLM 스트림 즉시 close**·LangGraph task 취소), 타임아웃 기준표(first-token 10s / 스트림 상한 90s / AI→Spring 3s 통일 / LLM 30s+1재시도 — config 기본값, 계약은 초과 시 동작). (2) **§2.5 확장**: `409`·`504 UPSTREAM_TIMEOUT` 추가 — 스트림 전 오류 통합표化. (3) **§2.8 레이트 리밋 확정**: 목적 = 무분별 남용 차단, FastAPI 미들웨어 + in-memory(다중 인스턴스 시 Redis 이관 단서), 분당 10/시간당 100(config), **C-11 축소**(잔여 = 허용 오리진만). (4) **§6.3 신설(운영 요구)**: 대화 저장(수신 즉시 user 저장 / 완료 후 assistant 저장 / `COMPLETED`·`FAILED`·`CANCELLED`, 부분 텍스트 보존), 로그 필드(requestId·userId·conversationId·first-token/total latency 분리·model·tokens·errorType, message 원문 로깅 금지). FE 히스토리 복원 API는 미결로 등재. |
| v0.6.0 | 2026-07-15 | **[BREAKING] 장바구니 계약 BE I-2 문서 채택 + 조회 신설 — 사용자 확정.** (1) **§4.1 재작성**: `POST /internal/cart/items` 단건 + `X-Internal-Token` 서비스 토큰 + 본문 신원(AI-검증 JWT `sub` 유래) — 구 v0.3.0 제안(사용자 JWT 포워딩·`items[]` 다건) **폐기**, 묶음은 반복 호출. (2) **게스트 담기 허용**(BE 02 D30) — AI-side 차단·`GUEST_NOT_ALLOWED` 폐기, 로그인 유도는 결제 시점 FE 몫. **결정 8 개정 필요(§8 항목 7)**. (3) **옵션 되물음 멀티턴**: `400 CART_OPTION_REQUIRED`(options 목록 포함) → 실패 `action` 없이 `token` 재질문 → `optionId` 해석 후 재담기; `CART_OPTION_INVALID`는 1회 재시도 후 `CART_ERROR`. (4) **`action.reason` 재편**: `PRODUCT_NOT_FOUND`/`CART_ERROR`/`OUT_OF_STOCK`(I-2에 재고 코드 부재 — 🔴 협의). (5) **장바구니 조회 신설(§4.9, C-16)**: `GET /internal/cart` — 장바구니 질의 응답(`token` 텍스트) + 담기 시 기존 보유·수량 합산 안내(합산 권위는 Spring, 조회 실패 시 담기 진행). (6) 레인 (c) 6건→7건. C-3 재작성. |
| v0.5.1 | 2026-07-15 | **[정정] AI 생성물 저장 존속 + pull 배치 부활 — 용어 오해 정정.** v0.5.0의 "enrichment/임베딩 채택 안 함"은 오독이었음 — 채택하지 않는 것은 **상품 원본 컬럼의 AI측 사본**뿐. **AI 생성물(extras·search_doc·임베딩 벡터)은 AI Postgres에 저장·유지**(결정 3 Layer 2/3·결정 6 존속), 갱신은 **pull 배치**(`GET /products/changes?since={cursor}`, §4.8 신설, **C-4 부활**; Spring 주기 push 기각). **질의 시점 후보 흐름은 OPEN**(§4.8 말미) — 방식 1(AI 벡터 → Spring id 제약 조회) vs 방식 2(Spring 검색 → 임베딩 보조) 병행 검토, hk-final `SearchBackend`로 교체 가능 구현 후 골든셋/실측 확정. §8 항목 4 정정(결정 3/6 효력 유지). |
| v0.5.0 | 2026-07-15 | **[BREAKING] 검색 위임 영구 확정 + 주문 알림 폐기 — 사용자 최종 확정.** (1) **후보 검색 = 질의 시점 Spring 위임(`POST /products/search`, §4.6, C-15 신설)** 을 유일·영구 경로로 확정 — AI 카탈로그 사본(미러)·pgvector 카탈로그 벡터 검색·enrichment/임베딩·bulk export 배치(이원 주기)는 **채택하지 않음**(고도화 유예 아님, C-4 폐기). 구현 기준 = `~/projet/hk-final`(jarvis-ai) 스캐폴드. (2) **주문 알림(구 `POST /events/order`)·주문 미러 폐기** → **질의 시점 구매 이력 조회(`GET /internal/members/{id}/orders`, §4.7, C-6 재정의)** — dedup(14-F 동작 불변: exact 제외·소모품 억제·되돌리기 칩)·프로필 구매 소스 공용, 게스트 스킵, 실패 시 dedup 없이 degrade. (3) Spring → AI 이벤트는 **`/events/session-end` 1종만** MVP 유지(병행 PRD 라인과의 유일한 차이 — PRD 정정 필요, §8 항목 6). (4) §1.2 레인 재편 — AI→Spring 질의 시점 6건. 가격 신선도 트레이드오프 소멸(질의 시점 검색·조회). SPEC 후속: RECOMMEND-001 검색 tool·CATALOG-DATA-001 재범위·PROFILE-001 구매 소스(§7). |
| v0.4.0 | 2026-07-15 | **판매자 확대(Batch 1) + 카탈로그 배치 전환(Batch 2), 2026-07-15 사용자 확정.** **(Batch 1)** `POST /seller/chat` 범위 확대 — 통계 Q&A **+ 상세 수정 draft 흐름**(§3.2). 판매자 SSE = `token`/`draft`/`done`/`error`만, `finishReason`=`stop` 단일. 통계 원천 = **Spring 집계 I-6 질의 시점 콜백**(§4.4) → **C-7 해소**, 구 결정 20 기본안(주문 미러 sellerId·금액 확장) **폐기**. draft = **I-7 상세 읽기**(§4.5) → LLM 개정안 → SSE `draft`{productId(string), changes:[{field,before,after}]} → FE diff 카드 → FE가 Spring **S-3 PATCH**로 반영(FE↔Spring 전제, AI 표면 밖). 대화 발화는 동의 아님. `brandId`는 AI 미보유(Spring이 sellerId→brandId 해소). 신규 역호출 I-6/I-7 인증 = 판매자 JWT 포워딩 제안(🔴). §1.2 레인 갱신(AI→Spring 질의 시점 = 장바구니·목록 push·I-6·I-7). **(Batch 2)** `POST /events/catalog` **완전 폐기** → **`GET /products/changes?since={cursor}` bulk export 배치 폴링**(§4.6, 제안 🔴). **이원 주기**(가격·재고 짧은 주기 미러 UPDATE / 콘텐츠 긴 주기 재임베딩, contentHash 비교) — 결정 9-A 경량/전체 분기를 이벤트→배치로 이식. 배치가 곧 동기화(일 1회 보정이 백업 아님). 신선도 트레이드오프(필터 경계 오류) 수용 명시. **`/events/order`·`/events/session-end`는 MVP 유지**(이벤트 폐기는 카탈로그 한정). **(C-table)** C-4 재정의(bulk export 🔴), C-6 4필드 유지 확정, C-7 해소(I-6), C-13 I-6 신설(🔴), C-14 I-7 신설(🔴), 나머지 v0.3.0 유지. **[provenance]** 본 버전은 **미비준 병렬 초안**(no-mirror + 질의 시점 `POST /products/search` + `/events/*` 고도화 유예 + 판매자 AI DB 시드; 가칭 결정 22/23/24)을 **폐기·대체**한다 — 비준 노선은 **미러 + 배치 동기화**(본 세션). SPEC 동기화 개정 목록은 §7. |
| v0.3.0 | 2026-07-15 | **[BREAKING] FE/BE 팀 챗 API 문서("추천 챗봇 CH-2")를 명명 기준으로 채택 + 상품 목록 경로 B 도입.** (1) SSE 이벤트 집합 재편(`text.delta`→`token`, `products` 카드 삭제, `conditions`/`action`/`products.ready` 신설, `done.finishReason`=`stop`/`zero_result`, in-stream `error` 4종). 전 페이로드 **camelCase**. (2) 경로 B: SSE 상품 카드 제거, AI→Spring 목록 push(C-9) + FE←Spring 목록 GET(C-12), point 조회 삭제. (3) 인증 확정(RS256+JWKS, `401` 통일, `role`, `CHAT_SESSION_EXPIRED` 폐기). (4) `productId` 문자열 전면 통일(숫자 예시와 상충 → 양팀 통보). (5) 장바구니 `action` + JWT 포워딩 + 실패 4종. (6) suggestions/relaxationNotice/budget SSE 측 탑재. SPEC-RECOMMEND-001 §5.3 / SPEC-PROFILE-001 §5.4 동기화 개정 필요(§7). |
| v0.2.0 | 2026-07-14 | [BREAKING] FE 직접 호출 아키텍처 반영. 사용자 대면 API를 FE → AI 직접 호출로 전환. 요청 본문에서 `user_id`·`seller_id` 제거 — 토큰 클레임 추출. 인증 2종 분리, 401 만료 재발급·SSE 연결 시점 인증, CORS·레이트 리밋 신설, `GET /profile/{user_id}` → `GET /profile/me` IDOR 방지. |
| v0.1.0 | 2026-07-14 | 최초 작성. `/chat`(SSE)·`/seller/chat`(최소판)·`GET /profile/{user_id}`·`/events/{catalog,session-end,order}` 제공 API와 장바구니·point 조회 요구 계약 정의. 🔴 항목 10건(C-1~C-10) 등록. |

### 6.3 운영 요구 — 대화 저장·로그/모니터링 (AI 서버 내부) [v0.7.0 신설]

외부 계약이 아닌 **AI 서버 내부 운영 요구**다(FE/Spring 협의 불필요). 2026-07-15 사용자 확정. PRD·소유 SPEC에 비기능 요구로 편입한다.

#### (a) 대화 저장 규약

저장소 = LangGraph checkpointer(AI Postgres, `sessionId` = thread 키 — 프로필 파이프라인의 세션 종료 스캔 원천).

| 시점 | 저장 대상 | 상태 |
|---|---|---|
| 사용자 메시지 **수신 즉시** | user 메시지 원문 | — |
| 스트리밍 **완료 후** | assistant 응답 전문 | `COMPLETED` |
| 스트림 실패(in-stream `error`·LLM 재시도 소진) | 부분 생성 텍스트 | `FAILED` |
| 클라이언트 취소(§2.9 b) | **부분 생성 텍스트 보존** | `CANCELLED` |

- `FAILED`/`CANCELLED`의 부분 텍스트도 다음 턴 컨텍스트·프로필 스캔(결정 4-A sleep-time)에 포함한다.
- FE 채팅 히스토리 복원(`GET /chat/history` 류)은 **미결** — 지원 결정 시 이 저장소를 원천으로 계약 신설.

#### (b) 로그/모니터링 필드 (요청 단위 구조화 로그)

| 필드 | 비고 |
|---|---|
| `requestId` | §2.5 오류 봉투와 동일 키 — 전 구간 상관관계 |
| `userId`(또는 guestId) / `role` | JWT `sub` 유래 |
| `conversationId` | = `sessionId` |
| `latencyFirstToken` / `latencyTotal` | SSE 2분할 — 체감 응답성 vs 전체 시간(§2.9 c 기준 대비) |
| `model` | 호출 모델 id(Haiku/Sonnet, 노드별 다중 기록) |
| `promptTokens` / `completionTokens` | LLM 호출별 합산 |
| `errorType` | in-stream `error` 코드·`FAILED` 사유·타임아웃 구간 |
| `streamStatus` | `COMPLETED` / `FAILED` / `CANCELLED` (a와 동일 enum) |

- **PII 정책**: 사용자 message **원문은 로그에 남기지 않는다**(길이·해시만) — 원문은 (a) 대화 저장소에만 존재.
- 레이트 리밋(§2.8)·409(§2.9 a) 발동도 `errorType`으로 집계해 상한값 튜닝 근거로 쓴다.

---

## 7. 후속 SPEC 동기화 개정 목록 (Follow-up SPEC Amendments)

본 개정의 명명 기준 채택·경로 B·판매자 확대로 아래 SPEC들이 **정렬이 깨졌다.** 본 문서는 SPEC을 편집하지 않으며, 아래를 후속 동기화 개정(sync amendment) 대상으로 등록한다.

### 7.1 `SPEC-RECOMMEND-001` §5.3 (SSE 페이로드 스키마) — 개정 범위

- **이벤트명 교체**: `text.delta` → `token`; `products`(SSE 카드) → **삭제**(경로 B로 이관, `products.ready` 신호만 SSE).
- **필드 camelCase 전환**: `finish_reason`→`finishReason`, `product_id`→`productId`, `verified_sum`/`within_budget`/`dropped_items`/`feasibility_notice`→camelCase, `est_count`→`estCount` 등 전부.
- **`done.finishReason` 값**: `completed`→`stop`, `zero_result` 유지.
- **`error.code` 집합 교체**: `DECOMPOSE_FAILED`/`RERANK_FAILED` 등 스테이지 코드 → `LLM_TIMEOUT`/`LLM_UNAVAILABLE`/`SEARCH_FAILED`/`INTERNAL`(rerank 실패는 여전히 `done` degrade).
- **`ProductPayload` 이관**: `products` 카드 스키마는 SSE에서 제거되고, `productId`+`rank`+`reason`만 목록 push(§4.2)로 이관, 표시 필드는 Spring enrich(§4.3). EX-5/AC-REC-10 정신 유지·강화.
- **AC 갱신**: 이벤트 순서 `text.delta→products→done` → `token→products.ready→done`; "`products` 정확히 1회" → "`products.ready` 정확히 1회".
- **주의**: 서브그래프 동작·불변식(decompose 1회, rerank 상한, 하드 제약, degrade 정책)은 불변.

### 7.2 `SPEC-PROFILE-001` §5.4/§6.9 (`GET /profile/me`) — 개정 범위

- **경로**: `GET /profile/{user_id}` → `GET /profile/me`(IDOR 방지, 결정 19).
- **필드 camelCase**: `ProfileViewResponse` `user_id`/`generated_at` → `userId`/`generatedAt`. `exists`/`markdown` 유지.
- **구매 소스 개정 [v0.5.0]**: write 소스 "주문 이벤트 미러 스캔"(결정 16) → **질의 시점 구매 이력 조회(`GET /internal/members/{id}/orders`, §4.7)** 호출로 교체(sleep-time 배치). 게이트·델타 동작은 불변.
- **응답 구조·동작 불변**: 위 항목 외 스키마·REQ(PROF-081 등) 변경 없음.

### 7.3 이벤트 채널 SPEC 정합

- `/events/session-end` HTTP 계약은 본 문서 소유(결정 21). 개정 시 소비 SPEC의 필드명(camelCase) 전제와 정합을 확인한다. **수신 후 동작**은 소비 SPEC 소관 불변. (주문 알림은 v0.5.0에서 미채택 — §3.6·§4.7.)
- **카탈로그 동기화 참조 정합 [v0.5.0]**: 카탈로그 변경 이벤트·배치 동기화·AI 사본이 모두 채택되지 않으므로(§4.6 말미), 카탈로그 동기화·3계층 메타데이터·임베딩을 참조하는 SPEC-RECOMMEND-001(검색 tool을 pgvector 단일 SQL로 규정한 조항)·SPEC-CATALOG-DATA-001(enrichment→임베딩→적재 단계) 문구는 **질의 시점 Spring 위임(§4.6)에 맞춰 후속 개정/재범위**가 필요하다(§8 항목 4 연계).

---

## 8. product.md §12-A 결정과의 정합 — 사용자 확인 필요 항목 (결정 개정 필요 목록)

본 개정은 product.md의 여러 binding 결정과 긴장/상충한다. **product.md는 편집하지 않으며**, 아래를 **결정 개정 필요 항목(신규/개정 결정 레코드 대상)** 으로 등록한다. product.md 결정 로그는 현재 **결정 21**까지 있으며, 본 문서가 한때 참조한 결정 22/23/24는 **미비준 병렬 초안 소산으로 폐기**되었다(§6.2). 아래 8항목이 실제 필요한 개정이다.

### 항목 1 (상충 — 신규 결정 레코드 필요) — 경로 B: point 조회 폐기 + AI→Spring 역방향 예외 증가

결정 9-B(binding)의 "Spring 유일 접촉 = point 조회" 구체 문구와 경로 B(목록 push + 목록 GET)가 상충한다. 원칙(표시 권위 = Spring, AI 인덱스 표시 필드 미보유)은 유지·강화되나, 단방향 원칙의 AI→Spring 역방향 예외가 **장바구니 1건 → 장바구니·목록 push·I-6·I-7 4건**으로 증가한다. **경로 B + 판매자 역호출에 대한 product.md 신규 결정 레코드가 필요**하다. 사용자 확인·PRD 반영 요망.

### 항목 2 (긴장 — 정책 확인) — verifiedSum(검색 응답가) vs Spring enrich 표시가 괴리

BudgetSummary `verifiedSum`은 §4.6 검색 응답 가격 기준 결정론 합산(결정 14-A 원칙 유지)인데, 경로 B에서 실제 표시가는 Spring이 목록 GET 시점에 다시 채운다(§4.3). 검색~표시 사이 가격 변경 시 SSE `budget`과 우측 패널 표시가가 순간 괴리할 수 있다. **예산 표시 UX 정책**은 🔴 기획·Spring 협의 필요. (기존 OPEN-11 연장 — 질의 시점 검색이라 괴리 창은 크게 축소됨.)

### 항목 3 (개정 — 결정 20 확대) — 판매자 agent 상세 수정 draft 흐름 MVP 편입 + 데이터 소스 I-6 전환

결정 20(binding)은 판매자 MVP를 "매출/판매 통계 Q&A만"으로 한정하고, 데이터 소스 기본안을 **주문 미러 확장(`sellerId`·금액)** 으로 두었다(product.md line 134·695). 본 개정은 (1) **상세 수정 draft 흐름을 MVP로 확대**하고, (2) 데이터 소스를 **주문 미러 확장에서 I-6 집계 콜백으로 전환**한다(주문 미러 자체가 폐기됨 — 항목 5). **결정 20 개정 레코드가 필요**하다. C-7은 이로써 해소되나 I-6 계약 세부(C-13)는 협의 잔존.

### 항목 4 (개정 — 결정 9/9-A/9-B) — 상품 컬럼 사본·이벤트 동기화 폐기, AI 생성물 pull 배치로 대체 [v0.5.1 정정]

결정 9/9-A/9-B(binding)는 "필터 컬럼 최소 미러 + 이벤트 기반 준실시간 동기화"를 확정했으나(product.md line 132·310·325·340), 2026-07-15 사용자 최종 확정으로 **상품 원본 컬럼의 AI측 사본과 이벤트(웹훅) 동기화를 폐기**한다. **AI 생성물(extras·search_doc·임베딩)은 존속** — 결정 3의 Layer 2/3·결정 6(임베딩 모델)은 **유효**하며, 갱신만 **pull 배치(bulk export, §4.8)** 로 바뀐다. 질의 시점 후보 흐름(AI 벡터 검색 ↔ Spring 검색 결합)은 OPEN(§4.8 말미) — 확정 시 결정 레코드에 포함. **결정 9/9-A/9-B 개정(사본·이벤트 폐기 + pull 배치) 신규 결정 레코드가 필요**하다. SPEC-CATALOG-DATA-001의 enrichment→임베딩 단계는 §4.8 배치와 통합 재범위.

### 항목 5 (개정 — 결정 14-F/16 구현 방식) — 주문 이벤트 미러 → 질의 시점 구매 이력 조회

결정 14-F(dedup)·결정 16(프로필 구매 소스)은 "주문 이벤트 → AI 경량 미러" 구현을 전제했으나, 주문 알림·미러를 폐기하고 **추천 직전/sleep-time의 질의 시점 조회(`GET /internal/members/{id}/orders`, §4.7)** 로 대체한다(2026-07-15 확정). **동작 요구(exact 제외·소모품 억제·되돌리기 칩·구매 신호 델타)는 불변** — 데이터 획득 방식 개정 레코드 필요. SPEC-PROFILE-001 구매 소스 문구 개정(§7.2).

### 항목 6 (정정 — 병행 PRD) — events scope

병행 PRD 초안(docs/PRD.md v1.1.0)은 이벤트 채널 전부를 고도화로 옮겼으나, 확정안은 **`/events/session-end` 1종을 MVP에 유지**한다(주문 알림은 미채택으로 정리됨). PRD의 events-scope와 일정표(7/15 행의 "하이브리드 통합" 표현 포함)를 본 문서 v0.5.0 기준으로 정정해야 한다.

### 항목 7 (개정 — 결정 8) — 게스트 장바구니 담기 허용 [v0.6.0]

결정 8(binding)은 "장바구니 담기·구매는 회원 전용"으로 확정했으나, BE 팀 I-2 문서(02 D30, 2026-07-10 개정)가 **게스트(guestId) 담기 성공**을 확정했고 2026-07-15 사용자가 이를 채택했다(§4.1). **개정 범위**: (1) 장바구니 담기는 게스트 허용(로그인 유도는 결제 시점 FE 몫), (2) 구매는 계속 회원 전용, (3) 검색/추천 무제한·개인화 미적용·AI 서버 무상태 원칙은 불변. 구 AI-side 게스트 차단(`GUEST_NOT_ALLOWED`)은 폐기. **결정 8 개정 레코드가 필요**하다. 아울러 결정 7의 구현 세부(인증)는 "사용자 JWT 포워딩"이 아닌 **I-2 서비스 토큰 + 본문 신원(AI-검증 JWT `sub` 유래)** 으로 확정됨 — 결정 7 자체(경로: AI→Spring API 위임)는 불변이므로 별도 개정 불요, C-3 세부로 처리.

### 항목 8 (개정 — 결정 20) — 판매자 모델 전면 확대 + 쓰기 모델 전환 [v0.9.0]

BE internal API 배치(11종 PDF, 2026-07-16) 반영으로 결정 20(판매자 MVP = 통계 Q&A + draft)이 크게 확대된다. **개정 범위**: (1) 판매자 그래프가 **서브에이전트 다수**(sales_anomaly·conversion·behavior·churn·abuse·general·recommend·chart·`product_agent`)로 구성되고 조회/집계 7종 + 상품 CRUD 4종을 소비. (2) **쓰기 모델 전환** — 구 "AI는 제안만, 반영은 FE S-3 PATCH(AI 표면 밖)"에서 **"AI가 Spring internal API로 직접 쓰기(등록/수정/삭제) + 파괴적 작업은 HITL interrupt/resume 승인"**. "대화 발화 ≠ 동의" 원칙은 HITL로 유지. (3) `brandId`=JWT 클레임(항목 없던 신규). (4) 전역 I-8(account-events)은 admin 소유 협의. **결정 20 개정 + 신규 결정 레코드(판매자 쓰기·HITL) 필요**. 판매자 SPEC 신규 작성 필요.

> 위 8항목 외 인증(결정 19)·식별자(결정 19)는 결정과 정합하며 별도 사용자 확인 불필요. ~~장바구니 JWT 포워딩(결정 7·19) 정합~~은 v0.6.0에서 I-2 서비스 토큰 방식으로 대체되었다(항목 7 말미).

---

*문서 끝.*
