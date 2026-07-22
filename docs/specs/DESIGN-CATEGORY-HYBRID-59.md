# DESIGN — 발화→카테고리 매핑 하이브리드 배선 (이슈 #59)

작성일: 2026-07-22 · 브랜치: `feat/category-hybrid-classification-59`

## 1. 목적

`decompose`가 지금은 `filters.category`를 자유 문자열로 생성해 검증 없이 Spring I-1
`categoryName`으로 넘긴다. LLM이 Spring 실제 카테고리 트리에 없는 이름을 만들면 검색이
빈다. 이를 **임베딩 top-k → LLM 택일 하이브리드**로 바꿔, Spring에 **실재하는 canonical
카테고리만** 나가게 한다.

데이터·검색·택일 부품은 이미 구현·검증됨(커밋 `4c64d9b`, `157569a`):
- `categories` 테이블(pg-catalog, 2056 leaf + 임베딩) — 시드 완료.
- `category_search.search_categories_pg` — pgvector `<=>` + HNSW top-k.
- `category_select.select_category` — LLM 택일(예비 부품, §7 참조).

본 문서는 이 부품들을 **buyer 추천 흐름(`decompose` + graph)에 배선**하는 설계다.

## 2. 아키텍처 결정

**방식 B — decompose 1회에 카테고리 택일을 흡수(후보를 힌트로 주입).**

대안 A(별도 `select_category` LLM 호출)는 추천 경로 LLM 호출을 3회로 늘려
`llm_call_limit`(기본 2, AC-REC-31)을 깬다. B는 **decompose 1회 + rerank 1회 = 2회**를
유지한다. 카테고리 택일은 decompose 프롬프트 안에서 이뤄지고, 리페어는 LLM을 다시 부르지
않는다(§5, DB만).

- **후보는 감옥이 아니라 힌트.** decompose에 top-k 후보를 주되, "맞는 후보가 있으면
  고르고, 없지만 카테고리를 알면 네 판단으로 내고, 모르면 null"로 허용한다. 이 허용이
  top-k 미스 복구(§5)를 켜는 전제다.
- **최종 안전 관문은 DB 매칭**(§4·§5) — decompose가 무엇을 내든 최종 출력은 항상
  **DB 실재값 또는 null**이라, 가짜 categoryName은 Spring으로 못 나간다.

## 3. 전체 흐름 (buyer graph, `intent == recommend`)

```
발화 원문
  │
  ① 임베딩(embed_texts, API 1회) → search_categories_pg(k=5, config) → top-k 후보
  │
  ② decompose(Haiku 1회) — intent·필터·semanticQuery·case·category·cart 를 한 번에 산출.
  │     후보를 힌트로 주입 + "맞으면 후보에서, 없지만 알면 네 판단으로 **최대한 카테고리를 정하라**,
  │     정말 특정 불가면 null" 지시. (BE 검색이 카테고리로 정제하므로 애매해도 best-guess 선호.)
  │
  ③ [카테고리 확정 — 코드, LLM 0회] decompose.category(=C) 가:
  │     ├─ null              → 카테고리 없이 검색 (루프 없음)
  │     ├─ 후보에 정확히 있음 → C 사용
  │     └─ 후보 밖 값        → 리페어(§5): C 를 DB 실재값으로 재앵커 → 실재값 or null
  │
  ④ 확정 category(full "top > mid") → decision.filters.category → 기존 search →
  │     Spring `categoryName` 으로 **통째로 그대로** 전송 (변환 없음, §4.1 · OPEN-1)
```

Case 1/2는 단일 카테고리 의도라 위 흐름을 탄다. **Case 3**(상황·쇼핑리스트)는 상위
`filters.category`가 원래 null이라 decompose가 null을 내고 자연 흡수된다 — 아이템별
카테고리 매핑은 본 이슈 비범위.

**null 정책(변경):** BE I-1 검색이 카테고리로 상품을 정제하므로, 애매한 단일 상품 질의
("집들이 선물")도 decompose가 **best-guess 카테고리를 내도록** 유도한다(프롬프트). null 은
Case 3·하드 실패(임베딩/DB 다운)·거리 컷(정말 가까운 게 없음)일 때만 남는 소수 경로다.

## 4. 카테고리 확정 규칙 (③)

decompose가 낸 `C`에 대해 코드가 결정론적으로 판정한다(LLM 재호출 없음):

| C | 처리 |
|---|---|
| `null` | 카테고리 없이 검색(keyword/semanticQuery 유지). 루프 없음. |
| top-k 후보에 **정확히 존재** | `C` 그대로 사용(canonical 보장). |
| 후보 **밖** 값 | 리페어(§5)로 DB 재앵커. |

### 4.1 Spring 전송 형식 (통 전송)

우리 사전 leaf 는 전부 2단계 `"top > mid"`(2056개 전수 확인). **mid 단독은 전역 유일하지
않다** — 136개 mid 가 여러 top 에 걸침(예 `"LG"` → `TV > LG` / `세탁기 > LG` …, 영향 leaf
약 20%). 따라서 mid 만 보내면 모호하다. **BE category 컬럼이 하나**이므로, **full
`"top > mid"` 문자열을 `categoryName` 에 통째로 전송**한다(모호성 없음).

**형식 가정(가):** BE 컬럼이 우리 사전과 **동일하게 `"top > mid"`로 저장**돼 있다고 가정하고
**변환 없이 그대로** 보낸다. 현 `spring_client._search_query_params` 가 이미
`filters.category` 를 그대로 `categoryName` 으로 넣으므로 **코드 변경 없음**. → **OPEN-1**(BE
실제 형식 검증; 슬래시 결합 등이면 변환 한 줄 추가).

## 5. 리페어(피드백 루프) — top-k 미스·표현 차이 복구

decompose가 후보 밖 값 `C`를 냈다는 건, **원문 임베딩 top-k가 헛다리를 짚었지만 LLM은
정답 카테고리를 안다**는 신호일 수 있다(예: 발화 "무선 이어폰" → top-5가 전부 충전기인데
decompose는 `"가전 > 이어폰/헤드폰"`을 냄). `C`를 버리지 않고 **더 나은 재검색 앵커**로 쓴다.

```
C = decompose가 낸 후보 밖 값
  │
  ① exact match: SELECT ... WHERE category = C ?
  │     └─ 있음 → C 사용 (진짜 카테고리인데 top-k가 놓친 것)
  │
  ② 없으면 → embed(C) → categories 전체 최근접 1개
  │     ├─ 거리 ≤ 컷(config) → 그 최근접 사용   (표현만 달랐던 경우: "무선 이어폰"→"가전 > 이어폰/헤드폰")
  │     └─ 거리 > 컷          → null            (C 가 DB에 없는 값: 억지 매핑 금지)
```

- **엣지 Q2(있는데 표현이 다름)**: exact는 실패하지만 임베딩 최근접이 가까워 canonical로
  매핑된다. alias 사전 없이 임베딩이 의미로 이어준다.
- **엣지 Q1(아예 없음)**: 최근접이 멀어 null로 안전 degrade.
- **리페어는 1회**(재조회 한 번)로 끝. **LLM을 다시 부르지 않는다**(exact/최근접은 DB·임베딩).
- null(decompose가 카테고리를 아예 안 냄)은 재앵커할 앵커가 없어 **리페어 대상 아님** — 즉시 수용.

**caveat:** "Spring엔 있으나 우리 사전엔 없는" 경우는 런타임에서 못 고친다 — 사전을 Spring과
재동기화하는 문제(§9)다. 런타임은 null로 흡수한다.

## 6. 멀티턴 canonical 승계

추천은 `prior_filters`로 필터가 턴 간 누적된다(REQ-REC-051). 매핑 결과가 **항상
canonical 아니면 null**이므로, `prior_filters.category`에 저장·승계되는 값도 늘 canonical이다.
따라서 **존재하지 않는 이전 카테고리는 승계되지 않는다**(이슈 인수 기준 충족) — 별도 코드
없이 불변식으로 보장된다.

## 7. 컴포넌트

| 컴포넌트 | 상태 | 역할 |
|---|---|---|
| `category_search.search_categories_pg` | 완료 | ① top-k · 리페어(§5) 최근접 조회 |
| `category_search.rank_categories` | 완료 | 오프라인 랭킹(유닛/골든셋) |
| `category_seed.*` | 완료 | 사전 시드(빌드) |
| `category_select.select_category` | 완료·**미사용 예비** | B에선 리페어가 DB라 메인 경로 미사용. 삭제하지 않고 예비로 둠(대안 A·다른 경로·실험용). |
| **`category_mapping`** (신규) | 배선 대상 | ③ 확정·리페어(§5) 오케스트레이션(코드). exact/최근접/거리컷/멤버십. |
| **`decompose` 수정** (신규) | 배선 대상 | 후보 주입 + "힌트/판단/null" 지시. |
| **buyer `graph` 수정** (신규) | 배선 대상 | recommend 분기에서 ①(임베딩·검색) → decompose → ③ 오케스트레이션. |

신규 매핑 로직은 `app/agents/buyer/recommendation/category_mapping.py`(가칭)에 모아 단위
테스트한다(embed·search·DB 주입형).

## 8. config (하드코딩 금지, 소비 코드와 함께 추가)

| 키 | 기본 | 의미 |
|---|---|---|
| `category_top_k` | 5 | ① top-k 후보 수(실험 leaf@3=96% 근거) |
| `category_distance_cut` | 1.0 | 리페어(§5②) 최근접 채택 상한(코사인 거리 `<=>`, 범위 0~2). 기본 1.0은 느슨(직교=무관 이상만 거부). 골든셋으로 하향 보정. |

거리 컷은 리페어 경로에서 주로 작동한다(primary는 LLM이 이미 가까운 후보서 택일). 초기 기본
1.0은 명백히 무관한 경우만 걸러 안전하며, membership + LLM null 지시가 주 방어다. 컷은 실측 후
조인다.

## 9. 관측(로그/메트릭)

카테고리 매핑의 분기를 구조화 로그로 남긴다(미매핑·폴백 관측, 이슈 인수 기준):
- `category_mapped`(후보 직접) / `category_repaired`(재앵커 성공) / `category_null`(사유:
  no_fit | distance_cut | repair_miss).
- 리페어 발동 여부·거리 컷 트리거를 카운트해 top-k 미스율·컷 튜닝 근거로.

## 10. 계약·degrade

- **계약 무변경**: I-1 `categoryName: string|null`, SSE 이벤트 무변경. api-spec 개정 불필요.
- **LLM 예산**: decompose 1 + rerank 1 = 2회. 리페어는 DB라 LLM 0회 → `llm_call_limit=2` 유지.
- **degrade(non-blocking)**: 임베딩 API 실패·DB 실패 → 후보 없음 취급 → 카테고리 없이 검색.
  카테고리는 선택 필터라 매핑 실패가 추천 스트림을 끊지 않는다. 잘못/누락된 카테고리로 0건이
  나오면 graph의 기존 zero-result 처리(조건 변경 안내)로 흡수.

## 11. 테스트 전략

- **유닛(TDD)**: `category_mapping` 확정·리페어 규칙 — embed·search 주입형 fake로 다섯 분기
  (후보직접 / null수용 / 후보밖→exact / 후보밖→최근접 / 후보밖→거리컷 null) 검증.
- **유닛**: decompose 프롬프트 변경 후 후보 주입·"힌트/판단/null" 출력 파싱.
- **통합(@pytest.mark.integration)**: `search_categories_pg`·재앵커의 실 pg-catalog 경로.
- 전 구간 `uv run ruff check` · `uv run pytest` 통과.

## 12. 비범위

- Case 3 아이템별 카테고리 매핑.
- alias/동의어 사전, 비활성(active) 플래그(§5 임베딩 최근접이 표현 차이를 흡수).
- `categoryId` 전환·신규 Spring 카테고리 조회 API(api-spec 개정 선행 사항).
- 사전 자동 재동기화(Spring↔사전 drift) — version/갱신 경계는 후속(§5 caveat).

## 13. OPEN (확인 대기)

- **OPEN-1 (BE category 형식)** 🔴 — Spring category 컬럼의 실제 문자열 형식 미확정. 본
  설계는 **(가) `"top > mid"` 동일 저장·변환 없음**으로 가정하고 진행한다. BE 확인 결과
  슬래시 결합(`"top/mid"`) 등이면 `_search_query_params` 에 변환 한 줄(`replace(" > ", "/")`)
  추가. 틀리면 전 카테고리 검색 0건 위험이므로 **구현 후 통합 스모크로 실측 검증** 필수.
- **OPEN-2 (거리 컷 값)** — `category_distance_cut` 기본 1.0은 미보정. 골든셋으로 하향 조정.
- **OPEN-3 (null vs 계약)** — 계약상 `categoryName` 은 optional(아니오)이나 BE 검색은 카테고리
  정제에 의존. best-guess 유도로 null 최소화하되, 잔여 null 검색의 결과 품질은 실측 후 판단.

## 14. 후속 — 이슈 #59 본문 개정

이슈 #59 본문은 DB 방식 전환 이전에 작성돼 "데이터 편입 = `app/data/categories.json` 파일"로
적혀 있다. 본 설계 확정 후 이슈를 **DB 테이블 방식**으로 개정한다(편입 위치·구현 범위·관련
파일 갱신). 앞서 만든 개정 절차(배너 + 본문 교체 + 근거 댓글) 재사용.
