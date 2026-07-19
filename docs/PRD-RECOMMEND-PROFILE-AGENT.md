# PRD — 상품 추천 · 프로필 개인화 · 장바구니 에이전트

| 항목 | 값 |
|---|---|
| 문서 상태 | draft |
| 작성일 | 2026-07-17 (2026-07-17 카탈로그 데이터 구축을 별도 문서로 분리) |
| 담당 범위 | 상품 추천 에이전트(추천 서브그래프) + 프로필 구축/개인화 에이전트(프로필 파이프라인) + 장바구니 에이전트(cart 서브그래프) |
| 관계 정본 | 계약: [api-spec.md](api-spec.md) v0.15.8 · 기술 명세: [SPEC-RECOMMEND-001](specs/SPEC-RECOMMEND-001.md) v0.8.0 / [SPEC-PROFILE-001](specs/SPEC-PROFILE-001.md) v0.2.0 |

## 0. 이 문서를 읽는 법 — 문서 간 관계

이 프로젝트는 이미 아래 계층으로 문서가 나뉘어 있다. 본 PRD는 그중 **PM 관점 종합 문서**이고, 기술적 동작 계약은 여전히 하위 SPEC이 확정한다.

| 문서 | 성격 | 우선순위 |
|---|---|---|
| `api-spec.md` v0.15.8 | 외부 인터페이스 계약(엔드포인트·SSE 이벤트·필드·오류 코드)의 **정본** | 최우선 — 계약 충돌 시 이 문서가 이긴다 |
| `SPEC-RECOMMEND-001` / `SPEC-PROFILE-001` | 추천 서브그래프·프로필 파이프라인의 EARS 요구사항·인수 기준 | api-spec과 어긋나면 api-spec을 따른다(각 SPEC 헤더 명시) |
| `SPEC-CART-001` (2026-07-17 신설) | 장바구니 서브그래프의 EARS 요구사항·인수 기준. 본 PRD §3.3/§5.4/§7/§10-F를 구체화한 신규 초안 — **기획 저장소에 대응 정본이 아직 없어**, 본 SPEC 자체가 정본 등록 후보다(SPEC 헤더에 명시) | 팀 검토·정본 등록 전까지는 본 SPEC이 유일한 상세 소스 |
| **본 PRD** | 세 에이전트를 묶은 제품 관점 요약 — 범위·시나리오·데이터 모델·아키텍처·성공지표·리스크 | 신규 결정 사항은 본 PRD가 먼저 정리하고, **승인 후 위 SPEC들의 동기화 개정 입력**으로 쓴다(팀 확정 방향) |

즉 본 PRD는 SPEC을 대체하지 않는다 — 추천/프로필/장바구니의 EARS 요구사항(REQ-REC-xxx, REQ-PROF-xxx, REQ-CART-xxx)은 본 PRD에서 재작성하지 않고 **§ 참조로만 인용**한다. 코드 구현 시 상세 로직은 항상 SPEC을 본다.

---

## 1. 개요

자비스는 대화를 통해 상품을 추천하고 사용자의 개인 취향을 반영해가는 agentic 쇼핑몰 AI 서버다. 그중 본 PRD가 다루는 세 에이전트는:

- **상품 추천 에이전트**: 자연어 질의 → 조건 분해 → (Spring 1차 필터 위임 + AI 2차 attribute매칭·임베딩유사도) 후보 압축 → 프로필 반영 재랭킹 → 근거 있는 추천 응답까지의 파이프라인.
- **프로필 구축/개인화 에이전트**: 대화·구매 이력에서 취향 신호를 검증·누적해 장기 프로필을 쌓고, 추천 진입 시 그 프로필을 저지연으로 공급하는 파이프라인.
- **장바구니 에이전트**: "담아줘"류 발화에서 (상품·옵션·수량) 의도만 확정하고, 실제 담기·검증·합산 실행은 Spring에 위임하는 파이프라인. 게스트도 이용 가능.

세 에이전트는 서로 연결된다 — 프로필이 추천을 개인화하고, 추천 세션이 다시 프로필의 입력이 되며, 추천/장바구니 대화 흐름은 같은 구매자 그래프(`app/agents/buyer/graph.py`) 안에서 intent router로 갈린다. 추천 에이전트의 `search` 2차 압축은 별도 담당인 카탈로그 데이터 구축 파이프라인의 산출물(catalog DB)을 소비한다 — 그 구축 자체는 본 PRD 범위가 아니다.

---

## 2. 범위 (In Scope / Out of Scope)

### In Scope

- 추천 서브그래프 4단계: `decompose`(Haiku, 조건 분해) → `search`(**1차** Spring 위임 후보 조회+구매이력 dedup, **2차** AI가 catalog DB로 attribute 매칭·임베딩 유사도 압축) → `rerank`(Sonnet, 프로필 반영 재랭킹 + 근거 생성) → `respond`(SSE 응답).
- Case 1(상품 검색)/2(구조화 필터)/3(상황 기반 다중 니즈) 분기, 0건 완화, 멀티턴 병합(add/replace/reset), 총액 예산 처리.
- 프로필 파이프라인 3요소: `reader`(요청 경로, 동기 조회) / `builder`(요청 경로 밖, 델타 생성 + sleep-time consolidation) / `gate`(승격 판정).
- `GET /profile/me`(마이페이지 조회), `POST /events/session-end` 수신(프로필 조기 트리거).
- 장바구니 서브그래프: "담아줘" 의도 추출(상품·옵션·수량) → Spring 담기(I-2)·조회(I-18) 위임 → SSE `action` 응답. 옵션 되물음 멀티턴, 게스트 담기 허용.
- **추천 검색의 2차 압축**: Spring이 1차 필터 후보 + attribute를 반환하면, AI가 그 attribute를 decompose의 유연 조건과 매칭하고 catalog DB의 임베딩으로 유사도 정렬해 rerank 입력을 만드는 로직(§3.1/§6 참조) — catalog DB를 **소비**하는 이 로직만 본 PRD 범위이며, catalog DB를 만드는 쪽은 별도 담당.
- 위 세 에이전트가 소비/생산하는 데이터 모델·상태 스키마·SSE 페이로드 스키마.

### Out of Scope (다른 담당/다른 SPEC)

| 항목 | 사유 | 관련 문서 |
|---|---|---|
| 장바구니 실행·검증·수량 합산 자체 | **Spring 소유** — AI는 의도만 확정, 실행은 위임(결정 7). 커머스 DB에 직접 write 안 함 | api-spec §4.1 |
| 판매자 그래프(통계·draft) | 별도 에이전트 | mvp-plan.md §4 |
| 상품 원본 데이터의 1차 검색·필터링 실행(카테고리·가격·브랜드 등 고정 컬럼) | **Spring DB 소유** — AI는 위임 호출자일 뿐 그 검색 자체의 구현 주체 아님. AI 몫은 그 이후(attribute 매칭·유사도) | api-spec §4.6 |
| **카탈로그 데이터 구축(크롤 검증→enrichment→임베딩→인덱스 적재→스냅샷)** | **별도 담당**(2026-07-17 분리) — 오프라인 배치라 실시간 대화형 에이전트 3개와 작업 성격이 달라 분리 | `SPEC-CATALOG-DATA-001` |
| 공통 인증·SSE 수명주기·레이트 리밋 | 전 엔드포인트 공유 인프라, 개별 에이전트 소관 아님 | mvp-plan.md §0 |
| 리뷰 분석 에이전트 | 별도 그래프, MVP 이후(고도화) | roadmap.md |
| 프로필 마이페이지 편집(PUT) | MVP는 GET only, PUT은 고도화 범위 | `SPEC-PROFILE-001` EX-P3 |
| NL 팩트 유닛(자연어+벡터 온디맨드 취향 검색) | 원본 아이디어 문서에서도 "MVP 단계에서는 고려 X"로 명시 | 저장 방식.pdf §2 |

---

## 3. 핵심 기능

### 3.1 상품 추천

| 기능 | 설명 | 근거 |
|---|---|---|
| 조건 분해 | Haiku 4.5 1회 호출로 발화 → 구조화 필터(`FilterSet`) + 시맨틱 쿼리 + Case(1/2/3) 산출. 별도 분류 호출 없음 | REQ-REC-001/002 |
| 명시/비명시 조건 태깅 | 발화에 직접 있으면 `user`, 프로필/기본값 파생이면 `derived`로 필드별 출처 태깅 — 0건 완화 우선순위 판단 근거 | REQ-REC-047 |
| Case 3 다중 니즈 분해 | 상황("유럽여행")을 아이템 목록으로 분해, 니즈 수 하드 캡 없음. 각 아이템에 `priority`(1 필수/2 권장/3 선택) 태깅 — "빠지면 그 상황이 성립하는가"가 판정 기준 | REQ-REC-004, 결정 14-H |
| 후보 검색) | **1차 — Spring 위임**: `GET /internal/products/search`(I-1)로 고정 컬럼(카테고리·가격·브랜드·평점) 매칭 후보를 확보하고, **응답에 Layer2 `attributes`를 포함**시켜 받는다. **2차 — AI 압축**: catalog DB(pgvector)에서 후보들의 임베딩을 `productId`로 조회해 ① `attributes`를 decompose의 유연 조건과 매칭(값 매칭), ② `semantic_query` 임베딩과의 코사인 유사도로 정렬 — 이 압축된 결과가 `rerank`의 입력이 된다. 원본 아이디어 문서("추천 흐름 v2.pdf") 4단계 검색의 2단계(고정 컬럼)를 Spring이, 3단계(유연 필터+유사도)를 AI가 맡는 구조와 동일 | api-spec §4.6 · 본 PRD §6.2(아키텍처 결정 상세) |
| 구매 이력 dedup | `GET /internal/members/{id}/orders`(I-19)를 조회해 exact `productId` 항상 제외 + 소모품 카테고리 억제(MVP: boolean 플래그). 억제는 non-blocking, 되돌리기 제안 칩 제공. 게스트는 스킵 | REQ-REC-100~103, api-spec §4.7 |
| 재랭킹 | Sonnet 5 1회 호출로 최대 30개 후보를 `profile_summary` 반영해 재랭킹 + 상품당 1문장 근거 + 전체 코멘트 1개. **MVP는 가중합 스코어링 없이 LLM 판단이 곧 순위**(결정 14-C A안) — 결정론적 가중 스코어링(B안)은 실트래픽 데이터가 쌓인 뒤의 고도화 실험으로 유예 | REQ-REC-020/021, 결정 14-C |
| 재랭킹 견고성 | 후보 순서 셔플(유사도순 나열 금지) + 출력 결정적 검증(후보 외 ID 제거, 근거 속성 대조) — 검증 실패 시 검색 순서로 degrade | REQ-REC-080~083 |
| 0건 완화 | config 상한 라운드(기본 3) 내에서 비명시·약한 조건 우선 자동 완화, 명시 제약(특히 가격)은 자동 완화 대신 예상 결과 수 포함 제안 칩으로 위임 | REQ-REC-040~046 |
| 멀티턴 병합 | add(조건 추가)/replace(조건 교체)/reset(주제 전환, 가격 등 범용 명시 제약만 캐리) — 판단은 `decompose` 단일 호출 내에서, 별도 분류 호출 없음 | REQ-REC-050~056 |
| 총액 예산 | Case 3 + "~내로 다 맞춰줘"류 질의는 개별 상한이 아닌 묶음 총액 상한으로 처리, 코드가 인덱스 가격으로 결정론적 합산·보정(LLM 산수 불신) | REQ-REC-070~077 |
| 응답 | 최종 노출 **5~8개**(config), 결과는 SSE에 카드로 싣지 않고 Spring에 push 후 FE가 별도 GET("경로 B") | api-spec §3.3 |
| 무관 질문 패널 유지 | 쇼핑과 무관한 질문에는 검색 없이 안내 응답하되, 우측 패널은 비우지 않고 `GET /internal/products/popular`(I-3, 서비스 토큰)로 인기 상품을 채워 유지 | Notion API 명세서 I-3, 추천 흐름 v2.pdf 판정 순서 5 |

### 3.2 프로필 구축 / 개인화

| 기능 | 설명 | 근거 |
|---|---|---|
| reader (읽기) | 그래프 진입 시 `profile_summary`를 PostgresStore 단일 get **1회**로 동기 조회. **LLM 호출 0회** — 지연 크리티컬 경로이므로 요약은 sleep-time에 미리 계산해둔 것을 읽기만 함. 게스트·신규 회원은 `None` | REQ-PROF-001~003 |
| profile_summary 구조 | 하이브리드 단일 마크다운 문자열 = ① 구조화 블록(FilterSet 매핑 가능 속성만: 가격 성향·선호/회피 브랜드·평점 성향·Layer2 속성) + ② 산문(자연어 취향 서술) + ③ 최근 맥락(episodic 하이라이트 2~3개). 문자 상한 기본 1,000자(config), **집행은 생성 측 압축 재작성** | REQ-PROF-010~017 |
| builder 1단계 — 델타 생성 | 세션 종료 후, 미처리 세션당 Sonnet **1회**로 후보 델타 산출 + 그 호출 안에서 명시성·현저성·transient(일시적 여부) 함께 태깅. 턴 중에는 절대 write하지 않음 | REQ-PROF-020~025 |
| builder 2단계 — sleep-time consolidation | 여러 세션 델타를 위키에 병합·중복 제거·모순 해소. **텍스트 통합만 LLM**, EMA 누적·승격 판정·recency-wins(최신성 충돌)·supersede는 전부 **결정론적 코드** | REQ-PROF-030~037 |
| gate — 3조건 승격 | 반복성(EMA confidence 누적) · 현저성(salience) · 명시성(explicitness) 3조건으로 승격 판정. "기억해"는 즉시 기록되는 hot-path 예외. 구매 신호는 명시성 없이 반복성·현저성만으로 승격 가능 | REQ-PROF-040~046 |
| transient 격리 | "이번엔 비싸도 돼", "친구 선물로" 같은 일시적/타인 지칭 발화는 session_context에만 남고 장기 프로필 후보에서 배제, 세션 종료 시 폐기 | REQ-PROF-042 |
| 트리거 | 정합성의 원천은 **저장된 대화의 미처리 스레드 스캔**(워터마크). `POST /events/session-end`(Spring, best-effort·멱등)은 조기 실행 신호일 뿐 — 유실돼도 다음 배치가 회수 | REQ-PROF-050~055 |
| GET /profile/me | 마이페이지용 사람이 읽는 마크다운 passthrough. 게스트·미보유는 `exists:false, markdown:null`을 **정상 200**으로. **PUT 없음**(MVP) | api-spec §3.4, REQ-PROF-080~082 |
| 저장 안전성 | fact는 삭제 대신 `supersededBy`로 이력 보존(supersede), 최신성 충돌은 항상 코드 결정론적 recency-wins — LLM에 최신성 판단 위임 금지 | REQ-PROF-033/034 |

### 3.3 장바구니

`SPEC-CART-001`(2026-07-17 신설)의 REQ-CART-xxx를 요약한 것이다. 이하 표의 근거란은 SPEC § 참조로 남긴다.

| 기능 | 설명 | 근거 |
|---|---|---|
| 의도 추출 | "담아줘"류 발화에서 (상품·옵션·수량)만 확정. **AI는 커머스 DB에 직접 write하지 않는다** — 담기 실행·검증은 항상 Spring 위임(결정 7) | REQ-CART-001~011 |
| 담기 실행 | `POST /internal/cart/items`(I-2)에 단건 위임. **Case 3 묶음 담기는 상품별 반복 호출**(항목별 성공/실패가 자연히 분리되어 SSE `action`도 항목별 emit) | REQ-CART-005/006/010 |
| 게스트 담기 허용 | `role == guest`여도 `guestId`로 담기 성공(2026-07-10 BE 개정으로 구 차단 폐기). 로그인 유도는 결제 시점 FE 몫 | REQ-CART-040/041 |
| 수량 합산 | 동일 상품·옵션이 이미 있으면 **Spring이 합산**(AI는 합산을 계산하지 않음). 담기 전 `GET /internal/cart`(I-18)로 기존 보유를 조회해 "이미 담겨 있어 N개로 늘렸어요"류 안내만 생성 — **조회 실패해도 담기는 진행**(degrade) | REQ-CART-030~033 |
| 옵션 되물음 멀티턴 | 옵션 필수 상품에 `optionId` 없이 담으려 하면 I-2가 `400 CART_OPTION_REQUIRED`(options 목록) 반환 → 실패 `action` 없이 `token`으로 "어떤 색상으로 담을까요?" 재질문 → 다음 턴 답을 `optionId`로 해석해 재담기. `CART_OPTION_INVALID`는 1회 재시도 후 `CART_ERROR` | REQ-CART-020~023 |
| 장바구니 질의 응답 | "장바구니에 뭐 있어?"는 별도 SSE 이벤트 없이 `GET /internal/cart`(I-18) 조회 후 `token` 텍스트로 답변 | REQ-CART-034~036 |
| 결과 통지 | SSE `action`(`CART_ADDED{cartItemId}` / `CART_ADD_FAILED{reason}`). `reason`은 `PRODUCT_NOT_FOUND`/`CART_ERROR` 2종(`OUT_OF_STOCK`은 v0.15.5로 폐기 — 담기 시점 재고검증 없음) | REQ-CART-050~052 |

> 카탈로그 데이터 구축(모듈 A~E, 오프라인 배치)은 별도 담당이다. 본 PRD는 그 산출물(catalog DB)을 §3.1 "후보 검색" 2차 압축에서 **소비**만 한다.

---

## 4. 사용자 시나리오 / 유즈케이스

| ID | 시나리오 | Given | When | Then |
|---|---|---|---|---|
| UC-1 | 단순 상품 검색 | 로그인 사용자 | "5만원 이하 무선 이어폰" 발화 | Case 2, 가격 상한이 WHERE로 항상 적용, 5~8개 근거 있는 추천 |
| UC-2 | 상황 기반 다중 니즈 | 로그인 사용자 | "유럽여행 가는데 뭐 필요해?" 발화 | 선케어·어댑터·우비 등 니즈로 분해, 니즈별 카테고리 묶음 카드 + priority에 따른 노출 순서 |
| UC-3 | 동일 질문의 개인화 차이 | 확정 프로필 보유 사용자 vs 신규 회원 | 동일 질의("이불 추천해줘") | 보유 사용자는 `profile_summary`(예: 가성비 취향) 반영 재랭킹, 신규는 인기 상품 위주로 성립(콜드스타트 폴백) |
| UC-4 | 취향 학습(반복 발화 승격) | 두 세션에 걸쳐 "저렴한 걸로"류 발화 반복 | 두 번째 세션 종료 | 예산대 태그가 candidate→confirmed로 승격, `profile_summary`에 반영, 이후 세션부터 재랭킹에 즉시 영향 |
| UC-5 | "기억해" 즉시 기억 | 대화 중 | "이거 기억해줘" 발화 | 3조건 게이트 우회, fact 즉시 store 기록(단, `profile_summary` 재생성은 다음 sleep-time) |
| UC-6 | 0건 완화 + 투명 안내 | 브랜드 조건(비명시)으로 0건 | 검색 실행 | 최대 3라운드 내 최소 이탈 자동 완화 + "조건을 조금 넓혔어요" 안내, 명시 가격 조건은 자동 완화 대신 제안 칩 |
| UC-7 | 총액 예산 준수 | "5만원 내로 유럽여행 준비물" | Case 3 + total_budget | 코드가 인덱스 가격으로 합산 검증, 예산 초과 시 저가 대안 자동 교체(상한 내), 필수 아이템 초과 시 조용히 누락하지 않고 안내 |
| UC-8 | 게스트 이용 | 비로그인 | 임의 추천 질의 | `profile_summary = None`이라 개인화는 스킵되지만 추천 자체는 정상 성립, 예외 없음 |
| UC-9 | 최근 구매 재추천 방지 | 최근 소금을 구매한 회원 | "장보기 리스트 추천해줘" | 소금 니즈는 억제되되 나머지는 정상 추천 + "소금은 최근 구매 — 다시 추천받기" 되돌리기 칩 |
| UC-10 | 마이페이지 프로필 확인 | 확정 프로필 보유 사용자 | 마이페이지 접속(`GET /profile/me`) | 사람이 읽는 프로필 마크다운 표시, 편집 불가(GET only) |
| UC-11 | 민감 정보 일반화 | "당뇨가 있어서" 발화 | 세션 종료 델타 생성 | 원인(질병)은 버리고 파생 취향(무설탕 선호)만 태그로 승격 후보화 |
| UC-12 | 담기 해피패스 | 옵션 없는 상품 | "이거 담아줘" | I-2 단건 호출 → 성공 → SSE `action: CART_ADDED{cartItemId}` |
| UC-13 | 옵션 되물음 | 옵션 필수 상품 | "이 이어폰 담아줘"(옵션 미지정) | `CART_OPTION_REQUIRED` → 실패 action 없이 "어떤 색상으로 담을까요?" 재질문 → 사용자가 "블랙"이라고 답하면 `optionId` 해석 후 재담기 성공 |
| UC-14 | 게스트 담기 | 비로그인 | "이거 담아줘" | `guestId`로 담기 성공(차단 없음), 결제 시점에만 로그인 유도(FE 몫) |
| UC-15 | 담기 전 보유 안내 | 이미 같은 상품 1개 보유 | "이거 하나 더 담아줘" | Spring이 수량 합산(2개) 처리, AI는 조회 결과로 "이미 담겨 있어 2개로 늘렸어요" 안내 문구 생성 |
| UC-16 | 장바구니 조회 실패 시 degrade | I-18 호출이 타임아웃 | 담기 시도 중 조회 실패 | 보유 안내 없이도 담기(I-2)는 정상 진행 — 조회 실패가 담기를 막지 않음 |
| UC-17 | 장바구니 질의 응답 | 담긴 상품 있음 | "장바구니에 뭐 있어?" | 별도 이벤트 없이 `token` 텍스트로 담긴 목록을 자연어로 답변 |
| UC-18 | 유연 조건 매칭(2차 압축) | "방수 되는 여행용 파우치" | Spring이 1차 후보 30개 + attributes 반환 | AI가 `attributes.방수 == true`로 매칭·정렬 후, catalog DB 임베딩으로 "여행용" 시맨틱 유사도까지 반영해 rerank 입력을 압축 |

---

## 5. 데이터 모델

각 파이프라인이 다루는 데이터의 **개념적 형태만** 요약한다. 정확한 필드·타입·기본값은 전부 해당 SPEC이 원본이며, 여기서 다시 정의하지 않는다(§0 원칙 — SPEC이 있는 곳에서 PRD가 스키마를 복제하면 SPEC 개정 시 PRD도 매번 같이 고쳐야 하는 유지보수 부담이 생긴다).

### 5.1 프로필 저장 모델

**OKF 자연어 위키 + PostgresStore**(LangGraph `BaseStore`) 모델을 채택한다(기존 `SPEC-PROFILE-001` 그대로, 관계형 태그+count 방식인 원본 아이디어 문서 "저장 방식.pdf" 모델은 미채택·참고용).

다루는 데이터는 크게 3종 — ① 승격된 fact 본문+메타(Store item, 네임스페이스 `profile|facts|episodes`), ② 세션에서 뽑힌 후보 델타, ③ 게이트가 관리하는 EMA·승격 상태. 전체 스키마(`StoreItemValue`/`ProfileDelta`/`GateState`)는 `SPEC-PROFILE-001` §5.2/§5.3 참조.

### 5.2 추천 요청/응답 모델

다루는 데이터는 누적 필터(`FilterSet`), Case 3 니즈 목록(`ShoppingItem`), 검색 후보(`Candidate`), 예산 묶음(`BundleState`) 등이며, 와이어 포맷은 CLAUDE.md 규약대로 camelCase(`CamelModel`)다.

PRD 차원에서 특히 짚어둘 통찰 하나: `Candidate.product_id`가 **Spring의 1차 응답과 AI catalog DB의 임베딩을 잇는 조인 키**다 — 둘이 어긋나면(신선도 지연 등) 2차 압축이 실패하므로 §10-A에 리스크로 등록돼 있다. 전체 스키마는 `SPEC-RECOMMEND-001` §5.1~5.3 및 `api-spec.md` §3.1 참조.

### 5.3 물리 저장소

| DB | 소유 | 내용 | 비고 |
|---|---|---|---|
| catalog DB (AI Postgres, pgvector) | **별도 담당**(`SPEC-CATALOG-DATA-001`) | AI 생성물(extras·search_doc·임베딩)만 — 상품 원본 컬럼 사본 없음 | 본 PRD의 추천 `search` 2차 압축(attribute 매칭·임베딩 유사도)이 이 DB를 **읽기만** 함(§3.1/§6.2) — 스키마·구축은 별도 담당 |
| profile DB (AI Postgres, pgvector) | 본 PRD 범위 | `PostgresStore` 프로필 위키, checkpointer(session_context) 동거 여지 | catalog DB와 계정 분리 — cross-DB 조인 금지 |

단일 Postgres 인스턴스 안의 별도 데이터베이스 2개 + 계정 분리(결정 16-A) — 물리 인스턴스 완전 분리는 부하 격리가 실제 필요해질 때의 고도화 승격 경로.

### 5.4 장바구니 요청/응답 모델

장바구니는 AI 측에 영속 저장소가 없다 — state는 대화 턴 사이 되물음 상태(멀티턴)로만 잠깐 유지되고, 실제 데이터는 전부 Spring 소유다. 다루는 데이터는 의도 추출 결과(`CartIntent`)와 I-2/I-18 요청·응답, SSE `action` 페이로드뿐이며, 전체 스키마는 `SPEC-CART-001` §5.1/§5.2 및 `api-spec.md` §3.1(3)/§4.1/§4.9 참조.

---

## 6. 시스템 아키텍처

### 6.1 컴포넌트 개요

```
FE (React) ──JWT──▶ FastAPI(자비스 AI 서버)
                        │
                POST /ai/chat (SSE)
                        │
              구매자 그래프 entry
                        │
        ┌───────────────┼───────────────────┐
        │  reader (profile_summary 동기 get) │   ← 프로필 에이전트 (요청 경로)
        └───────────────┼───────────────────┘
                        │
                 intent router
                        │
        ┌───────────────┴───────────────┐
        │                                │
  recommendation 서브그래프       cart 서브그래프      ← 추천 / 장바구니 에이전트
        │                                │
dedup(I-19) ∥ decompose(Haiku)    의도 추출(상품·옵션·수량)
        │                                │
        ▼                          Spring 위임: 담기(I-2) / 조회(I-18)
  search — 2단계 압축                    │
   1차: Spring 위임(I-1)           SSE action(CART_ADDED/
       고정필터+attributes 반환    CART_ADD_FAILED) 또는
   2차: catalog DB(pgvector) 조회   token(옵션 되물음·조회 응답)
       attribute 매칭 + 임베딩 유사도
        │
rerank(Sonnet, profile_summary 반영)
        │
respond: SSE(token/conditions/
suggestions/budget/
products.ready/done/error)
        │
  목록 push → Spring(§4.2)
        │
  Spring이 표시필드 enrich, listId 저장
        │
FE: products.ready 수신 → 목록 GET(§4.3) → 우측 패널 렌더
```

> catalog DB(pgvector)를 만드는 오프라인 배치 파이프라인은 별도 담당이다. 위 다이어그램의 "catalog DB(pgvector) 조회" 상자가 그 파이프라인의 산출물(frozen 스냅샷)을 가리킨다.

```
[프로필 파이프라인 — 요청 경로 밖, sleep-time]

Spring POST /events/session-end (best-effort, 멱등)
        │                              ┌────────────────────┐
        └──────조기 트리거──────────▶  │ 미처리 스레드 스캔   │  ← 정합성의 실제 원천
                                       │ (워터마크 기준)      │     (통지 유실 무관)
                                       └─────────┬───────────┘
                                                 │
                              builder 1단계: 세션당 Sonnet 1회
                              → 델타(assertion + explicitness/salience/transient)
                                                 │
                              gate: 3조건 판정(반복성 EMA·현저성·명시성)
                                                 │
                              builder 2단계: consolidation
                              (위키 병합, recency-wins, supersede — 전부 코드)
                              + profile_summary 재생성(문자 상한 내 압축)
                                                 │
                              PostgresStore(profile DB) 저장
                                                 │
                    다음 그래프 진입 시 reader가 단일 get으로 소비
```

### 6.2 중요한 아키텍처 결정 — 후보 검색은 "Spring 1차 + AI 2차" 2단계 (2026-07-17 확정)

**1차 필터링은 Spring `GET /internal/products/search`(I-1)에 질의 시점 위임한다** — 이는 2026-07-15 팀 최종 확정 사항이다(api-spec 헤더 v0.5.0 노트).

여기까지는 api-spec에 이미 있던 내용이고, **2026-07-17에 그 다음 단계를 확정**했다: Spring 응답에 `attributes`(Layer2 속성)도 함께 실려오면, **AI가 catalog DB(pgvector)에서 그 후보들의 임베딩을 `productId`로 조회**해 ① attribute 값 매칭, ② `semantic_query`와의 코사인 유사도 정렬을 수행하고, 그 결과를 `rerank`(LLM) 입력으로 넘긴다. 즉 **1차(고정 컬럼) = Spring, 2차(유연 필터 + 유사도) = AI**로 역할이 나뉜다 — 원본 아이디어 문서("추천 흐름 v2.pdf")의 4단계 검색 설계(2단계 고정 필터 → 3단계 유연 필터: attribute매칭+유사도 → 4단계 LLM 스코어링)를 그대로 계승한 것이고, api-spec §4.8 말미가 "OPEN"으로 남겨뒀던 두 결합 방식(방식1/방식2) 중 **방식2를 채택·구체화**한 결정이다.

**주의**: 이건 profile DB와 catalog DB의 "cross-DB 조인"이 아니다 — `rerank`에 주입되는 `profile_summary`는 그래프 진입 시 이미 별도로 읽어둔 값이고, `search` 단계의 catalog DB 조회는 그것과 별개의 애플리케이션 레벨 쿼리다. 결정 16-A의 "cross-DB 조인 금지"는 SQL 레벨 조인 금지이지, 애플리케이션이 두 DB를 각각 읽는 것까지 막지 않는다.

### 6.3 모델 배치

| 노드/컴포넌트 | 모델 | 호출 수 | 비고 |
|---|---|---|---|
| decompose | Claude Haiku 4.5 | 요청당 정확히 1회 | Case 판별도 이 출력에서 파생, 별도 분류 호출 없음 |
| rerank | Claude Sonnet 5 | 요청당 1회(Case 3 묶음 전략 시 config 상한 내 최대 2~4) | MVP는 순수 LLM 판단, 가중합 스코어링 없음 |
| profile builder 1단계 | Claude Sonnet 5 | 미처리 세션당 1회 | 델타 생성 + 태깅 동시 수행 |
| profile builder 2단계 | Claude Sonnet 5 | sleep-time 배치당 | 텍스트 통합에만 사용, 판정은 코드 |
| profile reader | — | 0회 | store 단일 get만, 지연 크리티컬 |
| cart 의도 추출 | Claude Haiku 4.5(2026-07-17 확정) | 요청당 1회 | `decompose`와 동일한 2-tier 배정 원칙(결정 5) — 경량 구조화 추출, 복잡한 종합 판단 불필요. `SPEC-CART-001` REQ-CART-007 |

> catalog enrichment/임베딩 모델 배정은 별도 담당 파이프라인 소관.

---

## 7. API / 인터페이스 설계

api-spec.md v0.15.8을 정본으로 인용한다. camelCase, productId 등 상품/옵션/장바구니/주문 id는 숫자(BIGINT), guestId만 UUID 문자열. 신원은 항상 JWT `sub` 도출(요청 본문 신뢰 금지).

### 7.1 FE 대면 (AI 서버 소유)

**`POST /ai/chat`** — SSE, 요청 `{sessionId, threadId, message}`. 이벤트: `token`(근거 토큰, 0회+) → `conditions`(0~1회, 필터 칩) → `action`(장바구니 결과, 0회+) → `suggestions`/`budget`(해당 시) → `products.ready`(성공 시 정확히 1회, `{sessionId, listId}`) → `done`(`finishReason: stop|zero_result`) / `error`(`LLM_TIMEOUT`|`LLM_UNAVAILABLE`|`SEARCH_FAILED`|`INTERNAL`). 상품 카드는 SSE에 실리지 않는다(경로 B). — api-spec §3.1, §3.3

**`action`** 이벤트(장바구니 전용): `{ type: "CART_ADDED", cartItemId }` 또는 `{ type: "CART_ADD_FAILED", reason }`(`reason` ∈ `PRODUCT_NOT_FOUND`/`CART_ERROR`, `OUT_OF_STOCK`은 v0.15.5로 폐기). 옵션 되물음은 `action` 실패가 아니라 `token` 텍스트 재질문으로 처리되고, 장바구니 조회 응답도 별도 이벤트 없이 `token`으로 온다 — api-spec §3.1 (3)

**`GET /profile/me`** — 경로 파라미터 없음(IDOR 방지, 결정 19). 응답 `{userId, exists, markdown, generatedAt}`. 게스트/미보유는 `exists:false`가 정상 200. — api-spec §3.4

**`POST /events/session-end`** — Spring → AI, best-effort·멱등. `{eventId, userId, sessionId, endedAt, reason}`. 202 Accepted. — api-spec §3.5

### 7.2 AI → Spring 위임

Notion "📡 API 명세서" DB(팀 결정 원장, 2026-07-16 확인)를 기준으로 아래와 같이 확정/잔여를 구분한다.

| API | 용도 | 상태 |
|---|---|---|
| `GET /internal/products/search`(I-1, 서비스 토큰) | 후보 검색 | **메서드 GET 확정**(Notion 결정). 잔여: 구조화 필터·`excludeProductIds` 배열의 GET 쿼리스트링 인코딩 규약 🔴 C-15 + **응답 `attributes` 필드 구조 미확정**(🔴 C-5, 2단계 검색의 전제 조건) |
| `GET /internal/members/{id}/orders`(I-19, 서비스 토큰) | 구매 이력(dedup·프로필 구매 소스 공용) | 경로·메서드·인증 레인 확정. **[2026-07-18] BE가 응답 본문을 상태이름·배송비 0원·숫자 id 기준으로 재작성 완료 — "이대로 가도 되는지" LLM팀(우리) 확인 요청 중** 🔴 C-6 |
| `GET /internal/products/popular`(I-3, 서비스 토큰) | 무관 질문 패널 유지용 인기 상품 | 확정 |
| `POST /events/session-end`(I-20, 서비스 토큰) | 세션 종료 통지(Spring→AI) | 경로·인증 레인 확정. **[2026-07-18 신규 발견] 세션 ID 형식 불일치**: Spring은 UUID로 발급하는데 이 엔드포인트 스펙은 `S-abc123` 형식만 받게 돼 있음 — **BE가 UUID 그대로 받아달라고 우리(LLM팀)에 요청 중**, 확정 전까지 실제 통지가 전부 거절될 위험 🔴 C-8 |
| `POST /internal/cart/items`(I-2, 서비스 토큰) | 장바구니 담기(단건, 묶음은 반복 호출) | 경로·메서드·인증 확정(BE 문서 채택). **[v0.15.5 확정]** 담기(I-2) 시점 재고 검증은 없음 — 재고 차감·품절 판정은 주문(O-1) 시점에만 발생. 실패 사유는 `PRODUCT_NOT_FOUND`/`CART_ERROR` 2종으로 확정(`SPEC-CART-001` OPEN-CART-1 해소) |
| `GET /internal/cart`(I-18, 서비스 토큰) | 장바구니 조회(질의 응답 + 담기 전 보유 확인) | 경로·메서드·인증 확정. **`productName`/`optionName` 필수 포함 확정**(BE 문서, 2026-07-18) |
| `POST /internal/recommendations`(I-21, 서비스 토큰) | 추천 목록 콜백(AI→Spring, "경로 B" push) | 경로·메서드·인증 레인 확정. **[2026-07-18] 스키마 확정 책임이 LLM팀(우리)에 있다고 BE가 명시** — 확정할 것: ① `listId` 형식·유효시간(SSE로 FE 전달되는 키) ② 추천 이유(`reason`)는 SSE가 아니라 I-21 콜백(`reasons[{productId, reason}]`)에 실어 Spring이 Redis 저장 → CH-5 카드에 echo(SSE는 채팅용 자연어 설명만 담당) — 확정 |
| `GET /api/chat/lists/{listId}`(CH-5, 인증 불필요) | 추천 목록 조회(FE↔Spring, "경로 B" GET) | 경로·메서드·인증 레인 확정. **[2026-07-18] 응답 스키마는 FE와 Spring이 정함 — LLM팀 사안 아니라고 BE가 명시**(우리 리스크 아님) |

**참고**: catalog DB(pgvector) 조회 자체는 Spring API가 아니다 — AI가 자기 소유 DB를 직접 읽는 **내부 쿼리**다(§5 데이터모델 vs API 논의 참조). §4.6/§4.7/§4.9/I-2/I-20만 실제 "API"(시스템 경계를 넘는 호출)이고, catalog DB 2차 압축은 그 경계 안쪽에서 일어난다. catalog DB를 채우는 pull 배치(I-17)는 별도 담당.

목록 push/GET("경로 B")은 **2026-07-18부로 Notion 결정 원장에 경로가 등재**됐다(I-21/CH-5) — 다만 요청/응답 바디 스키마는 여전히 OPEN이라 완전히 해소된 건 아니다(§10 참조).

**주의(2026-07-17 자체 검토로 발견)**: I-1의 `attributes` 응답 필드는 api-spec §4.6에서 이미 **"구조 미확정 🔴(C-5)"**로 명시돼 있다 — 즉 2단계 검색(§6.2, §3.1)이 전제하는 "Spring이 attributes를 쓸만한 형태로 반환한다"는 가정 자체가 아직 Spring과 확정 안 된 상태다. I-1 행의 잔여 협의에 이 항목을 추가해야 한다.

이 표의 잔여 협의 항목은 `mvp-todo.md`의 착수 우선순위 1번(Spring 협의)에 해당하며, 본 PRD 담당 두 에이전트 모두 이 협의가 선행돼야 실제 동작한다.

### 7.3 내부 컴포넌트 계약 (그래프 노드/함수)

- `app/agents/buyer/recommendation/`: `decompose(state) -> filters, semantic_query, case` / `search(filters) -> candidates`(Spring 위임 wrapper) / `rerank(candidates, profile_summary) -> ranked, rationale` / `respond(ranked) -> SSE`.
- `app/agents/profile/reader.py`: `read_profile_summary(user_id: str) -> dict | None`.
- `app/agents/profile/builder.py`: `generate_session_delta(user_id, thread_id) -> None`(store에 직접 write) / `consolidate(user_id) -> None`.
- `app/agents/profile/gate.py`: `should_promote(*, salience, explicit, repetition_ema, threshold) -> bool`.
- `app/agents/buyer/cart/`: 의도 추출(상품·옵션·수량) → `spring_client.add_to_cart`(I-2) / `spring_client.get_cart`(I-18) 호출 → 옵션 되물음 상태 관리(멀티턴) → SSE `action` 분기.

현재 전부 `NotImplementedError` 스텁 — 구현 착수 시 각 파일 docstring의 SPEC § 참조를 따른다(장바구니는 `SPEC-CART-001`, 2026-07-17 신설). `app/pipelines/*`(카탈로그 데이터 구축)는 별도 담당.

---

## 8. 비기능 요구사항

### 지연

- `reader`는 요청 경로에 포함되는 유일한 프로필 컴포넌트 — LLM 0회·store 단일 get으로 가장 가벼워야 함(최적화 우선순위 최상).
- `rerank`(Sonnet, 최대 30개 후보 입력)가 추천 파이프라인의 지배적 지연/비용원.
- `builder`/`gate`/consolidation은 요청 경로 밖(sleep-time)이라 응답 지연과 무관.
- 절대 초 단위 SLO는 하드코딩하지 않음(OPEN-4) — 단 공통 인프라 확정값(AI→Spring 3s, first-token 10s, 스트림 상한 90s)은 준수.

### LLM 호출 / 비용 가드레일

- 추천: `decompose` 1회(항상) + `rerank` 1회(Case 3 묶음 시 config 상한, 기본 최대 2~4). 니즈 수만큼의 무제한 fan-out 금지.
- 프로필: `reader` 0회, `builder` 1단계 세션당 1회(태깅 동시 흡수, 추가 호출 없음), 2단계는 배치.
- 합산·EMA·승격·최신성 판단은 전부 결정론적 코드 — LLM에 위임 금지.
- 공유 시스템 프롬프트는 프롬프트 캐싱(ITPM 한도 제외).
- 모든 튜너블(요약 문자 상한·EMA α·승격 임계·완화 라운드 상한·top_k 등)은 `core/config.py` 주입 — 하드코딩 금지.

### 안전/일관성 불변식 (must-hold)

- 가격 등 정확 제약은 항상 SQL/Spring WHERE 필터 — 유사도 근사로 대체 금지.
- `products.ready`는 성공 시 정확히 1회.
- 리랭크 출력 `productId`는 항상 검색 후보 집합의 부분집합(후보 외 환각 차단).
- 명시적 사용자 제약은 동의(제안 칩) 전 자동 완화 금지.
- fact는 삭제 대신 supersede — 이력 보존.
- `profile_summary`는 게이트 통과·미폐기 fact만 반영.

### 장바구니 관련 불변식

- 수량은 **1~99**, 동일 상품·옵션 합산의 실행 권위는 항상 **Spring**(AI가 합산 계산을 자체 수행하지 않음).
- 담기 전 보유 조회(I-18)가 실패해도 **담기(I-2)는 진행**한다(degrade — 조회는 안내용일 뿐 담기의 전제조건이 아님).
- AI는 커머스 DB에 **직접 write하지 않는다** — 모든 담기 실행·검증은 Spring 위임.
- AI→Spring 전 구간과 동일하게 **3s 타임아웃**(mvp-plan.md §0).

### 개인정보/보안

- 신원은 절대 요청 본문에서 받지 않는다 — JWT `sub` 도출(IDOR 방지). 장바구니 담기·조회 요청의 `userId`/`guestId`도 예외 없이 이 원칙을 따른다.
- 질병·종교·신념 등 민감 정보는 원인을 버리고 파생 취향만 태그화(예: "당뇨" → "무설탕 선호").
- 대화 원문은 로그에 남기지 않음(길이·해시만) — 원문은 대화 저장소 전용, 보존 기간은 config(§9 OPEN-P5, TBD).

---

## 9. 성공 지표

정량 목표치는 대부분 **평가 하니스(골든셋 + 유저 시뮬레이터) 실측 후 확정**하도록 이미 SPEC에 명시돼 있다(문헌이 절대 기준치를 주지 않기 때문). 본 PRD는 측정 프레임을 정의하고, 목표 수치는 TBD로 남긴다.

| 지표 | 측정 방법 | 목표치 |
|---|---|---|
| 검색·재랭크 품질 | ESCI식 골든셋 NDCG@10 / recall@K | TBD — 실측 후 확정(REQ-REC-090) |
| 종단 대화 품질 | 유저 시뮬레이터, ≤5라운드, recall@K | TBD — 실측 후 확정(REQ-REC-091) |
| 개인화 체감 | 동일 질의에 대해 confirmed 프로필 유무 간 재랭킹 결과 차이가 관찰 가능한가(정성 데모 기준) | 데모에서 재현 가능해야 함(UC-3) |
| 콜드스타트 폴백 발생률 | 게스트/신규 세션 중 인기 상품 폴백으로 응답된 비율 | 모니터링 지표로 관찰(목표치 미설정) |
| 프로필 승격 정밀도 | 마이페이지 노출 후 사용자가 "이상하다"고 느끼는 태그 비율(정성, 고도화의 PUT 편집 도입 후 정량화 가능) | 고도화 대상 |
| degrade 발동률 | rerank 실패/출력 검증 실패로 검색 순서 degrade된 요청 비율 | 낮을수록 좋음, 목표치 미설정 — 로깅으로 우선 관찰 |
| 0건 최종 도달률 | 완화 상한(3라운드)까지 시도해도 0건으로 끝나는 질의 비율 | 카탈로그 커버리지와 결합 지표, 목표치 미설정 |
| LLM 호출 상한 준수 | 요청당 LLM 호출 수가 config 상한을 넘지 않는 비율 | 100%(불변식) |
| 담기 성공률 | I-2 호출 대비 `CART_ADDED` 비율 | 목표치 미설정 — 모니터링 지표 |
| 옵션 되물음 회복률 | `CART_OPTION_REQUIRED` 발생 후 재담기 성공까지 이어지는 비율 | 목표치 미설정 |
| 조회 degrade 발생률 | I-18 실패에도 담기가 정상 진행된 비율(= degrade 정책이 실제로 작동하는지) | 100%에 가까워야 함(담기가 조회 실패로 막히면 안 됨) |
| 2차 압축 조인 성공률 | `search` 1차(Spring) 후보 중 catalog DB에서 임베딩을 찾은 `productId` 비율 | 목표치 미설정 — 낮으면 신선도/동기화 문제 신호(§10-A) |

추천 스냅샷(질의·필터·후보·최종 랭킹·근거)은 1일차부터 로깅해 위 지표의 오프라인 재현·튜닝 재료로 남긴다(REQ-REC-095). 카탈로그 자체의 커버리지 지표는 별도 문서에서 관리한다.

---

## 10. 리스크와 오픈 이슈

### (A) 계약 미동기화

- **SPEC-PROFILE-001 §5.4/§6.9 불일치**: `GET /profile/{user_id}` → `GET /profile/me`(IDOR 방지), camelCase, 구매 소스가 "주문 미러 스캔"에서 "질의 시점 `GET /orders/recent` 조회"로 개정 필요(api-spec §7.2).
- **후보 검색 소유 전환**: SPEC-RECOMMEND-001 §5.2/§6.3이 여전히 "AI 자체 pgvector 카탈로그 검색 tool" 서술로 남아 있는데, 실제 계약(v0.5.0)은 Spring 위임이다. 이 부분은 문구 재작성이 아니라 **아키텍처 전제 자체가 바뀐 것**이라 영향 범위가 크다(api-spec §8 항목 4).
- **[2026-07-17 해소] AI 임베딩 ↔ Spring 검색 결합 방식**: 방식2(Spring 검색 우선 → AI가 attribute 매칭 + 임베딩 유사도로 2차 압축)로 **확정**(§6.2). 다만 이 확정이 아직 `SPEC-RECOMMEND-001`(§5.2/§6.3, "카탈로그 검색 tool + pgvector 단일 SQL" 서술)에는 반영 안 된 채로 남아 있다.
- **`productId` 조인 신선도**: Spring의 1차 후보와 catalog DB의 임베딩을 `productId`로 조인하는데, catalog DB 갱신은 pull 배치(I-17, 별도 담당) 주기라 Spring 원본과 시차가 생길 수 있다 — 배치 주기 사이 신규 상품은 Spring 후보엔 있어도 catalog DB엔 임베딩이 없을 수 있다(조인 실패 시 처리 정책 미정, §9 성공지표 "2차 압축 조인 성공률" 참조).
- **`attributes` 응답 구조가 아직 미확정**: 2단계 검색 전체가 Spring의 I-1 응답 `attributes` 필드를 전제로 하는데, 이 필드는 api-spec §4.6에 **"구조 미확정 🔴(C-5)"**로 이미 명시돼 있다 — 즉 2단계 검색은 아직 확정 안 된 필드 위에 설계된 것이다. Spring 협의(C-5/C-15)에서 이 구조가 확정돼야 실제로 attribute 매칭이 성립한다.
- **속성 어휘(vocabulary) 정합성 미보장**: attribute 매칭이 성립하려면 Spring이 반환하는 `attributes` 값과 카탈로그 파이프라인의 속성 사전(모듈B 통제 어휘)이 **같은 값 체계**를 써야 한다 — 예: Spring이 `"방수여부": "Y"`, 속성 사전이 `attributes.방수: true`면 매칭이 조용히 실패한다. 두 값 체계를 누가 통일하는지 어느 SPEC에도 정의돼 있지 않다 — 신규 협의/설계 항목으로 등록 필요(카탈로그 쪽과 공유하는 리스크).

### (B) 프로필 파이프라인 오픈 이슈 (SPEC-PROFILE-001 §9)

MVP는 모두 config 기본값으로 동작 — 아래는 그 기본값의 정밀 튜닝을 실측 후로 미룬 항목(막는 이슈 아님).

| ID | 이슈 |
|---|---|
| OPEN-P2 | EMA α·승격 confidence 임계 정밀값 |
| OPEN-P5 | 대화 보존 기간(`conversation.retention_period`) |
| OPEN-P7 | 3조건 게이트가 strict AND인지 가중 앙상블인지 — 본 PRD/SPEC은 가중 앙상블(명시성 필수 아님)로 가정, 상위 결정 계층 확인 필요 |
| OPEN-P8 | 최근 맥락 섹션의 episodic 하이라이트가 반복성 게이트를 우회하는 경계 |
| OPEN-P9 | checkpointer(session_context) 물리 배치·스키마 소유 경계 |
| OPEN-P10 | 마이페이지 GET의 노출 범위(압축 요약만 vs 전체 위키 조립) |

### (C) 추천 파이프라인 오픈 이슈 (SPEC-RECOMMEND-001 §9)

| ID | 이슈 |
|---|---|
| OPEN-4 | 지연 SLO 절대 수치 미정(부하 테스트 후) |
| OPEN-5 / 13 | Case 3 분해 품질, 멀티턴 reset 오판율 — 정량 기준치는 평가 하니스 실측 후 |
| OPEN-11 | 예산 검증(`verifiedSum`, 인덱스 가격 기준) vs 표시/결제 시점 가격의 순간 괴리 — api-spec §8 항목 2와 동일 이슈 |
| OPEN-12 | 단일 rerank 콜이 "설명하기 쉬운 상품"을 편애하는 편향(Prism) 관측 시 랭킹/근거 2단계 분리 재검토 |

### (D) 외부 의존 리스크

- `/api/chat/lists/{listId}`(CH-5, 인증 불필요)로 경로·메서드·인증 레인은 확정. **I-21 스키마는 "우리(LLM팀)가 확정할 책임"이라고 BE가 명시** — 즉 Spring을 기다리는 게 아니라 우리가 제안해서 확정해야 하는 액션 아이템이다(`listId` 형식·유효시간). `reason`은 I-21 콜백에 포함해 CH-5로 echo하는 방식으로 확정됐다. 반대로 **CH-5 응답 스키마는 FE·Spring 소관으로 명시**돼 우리 리스크에서 빠진다.
- `GET /internal/members/{id}/orders`(I-19) 응답 본문이 BE 기준(상태이름 통일·배송비 항상 0원·숫자 id)으로 재작성됐다 — dedup(REQ-REC-100~103)·프로필 구매 소스가 이 응답을 쓰므로 "이대로 가도 되는지" 우리 쪽 확인이 필요하다(BE가 명시적으로 요청).
- 검색 품질이 Spring DB의 텍스트 검색 능력에 사실상 전적으로 의존한다(AI 벡터 인덱스가 질의 흐름에 아직 안 붙어 있어서) — (A)의 결합 방식 미정과 직결되는 품질 리스크.

### (E) 스코프 결정에 따른 트레이드오프

- MVP는 가중합 스코어링 없이 **LLM 단독 판단으로 재랭킹**한다(이번 PRD에서 확정). 장점은 콜드스타트에서도 즉시 동작(제로샷)한다는 것이고, 트레이드오프는 결정론적 스코어링 대비 재현성·설명 가능성이 낮을 수 있다는 것이다. 결정론적 가중 스코어링(B안)은 실트래픽이 쌓인 뒤 골든셋 비교에서 A안을 이길 때만 전환하는 것으로 이미 설계돼 있다(결정 14-C, `Ranker` 인터페이스로 드롭인 교체 가능).
- 프로필 저장 모델을 원본 아이디어 문서(count 기반 candidate/confirmed 태그 테이블)가 아니라 기존 SPEC-PROFILE-001의 OKF 위키 + EMA 모델로 유지하기로 했다 — 더 정교하지만 구현 복잡도가 더 높다. count 기반 모델은 디버깅·설명이 더 쉽다는 장점이 있었으나, 이번 PRD에서는 채택하지 않는다.

### (F) 장바구니 에이전트 리스크

- **결정 8 개정 필요**: 게스트 장바구니 담기 허용은 기존 "장바구니·구매는 회원 전용" 결정과 상충해 별도 개정 결정 레코드가 필요하다(api-spec §8 항목 7, 아직 미등록 상태로 보임).

카탈로그 데이터 구축 자체의 리스크(규모 갭·임베딩 모델 택1·스냅샷 소유권 이중배정 등)는 별도 담당 범위다.

---

## 부록 — 참조 문서

- [`api-spec.md`](api-spec.md) v0.15.8 — 외부 계약 정본
- [`specs/SPEC-RECOMMEND-001.md`](specs/SPEC-RECOMMEND-001.md) v0.8.0 — 추천 서브그래프 EARS 명세
- [`specs/SPEC-PROFILE-001.md`](specs/SPEC-PROFILE-001.md) v0.2.0 — 프로필 파이프라인 EARS 명세
- [`specs/SPEC-CART-001.md`](specs/SPEC-CART-001.md) v0.2.4 — 장바구니 서브그래프 EARS 명세(2026-07-17 신설, 기획 저장소 정본 등록 전)
- [`app/agents/buyer/cart/__init__.py`](../app/agents/buyer/cart/__init__.py) — 장바구니 스텁 docstring
- [`mvp-plan.md`](mvp-plan.md) / [`mvp-todo.md`](mvp-todo.md) / [`roadmap.md`](roadmap.md)
- 팀 원본 아이디어 문서(노션, 2026-07-11~13): 추천 흐름 v2 / 사용 방식 / 저장 방식
- Notion "📡 API 명세서" DB(팀 공식 API 결정 원장, 2026-07-16 대조 · 2026-07-18 CSV 재대조로 I-21/CH-5 신규 등재 확인) — 엔드포인트별 경로·Method·인증 레인의 최종 소스. `docs/api-spec.md`가 아직 완전히 동기화되지 않은 항목(GET 확정, 목록 push/GET 신규 등재 등)이 있어 본 PRD는 이 DB를 최신 기준으로 §7에 반영함
- BE 팀 공유 문서(2026-07-18) — I-21 스키마 확정 책임이 LLM팀에 있음을 명시, I-20 세션 ID 형식 불일치(UUID vs `S-abc123`) 최초 확인, I-19 응답 재작성 확인 요청, 재고(`stock_quantity`) 도입 및 장바구니 실패 사유 개수 확인 요청 — §7.2/§10-A/§10-D에 반영
