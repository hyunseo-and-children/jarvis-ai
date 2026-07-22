---
id: SPEC-RECOMMEND-001
version: 0.8.0
status: draft
created: 2026-07-07
updated: 2026-07-13
author: navis
priority: high
issue_number: null
---

> ⚠️ **동기화 사본(mirror)** — 정본은 기획 저장소 `.moai/specs/SPEC-RECOMMEND-001/spec.md`.
> 외부 **계약**(SSE 이벤트명·엔드포인트·필드·오류 코드)의 상위 소스는 **api-spec v0.7.0**
> ([docs/api-spec.md](../api-spec.md)) 다 — 본 SPEC과 어긋나면 **api-spec을 따른다**.
> 후속 동기화 개정 목록은 api-spec §7. 동기화: **2026-07-16 (SPEC v0.8.0)**.
> 참고: SSE 페이로드(§5.3)는 CH-2 명명(`token`/`conditions`/`products.ready`)으로 후속 개정 대상(api-spec §7.1).

# SPEC-RECOMMEND-001 — 상품 추천 서브그래프 (Product Recommendation Subgraph)

> 본 SPEC은 product.md Section 12-A **결정 14**(추천 서브그래프 상세 설계)를 직접 입력으로 하여, 구매자 챗봇 그래프(`POST /chat`) 내부의 **상품 추천 서브그래프**를 EARS 요구사항 수준으로 확정한다.
> 결정 14의 노드 구성·Case 판별·0건 폴백·멀티턴 병합·재랭킹 규모·SSE 이벤트 타입은 **구속 제약(binding)** 이며 본 SPEC에서 재논의하지 않는다. 본 SPEC은 그 위에 State 스키마 필드명, 검색 tool I/O 스키마, SSE 페이로드 필드 스키마, 오류 처리, 인수 기준을 확정한다.
> v0.2.0은 추가로 **결정 9-A**(가격·재고 컬럼을 검색 인덱스에 유지, 경량 동기화)와 **결정 14-A**(총액 예산 처리)를 구속 제약으로 반영한다.
> v0.4.0은 추가로 **결정 14-C**(랭킹 전략 단계적 도입 — MVP=A LLM 리랭크 / B 결정론 스코어링=고도화 실험, 교체 인터페이스·스냅샷 로깅)와 **결정 14-D**(0건 완화 재설계 — config 상한 라운드, 명시/비명시 태깅 기반 최소 이탈 자동 + 명시 제약 제안 칩)를 구속 제약으로 반영한다.
> v0.5.0은 추가로 **결정 14-E**(Case 3 다중 니즈 랭킹·선택 전략 — 니즈 수 무제한, 니즈당 후보·노출을 니즈 수에 반비례로 축소하여 총 rerank 입력을 config 예산으로 고정, 랭킹·선택 3방식[코드 per-item·LLM 묶음 병렬·단일 콜] config 선택[MVP 기본=코드 per-item], LLM 호출 상한 "2회"→config화, 극단 니즈 수 시 essential 우선)를 구속 제약으로 반영한다.
> v0.6.0은 추가로 **결정 14-F**(구매 이력 기반 추천 제외 — `search` 단계 필터로 Case 1/2·3 공통 적용, exact `product_id` 항상 제외 + 소모품 카테고리 억제[MVP는 단순판=소모품 boolean 플래그], non-blocking 되돌리기 제안 칩[결정 14-D `suggestions` 재사용], 게스트 스킵[결정 8], 정교한 재구매 주기·다양성 모델은 고도화 유예)를 구속 제약으로 반영한다.
> v0.7.0은 추가로 **결정 14-G**(멀티턴 주제 전환 초기화 — 기존 add/replace에 **reset** 전이 추가, `decompose`가 판단[LLM 추가 호출 없음], 범용 명시 제약[가격 등 `source: user`]은 config `multiturn.carry_on_reset`[기본 `["price"]`]로 캐리, "~도/그리고 + 둘 다 상품"은 reset이 아니라 Case 3 다중 니즈로 승격, 정교한 부분 캐리·병렬 승격 판별은 고도화 유예)를 구속 제약으로 반영한다.
> v0.8.0은 추가로 **결정 14-H**(Case 3 니즈 우선순위 3단계 — `essential` boolean을 `priority`[1 필수/2 권장/3 선택]로 개정, 판정 기준 "이게 빠지면 그 상황/요리가 성립하는가"는 decompose 프롬프트, 활용은 노출 순서·예산 배분·예산 부족 시 제거 순서[선택→권장, 필수 최후], 하드 절단 금지 불변)를 구속 제약으로 반영한다.

## HISTORY

- **v0.8.0 (2026-07-13)** — 결정 14-H(Case 3 니즈 우선순위 3단계) 반영. `ShoppingItem.essential`(boolean 2단계)을 **`priority`(1=필수/2=권장/3=선택) 3단계**로 개정 — 판정은 `decompose`가 프롬프트 기준(**"이게 빠지면 그 상황/요리가 성립하는가"**: 1 없으면 목적 불성립[감자탕에 등뼈] / 2 없으면 아쉽지만 목적 달성[들깨가루] / 3 있으면 더 좋은 정도[청양고추])으로 태깅하며 LLM 추가 호출 없음. 활용 3곳 — ① 극단 니즈 수 시 노출 순서(기존 essential 역할 승계) ② 총액 예산 배분 ③ 예산 부족·대안 소진 시 제거 순서(priority 3 선택부터, 1 필수는 최후·투명 안내). REQ-REC-004/098 개정, REQ-REC-075/076에 priority 제거 순서 명시, §5.1 `ShoppingItem.priority` 스키마 교체, AC-REC-28 갱신, §4 결정 14-H 행 추가. priority는 절단 근거가 아니라 노출·제거 **순서** 신호(하드 절단 금지 REQ-REC-098 불변). 아울러 **결정 17**(상품 질문 흐름의 부정 리뷰 답변 시 추천 서브그래프 재호출) 연계로 §1.3 의존성 행·OPEN-15 신설 — 진입 계약 상세는 상품 질문 SPEC 소관이며 본 서브그래프 내부 변경 없음. 파이프라인 골격·decompose 1회·LLM 콜 상한 불변. 출처: 팀 "추천 흐름 v2" 문서(2026-07-13) 검토.
- **v0.7.1 (2026-07-10)** — 결정 16 / SPEC-PROFILE-001 반영으로 **OPEN-9(`profile_summary` 계약) 해소** 표기. `profile_summary` 계약(포맷/최대 크기/필드 규약)의 소유·정의가 SPEC-PROFILE-001로 이관됨 — 하이브리드 단일 마크다운 문자열, 문자 기반 1,000자 config 상한. 본 추천 서브그래프는 계약 변경 없이 여전히 이를 **read-only 불투명 문자열**로만 소비하며(REQ-REC-005/006 불변, State `profile_summary: str | None` 무개정), §9 OPEN-9 라인에 해소 주석만 추가한다. 그 외 본 파일의 요구사항·스키마·인수 기준은 일절 변경하지 않는다.
- **v0.7.0 (2026-07-10)** — 결정 14-G(멀티턴 주제 전환 초기화, reset) 반영. 멀티턴 병합에 기존 add/replace에 더해 **reset(주제 전환)** 전이를 추가 — 후속 질의의 카테고리/상품이 직전과 무관하면 `decompose`가 직전 주제 필터를 폐기하고 새 주제로 재시작한다. 판단은 **decompose 안에서**(LLM 추가 호출 없음, 결정 14 원칙). reset 시 **범용 명시 제약(`source == user`, config `multiturn.carry_on_reset` 대상, 기본 `["price"]`)은 캐리**하고 카테고리·상품별 속성은 폐기. "~도/그리고" 병렬 신호 + **둘 다 상품**이면 reset이 아니라 **Case 3 다중 니즈로 승격**. §6.7에 REQ-REC-054~056 신설(REQ-REC-050~053 불변), §2 EX-10 신설, §4 결정 14-G 참조 행 추가, AC-REC-35~37 추가. **OPEN-8 부분 해소**(reset 도입으로 주제 전환 누적 경계 해소) + 신규 고민 항목 OPEN-13(reset 오판율)·OPEN-14(캐리 경계) 추가. 정교한 **부분 캐리 규칙·병렬 승격의 신뢰성 판별**은 고도화 범위로 EX-10에 명시(MVP 비구현). 파이프라인 골격·decompose 1회 불변. 이전 HISTORY(v0.6.0 이하) 및 §6.8/§6.14 불변.
- **v0.6.0 (2026-07-10)** — 결정 14-F(구매 이력 기반 추천 제외, dedup) 반영. 최근 구매 상품을 `search` 단계 필터로 제외하며 Case 1/2(단일)·Case 3(니즈별) 공통. **두 층위 제외** — (1) **exact `product_id` 항상 제외**(안전), (2) **소모품 카테고리 억제**(재구매 성향 의존 — MVP는 소모품 boolean 플래그 단순판, 다양성 상품은 exact만 제외·비슷한 상품 계속 추천). 억제는 **non-blocking** — 조용한 누락 금지, 나머지 추천은 유지하며 억제 니즈는 **되돌리기 제안 칩**(`suggestions`, 결정 14-D 재사용)으로 재포함 가능하게 제시. 게스트는 구매 이력 없어 스킵(결정 8). 카테고리 억제 판단은 **카테고리 재구매 메타**(결정 15 카테고리 속성 사전에 신규 정의, MVP는 소모품 boolean)에 의존하며, 정교한 재구매 주기·다양성 성향 모델은 **고도화 범위(MVP 비구현)**. §6.14 신설(REQ-REC-100~103), §2 EX-9 신설, §4 결정 14-F 참조 행·§1.3 의존성(구매 이력 read·카테고리 재구매 메타) 추가, AC-REC-32~34 추가. 파이프라인 골격·decompose 1회 불변. §6.8 REQ-REC-073/077 및 v0.5.1 HISTORY 불변.
- **v0.5.1 (2026-07-09)** — 결정 14-A ↔ 14-E 정합 보강. (1) REQ-REC-073 일반화: 총액 예산 묶음은 multiple-choice knapsack(니즈당 대안 중 1개 선택)이며 제안 주체는 랭킹·선택 전략(방식1 코드 / 방식2·3 LLM)을 따르되 합산·보정은 항상 코드. (2) REQ-REC-077 신설: total_budget 모드는 니즈당 노출 수(반비례 축소)와 별개로 보정 루프용 대안 후보 풀을 config 최소 수 이상 유지(노출 1개여도 저가 대안 확보).
- **v0.5.0 (2026-07-09)** — 결정 14-E(Case 3 다중 니즈 랭킹·선택 전략) 반영. (1) Case 3 니즈 수 **무제한** — shopping_list에 하드 캡을 두지 않음(레시피 재료 전부 커버), essential 태깅 유지(§6.1 REQ-REC-004 보강). (2) 니즈당 후보·노출을 **니즈 수에 반비례**로 축소해 총 rerank 입력을 config 예산으로 고정 — Case 3 니즈당 `top_k`는 고정 30이 아니라 반비례 config 입력 예산으로 산정(§6.3 REQ-REC-012 보강, §6.4 REQ-REC-096 신설). (3) 랭킹·선택 **3방식 config 선택** — 코드 per-item 결정론 선택 + LLM 전체 코멘트 1회(**MVP 기본**) / LLM 묶음 병렬(config 상한 예 3~4콜) / 단일 콜(LLM4Rerank arXiv:2406.12433 다니즈 품질 저하). REQ-REC-023 개정(경직된 "정확히 단일 콜/fan-out 절대 금지" → config 상한 표현), REQ-REC-097 신설. (4) 극단적 니즈 수 시 essential 우선 노출·하드 절단 금지(REQ-REC-098 신설). (5) LLM 호출 상한 "정확히 2회" → **config 상한(기본 2, Case 3 묶음 병렬 시 예: 최대 4)**으로 완화(decompose 1회는 유지; 완화·per-item 검색·코드 선택은 LLM 아님) — §비기능 갱신. AC-REC-28~31 추가. MVP 기본=방식1이며 방식2/3은 config 옵션, 승자는 골든셋/시뮬레이터(§6.12)로 측정 후 확정. 파이프라인 골격·decompose 1회 불변.
- **v0.4.0 (2026-07-09)** — 결정 14-C(랭킹 전략 A/B 단계적 도입) + 결정 14-D(0건 완화 재설계) 반영. (1) §6.6 완화 개정 — "정확히 1회" → **config 주입 최대 라운드(기본 3)**, brand→rating→price **고정 순서 폐기** → 비명시·약한 조건 최소 완화 자동 + 명시 제약(특히 가격)은 자동 완화 금지·**제안 칩**으로 위임(REQ-REC-040/041 개정, REQ-REC-045/046 신설). (2) decompose가 각 필터 조건을 **명시(user 발화) vs 비명시(프로필/기본값)** 로 태깅 → §6.1 REQ-REC-047 신설, `FilterSet.sources` 추가. (3) 수치 제약 완화는 뭉뚱그린 비율이 아니라 **결과가 나오는 최소 초과분** 계산(REQ-REC-045), 명시 제약 완화는 예상 결과 수(COUNT) 붙인 제안 칩·0건 칩 배제(REQ-REC-046). (4) SSE `products` 페이로드에 `suggestions` 필드 추가(§5.3). (5) 랭킹 전략 — §6.13 신설: 랭킹은 교체 가능한 `Ranker` 인터페이스(`rank.strategy` config), MVP=A(LLM 리랭크) / B(결정론 스코어링)=고도화 실험(전환은 골든셋 비교 게이트), 추천 스냅샷 로깅 1일차부터(REQ-REC-094/095). AC-REC-23~27 추가, AC-REC-06/07 개정. EX-8 개정, 불변식 "완화 1회 한정" → "config 상한 내 + 명시 제약 무단 위반 금지 + 매 완화 알림". **OPEN-2 해소**(고정 순서 폐기 → 최소 이탈 자동 + 명시 제약 제안 칩). LLM 호출 수 상한 최대 2회 불변(완화는 SQL 재검색·칩 COUNT는 코드).
- **v0.3.0 (2026-07-08)** — 결정 14-B(추천 서브그래프 견고성 보강) 반영. 문헌 검증(취향 반영 추천 26편, 적대적 3표) 기반 견고성 보강 — (1) 리랭크 출력 결정적 검증(후보 ID 집합 대조 + 근거 속성 대조) → §6.10 신설(REQ-REC-081/082), (2) 후보 순서 무작위화(config 주입 셔플) → REQ-REC-080, (3) degrade 트리거 확장(출력 검증 실패 포함) → REQ-REC-083(REQ-REC-062 보강), (4) 모호 속성 semantic_query 라우팅 → §6.11 신설(REQ-REC-084, REQ-REC-003 정련), (5) 평가 2계층(ESCI식 골든셋 + 유저 시뮬레이터 종단, ≤5라운드) + 누출 방지 → §6.12 신설(REQ-REC-090~092), (6) 프로필 상류 디노이징 재확인(결정 4-A). 선택 명확화 경로 REQ-REC-085(실험 플래그, MVP 비범위 가능) 추가. State/SSE 스키마에 셔플·출력 검증 상태 최소 확장, AC-REC-18~22 추가. **OPEN-3/OPEN-6 해소**(degrade 정책 유지 + 출력 검증 실패 트리거 추가 / 프롬프트 제약 + 결정적 사후 속성 대조 2중 가드). LLM 호출 수 상한 최대 2회 불변(순서 무작위화·출력 검증은 코드).
- **v0.2.0 (2026-07-07)** — 결정 9-A/14-A 반영. (1) OPEN-1 해소: 가격·재고를 Layer 1 컬럼으로 인덱스에 유지(경량 UPDATE 동기화), "제외"는 화면 표시값의 응답 시점 원본 조회로 한정 재해석(결정 9-A) → REQ-REC-013/014/015, Candidate/§5.2 스키마 갱신. (2) 총액 예산 처리 신설: `price_scope`(per_item/total_budget) + LLM 제안·코드 검증 묶음 조합 + 결정론적 보정 루프(결정 14-A) → §6.8 신설(REQ-REC-070~076), State/스키마 확장, AC-REC-14~17 추가.
- **v0.1.0 (2026-07-07)** — 최초 작성. 결정 14를 서브그래프 EARS 명세로 구체화. State/검색 tool/SSE 페이로드 스키마 초안 확정. 가격 필터 vs 휘발성 필드 제외(결정 3 ↔ 결정 9) 긴장은 미해결 항목으로 명시.

---

## 1. 개요 & 범위 (Overview & Scope)

### 1.1 목적

사용자의 자연어 질의를 구조화 필터 + 시맨틱 쿼리로 분해하고, 카탈로그 검색 tool로 후보를 조회한 뒤, 사용자 프로필로 재랭킹하여 개인화된 상품 추천을 SSE 스트리밍으로 응답하는 **추천 서브그래프**의 동작을 정의한다. 서브그래프 노드는 `decompose`(Haiku 4.5) → `search`(카탈로그 검색 tool) → `rerank`(Sonnet 5) → `respond`의 선형 흐름에 0건 폴백·멀티턴 재분해 conditional edge를 더한 구조다.

### 1.2 In Scope (본 SPEC이 확정하는 것)

- 추천 서브그래프 4개 노드(`decompose`/`search`/`rerank`/`respond`)의 관찰 가능한 동작(behavior) 계약.
- Case 1/2/3 분기(decompose 출력에서 파생), Case 3 쇼핑리스트 분해 + 카테고리별 묶음 추천.
- 0건 자동 완화(1회) + 투명 안내, 멀티턴 필터 병합(add/replace) 동작 요구사항.
- 서브그래프 State 필드 스키마(필드명/타입/설명).
- 카탈로그 검색 tool의 입력/출력 스키마(Pydantic 수준 계약).
- SSE 이벤트(`text.delta`/`products`/`done`/`error`)의 상세 페이로드 필드 스키마.
- 노드별 지연 예산 가이드라인, 토큰/비용 가드레일(하드 시간 추정 없음), 오류 처리, 인수 기준.

### 1.3 의존성 (Dependencies — 본 SPEC 외부, 참조만)

이 서브그래프는 아래 컴포넌트가 제공하는 계약에 의존하나, 그 구현은 본 SPEC의 범위가 아니다.

| 의존 대상 | 제공하는 것 | 소유 |
|---|---|---|
| Intent router (구매자 그래프) | 추천 서브그래프로의 진입 결정 | 구매자 그래프 SPEC(별도) |
| 프로필 리더 (`agents/profile/reader.py`) | 진입 시 주입되는 `profile_summary` (결정 4/4-A) | 프로필 파이프라인 SPEC(별도) |
| Enrichment/Embedding 파이프라인 | Layer 2 태그, Layer 3 `search_doc` 임베딩 (결정 3/6) | 파이프라인 SPEC(별도) |
| 카탈로그 동기화 (결정 9 / 9-A / 9-B) | 인덱스 신선도, 가격·재고 컬럼의 경량 UPDATE 동기화(9-A), Spring MySQL 원본 → AI Postgres+pgvector 인덱스로의 쓰기 시점 이벤트 동기화·필터 컬럼 미러(9-B) | 카탈로그 이벤트 SPEC(별도) |
| 최근 구매 이력 (Spring 주문 데이터, 결정 14-F) | 사용자별 최근 구매 상품 목록(exact `product_id` + 카테고리) — read-only 조회. 프로필 요약과 별개의 원시 구매 이력 | Spring 주문/카탈로그 이벤트 SPEC(별도) |
| 카테고리 재구매 메타 (결정 15 / 14-F) | 카테고리별 재구매 성향 메타데이터(MVP는 소모품 boolean 플래그, 고도화는 재구매 주기·다양성 성향) — 카테고리 억제 판단 근거 | 카탈로그 시드/속성 사전 SPEC(별도) |
| `search_service.py` 내부 SQL/pgvector 튜닝 | 단일 SQL(WHERE + 벡터 유사도) 실행 | 본 SPEC은 tool **계약**만 정의, HNSW·거리 파라미터 튜닝은 위임 |
| 상품 질문 흐름 (결정 17) | 부정 리뷰 답변 시 본 서브그래프 **재호출**(카테고리 승계 + 질문 속성의 `semantic_query` 우대 신호로 재진입) — 진입 계약·부정 판정 기준은 상품 질문 SPEC 소관(OPEN-15) | 상품 질문 흐름 SPEC(별도) |

---

## 2. Exclusions (What NOT to Build)

[HARD] 본 서브그래프에서 **구현하지 않는** 항목을 명시한다.

- **EX-1 Intent 라우팅**: "이 질의가 추천인지" 판별하는 intent router는 구매자 그래프 상위 계층 소관. 서브그래프는 이미 추천으로 라우팅된 요청만 받는다.
- **EX-2 장바구니 담기**: "담아줘"는 별도 cart 서브그래프(결정 7, Spring 장바구니 API 경유). 본 서브그래프는 추천까지만.
- **EX-3 프로필 write/갱신**: `profile_summary`는 read-only로 주입만 받는다. 프로필 델타 생성·승격 게이트·세션 finalization 병합(결정 4-A)은 전적으로 프로필 파이프라인 소관.
- **EX-4 무관한 질문 폴백**: 감자탕 레시피 등 비추천 질의는 fallback 서브그래프 소관.
- **EX-5 화면 표시용 가격/재고 확정**: 가격·재고 컬럼은 검색 필터·예산 계산용으로 인덱스에 유지되나(결정 9-A), **화면 표시값**의 최신 확정은 응답 시점 원본 조회(소비자 Spring/FE 오버레이) 소관이다. 본 서브그래프의 `products` 페이로드는 안정 식별자 + 검색 메타데이터만 반환하고 표시용 가격/재고를 실어 보내지 않는다.
- **EX-6 카탈로그 색인/임베딩 생성**: Layer 2 태그 추출·`search_doc` 임베딩은 오프라인 파이프라인 소관(결정 3). 서브그래프는 이미 색인된 데이터를 질의만 한다.
- **EX-7 별도 분류 LLM 호출**: Case 판별은 `decompose` 출력에서 파생하며, 전용 classification 호출을 두지 않는다(결정 14).
- **EX-8 무제한 조건 완화**: 0건 폴백은 config 상한 내 완화(기본 3라운드)로 제한하며(결정 14-D), 명시 제약(특히 가격)은 사용자 동의(제안 칩) 없이 무단 위반하지 않는다. 상한 초과 반복 완화·명시 제약 무단 위반 완화는 명시적 비범위.
- **EX-9 정교한 재구매 주기·다양성 성향 모델**: 구매 이력 제외(결정 14-F)의 카테고리 억제 판단은 MVP에서 **소모품 boolean 플래그**(카테고리 재구매 메타, 결정 15)로만 수행한다. 정교한 재구매 주기 계산, 주기성 상품(화장품·영양제) 주기 내 억제, 옷류 variety-seeking 처리 등 **정교한 재구매/다양성 모델은 MVP 비범위(고도화)** — 본 서브그래프에서 구현하지 않는다(REQ-REC-103). 또한 구매 이력 원천 조회(Spring 주문 데이터)의 소유·계약은 카탈로그/주문 이벤트 SPEC 소관이며 본 서브그래프는 read-only로 소비만 한다.
- **EX-10 정교한 부분 캐리 규칙·병렬 승격의 신뢰성 판별**: 멀티턴 reset(결정 14-G)의 MVP는 **범용 명시 제약(config `multiturn.carry_on_reset`, 기본 `["price"]`)만 캐리**하는 단순판이다. 가격 외에 브랜드·용도 등 어디까지 "범용"으로 유지할지 결정하는 **정교한 부분 캐리 규칙**, "~도/그리고 + 둘 다 상품"의 Case 3 승격을 **신뢰성 있게 판별**하는 정교한 로직, 주제 전환 감지 정확도 향상 학습은 **MVP 비범위(고도화)** — 본 서브그래프에서 구현하지 않는다(REQ-REC-055/056은 단순 신호 기준). reset·캐리·승격 판단은 전적으로 `decompose` 자연어 이해에 흡수되며 별도 분류 호출을 두지 않는다(EX-7 연계).

---

## 3. 용어 (Glossary)

| 용어 | 정의 |
|---|---|
| `decompose` | Haiku 4.5로 자연어 질의를 구조화 필터 + 시맨틱 쿼리로 분해하고 Case를 파생하는 노드 |
| `search` | 구조화 필터(WHERE) + pgvector 유사도 정렬을 단일 SQL로 결합해 후보를 조회하는 노드/tool |
| `rerank` | Sonnet 5로 후보를 프로필 기반 재랭킹하고 근거(rationale)를 생성하는 노드 |
| `respond` | 재랭킹 결과를 SSE로 스트리밍 응답하는 노드 |
| `filters` | 누적 구조화 필터 집합(가격/카테고리/브랜드/평점 + Layer 2 속성). 멀티턴에 걸쳐 누적 |
| `semantic_query` | 정형 제약을 제외한, 벡터 유사도 검색용 자연어 쿼리 |
| Case 1 / 2 / 3 | 상품명 기반 / 구조화 필터 기반 / 상황 기반 질의 |
| `profile_summary` | 그래프 진입 시 주입되는 압축 취향 요약(결정 4). 비회원은 `None` |
| 쇼핑리스트(shopping_list) | Case 3에서 상황("유럽여행")을 필요 아이템 목록으로 분해한 결과 |
| 완화(relaxation) | 0건일 때 덜 중요한 필터부터 1단계 넓혀 재검색하는 폴백 |
| 가격·재고 컬럼 | 자주 바뀌는 필드지만 검색 필터·예산 계산용으로 인덱스에 유지(경량 UPDATE 동기화, 결정 9-A). 단, 화면 **표시값**은 응답 시점 원본 조회 |
| `price_scope` | 가격 제약의 적용 범위 — `per_item`(각 상품 상한) vs `total_budget`(묶음 총액 상한). decompose가 판별(결정 14-A) |
| 예산 보정 루프(repair loop) | 총액 예산 초과 시 가장 비싼 아이템을 저가 대안으로 교체하는 결정론적(코드) 루프. 상한 횟수 존재(결정 14-A) |

---

## 4. 관련 결정 참조 (Related Decisions)

본 SPEC은 아래 확정 결정을 구속 제약으로 상속한다(product.md Section 12-A).

| 결정 | 내용 | 본 SPEC 반영 |
|---|---|---|
| 결정 1 | 구매자 단일 그래프, `POST /chat` SSE, intent router 후 서브그래프 분기 | 서브그래프 경계·진입 조건 |
| 결정 2 | 검색 tool + agent 조합 | 노드 구성의 근간 |
| 결정 3 | 3계층 메타데이터 + 질의 시점 단일 SQL(WHERE + pgvector), 정확 제약은 항상 WHERE | REQ-REC-010/014 |
| 결정 4 / 4-A | 프로필 read-only 주입, 재랭킹 시 속성 검증 가드 | REQ-REC-005/022 |
| 결정 5 | Haiku 4.5(분해) + Sonnet 5(재랭킹), 캐시 입력 ITPM 미차감, Sonnet 토크나이저 ~30%↑ | 노드-모델 매핑, 비용 가드 |
| 결정 6 | 한국어 임베딩 셀프호스트 1024차원 | `semantic_query` 임베딩 경로(의존) |
| 결정 8 | 비회원 검색/추천 허용, 개인화 없음, AI 서버 무상태 | REQ-REC-006 |
| 결정 9 | 이벤트 기반 준실시간 동기화, 원본 = Spring / 인덱스 = AI 서버 | REQ-REC-013/032 |
| 결정 9-A | 가격·재고를 Layer 1 컬럼으로 인덱스 유지(경량 UPDATE), "제외"는 표시값 원본 조회로 한정 재해석 | REQ-REC-013/014/015, §5.2 (OPEN-1 해소) |
| 결정 9-B | AI 검색 인덱스 DB — Spring MySQL 원본 / AI Postgres+pgvector, 필터 컬럼 미러 + 쓰기 시점 동기화 | §1.3 의존성 |
| 결정 11 | `/chat` SSE 정식 확정, 이벤트 스키마는 SPEC에서 정의 | §5.3 SSE 스키마 |
| 결정 14 | 추천 서브그래프 상세 설계(노드/Case/폴백/멀티턴/규모/SSE 타입) | 본 SPEC 전체 |
| 결정 14-A | 총액 예산 처리 — `price_scope` 구분 + 코드 검증 묶음 + 보정 루프 (14-E 정합: knapsack·대안 풀) | §6.8, REQ-REC-070~077 |
| 결정 14-B | 추천 서브그래프 견고성 보강 — 리랭크 출력 결정적 검증(후보 ID·근거 속성 대조), 후보 순서 무작위화, degrade 트리거 확장, 모호 속성 semantic_query 라우팅, 평가 2계층 + 누출 방지, 프로필 상류 디노이징 재확인 | §6.10/6.11/6.12, REQ-REC-080~085/090~092 |
| 결정 14-C | 랭킹 방식 단계적 도입 — MVP=A(LLM 리랭크, 본 SPEC 현행) / B(결정론 스코어링)=고도화 실험, 교체 가능한 `Ranker` 인터페이스(`rank.strategy` config), 전환은 골든셋 비교 게이트, 추천 스냅샷 로깅 | §6.13, REQ-REC-094/095 |
| 결정 14-D | 0건 완화 재설계 — config 상한 라운드(기본 3), 고정 순서 폐기 → 비명시·약한 조건 최소 완화 자동 + 명시 제약 제안 칩(예상 결과 수 포함), 수치 최소 초과분 완화, decompose 명시/비명시 태깅 | §6.1/6.6, REQ-REC-040/041 개정·045/046/047 |
| 결정 14-E | Case 3 다중 니즈 랭킹·선택 전략 — 니즈 수 무제한, 니즈당 후보·노출을 니즈 수에 반비례 축소(총 rerank 입력 config 예산 고정), 랭킹·선택 3방식(코드 per-item[MVP 기본]/LLM 묶음 병렬/단일 콜) config 선택, LLM 호출 상한 config화(기본 2, 묶음 병렬 시 예 최대 4), 극단 니즈 수 시 essential 우선(→결정 14-H로 priority 3단계 개정) | §6.1/6.3/6.4, REQ-REC-004/012 보강·023 개정·096~098 신설, §비기능 |
| 결정 14-F | 구매 이력 기반 추천 제외(dedup) — `search` 단계 필터로 Case 1/2·3 공통, exact `product_id` 항상 제외 + 소모품 카테고리 억제(MVP=소모품 boolean 단순판, 다양성 상품은 exact만), non-blocking 되돌리기 제안 칩(결정 14-D `suggestions` 재사용), 게스트 스킵(결정 8), 정교한 재구매/다양성 모델은 고도화 유예 | §6.3/6.14, REQ-REC-100~103 신설, §2 EX-9 |
| 결정 14-G | 멀티턴 주제 전환 초기화(reset) — 기존 add/replace에 reset 전이 추가, `decompose`가 판단(LLM 추가 호출 없음), 범용 명시 제약(가격 등 `source == user`)은 config `multiturn.carry_on_reset`(기본 `["price"]`)로 캐리·카테고리/상품별 속성은 폐기, "~도+둘 다 상품"은 Case 3 승격, 정교한 부분 캐리·병렬 승격 판별은 고도화 유예 | §6.7, REQ-REC-054~056 신설, §2 EX-10, OPEN-8 부분 해소 |
| 결정 14-H | Case 3 니즈 우선순위 3단계 — `essential`(boolean) → `priority`(1 필수/2 권장/3 선택), decompose 프롬프트 판정("이게 빠지면 그 상황/요리가 성립하는가"), 활용: 노출 순서·예산 배분·제거 순서(선택→권장, 필수 최후·투명 안내), 하드 절단 금지 불변 | §5.1/6.1/6.4/6.8, REQ-REC-004/098 개정·075/076 보강, AC-REC-28 |
| 결정 17 | 상품 질문 흐름의 부정 리뷰 답변 시 추천 서브그래프 재호출(대안 추천 전환) — 본 SPEC엔 진입 경로(의존성)만 추가, 계약 상세는 상품 질문 SPEC 소관 | §1.3, OPEN-15 |

---

## 5. 인터페이스 정의 (Interface Definitions)

### 5.1 서브그래프 State 스키마

LangGraph State 채널. `profile_summary`는 그래프 진입 시 주입되고 서브그래프 내부에서 read-only다. `filters`는 thread checkpointer(session_context)를 통해 멀티턴에 걸쳐 누적된다.

| 필드명 | 타입 | 설명 | 갱신 노드 |
|---|---|---|---|
| `query` | `str` | 현재 턴의 사용자 원문 질의 | (입력) |
| `filters` | `FilterSet` | 누적 구조화 필터. 멀티턴 병합 결과 반영 | `decompose` |
| `semantic_query` | `str` | 벡터 유사도 검색용 자연어 쿼리(정형 제약 제외) | `decompose` |
| `case` | `Literal[1, 2, 3]` | decompose 출력에서 파생된 질의 유형 | `decompose` |
| `shopping_list` | `list[ShoppingItem] \| None` | Case 3에서 상황을 분해한 아이템 목록. Case 1/2는 `None` | `decompose` |
| `candidates` | `list[Candidate]` | 검색 tool이 반환한 후보(그룹 없는 평면 목록) | `search` |
| `ranked` | `list[RankedItem]` | 재랭킹된 최종 노출 후보 + 근거 | `rerank` |
| `overall_comment` | `str` | 전체 추천 코멘트(1개) | `rerank` |
| `profile_summary` | `str \| None` | 진입 시 주입되는 압축 취향 요약. 비회원 `None`. **read-only** | (주입) |
| `relaxation` | `RelaxationState \| None` | 0건 완화 적용 여부/내용. 미적용 시 `None` | `search`(폴백 경로) |
| `bundle` | `BundleState \| None` | Case 3 + `total_budget`일 때 코드 검증된 묶음(합산·보정 결과). 그 외 `None` | `rerank`+코드 검증 |
| `is_guest` | `bool` | user_id 부재 여부(개인화 스킵 플래그, 결정 8) | (주입) |
| `rerank_validation` | `RerankValidation \| None` | rerank 출력 결정적 검증 결과(후보 외 제거 id·속성 대조 실패 플래그). 미검증 시 `None`(결정 14-B) | `rerank`+코드 검증 |

`filters.price_scope`가 `total_budget`이고 `case == 3`일 때만 `bundle` 채널이 사용된다(결정 14-A).

`candidates`는 `rerank` 프롬프트 투입 직전 config 주입 셔플로 순서가 무작위화된다(유사도순 나열 금지, REQ-REC-080). 셔플은 State에 새 채널을 추가하지 않고 `search`→`rerank` 경계의 코드 단계로 수행하며, `rerank` 출력은 항상 검증 후 노출된다(REQ-REC-081/082).

보조 타입:

```python
class FilterSet(BaseModel):
    category: str | None = None        # 계층형 카테고리 경로 (예: "여행/보안용품")
    price_min: int | None = None
    price_max: int | None = None       # 정확 가격 상한 — 항상 WHERE 필터 (인덱스 price 컬럼 기준, 결정 9-A)
    price_scope: Literal["per_item", "total_budget"] = "per_item"  # 가격 제약 범위 (결정 14-A)
    total_budget: int | None = None    # price_scope == total_budget일 때 묶음 총액 상한
    brand: list[str] | None = None
    rating_min: float | None = None
    in_stock_only: bool = False        # 품절 제외 필터 (인덱스 stock 컬럼 기준, 결정 9-A)
    attributes: dict[str, Any] = {}     # Layer 2 JSONB 속성 필터 (예: {"방수": true})
    # 조건별 출처 태깅 (결정 14-D, REQ-REC-047): 필드명 → "user"(발화 명시) | "derived"(프로필/기본값).
    # 0건 완화 우선순위 판단 근거 — derived는 최소 자동 완화, user(특히 price_*)는 제안 칩으로 위임.
    sources: dict[str, Literal["user", "derived"]] = {}

class ShoppingItem(BaseModel):
    label: str                          # 아이템명 (예: "여행용 자물쇠")
    category_hint: str | None = None    # 추정 카테고리
    filters: FilterSet                  # 아이템별 서브 필터
    semantic_query: str
    priority: int = 2                   # 1=필수, 2=권장, 3=선택 (결정 14-A/14-H) — 노출 순서·예산 배분·제거 순서에 활용. 판정: "이게 빠지면 그 상황/요리가 성립하는가"

class RelaxationState(BaseModel):
    applied: bool                       # 완화 적용 여부
    relaxed_filter: Literal["brand", "rating", "price"] | None
    notice: str                         # 사용자 안내 문구 (예: "조건을 조금 넓혔어요")

# Case 3 + total_budget 전용 — LLM 제안을 코드가 검증한 최종 묶음 (결정 14-A)
class BundleState(BaseModel):
    items: list[BundleItem]             # 아이템당 1개 선택 상품
    verified_sum: int                   # 코드가 index price로 결정론적 합산한 값 (LLM 산수 아님)
    total_budget: int
    within_budget: bool                 # verified_sum <= total_budget
    repair_iterations: int = 0          # 보정 루프 실행 횟수 (상한 존재)
    dropped_items: list[str] = []       # 예산 초과로 제외된 아이템 label (투명 안내 대상)
    feasibility_notice: str | None = None  # 부분 충족 안내 문구 (불가능 시)

class BundleItem(BaseModel):
    item_label: str                     # 대응 ShoppingItem.label
    product_id: str
    index_price: int                    # 합산 검증에 사용한 인덱스 시점 가격 (표시값 아님)

# rerank 출력 결정적 검증 결과 (결정 14-B) — LLM 아닌 코드 산출
class RerankValidation(BaseModel):
    dropped_out_of_candidate_ids: list[str] = []  # 후보 집합에 없어 제거된 product_id (REQ-REC-081)
    attr_mismatch: bool = False                    # 근거 속성 대조 실패 존재 여부 (REQ-REC-082)
    degraded: bool = False                         # 검증 실패로 검색-순서 degrade 발동 여부 (REQ-REC-083)
```

### 5.2 카탈로그 검색 tool 입력/출력 스키마

Case 1/2는 tool을 1회, Case 3는 아이템별로 병렬 호출한다. 결정 9-A에 따라 `Candidate`는 인덱스 시점 `price`/`stock`을 포함하며, 이 값은 **필터·예산 계산용으로만** 사용된다. **화면 표시용 최신 가격/재고는 소비자(Spring/FE)가 응답 시점 원본에서 조회**하며, `products` 페이로드에는 표시용 가격/재고를 싣지 않는다(EX-5, §5.3 참조).

```python
class SearchToolInput(BaseModel):
    filters: FilterSet                  # WHERE 절로 변환될 정형 제약 (price/stock 컬럼 포함, 결정 9-A)
    semantic_query: str                 # 벡터 유사도 정렬 기준
    top_k: int = 30                     # config 주입 기본값 (결정 14)

class Candidate(BaseModel):
    product_id: str                     # 안정 식별자 (소비자 오버레이 키)
    name: str
    category: str                       # 계층형 카테고리 경로
    brand: str | None = None
    rating: float | None = None
    index_price: int | None = None      # 인덱스 시점 가격 (경량 UPDATE 동기화, 결정 9-A).
                                        # 필터·예산 계산 전용 — 화면 표시값 아님
    in_stock: bool | None = None        # 인덱스 시점 재고 유무 (필터 전용, 결정 9-A)
    similarity: float                   # pgvector 유사도 점수 (0.0~1.0)
    matched_tags: list[str] = []        # 매칭된 Layer 2 태그/상황 태그
    doc_snippet: str | None = None      # search_doc 발췌(근거 생성용)

class SearchToolOutput(BaseModel):
    candidates: list[Candidate]
    total_found: int                    # 완화 판단용 실제 매칭 건수
```

### 5.3 SSE 이벤트 페이로드 스키마

결정 11의 SSE로 스트리밍한다. 이벤트 타입은 결정 14의 4종(`text.delta`/`products`/`done`/`error`)으로 고정한다. 각 이벤트는 SSE `data:` 라인에 아래 JSON을 직렬화한다.

```python
# 1) text.delta — 근거/코멘트 토큰 증분 (rerank 이후 스트리밍)
{"type": "text.delta", "data": {"text": str}}

# 2) products — 재랭킹 완료 시 정확히 1회 push
{"type": "products", "data": {
    "case": 1 | 2 | 3,
    "overall_comment": str,
    "relaxation_notice": str | None,        # 완화 미적용 시 null
    "items": [ ProductPayload, ... ],        # Case 1/2: 평면 목록 (5~8개)
    "groups": [ ProductGroup, ... ] | None,  # Case 3: 카테고리별 묶음. Case 1/2는 null
    "budget": BudgetSummary | None,          # Case 3 + total_budget일 때만. 그 외 null
    "suggestions": [ Suggestion, ... ]       # 제안 칩 목록 (결정 14-D). 없으면 []
}}

# Suggestion (제안 칩 — 결정 14-D, REQ-REC-046)
# 완화 미적용/명시 제약(특히 가격) 완화 옵션을 사용자에게 제시. relaxation_notice와 공존.
{
    "label": str,                       # 칩 문구 (예: "6만원대까지 볼까요?")
    "relaxation": {                      # 이 칩이 완화하는 조건 (필드 → 제안 값)
        "field": str,                   # 예: "price_max"
        "value": Any                    # 예: 65000
    },
    "est_count": int                    # 완화 적용 시 예상 결과 수(COUNT). 0건 칩은 목록에서 제외됨
}

# BudgetSummary (Case 3 총액 예산 — 결정 14-A)
{
    "total_budget": int,
    "verified_sum": int,                # 코드가 index price로 결정론적 합산한 값
    "within_budget": bool,              # verified_sum <= total_budget
    "dropped_items": [str],             # 예산 초과로 제외된 아이템 label
    "feasibility_notice": str | None    # 부분 충족 안내 (예: "X·Y까지는 5만원, Z 포함 시 약 7만원")
    # 주의: verified_sum은 인덱스 가격 기준. 표시/결제 최종값은 원본 기준(§9 OPEN-11)
}

# ProductPayload (items[] / groups[].items[] 공통)
{
    "product_id": str,                  # 소비자가 현재 price/stock 오버레이하는 키
    "name": str,
    "category": str,
    "brand": str | None,
    "rating": float | None,
    "rank": int,                        # 1부터 시작하는 노출 순위
    "rationale": str,                   # 상품당 1문장 근거 (결정 14)
    "matched_reasons": [str]            # 근거 태그 (재현/디버그용)
    # price/stock 없음 — 소비자 오버레이 (EX-5)
}

# ProductGroup (Case 3 묶음)
{"category": str, "items": [ ProductPayload, ... ]}

# 3) done — 정상 종료
{"type": "done", "data": {"finish_reason": "completed" | "zero_result"}}

# 4) error — 오류 종료
{"type": "error", "data": {"code": str, "message": str}}
```

`error.data.code` 허용 값: `DECOMPOSE_FAILED`, `SEARCH_FAILED`, `RERANK_FAILED`, `INTERNAL`.

---

## 6. 기능 요구사항 (Functional Requirements — EARS)

### 6.1 분해 (decompose)

- **REQ-REC-001** (Event-Driven): **When** 추천 서브그래프에 요청이 진입하면, the `decompose` 노드 **shall** Claude Haiku 4.5를 **정확히 1회** 호출하여 `filters`(구조화 제약), `semantic_query`, `case`를 함께 산출한다.
- **REQ-REC-002** (Ubiquitous): The `decompose` 노드 **shall** `case`를 자신의 출력에서 파생한다 — 상품명 감지 시 Case 1, 구조화 필터만 존재 시 Case 2, 상황 키워드 존재 시 Case 3. 별도 classification 호출을 두지 **않는다**.
- **REQ-REC-003** (Ubiquitous): The `decompose` 노드 **shall** 정확한 수치·범주 제약(예: 가격 상한, 카테고리)을 `filters`에 넣고, 이를 `semantic_query`로 근사하지 **않는다**.
- **REQ-REC-004** (State-Driven): **While** `case`가 3인 동안, the `decompose` 노드 **shall** 상황을 필요 아이템 목록(`shopping_list`)으로 분해하고 각 아이템에 대한 서브 `filters`/`semantic_query`를 산출한다. **shall not** `shopping_list`의 니즈(아이템) 수에 하드 캡을 두지 않는다(결정 14-E) — 레시피 재료(예: 잡채 재료 10+)를 전부 커버하기 위함이며, 니즈가 많을 때의 규모 제어는 절단이 아니라 니즈당 후보·노출 축소(REQ-REC-096)와 `priority` 태깅(REQ-REC-098)으로 처리한다. 각 아이템은 `ShoppingItem.priority`(1=필수/2=권장/3=선택, 결정 14-H)로 태깅하며, 판정 기준은 decompose 프롬프트의 **"이게 빠지면 그 상황/요리가 성립하는가"** 다 — 1: 없으면 목적 자체 불성립(감자탕에 등뼈), 2: 없으면 아쉽지만 목적은 달성(들깨가루), 3: 있으면 더 좋은 정도(청양고추).
- **REQ-REC-047** (Ubiquitous, 결정 14-D): The `decompose` 노드 **shall** 각 필터 조건을 **명시(user 발화에 직접 존재)** vs **비명시(프로필·기본값 파생)** 로 태깅하여 `FilterSet`에 조건별 `source`로 표기한다 — 이는 0건 완화 시 완화 우선순위(비명시·약한 조건 우선 자동 완화 vs 명시 제약 제안 칩)를 판단하는 근거다(§6.6 참조).

### 6.2 프로필 주입 (profile injection)

- **REQ-REC-005** (Ubiquitous): The 서브그래프 **shall** `profile_summary`를 그래프 진입 시 주입된 read-only 값으로만 사용하며, 서브그래프 내부에서 프로필을 write하지 **않는다**.
- **REQ-REC-006** (State-Driven): **While** `is_guest`가 true(= `profile_summary`가 `None`)인 동안, the 서브그래프 **shall** 개인화(프로필 기반 재랭킹)를 스킵하되 추천 자체는 정상 수행한다(결정 8).

### 6.3 검색 (search)

- **REQ-REC-010** (Event-Driven): **When** `decompose`가 완료되면, the `search` 노드 **shall** 카탈로그 검색 tool을 호출하여 WHERE 필터와 pgvector 유사도 정렬을 결합한 **단일 SQL**로 후보를 조회한다.
- **REQ-REC-011** (Ubiquitous): The `search` 노드 **shall** config 주입 기본값 `top_k = 30`으로 후보 수를 제한한다.
- **REQ-REC-012** (State-Driven): **While** `case`가 3인 동안, the `search` 노드 **shall** `shopping_list` 아이템별 검색을 병렬 실행하고 결과를 카테고리 단위로 그룹화한다. 이때 니즈당 `top_k`는 고정 30이 아니라 **니즈 수에 반비례하는 config 입력 예산**으로 산정한다(결정 14-E) — 총 rerank 입력이 config 예산(REQ-REC-096)을 넘지 않도록 니즈가 많으면 니즈당 후보를 줄이고 적으면 늘린다. 니즈당 `top_k` 산식·예산 값은 config 주입한다(하드코딩 금지).
- **REQ-REC-013** (Unwanted): The `search` 노드 **shall not** 인덱스의 가격/재고 값을 화면 표시용 권위값으로 취급하지 않는다 — 인덱스 값은 필터·예산 계산 전용이며, 화면 표시용 최신 가격/재고는 응답 시점 원본 조회(소비자 오버레이) 대상이다(결정 9-A).
- **REQ-REC-014** (Ubiquitous): The `search` 노드 **shall** `filters`의 정확한 수치 제약(예: `price_max`)을 인덱스의 자체 `price` 컬럼(경량 UPDATE 동기화, 결정 9-A)을 대상으로 SQL WHERE 술어로 엄격히 적용하며, 유사도 검색으로 대체하지 **않는다**(결정 3).
- **REQ-REC-015** (Optional): **Where** 요청에 품절 제외(`in_stock_only`)가 지정되면, the `search` 노드 **shall** 인덱스의 `stock` 컬럼을 대상으로 재고 있는 상품만 WHERE로 필터링한다(결정 9-A).

### 6.4 재랭킹 (rerank)

- **REQ-REC-020** (Event-Driven): **When** `search`가 후보를 반환하면, the `rerank` 노드 **shall** Claude Sonnet 5를 **정확히 1회** 호출하여 최대 30개 후보와 `profile_summary`로 재랭킹하고 `ranked`(상품별 근거 포함)를 산출한다.
- **REQ-REC-021** (Ubiquitous): The `rerank` 노드 **shall** 최종 노출을 5~8개(config 주입)로 제한하고, 상품당 1문장 근거 + 전체 코멘트 1개를 생성한다.
- **REQ-REC-022** (Unwanted): The `rerank` 노드 **shall not** 후보가 실제로 갖지 않은 속성을 주장하는 근거를 생성하지 않는다(선호/속성 환각 방지 — 결정 4의 속성 검증 가드).
- **REQ-REC-023** (State-Driven, 개정 — 결정 14-E): **While** `case`가 3인 동안, the `rerank`(랭킹·선택) 단계 **shall** config로 선택된 랭킹·선택 전략(코드 per-item 결정론 선택[**MVP 기본**] / LLM 묶음 병렬 / 단일 LLM 콜, REQ-REC-097)을 따르며, LLM 호출은 config 상한(기본 2, Case 3 묶음 병렬 시 예: 최대 4) 내로 제한한다. 이는 이전 판의 경직된 "정확히 단일 콜/fan-out 절대 금지" 표현을 **config 상한 표현으로 완화**한 것이다 — 니즈 수만큼의 무제한 LLM fan-out은 여전히 금지하되(REQ-REC-097), 데이터로 선택된 전략에 따라 소수의 병렬 콜(config 상한 내)은 허용한다.
- **REQ-REC-096** (State-Driven, 결정 14-E): **While** `case`가 3인 동안, the 랭킹·선택 단계 **shall** 니즈당 후보·노출 수를 **니즈 수에 반비례**로 축소하여 총 rerank 입력을 config 주입 예산(예: ~40) 이내로 고정한다 — 니즈가 많으면 니즈당 1개, 적으면 2~3개로 산정하며, 니즈 수에 하드 캡을 두지 않는다(REQ-REC-004). 총 입력 예산·반비례 산식은 config 주입한다(하드코딩 금지).
- **REQ-REC-097** (Ubiquitous, 결정 14-E): The 랭킹·선택 단계 **shall** 랭킹·선택 방식을 config로 선택 가능하게 하되 **기본값은 방식1(코드 per-item 결정론 점수 top-1/few 선택 + LLM 전체 코멘트 1회)**로 하고, 방식2(LLM 묶음 병렬 — 작은 묶음별 병렬 LLM, config 상한 예: 3~4콜)와 방식3(단일 LLM 콜 — 그룹 전체 1회)을 config 옵션으로 제공한다. **shall not** 니즈 수만큼 LLM을 무제한 fan-out하지 않으며(방식2도 config 상한 내 소수 콜), 스테이플·커머디티 다수 상황에서는 방식1이 기본이다(단일 물품 선택에 LLM 불필요). 어느 방식이 우세한지는 골든셋/시뮬레이터(§6.12)로 측정 후 확정한다.
- **REQ-REC-098** (State-Driven, 결정 14-E/14-H): **While** 니즈 수가 config 임계를 초과하는 극단적 상황인 동안, the 랭킹·선택 단계 **shall** `ShoppingItem.priority` 오름차순(1 필수 먼저, 2 권장, 3 선택 순)으로 니즈를 우선 노출하며, 니즈(레시피 재료)를 하드 절단하지 **않는다** — priority는 극단적 과부하 시 노출 우선순위 신호일 뿐 아이템 목록에서 제거하는 근거가 아니다.

### 6.5 응답 (respond)

- **REQ-REC-030** (Event-Driven): **When** `rerank`가 완료되면, the `respond` 노드 **shall** 근거/코멘트를 `text.delta` 이벤트로 스트리밍하고, 상품 목록을 `products` 이벤트로 push한 뒤 `done` 이벤트를 emit한다.
- **REQ-REC-031** (Ubiquitous): The `respond` 노드 **shall** `products` 이벤트를 재랭킹 완료 후 **정확히 1회** push한다(결정 14).
- **REQ-REC-032** (Ubiquitous): The `respond` 노드 **shall** 각 상품 페이로드에 안정 식별자(`product_id`) + 검색 메타데이터만 포함하고 현재 가격/재고는 포함하지 **않는다** — 소비자(Spring/FE)가 오버레이한다(결정 9).

### 6.6 0건 폴백 (zero-result fallback — 결정 14-D 개정)

결정 14-D로 완화 전략을 개정한다: 고정 1회·고정 순서(brand→rating→price) 폐기 → **config 상한 라운드(기본 3)** 내에서 **비명시·약한 조건은 최소 이탈 자동 완화**, **명시 제약(특히 가격)은 자동으로 넘지 않고 제안 칩**으로 사용자에게 위임한다. 완화 우선순위 판단은 REQ-REC-047의 조건별 `source`(명시/비명시) 태깅을 근거로 한다.

- **REQ-REC-040** (Event-Driven, 개정): **When** `search`가 0건을 반환하면, the 서브그래프 **shall** config 주입 최대 완화 라운드(기본 `max_relaxation_rounds = 3`) 이내에서 자동 완화 재검색을 수행한다.
- **REQ-REC-041** (Ubiquitous, 개정): The 서브그래프 **shall** 완화 시 **비명시·약한 조건(REQ-REC-047의 `source == derived`인 브랜드·평점 등)을 우선 최소 완화**하고, **명시 제약(`source == user`, 특히 가격)은 자동으로 완화하지 않는다** — 고정 순서(brand→rating→price)를 사용하지 않는다.
- **REQ-REC-042** (Event-Driven): **When** 완화가 적용되면, the `respond` 노드 **shall** "조건을 조금 넓혔어요"에 해당하는 투명 안내(`relaxation_notice`)를 응답에 포함한다.
- **REQ-REC-043** (Unwanted): The 서브그래프 **shall not** 사용자의 명시적 제약을 조용히 위반하지 않는다 — 명시 제약은 사용자가 제안 칩으로 동의하기 전에는 자동 완화하지 않으며, 완화는 config 상한 라운드를 초과하지 않는다(EX-8).
- **REQ-REC-044** (Event-Driven): **If** config 상한 라운드까지 완화한 뒤에도 0건이면, **then** the `respond` 노드 **shall** 상품 목록 없이 조건 변경을 유도하는 응답을 생성하고 `done` 이벤트의 `finish_reason`을 `zero_result`로 설정한다.
- **REQ-REC-045** (Ubiquitous, 결정 14-D): The 서브그래프 **shall** 수치 제약을 완화할 때 뭉뚱그린 고정 비율(예: ±20%)이 아니라 나머지 조건을 유지한 채 **결과가 나오는 최소 초과분**을 계산해 그만큼만 넓힌다(예: 가격 오름차순으로 결과가 나오는 최소 상한을 계산).
- **REQ-REC-046** (Event-Driven, 결정 14-D): **When** 명시 제약(특히 가격)의 완화가 필요하면, the 서브그래프 **shall** 자동 완화 대신 **제안 칩**으로 제시하며, 각 칩에 예상 결과 수(COUNT 질의로 계산)를 붙이고 결과 0건인 칩은 제외한다.

### 6.7 멀티턴 병합 (multiturn merge)

- **REQ-REC-050** (Event-Driven): **When** State에 직전 `filters`가 존재하면, the `decompose` 노드 **shall** 직전 `filters`를 입력으로 함께 받아 병합된 필터 집합을 출력한다.
- **REQ-REC-051** (Ubiquitous): The 서브그래프 **shall** add/replace 판단(병합 로직)을 `decompose` 프롬프트 안에서 처리하며, 그래프 코드에 병합 로직을 두지 **않는다**(결정 14).
- **REQ-REC-052** (Event-Driven): **When** 후속 질의가 직전 조건 범위를 좁히면(예: "그중에 검은색만"), the `decompose` 노드 **shall** 직전 필터 + 신규 필터의 합집합을 출력한다(add).
- **REQ-REC-053** (Event-Driven): **When** 후속 질의가 직전 필터와 모순되면(예: "아니 5만원 이하로"), the `decompose` 노드 **shall** 해당 필터를 교체한 집합을 출력한다(replace).
- **REQ-REC-054** (Event-Driven, 결정 14-G): **When** 후속 질의의 카테고리/상품이 직전 `filters`와 무관(주제 전환)하면, the `decompose` 노드 **shall** 직전 주제 필터를 폐기(reset)하고 새 주제로 재시작한다 — reset 판단은 `decompose` 출력에서 파생하며 별도 분류 호출을 두지 **않는다**(add/replace/reset 3종 전이, LLM 추가 호출 없음, 결정 14/14-G).
- **REQ-REC-055** (State-Driven / Optional, 결정 14-G): **While** reset이 적용되는 동안, the `decompose` 노드 **shall** 범용 명시 제약(`source == user`이며 config `multiturn.carry_on_reset` 대상, 기본값 `["price"]`)은 유지(캐리)하고, 카테고리·상품별 속성(예: 이어폰의 "검정")은 폐기한다. 캐리 대상 목록은 `core/config.py`에서 config 주입한다(하드코딩 금지).
- **REQ-REC-056** (Event-Driven, 결정 14-G): **When** 후속 질의가 병렬 신호("~도/그리고")로 직전 주제와 신규 주제를 **둘 다** 상품으로 요구하면, the `decompose` 노드 **shall** reset이 아니라 Case 3(다중 니즈)로 승격하여 두 주제를 모두 `shopping_list`로 추천한다(§6.1 REQ-REC-004 연계). MVP는 단순 병렬 신호 기준이며, 신뢰성 있는 승격 판별은 고도화 범위다(EX-10).

### 6.8 총액 예산 처리 (total-budget handling — 결정 14-A)

"5만원 내로 유럽여행에 필요한 거"처럼 가격 제약이 **묶음 총액 상한**인 경우를 개별 상품 상한과 구분하여 처리한다. MVP 범위.

- **REQ-REC-070** (Ubiquitous): The `decompose` 노드 **shall** 가격 제약에 `price_scope`(`per_item` | `total_budget`)를 부여하며, Case 1/2는 기본 `per_item`으로 산출한다.
- **REQ-REC-071** (Event-Driven): **When** Case 3 질의에 총액 신호("~내로", "총 ~", "예산" 등)가 존재하면, the `decompose` 노드 **shall** `price_scope`를 `total_budget`으로 설정하고 `total_budget` 금액을 산출한다.
- **REQ-REC-072** (State-Driven): **While** `price_scope`가 `total_budget`인 동안, the `search` 노드 **shall** 아이템별 WHERE에 총 예산을 **안전 상한(sanity cap)** 으로만 적용하여(단일 상품이 예산 전체를 초과하면 탈락) 저가 후보를 포함해 조회한다 — 개별 아이템에 예산 전액을 `price_max`로 걸지 않는다.
- **REQ-REC-073** (Event-Driven): **When** `price_scope`가 `total_budget`이면, the 서브그래프 **shall** 아이템당 1개 상품으로 "총합 ≤ 예산" 제약의 묶음(multiple-choice knapsack — 니즈당 대안 중 1개 선택)을 구성한다 — 후보 제안 주체는 랭킹·선택 전략(결정 14-E)을 따르며(방식1은 코드가 직접 선택, 방식2/3은 LLM이 니즈당 후보 제안), 합산·보정은 어느 전략에서도 항상 코드다(REQ-REC-074).
- **REQ-REC-074** (Unwanted): The 서브그래프 **shall not** 묶음 합산에 LLM 산수를 신뢰하지 않는다 — 합산은 **코드가 인덱스 가격으로 결정론적으로 검증**하며, 예산 초과 시 가장 비싼 아이템부터 저가 대안으로 교체하는 **상한 횟수(config)의 결정론적 보정 루프**를 실행한다.
- **REQ-REC-075** (Unwanted): **If** 필수(`priority == 1`) 아이템 최저가 합이 예산을 초과하면, **then** the 서브그래프 **shall** 아이템을 조용히 누락하지 않고 부분 충족을 투명하게 안내한다(예: "X·Y까지는 5만원, Z 포함 시 약 7만원 필요"). 제외 아이템은 `dropped_items`/`feasibility_notice`로 명시한다.
- **REQ-REC-076** (Ubiquitous): The 보정 루프 **shall** config 주입 상한 횟수 내에서만 반복하며, 대안이 없는 아이템은 제외 후 안내 대상으로 처리한다(무한 교체 금지). 대안 소진으로 제외가 불가피할 때의 제외 순서는 `priority` 역순(3 선택 → 2 권장)이며, 1 필수는 최후이자 반드시 투명 안내 대상이다(결정 14-H, REQ-REC-075 연계).
- **REQ-REC-077** (State-Driven, 결정 14-E 정합): **While** `price_scope`가 `total_budget`인 동안, the `search` 노드 **shall** 니즈당 **노출 수**(반비례 축소, REQ-REC-096)와 별개로 보정 루프(REQ-REC-074)용 **니즈당 대안 후보 풀을 config 주입 최소 수 이상 유지**한다 — 대안이 부족하면 교체가 불가능하므로, 예산 모드에서는 노출이 1개여도 니즈당 후보는 여러 개(저가 대안 포함)를 확보한다.

### 6.9 오류 처리 관련 요구 (see §7)

- **REQ-REC-060** (Unwanted): **If** `decompose` LLM 호출이 실패(오류/타임아웃)하면, **then** the 서브그래프 **shall** `error`(code `DECOMPOSE_FAILED`) 이벤트를 emit하고 `search`로 진행하지 **않는다**.
- **REQ-REC-061** (Unwanted): **If** 카탈로그 검색 tool이 오류(DB/연결 등)를 발생시키면, **then** the 서브그래프 **shall** `error`(code `SEARCH_FAILED`) 이벤트를 emit하고 후보를 **날조하지 않는다**.
- **REQ-REC-062** (Unwanted): **If** `rerank` LLM 호출이 1회 재시도 후에도 실패하면, **then** the 서브그래프 **shall** 검색 순서(이미 WHERE로 하드 제약이 적용된 안전한 후보) 상위 5~8개로 degrade하여 일반 코멘트와 함께 응답하되, 하드 제약을 위반하지 **않는다**(spec-level 결정, §9 OPEN-3에서 재검토 여지 명시).
- **REQ-REC-063** (Ubiquitous): The 서브그래프 **shall** 0건 결과를 오류가 아닌 정상 결과로 처리하며(폴백 경로), 0건을 이유로 `error` 이벤트를 emit하지 **않는다**.

### 6.10 재랭킹 견고성 (rerank robustness — 결정 14-B)

노출 5~8개 소규모 리랭킹 구간의 순서 민감성·후보 외 환각·근거 속성 환각을 코드로 차단한다. 아래 요구는 모두 **결정론적 코드**이며 LLM 호출을 추가하지 않는다(§비기능 "LLM 호출 수 상한 = 최대 2회" 불변).

- **REQ-REC-080** (Event-Driven): **When** `search` 후보를 `rerank` 프롬프트에 투입하기 직전, the `search`→`rerank` 경계 **shall** 후보 순서를 config 주입 셔플로 무작위화하며, 유사도순으로 나열하지 **않는다**(순서 민감성 방지 — 셔플 시드/횟수는 `core/config.py` 주입). *(지연 여유 시 셔플-병합 앙상블은 선택이며, 그 반복 횟수도 config 주입이다.)*
- **REQ-REC-081** (Unwanted): The `rerank` 출력 검증기 **shall not** `rerank`가 출력한 `product_id` 중 검색 후보 집합(`candidates`)에 없는 ID를 노출한다 — 코드로 후보 집합과 대조하여 후보 외 ID를 제거/거부한다(후보 외 환각 차단).
- **REQ-REC-082** (Unwanted): The `rerank` 출력 검증기 **shall not** 근거문(`rationale`)이 주장하는 속성(브랜드·평점·태그 등)을 후보 상품 데이터와의 대조 없이 노출한다 — 프롬프트 제약(REQ-REC-022) + 후보 데이터와의 결정적 사후 대조의 **2중 가드**를 적용하고, 대조 실패한 속성 주장을 제거한 뒤에만 노출한다.
- **REQ-REC-083** (Unwanted, REQ-REC-062 보강): **If** rerank 출력 검증이 실패(후보 외 `product_id` / 형식 오류 / 속성 대조 실패)하면, **then** the 서브그래프 **shall** 이를 degrade 트리거에 포함하여 검색(SQL+pgvector) 순서 상위 5~8개로 degrade하되, 하드 제약(예: `price_max` WHERE)을 위반하지 **않는다**. 본 요구는 REQ-REC-062(LLM 오류/타임아웃 degrade)를 확장하며, 두 트리거 모두 동일한 검색-순서 degrade 경로를 공유한다.

### 6.11 모호 속성 라우팅 (ambiguous-attribute routing — 결정 14-B)

- **REQ-REC-084** (Ubiquitous, REQ-REC-003 정련): The `decompose` 노드 **shall** 확신 없는 모호 속성을 하드 WHERE 필터(`filters`)로 변환하지 않고 `semantic_query`로 라우팅한다(over-filtering 방지). 본 요구는 REQ-REC-003을 **정련**하며 모순되지 않는다 — 정확·명시 제약(가격 상한·카테고리 등)은 REQ-REC-003/014에 따라 여전히 WHERE로 처리하고, 오직 **모호·저확신 속성**만 `semantic_query`로 흡수한다. 모호/정확 판별 기준(임계)은 config 주입한다.
- **REQ-REC-085** (Optional): **Where** decompose 신뢰도가 config 주입 임계 미만이거나 필수 슬롯(카테고리/예산/용도)이 결측된 경우에 한하여, the 서브그래프 **shall** 세션당 config 주입 상한 내에서 단일 명확화 질문 경로를 제공할 수 있다(실험 플래그, **MVP 비범위 가능**). 이는 선택 기능이며 기본 비활성이다.

### 6.12 평가 하니스 (evaluation harness — 결정 14-B)

아래 요구는 런타임 기능이 아니라 **테스트/평가 계층** 요구다. 컴포넌트 회귀와 종단 대화 품질을 2계층으로 분리하고 평가 누출을 차단한다.

- **REQ-REC-090** (Ubiquitous, 평가 계층): The 평가 하니스 **shall** 검색·리랭크 컴포넌트를 ESCI식 골든셋으로 회귀 게이트한다(NDCG@10 / recall@K 등 지표). 골든셋 경로·게이트 임계는 config 주입한다. *(문헌은 기준치를 주지 않으므로 절대 임계는 실측 후 확정 — §9 OPEN-5/OPEN-10.)*
- **REQ-REC-091** (Ubiquitous, 평가 계층): The 평가 하니스 **shall** 종단 대화 품질을 유저 시뮬레이터(페르소나·행동·명시 채점)로 N라운드(config 주입, 기본 상한 ≤ 5라운드) 진행 후 recall@K로 측정한다 — 정적 골든셋만으로는 명확화·잡담 턴을 오답 처리해 대화형 시스템을 과소평가하므로, 종단 계층을 별도로 둔다. 라운드 상한은 config 주입한다.
- **REQ-REC-092** (Unwanted, 평가 계층): The 평가 하니스 **shall not** 평가 누출을 허용하지 않는다 — 재랭크 프롬프트에 정답 유래 신호를 주입하지 않고, 시간 기반(train/test) 분할을 강제하며, 비정상 고성능 관측 시 누출 감사를 수행한다.

### 6.13 랭킹 전략 (ranking strategy — 결정 14-C)

랭킹을 교체 가능한 인터페이스로 두어, MVP는 A(LLM 리랭크, 본 SPEC의 현행 `rerank` 노드)를 사용하고 B(결정론적 가중 스코어링)는 고도화의 선택적 실험으로 남긴다. B는 실트래픽·데이터가 있어야 가중치 튜닝이 성립하나 본 프로젝트는 데모 목적이라 데이터가 없어, 제로샷으로 즉시 작동하는 A가 콜드스타트 구간의 기준선(baseline)이다. 상세 근거는 `.moai/docs/ranking-A-vs-B-deliberation.md`, `.moai/docs/reference-twitter-algorithm.md` 참조.

- **REQ-REC-094** (Ubiquitous, 결정 14-C): The 서브그래프 **shall** 랭킹을 교체 가능한 `Ranker` 인터페이스(`Ranker.rank(...)`)로 구현하고, MVP는 A(`LLMReranker`, 본 SPEC의 Sonnet 리랭크)를 기본 전략으로 사용하며, B(`ScoringReranker`, 결정론적 가중 스코어링)는 config 주입(`rank.strategy`)으로 교체·비교 가능한 드롭인으로 남긴다. B로의 전환은 골든셋 NDCG@K 비교 게이트(§6.12)에서 A를 이길 때만 허용한다.
- **REQ-REC-095** (Event-Driven, 결정 14-C): **When** 추천이 산출되면, the 서브그래프 **shall** 추천 스냅샷(질의·필터·후보·최종 랭킹·근거)을 로깅하여 오프라인 재현·A/B 비교·튜닝 재료로 남긴다 — 로깅 활성화 여부·보존 대상은 config 주입한다(소급 불가하므로 1일차부터 활성).

### 6.14 구매 이력 제외 (purchase-history dedup — 결정 14-F)

최근 구매한 상품을 추천에서 제외한다. `search` 단계의 필터로 적용하며 Case 1/2(단일)·Case 3(니즈별) 공통이다. **두 층위** — exact `product_id`는 항상 제외, 카테고리/니즈 억제는 재구매 성향(MVP는 소모품 boolean 플래그)에 의존한다. 억제는 **non-blocking**이며 억제된 니즈는 되돌리기 제안 칩(`suggestions`, 결정 14-D 재사용)으로 재포함 가능하게 제시한다. 게스트는 구매 이력이 없어 스킵한다(결정 8).

- **REQ-REC-100** (Ubiquitous): The `search` 노드 **shall** 최근 구매한 **exact `product_id`를 항상 제외**한다(방금 산 바로 그 상품 재추천 금지). **While** `is_guest`가 true인 동안(구매 이력 없음, 결정 8), the `search` 노드 **shall** 본 제외 로직을 스킵한다.
- **REQ-REC-101** (State-Driven / Optional): **While** 어떤 카테고리에 최근 구매가 있고 그 카테고리가 소모품으로 태깅된 동안(카테고리 재구매 메타 — MVP는 소모품 boolean 플래그, 결정 15), the `search` 노드 **shall** 해당 니즈/카테고리를 억제한다(MVP 단순판). **Where** 카테고리가 다양성 상품(옷·액세서리 등)이면, the `search` 노드 **shall** exact `product_id`만 제외하고 비슷한 상품은 계속 추천한다(카테고리 억제하지 않음).
- **REQ-REC-102** (Unwanted): The 서브그래프 **shall not** 한 아이템의 억제로 나머지 추천을 막지 않는다 — 억제는 **non-blocking**이며, 억제된 니즈를 조용히 누락하지 않고 되돌리기 제안 칩(`suggestions`, 결정 14-D "소금은 최근 구매 — 다시 추천받기" 형태)으로 재포함 가능하게 제시한다. 조용한 누락은 금지한다.
- **REQ-REC-103** (Ubiquitous): The 카테고리 억제 판단 **shall** 카테고리 재구매 메타(**MVP는 소모품 boolean 플래그**, 결정 15 카테고리 속성 사전)에 따른다. 정교한 재구매 주기 계산·주기성 상품(화장품·영양제) 주기 내 억제·옷류 variety-seeking 처리 등 **정교한 재구매/다양성 모델은 고도화 범위이며 MVP에서는 구현하지 않는다**(EX-9). 소모품 판정 임계·최근 구매 조회 윈도우 등 값은 config 주입한다(하드코딩 금지).

---

## 7. 오류 처리 (Error Handling)

| 실패 지점 | 감지 | 처리 | 안전 불변식 |
|---|---|---|---|
| `decompose` 실패 (LLM 오류/타임아웃) | Haiku 호출 예외 | 최대 1회 재시도 후 `error`(`DECOMPOSE_FAILED`). search 미진행 | 필터 없이 검색 금지 |
| `search` 실패 (DB/연결/쿼리 오류) | tool 예외 | 최대 1회 재시도 후 `error`(`SEARCH_FAILED`). 후보 날조 금지 | 존재하지 않는 상품 반환 금지 |
| `rerank` 실패 (LLM 오류/타임아웃) | Sonnet 호출 예외 | 1회 재시도 후 검색 순서 상위 5~8개로 degrade(근거 없이 일반 코멘트) + `done`. WHERE 제약 유지 | 하드 제약(가격 상한 등) 위반 금지 |
| `rerank` 출력 검증 실패 (후보 외 ID/형식 오류/속성 대조 실패, 결정 14-B) | 출력 검증기 코드 대조 | 검색 순서 상위 5~8개로 degrade + `done`. WHERE 제약 유지(REQ-REC-081/082/083) | 후보 외 ID·미검증 속성 주장 노출 금지, 하드 제약 위반 금지 |
| 0건 결과 | `total_found == 0` | 오류 아님 — 1회 완화(REQ-REC-040) → 여전히 0건이면 조건 변경 유도(`finish_reason: zero_result`) | 명시적 제약 무단 위반 금지 |
| 스트림 중 소비자 abort | HTTP 연결 종료 | 진행 중 LLM 호출 취소, 리소스 정리 | — |

- 재시도 정책은 MoAI constitution의 최대 3회/작업 원칙 하에서 노드별 1회 재시도를 기본으로 한다(구체 지수 백오프 값은 구현 결정).
- 모든 `error` 이벤트는 사용자에게 노출 가능한 안전한 `message`(내부 스택/PII 미포함)를 담는다.

---

## 8. 인수 기준 (Acceptance Criteria)

모든 기준은 관찰 가능/테스트 가능해야 한다. Given-When-Then 형식.

- **AC-REC-01 (Case 1 해피패스)**: **Given** "아이폰 15 케이스 추천해줘"라는 질의, **When** 서브그래프가 실행되면, **Then** `case == 1`이고, Haiku 호출은 1회, `products` 이벤트가 정확히 1회 push되며, 노출 상품 수는 5~8개, 각 상품에 1문장 `rationale`이 존재한다.
- **AC-REC-02 (Case 2 해피패스)**: **Given** "5만원 이하 무선 이어폰"이라는 질의, **When** 실행되면, **Then** `filters.price_max == 50000`이 SQL WHERE로 적용되고, 반환된 모든 후보/노출 상품은 가격 상한 제약을 만족한다(§AC-REC-08과 연계).
- **AC-REC-03 (Case 3 묶음 추천)**: **Given** "유럽여행 갈건데 필요한 거 다 추천해줘"라는 질의, **When** 실행되면, **Then** `case == 3`, `shopping_list`가 2개 이상 아이템으로 분해되고, 검색은 아이템별 병렬 실행되며, `products.groups`가 카테고리별로 묶여 반환되고, LLM 호출은 decompose 1회 + rerank 1회로 총 2회를 초과하지 않는다.
- **AC-REC-04 (멀티턴 add)**: **Given** 직전 턴 `filters = {category: 무선이어폰, price_max: 50000}`, **When** "그중에 검은색만"이 입력되면, **Then** decompose 출력은 직전 필터를 유지한 채 색상 속성이 **추가**된 필터 집합(`attributes.color == "검정"`, `price_max` 유지)이다.
- **AC-REC-05 (멀티턴 replace)**: **Given** 직전 턴 `filters = {price_max: 100000}`, **When** "아니 5만원 이하로"가 입력되면, **Then** decompose 출력의 `price_max`는 100000이 아닌 50000으로 **교체**된다.
- **AC-REC-06 (0건 완화 + 안내, 개정)**: **Given** 비명시(`source == derived`) 브랜드 조건으로 검색 시 0건이 되는 질의, **When** 실행되면, **Then** 서브그래프는 config 상한 라운드(기본 3) 이내에서 비명시 조건을 우선 최소 완화(고정 순서 아님)로 재검색하고, `products.relaxation_notice`가 non-null이며 사용자 안내 문구를 포함하고, 완화 라운드 수는 config 상한을 초과하지 않는다(REQ-REC-040/041).
- **AC-REC-07 (완화 후에도 0건, 개정)**: **Given** config 상한 라운드까지 완화한 뒤에도 0건인 질의, **When** 실행되면, **Then** `products.items`는 비어 있고 조건 변경 유도 응답 텍스트가 스트리밍되며 `done.finish_reason == "zero_result"`이고 `error` 이벤트는 발생하지 않는다(REQ-REC-044).
- **AC-REC-08 (가격 제약 불가침)**: **Given** `price_max`가 지정된 임의의 질의, **When** 정상/완화/degrade(rerank 실패) 어느 경로로든 응답되면, **Then** 노출된 모든 상품은 `price_max` WHERE 제약을 만족한다 — 유사도 근사로 상한을 초과한 상품이 노출되지 않는다. (단, 완화 순서상 `price ±20%`가 명시적으로 적용된 경우는 `relaxation_notice`로 고지된 상태여야 하며, 그 외 경로에서는 상한 초과 금지.)
- **AC-REC-09 (비회원 개인화 스킵)**: **Given** `is_guest == true`(user_id 없음, `profile_summary == None`), **When** 실행되면, **Then** 프로필 기반 재랭킹은 스킵되지만 `products` 이벤트가 정상 반환되고 추천이 성립한다(예외/에러 없음).
- **AC-REC-10 (휘발성 필드 미포함)**: **Given** 임의의 정상 추천 응답, **When** `products` 페이로드를 검사하면, **Then** 각 `ProductPayload`에는 `product_id`가 존재하고 현재 `price`/`stock` 권위 필드는 포함되지 않는다(소비자 오버레이 계약 준수).
- **AC-REC-11 (SSE 이벤트 순서/유일성)**: **Given** 정상 추천, **When** SSE 스트림을 수신하면, **Then** 이벤트는 `text.delta`(0회 이상) → `products`(정확히 1회) → `done`(1회) 순서로 관찰되며, `products`는 rerank 완료 이후에만 나타난다.
- **AC-REC-12 (decompose/search 실패 시 안전)**: **Given** decompose 또는 search가 강제 실패하도록 주입된 상태, **When** 실행되면, **Then** 각각 `error.code == DECOMPOSE_FAILED` 또는 `SEARCH_FAILED`가 emit되고, 후속 노드로 진행하거나 후보를 날조하지 않는다.
- **AC-REC-13 (rerank 실패 degrade)**: **Given** rerank가 재시도 후에도 실패하는 상태, **When** 실행되면, **Then** 검색 순서 상위 5~8개가 응답되고 모든 하드 제약(예: `price_max`)이 유지되며 `error`가 아닌 `done`으로 종료된다.
- **AC-REC-14 (총액 예산 해피패스)**: **Given** "5만원 내로 유럽여행 필요한 거"라는 질의, **When** 실행되면, **Then** `filters.price_scope == "total_budget"`, `total_budget == 50000`이고, `budget.verified_sum`은 **코드가 index price로 합산한 값**이며 `budget.verified_sum <= 50000`, `budget.within_budget == true`이다.
- **AC-REC-15 (price_scope 오판 가드)**: **Given** "각각 5만원 이하로 여행용품 추천"(per_item)과 "5만원 내로 여행용품 다 추천"(total_budget) 두 질의, **When** 각각 실행되면, **Then** 전자는 `price_scope == "per_item"`(각 상품 `price_max == 50000`), 후자는 `price_scope == "total_budget"`(묶음 합 ≤ 50000)로 서로 다르게 판별된다.
- **AC-REC-16 (예산 불가능 투명 안내)**: **Given** 필수 아이템 최저가 합이 예산을 초과하는 total_budget 질의, **When** 실행되면, **Then** 아이템이 조용히 누락되지 않고 `budget.feasibility_notice`가 non-null이며 `budget.dropped_items`에 제외 아이템이 명시되고, `budget.within_budget == false`가 안내된다.
- **AC-REC-17 (보정 루프 유한성)**: **Given** 첫 묶음 제안이 예산을 초과하는 total_budget 질의, **When** 보정 루프가 실행되면, **Then** `budget.repair_iterations`는 config 상한 이하이고, 합산 검증은 LLM이 아닌 코드 결과이며(재현 가능), 루프는 상한 도달 시 반드시 종료된다.
- **AC-REC-18 (후보 순서 무작위화)**: **Given** 동일한 `candidates` 집합과 고정 셔플 시드, **When** `rerank` 프롬프트 투입 직전 순서 무작위화가 적용되면, **Then** 투입 순서가 검색 유사도 내림차순과 동일하지 않음이 관찰 가능하고(유사도순 나열 아님), 셔플 시드/횟수는 config 주입값에서 온다(하드코딩 아님, REQ-REC-080).
- **AC-REC-19 (후보 외 ID 미노출)**: **Given** `rerank`가 검색 후보 집합(`candidates`)에 없는 `product_id`를 하나 이상 출력하도록 주입된 상태, **When** 출력 검증이 수행되면, **Then** 후보 외 ID의 노출 수는 0이고 해당 ID는 `rerank_validation.dropped_out_of_candidate_ids`에 기록된다(REQ-REC-081).
- **AC-REC-20 (근거 속성 대조 실패 처리)**: **Given** `rationale`이 후보 데이터와 불일치하는 속성(예: 실제와 다른 브랜드/평점)을 주장하도록 주입된 상태, **When** 결정적 속성 대조가 수행되면, **Then** 대조 실패 속성 주장은 그대로 노출되지 않고(속성 제거 또는 degrade), `rerank_validation.attr_mismatch == true`가 기록된다(REQ-REC-082).
- **AC-REC-21 (모호 속성 라우팅)**: **Given** 확신 없는 모호 속성이 포함된 질의(예: "감성 있는 조명"의 "감성"), **When** decompose가 실행되면, **Then** 해당 모호 속성은 `price_max`류 하드 WHERE 필터로 변환되지 않고 `semantic_query`에 반영되며, 명시적 정확 제약(가격 상한·카테고리)은 여전히 `filters`(WHERE)로 남는다(REQ-REC-084, REQ-REC-003 정련).
- **AC-REC-22 (출력 검증 실패 degrade)**: **Given** rerank 출력 검증이 실패(후보 외 ID/형식 오류/속성 대조 실패)하도록 주입된 상태, **When** 실행되면, **Then** 검색 순서 상위 5~8개가 응답되고 모든 하드 제약(예: `price_max`)이 유지되며, `rerank_validation.degraded == true`이고 `error`가 아닌 `done`으로 종료된다(REQ-REC-083, REQ-REC-062 보강).
- **AC-REC-23 (완화 최대 라운드 config)**: **Given** `max_relaxation_rounds`가 config로 주입되고(기본 3) 여러 라운드 완화가 필요한 0건 질의, **When** 실행되면, **Then** 자동 완화 라운드 수는 주입값 이하이며 하드코딩된 "1회" 상한에 묶이지 않는다(REQ-REC-040).
- **AC-REC-24 (수치 최소 초과분 완화)**: **Given** "5만원 이하"로 0건이 되지만 "5.3만원 이하"에서 결과가 나오는 질의(가격이 비명시·완화 대상으로 판정된 경우), **When** 수치 완화가 수행되면, **Then** 완화된 상한은 뭉뚱그린 고정 비율(±20% = 6만원)이 아니라 결과가 나오는 **최소 초과분**(예: 5.3만원)으로 계산되며, 그 근거가 관찰 가능하다(REQ-REC-045).
- **AC-REC-25 (명시 가격 제약 제안 칩)**: **Given** 사용자가 명시(`sources["price_max"] == "user"`)한 가격 상한으로 0건이 되는 질의, **When** 실행되면, **Then** 서브그래프는 가격을 자동 완화하지 않고 `products.suggestions`에 완화 옵션 칩을 제시하며, 각 칩은 `est_count`(예상 결과 수)를 포함하고 `est_count == 0`인 칩은 목록에서 제외된다(REQ-REC-046, REQ-REC-043).
- **AC-REC-26 (decompose 명시/비명시 태깅)**: **Given** 사용자가 가격만 발화하고 브랜드는 프로필에서 파생된 질의, **When** decompose가 실행되면, **Then** `filters.sources["price_max"] == "user"`이고 `filters.sources["brand"] == "derived"`로 조건별 출처가 태깅된다(REQ-REC-047).
- **AC-REC-27 (랭킹 교체 인터페이스 + 스냅샷 로깅)**: **Given** `rank.strategy` config, **When** 추천이 산출되면, **Then** 랭킹은 `Ranker` 인터페이스를 통해 수행되고 MVP 기본값은 A(`LLMReranker`)이며(config로 B(`ScoringReranker`) 교체 가능), 추천 스냅샷(질의·필터·후보·최종 랭킹·근거)이 로깅된다(REQ-REC-094/095).
- **AC-REC-28 (Case 3 니즈 수 무제한)**: **Given** "잡채 재료 다 추천해줘"처럼 니즈가 10개 이상으로 분해되는 Case 3 질의, **When** decompose가 실행되면, **Then** `shopping_list`는 10개 이상 아이템을 전부 포함하며 니즈 수 하드 캡으로 절단되지 않고, 각 아이템에 `priority`(1 필수/2 권장/3 선택)가 태깅된다(REQ-REC-004, 결정 14-H).
- **AC-REC-29 (니즈당 후보·노출 반비례 + 총 입력 예산)**: **Given** 니즈 수가 서로 다른 두 Case 3 질의(예: 3니즈 vs 10니즈)와 config 주입 총 입력 예산, **When** 검색·랭킹이 실행되면, **Then** 니즈가 많은 질의는 니즈당 후보·노출이 더 적게(예: 니즈당 1개) 산정되고 적은 질의는 더 많게(예: 2~3개) 산정되며, 총 rerank 입력은 어느 경우에도 config 예산 이내이다(REQ-REC-012/096).
- **AC-REC-30 (랭킹·선택 전략 config 선택, 기본=코드 per-item)**: **Given** `case3.select_strategy` config, **When** 추천이 산출되면, **Then** 기본값은 방식1(코드 per-item 결정론 선택 + LLM 전체 코멘트 1회)이고, config로 방식2(LLM 묶음 병렬)·방식3(단일 콜)으로 교체 가능하며, 방식1에서는 개별 물품 선택에 LLM이 호출되지 않는다(REQ-REC-097).
- **AC-REC-31 (LLM 호출 config 상한 준수)**: **Given** config 주입 LLM 콜 상한(기본 2, Case 3 묶음 병렬 시 예: 최대 4), **When** 임의의 Case에서 추천이 산출되면, **Then** `decompose`는 1회이고 서브그래프 LLM 총 호출 수는 선택된 전략의 config 상한을 초과하지 않으며(방식2 묶음 병렬도 상한 준수, 니즈 수만큼 무제한 fan-out 아님), 재시도는 예외로 계수한다(REQ-REC-023/097, §비기능).
- **AC-REC-32 (exact 최근 구매 상품 미노출)**: **Given** 사용자가 최근 구매한 `product_id` P가 검색 후보에 포함되는 질의(회원, `is_guest == false`), **When** 서브그래프가 실행되면, **Then** `products` 페이로드의 어떤 항목에도 P가 노출되지 않는다(exact 항상 제외, REQ-REC-100).
- **AC-REC-33 (소모품 카테고리 억제 + 되돌리기 칩, 나머지 니즈 정상 추천)**: **Given** 소모품으로 태깅된 카테고리(예: 소금)에 최근 구매가 있고 다른 니즈도 함께 있는 Case 3 질의, **When** 실행되면, **Then** 해당 소모품 니즈는 억제되되(추천 목록에서 조용히 빠지지 않음) 나머지 니즈는 정상 추천되고, `products.suggestions`에 그 니즈를 재포함하는 되돌리기 칩(예: "소금은 최근 구매 — 다시 추천받기")이 존재한다(REQ-REC-101/102). 다양성 상품 카테고리(예: 옷)에서 같은 상황이면 exact만 제외되고 비슷한 상품은 계속 추천된다.
- **AC-REC-34 (게스트 제외 로직 스킵)**: **Given** `is_guest == true`(구매 이력 없음, `profile_summary == None`), **When** 실행되면, **Then** 구매 이력 제외 로직(exact 제외·카테고리 억제)이 스킵되고 추천이 정상 성립한다(REQ-REC-100, 결정 8).
- **AC-REC-35 (주제 전환 reset + 가격 캐리)**: **Given** 직전 턴 `filters = {category: 무선이어폰, price_max: 50000, attributes: {color: "검정"}, sources: {price_max: "user", ...}}`, **When** 직전과 무관한 주제("무선 키보드 추천해줘")가 입력되면, **Then** decompose 출력은 직전 주제 필터(무선이어폰 카테고리·색상 "검정")를 **폐기(reset)**하되 config `multiturn.carry_on_reset`(기본 `["price"]`) 대상인 `price_max == 50000`(`source == user`)은 **유지**한다(REQ-REC-054/055).
- **AC-REC-36 (병렬 신호 → Case 3 승격)**: **Given** 직전 턴 주제가 "무선 이어폰", **When** "무선 키보드도 추천해줘"(병렬 신호 + 둘 다 상품)가 입력되면, **Then** decompose는 reset이 아니라 `case == 3`으로 승격하고 `shopping_list`에 이어폰·키보드 두 주제가 모두 포함되어 둘 다 추천된다(REQ-REC-056, REQ-REC-004 연계).
- **AC-REC-37 (reset 판단이 decompose 파생, 별도 호출 없음)**: **Given** 주제 전환·병렬·정제(add/replace) 중 어느 멀티턴 질의든, **When** 서브그래프가 실행되면, **Then** add/replace/reset 전이 및 Case 3 승격 판단은 모두 `decompose`의 단일 출력에서 파생되고 별도 분류 LLM 호출이 발생하지 않으며, `decompose` LLM 호출은 여전히 1회다(REQ-REC-054/056, REQ-REC-001, EX-7/EX-10).

### Definition of Done

- [ ] REQ-REC-001~076, REQ-REC-080~085, REQ-REC-090~092, REQ-REC-094~098, REQ-REC-100~103, REQ-REC-054~056 전 항목이 테스트로 커버됨(REQ-REC-090~092는 평가 하니스 계층). 개정된 REQ-REC-004/012/023/040/041/047/045/046 및 신설 REQ-REC-096~098, REQ-REC-100~103, REQ-REC-054~056 포함.
- [ ] AC-REC-01~37 전 시나리오가 통과(pytest, integration은 docker compose 앱 + pgvector). 개정된 AC-REC-06/07 및 신설 AC-REC-28~31, AC-REC-32~34, AC-REC-35~37 포함.
- [ ] State/검색 tool/SSE 스키마가 Pydantic 모델로 구현되고 스키마 계약 테스트 존재(`RerankValidation`, `FilterSet.sources`, SSE `suggestions` 포함).
- [ ] 하드 불변식(가격 제약 불가침, 총액 예산 코드 검증, `products` 1회 push, 명시적 제약 무단 위반 금지·**완화 config 상한 내 + 매 완화 알림**, **리랭크 노출 전 후보 순서 무작위화**, **리랭크 출력 product_id ⊆ 검색 후보 집합**) 회귀 테스트 존재.
- [ ] 리랭크 출력 검증(후보 외 ID 제거·근거 속성 대조) 및 출력 검증 실패 degrade(REQ-REC-081/082/083, AC-REC-19/20/22) 회귀 테스트 존재.
- [ ] 0건 완화 재설계(config 상한 라운드·명시/비명시 태깅 기반 최소 이탈 자동·수치 최소 초과분·명시 제약 제안 칩, REQ-REC-040/041/045/046/047, AC-REC-06/07/23/24/25/26) 회귀 테스트 존재.
- [ ] 랭킹 교체 인터페이스(`Ranker`, `rank.strategy` config)와 추천 스냅샷 로깅(REQ-REC-094/095, AC-REC-27) 구현·테스트 존재.
- [ ] Case 3 다중 니즈 전략(니즈 수 무제한·니즈당 후보 반비례 축소로 총 입력 config 예산 고정·랭킹 선택 3방식 config[기본=코드 per-item]·priority 우선 노출[결정 14-H], REQ-REC-004/012/023/096/097/098, AC-REC-28~31) 구현·테스트 존재. LLM 콜 상한이 config 상한(기본 2, 묶음 병렬 시 예 최대 4) 내로 강제되는 회귀 테스트 포함.
- [ ] 평가 하니스 2계층(골든셋 컴포넌트 회귀 + 유저 시뮬레이터 종단, REQ-REC-090/091)과 누출 방지(REQ-REC-092)가 구현되고 게이트로 연결됨.
- [ ] 구매 이력 제외(결정 14-F, REQ-REC-100~103, AC-REC-32~34) 구현·테스트 존재 — exact `product_id` 항상 제외 + 소모품 카테고리 억제(MVP 단순판=소모품 boolean) + non-blocking 되돌리기 칩(`suggestions`) + 게스트 스킵. 정교한 재구매/다양성 모델은 MVP 비범위(EX-9)임을 회귀 테스트에 반영(고도화 미구현 경계).
- [ ] 멀티턴 reset(결정 14-G, REQ-REC-054~056, AC-REC-35~37) 구현·테스트 존재 — add/replace/reset 3종 전이가 `decompose` 단일 출력에서 파생(별도 분류 호출 없음, decompose 1회 유지) + 범용 명시 제약(config `multiturn.carry_on_reset`, 기본 `["price"]`) 캐리 + "~도+둘 다 상품" Case 3 승격. 캐리 목록·판별 임계는 config 주입(하드코딩 금지) 회귀 테스트 포함. 정교한 부분 캐리·병렬 승격 신뢰성 판별은 MVP 비범위(EX-10)임을 회귀 테스트에 반영(고도화 미구현 경계).
- [ ] §9의 미해결 항목이 후속 SPEC/이슈로 등록됨.

---

## 9. 미해결 / 후속 항목 (Open Questions & Follow-ups)

> **시점 관례** 🔴 — 아래 OPEN 항목은 MVP를 **막지 않는다**. 해당 기능은 **MVP에서 단순 기본값(config)으로 동작**하며, OPEN은 그 기본값의 **정밀 확정·튜닝(정량 목표·경계 재조정)만 MVP 이후**로 미룬 것이다. 스모크 검증은 §6.12 평가 하니스로 MVP 중에도 수행한다. 반면 "MVP 비구현" 기능은 §2 Exclusions(EX-*)에 별도로 명시한다(그쪽은 MVP에 동작 자체가 없음).

- **OPEN-1 (가격 필터 vs 휘발성 필드 제외 긴장) — [해소됨, 결정 9-A]**: 결정 3("가격은 WHERE 필터") ↔ 결정 9("가격은 인덱스 제외")의 모순이 **결정 9-A로 해소됨**. 가격·재고는 Layer 1 컬럼으로 인덱스에 유지하되 enrichment·재임베딩 없이 **컬럼만 즉시 UPDATE하는 경량 경로**로 동기화하고, 결정 9의 "제외" 가드는 "**화면 표시값은 응답 시점 원본 조회**"로 한정 재해석한다. 따라서 `price_max` WHERE는 인덱스 자체 `price` 컬럼을 참조한다(REQ-REC-014). 잔여 의존: Spring 상품 변경 이벤트에 변경 유형 구분(가격/재고 vs 콘텐츠)이 필요 — 카탈로그 이벤트 SPEC 소관.
- **OPEN-2 (완화 순서 튜닝) — [해소됨, 결정 14-D]**: 고정 순서(`brand → rating → price ±20%`) 자체를 **폐기**하고, **config 상한 라운드(기본 3) 내에서 비명시·약한 조건(REQ-REC-047 `source == derived`) 우선 최소 이탈 자동 완화 + 명시 제약(특히 가격)은 제안 칩으로 사용자 위임**으로 대체함(REQ-REC-040/041/045/046). 수치 완화는 뭉뚱그린 비율이 아니라 결과가 나오는 최소 초과분 계산(REQ-REC-045). 잔여: 라운드 상한 기본값·최소 초과분 임계는 데모 카탈로그 실측 후 config로 조정.
- **OPEN-3 (rerank 실패 degrade 정책 확정) — [해소됨, 결정 14-B]**: "검색 순서 degrade" 정책을 **유지 확정**하고, 추가로 **rerank 출력 검증 실패(후보 외 ID/형식 오류/속성 대조 실패)를 동일 degrade 트리거에 포함**한다(REQ-REC-083, REQ-REC-062 보강). 근거: 검색 순서는 WHERE로 하드 제약이 이미 적용된 안전한 후보이며, 지시 미학습·후보 외 선택 실패가 실측된 사례(PALR, arXiv:2305.07622)에서 안전 폴백의 필요성이 확인됨. 즉시 `error` 반환 대안은 UX 열위로 기각. 잔여: 개인화·근거 부재로 인한 품질 저하는 평가 하니스(§6.12)로 계측.
- **OPEN-4 (지연 SLO 수치)**: §비기능의 지연 예산은 상대 순서/가이드라인이며 절대 초 단위 SLO는 부하 테스트 후 확정(TBD).
- **OPEN-5 (Case 3 아이템 분해 품질)**: 상황 → 쇼핑리스트 분해의 아이템 taxonomy/카테고리 매핑 정확도는 프롬프트 엔지니어링 의존. **정량 기준치는 §6.12 평가 하니스(골든셋 컴포넌트 회귀 REQ-REC-090 + 유저 시뮬레이터 종단 REQ-REC-091)로 측정하며, 문헌은 절대 기준치를 주지 않으므로 실측 후 확정한다**(결정 14-B).
- **OPEN-6 (속성 검증 가드 구현 수위) — [해소됨, 결정 14-B]**: 프롬프트 제약(REQ-REC-022)만으로는 불충분하므로, **프롬프트 제약 + 후보 데이터와의 결정적 사후 속성 대조**의 **2중 가드**로 확정한다(REQ-REC-082, §6.10). 근거: 데이터→텍스트 생성에서 프런티어 모델도 환각이 잔존함을 보인 FaithJudge(arXiv:2505.04847; claude-3.7-sonnet 12.7%, 프런티어 6.65~28%) — 프롬프트 제약만으로 방어 불가. 대조 실패 속성 주장은 미노출/degrade 처리(AC-REC-20/22).
- **OPEN-7 (로딩/진행 안내 SSE)**: time-to-first-content가 decompose+search+rerank에 걸쳐 있어 첫 `products`까지 지연 가능. 조기 `text.delta` 로딩 문구("창고에서 뒤지는 중…") 삽입 여부는 FE 팀과의 UX 계약 사항(TBD).
- **OPEN-8 (멀티턴 필터 누적 경계) — [부분 해소, 결정 14-G]**: 멀티턴 병합에 **reset(주제 전환)** 전이를 도입하여(REQ-REC-054), 주제가 바뀌면 직전 주제 필터를 폐기함으로써 **주제 전환 시 누적 경계는 해소**됨. 잔여 항목은 아래 신규 open questions로 분리 — 캐리 경계(OPEN-14)와 "N턴 이상 누적 시 정리" 같은 **추가 누적 상한**의 필요 여부는 세션 수명(결정 12, 비활동 10분)·thread checkpointer 정책과 함께 상위 그래프 SPEC이 소관한다(TBD).
- **OPEN-9 (`profile_summary` 계약) — [해소됨, 결정 16 / SPEC-PROFILE-001]**: 주입되는 요약의 포맷/최대 크기/필드 규약이 **SPEC-PROFILE-001로 확정·이관**됨 — 하이브리드 단일 마크다운 문자열(구조화 블록[FilterSet 매핑 속성 한정] + 산문 + 최근 맥락 섹션), 문자 기반 기본 1,000자 config 상한, 게이트 통과·미폐기 fact만 반영, 신규 회원 `None`. 계약의 소유·정의는 프로필 파이프라인(SPEC-PROFILE-001)이 가지며, 본 추천 서브그래프는 변경 없이 이를 **read-only 불투명 문자열**로만 소비한다(State `profile_summary: str | None` 무개정, REQ-REC-005/006 불변). 구조화 블록은 decompose의 `source == derived` 필터 원천으로 REQ-REC-047과 연계된다.
- **OPEN-10 (price_scope 오판 잔여 리스크)**: 총액↔개별 오판(REQ-REC-070/071)은 decompose 프롬프트 예시 + AC-REC-15로 완화하나, 허용 오판율 정량 목표는 골든셋 평가 후 확정(TBD). 사유: 프롬프트 판별 정확도는 실측 필요. **정량 기준치 측정은 §6.12 평가 하니스(REQ-REC-090/091)로 수행하며, 문헌은 절대 기준치를 주지 않는다**(결정 14-B).
- **OPEN-11 (예산 검증 index 가격 vs 원본 표시/결제 가격 괴리)**: 묶음 합산 검증(`verified_sum`)은 인덱스 가격 기준인데 최종 표시/결제는 원본 기준이므로, 결정 9-A의 경량 동기화 지연 구간에서 예산 근접 묶음이 결제 시 예산을 초과할 수 있다. 예산 버퍼(예: 예산의 N% 여유)·안내 문구 정책은 **TBD**. 사유: 상품 가격 변동성·기획 UX 정책 소관이며 임의 결정 금지.
- **OPEN-12 (단일 rerank 콜 vs 랭킹/근거 2단계 분리)**: MVP는 단일 Sonnet 콜(랭킹+근거+코멘트)을 유지하되 출력 스키마가 랭킹 결정을 근거문보다 먼저 확정하도록 강제한다(결정 14-B 주의/가드). "설명하기 쉬운 상품 편애"(easy-to-explain bias, Prism, arXiv:2511.16543) 편향이 평가 하니스(§6.12)에서 관측되면 랭킹/근거 2단계 분리를 재검토 — LLM 호출 수 상한(최대 2회) 초과 여부 포함하여 후속 결정. 사유: 편향 실측 전 선제 분리는 비용·복잡도 증가.
- **OPEN-13 (멀티턴 reset 오판율)**: 주제 전환(reset) vs 병렬 승격(Case 3) vs 정제(add/replace)의 오판 — 특히 "~도"의 이중성(정제 신호일 수도, 병렬 승격 신호일 수도) — 은 decompose 프롬프트 판별에 의존한다(REQ-REC-054/056). 허용 오판율 정량 목표는 **§6.12 평가 하니스(골든셋 컴포넌트 회귀 REQ-REC-090 + 유저 시뮬레이터 종단 REQ-REC-091)로 측정 후 확정한다(TBD)** — 문헌·데모엔 실사용 로그가 없으므로 시뮬레이터로 대체(결정 14-G). 정교한 주제 전환 감지·병렬 승격 판별은 고도화 범위(EX-10).
- **OPEN-14 (reset 캐리 경계)**: reset 시 캐리 대상이 가격(config `multiturn.carry_on_reset` 기본 `["price"]`)만으로 충분한지, 어디까지가 "범용 선호"(브랜드·용도 등)인지는 **TBD**. 실사용 로그 기반 재조정이 이상적이나 데모엔 로그가 없어 시뮬레이터(§6.12)로 대체하며, 캐리 목록은 config 주입이므로 스키마 변경 없이 조정 가능하다(REQ-REC-055, 결정 14-G). 정교한 부분 캐리 규칙은 고도화 범위(EX-10).
- **OPEN-15 (상품 질문 → 추천 재호출 진입 계약, 결정 17)**: 상품 질문 흐름(예: "이 수박 달아?")이 리뷰 분석 집계(결정 10-A) 기반 답변이 **부정적**일 때 본 서브그래프를 재호출해 대안 추천(당도 좋은 수박)으로 패널을 전환한다 — 이때의 진입 계약(filters 구성: 카테고리 승계 + 질문 속성의 `semantic_query` 우대, 부정 판정 임계, 같은 응답 내 LLM 콜 계수[재호출은 별도 실행으로 계수], 패널 `update` 전환 규칙)은 **상품 질문 흐름 SPEC(별도)에서 확정**한다(TBD). 본 서브그래프는 재호출을 일반 진입과 동일 계약(intent router 경유)으로 취급하며 내부 변경이 없다.

---

## 비기능 요구사항 (Non-Functional Requirements)

> 하드 시간 추정을 두지 않는다. 지연은 상대적 예산/우선순위로 표현하며 절대 초 단위 SLO는 OPEN-4로 유예한다.

### 지연 예산 가이드라인 (노드별, 상대적)

- `search`(단일 SQL 왕복)는 세 노드 중 가장 가벼운 지연이어야 한다.
- `decompose`(Haiku 1회, 소형 구조화 출력)는 중간 수준.
- `rerank`(Sonnet 1회, 최대 30개 후보 입력)가 **지배적 지연/비용원**이다 — 최적화 우선순위 High.
- Case 3의 병렬 검색은 SQL 쿼리 수만 늘고 LLM 호출은 늘지 않으므로 지연 증가는 검색 병렬도에 의해 흡수되어야 한다.
- 첫 콘텐츠(`products`)는 rerank 완료 후에 나오므로, 지각 지연 완화를 위한 조기 로딩 안내는 OPEN-7로 유예.

### 토큰/비용 가드레일

- **LLM 호출 수 상한 (결정 14-E 완화)**: 서브그래프당 LLM 호출은 **config 상한(기본 2, Case 3 묶음 병렬 시 예: 최대 4)** 내로 제한한다 — 이전 판의 경직된 "정확히 2회" 표현을 config화한 것이다. `decompose`는 **항상 1회**(변경 없음). 랭킹·선택 LLM 호출은 config 선택 전략에 따라 방식1(코드 per-item + LLM 코멘트 1회)·방식3(단일 콜)이면 1회, 방식2(묶음 병렬)이면 config 상한 내 소수 병렬 콜이다(REQ-REC-023/097). 니즈 수만큼의 무제한 fan-out은 여전히 금지. 총액 예산 보정 루프(REQ-REC-074/076), **후보 순서 무작위화(REQ-REC-080)·리랭크 출력 검증(REQ-REC-081/082/083, 결정 14-B)**, **0건 완화 재검색·최소 초과분 계산·제안 칩 예상 수(COUNT)(REQ-REC-040/045/046, 결정 14-D)**, 그리고 **니즈당 병렬 검색(SQL)·니즈당 후보 반비례 축소·코드 per-item 결정론 선택(REQ-REC-012/096/097, 결정 14-E)**은 모두 **결정론적 코드·SQL**이므로 LLM 호출이 아니다. 재시도는 예외.
- **재랭킹 입력 크기**: 후보는 `Candidate`의 압축 표현(전체 `search_doc` 아님, `doc_snippet` 발췌)으로 직렬화하여 Sonnet 입력 토큰을 통제한다.
- **Sonnet 토크나이저 보정**: Sonnet 5는 동일 텍스트 대비 ~30% 토큰 증가(결정 5) — 비용 모델링 시 `count_tokens`로 재기준.
- **프롬프트 캐싱**: 공유 시스템 프롬프트는 프롬프트 캐싱하여 ITPM 한도에서 제외(결정 5)되도록 한다(데모 동시접속 대비).
- **config 주입 기본값**: `top_k = 30`, 재랭킹 입력 30, 최종 노출 5~8, `max_relaxation_rounds = 3`(결정 14-D), 수치 최소 초과분 계산 임계, 근거 1문장/상품, `rank.strategy`(A=`llm`/B=`scoring`, 결정 14-C), 추천 스냅샷 로깅 활성화 — 그리고 **결정 14-E**: 총 rerank 입력 예산(예: ~40)·니즈당 `top_k` 반비례 산식(고정 30 아님)·Case 3 랭킹·선택 전략(`case3.select_strategy` = `code_per_item`[기본]/`llm_bundle_parallel`/`single_call`)·LLM 콜 상한(기본 2, 묶음 병렬 시 예 최대 4)·priority 우선 노출 임계(결정 14-H) — 전부 `core/config.py` 주입(하드코딩 금지).

### 안전/일관성 불변식 (must-hold)

- 가격 등 정확 제약은 항상 WHERE, 인덱스 자체 price 컬럼 기준(REQ-REC-014, AC-REC-08).
- 총액 예산 합산은 항상 코드 결정론적 검증, LLM 산수 불신뢰(REQ-REC-074, AC-REC-14/17).
- `products` 이벤트 정확히 1회(REQ-REC-031, AC-REC-11).
- 명시적 제약 무단 위반 금지(명시 제약은 제안 칩 동의 전 자동 완화 금지), 완화는 config 상한 라운드 내 + 매 완화 투명 알림, 예산 초과 시 아이템 무단 누락 금지(REQ-REC-043/045/046/075, 결정 14-D).
- 화면 표시용 가격/재고 권위값 미반환 — 원본 오버레이(REQ-REC-032, AC-REC-10).
- **리랭크 노출 전 후보 순서 무작위화(유사도순 나열 금지) — config 주입 셔플**(REQ-REC-080, AC-REC-18, 결정 14-B).
- **리랭크 출력 `product_id`는 항상 검색 후보 집합(`candidates`)의 부분집합** — 후보 외 ID 미노출(REQ-REC-081, AC-REC-19, 결정 14-B).
