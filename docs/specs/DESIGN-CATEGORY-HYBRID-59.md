# DESIGN — 발화→카테고리 매핑 하이브리드 배선 (이슈 #59)

작성일: 2026-07-22 (개정) · 브랜치: `feat/category-hybrid-classification-59`

## 1. 목적

`decompose`가 지금은 `filters.category`를 자유 문자열로 생성해 검증 없이 Spring I-1
`categoryName`으로 넘긴다. LLM이 Spring 실제 카테고리 트리에 없는 이름을 만들면 검색이
빈다. 이를 **LLM 추측 → 임베딩 보정(방식 A) 하이브리드**로 바꿔, Spring에 **실재하는
canonical 카테고리만** 나가게 한다.

데이터·검색 부품은 이미 구현·검증됨(커밋 `4c64d9b`, `157569a`):
- `categories` 테이블(pg-catalog, 2056 leaf + 임베딩) — 시드 완료.
- `category_search.search_categories_pg` — pgvector `<=>` + HNSW top-k/최근접.
- `category_select.select_category` — 방식 B용 LLM 택일. **방식 A 채택으로 메인 경로 미사용**
  (삭제하지 않고 예비 유지).

본 문서는 이 부품들을 **buyer 추천 흐름(`decompose` + graph)에 배선**하는 설계다.

## 2. 아키텍처 결정 — 방식 A

**decompose가 카테고리를 직접 추측(여러 개 가능) → 각 추측을 임베딩으로 실제 DB 카테고리에
보정(exact match 또는 최근접).**

방식 B(발화로 top-k 후보를 뽑아 decompose에 힌트로 주입 → 택일)는 (a) 멀티 카테고리(상황형
질의)에 어색하고 — 발화 하나의 top-k를 여러 카테고리로 쪼갤 수 없음, (b) intent(recommend/
cart/general)는 decompose 후에 알 수 있어 cart/general 의도에도 임베딩 비용을 지불하게 된다.
방식 A는 decompose가 먼저 돌고(intent 확정), `recommend` 분기에서만 매핑하므로 낭비가 없고
단일·멀티가 하나의 메커니즘(추측→보정)으로 통일된다.

- **LLM 호출은 여전히 2회**(decompose 1 + rerank 1). 카테고리 보정은 DB·임베딩만, LLM 재호출
  없음.
- **최종 안전 관문은 DB 보정** — decompose가 무엇을 추측하든 최종 category는 항상 **DB 실재값**
  (exact 또는 최근접)이라 가짜 categoryName은 Spring으로 못 나간다. (하드 실패 예외는 §5.)

## 3. 전체 흐름 (buyer graph, `intent == recommend`)

```
발화 원문
  │
  ① decompose(Haiku 1회) — intent·필터·semanticQuery·categoryQueries·cart 산출.
  │     categoryQueries = [{category, query}, ...]  (상품/목적별 best-guess, 단일=1개·상황형=여러 개)
  │
  ② [recommend 분기, 카테고리 매핑 — 코드, LLM 0회] 각 추측 category(raw)마다:
  │     ├─ raw 가 DB에 exact match       → raw 사용
  │     ├─ raw 있으나 exact 아님          → embed(raw) → 최근접 1개 사용 (거리컷 없음, 항상 채택)
  │     ├─ raw == null                    → embed(발화) → top-1 사용 (발화 폴백)
  │     └─ 하드 실패(embed/DB 다운)       → raw 그대로(검증 없이) or 해당 leg 없음
  │     → canonical 리스트(순서보존 dedup, category_fanout_max 절단). **never-null: 항상 ≥1개.**
  │
  ③ fan-out 검색 — canonical 카테고리마다 Spring I-1 검색 leg 를 만들어 병렬 실행,
  │     결과를 병합(productId dedup + round-robin 인터리브 + merge_cap 절단).
  │
  ④ 병합 결과 → 기존 dedup(최근구매)·소모품억제·rerank(Sonnet 1회)·push(I-21 id배열)·products.ready.
```

각 leg 의 `categoryName` = canonical(full `"top > mid"`)을 변환 없이 통째로 전송(§4.1 · OPEN-1).

## 4. never-null 정책

BE I-1 검색이 카테고리로 상품을 정제하므로, **카테고리 없이는 사실상 상품을 못 찾는다.**
따라서 정상 흐름은 **항상 canonical 카테고리를 최소 1개** 낸다:

- decompose는 애매한 질의("집들이 선물")도 best-guess를 내도록 유도(정말 모르면 null 허용 →
  발화 폴백으로 흡수).
- **거리 컷 null 없음** — 최근접이 멀어도 채택한다(억지라도 카테고리를 보낸다). 잘못된
  카테고리로 0건이 나오면 graph의 기존 zero-result 안내로 흡수.
- **하드 실패**(임베딩 API·카테고리 DB 다운)만이 유일한 예외 경로다(§5).

### 4.1 Spring 전송 형식 (통 전송)

사전 leaf 는 전부 2단계 `"top > mid"`(2056개 전수 확인). **mid 단독은 전역 유일하지 않다** —
136개 mid 가 여러 top 에 걸침(예 `"LG"` → `TV > LG` / `세탁기 > LG` …, 영향 leaf 약 20%).
따라서 mid 만 보내면 모호. **BE category 컬럼이 하나**이므로 **full `"top > mid"` 문자열을
`categoryName` 에 통째로 전송**한다.

**형식 가정(가):** BE 컬럼이 사전과 동일하게 `"top > mid"`로 저장돼 있다고 가정하고 **변환
없이 그대로** 보낸다. `spring_client._search_query_params` 가 이미 `filters.category` 를 그대로
`categoryName` 에 넣으므로 코드 변경 없음. → **OPEN-1**(BE 형식 검증; 슬래시 결합 등이면 변환
한 줄 추가).

## 5. 하드 실패 degrade

임베딩 API(Google)나 카테고리 DB(pg-catalog)가 요청 시점에 죽으면 추측을 DB로 보정할 수
없다. 이때:

- decompose 추측(raw)이 있으면 **raw 를 검증 없이 그대로** 전송(never-null 유지, 대체로 실재
  카테고리라 동작, 아니면 0건→zero-result 흡수).
- raw 가 null이면 해당 카테고리 없음(그 leg 스킵) — 다른 leg 는 계속.

이는 카테고리 매핑만의 degrade다. Spring 상품검색은 별개 서비스라 매핑 DB가 죽어도 검색
자체는 살아있을 수 있다.

## 6. 멀티 카테고리 fan-out (경량)

상황형 질의("유럽여행 준비물")는 decompose가 카테고리 여러 개를 추출한다. 각각 canonical
매핑 후 **카테고리마다 Spring I-1 검색을 병렬 실행**하고 결과를 병합한다.

- **leg 구성**: canonical 카테고리마다 `decision.filters`를 복사해 `category`·`keyword`(해당
  `query` 있으면 그걸로)·`limit=category_fanout_per_cat_limit`로 교체.
- **병렬**: `asyncio.gather`, 각 leg 는 `SpringUnavailableError`를 삼켜 `[]`. AI→Spring 3s
  타임아웃은 기존 `spring_timeout_s` 재사용.
- **병합**: productId dedup + round-robin 인터리브(한 카테고리가 rerank 입력 독점 방지) +
  `category_fanout_merge_cap` 절단.
- 전량 leg 실패 → 기존 `SEARCH_FAILED`. 성공했으나 전체 0건 → 기존 zero-result 안내.

**비범위(Case 3 기능):** priority(필수/권장/선택), 예산 배분(budget), SSE `groups`(경로 B 평면
유지), 풀 `ShoppingItem` 스키마. 이 기능들은 코드에 없고(조사 확인) 별도 이슈 소관이다. 단일
카테고리 질의는 canonical 리스트 길이 1로 동일 경로를 탄다.

## 7. 멀티턴 canonical 승계

매핑 결과가 항상 canonical(§4)이므로 `prior_filters.category`에 저장·승계되는 값도 늘
canonical이다. **존재하지 않는 이전 카테고리는 승계되지 않는다** — 불변식으로 보장. 멀티
카테고리 턴은 대표 카테고리(canonical[0])만 `filters.category`로 승계(나머지는 턴 국소, 비범위
트레이드오프로 명시).

## 8. 컴포넌트

| 컴포넌트 | 상태 | 역할 |
|---|---|---|
| `category_search.search_categories_pg` | 완료 | ② 최근접/폴백 top-k 조회 |
| `category_search.exact_lookup` (신규) | 완료 | ② exact match(`WHERE category = ANY(...)`) |
| `category_search.rank_categories` | 완료 | 오프라인 랭킹(유닛) |
| `category_seed.*` | 완료 | 사전 시드(빌드) |
| `category_select.select_category` | 완료·**미사용 예비** | 방식 B용. 삭제 않고 예비 유지. |
| **`category_mapping.map_categories`** (신규) | 완료 | ② 추측→보정 오케스트레이션(embed·search·exact 주입형). never-null. **(canonical, query) leg 반환** — leg keyword 보존(§6). |
| **`decompose` 수정** | 완료 | `categoryQueries` 산출(§9), `filters.category` 제거. |
| **buyer `graph` 수정** | 완료 | recommend 분기에서 `map_categories` 호출 → `decision.category_legs`, 대표 canonical → `filters.category`. |
| **`recommendation/graph` 수정** | 완료 | fan-out 검색·병합(§6, `_merge_fanout_results`). |

## 9. decompose 출력 스키마 변경

- `_SYSTEM` 프롬프트 출력에서 `filters.category` 제거, 최상위에
  `"categoryQueries": [{"category": string|null, "query": string|null}, ...]` 추가.
  규칙: 상품/목적별 카테고리 최대한 추출(단일=1개, 상황형=여러 개), best-guess 우선(모르면
  null), `query`는 그 카테고리 검색용 짧은 키워드, 개수 상한 `category_fanout_max` 주입.
- `RouteDecision`에 `category_queries: list[CategoryQuery]`(추측, 매핑 전)와
  `category_legs: list[tuple[str, str | None]]`(매핑 후 (canonical, query) leg, 그래프가 채움) 추가.
  **매핑이 leg 별 query 를 보존**하는 이유: fan-out leg 마다 그 카테고리 전용 query 를 keyword 로
  써야 하므로(§6), canonical 만 담는 `list[str]` 로는 leg keyword 를 복원할 수 없다.
- `case: int`는 유지하되 미사용 명시(단일/멀티는 `len(categoryQueries)`로 판정).

## 10. config (하드코딩 금지)

| 키 | 기본 | 의미 |
|---|---|---|
| `category_top_k` | 5 | 최근접/발화폴백 조회 top-k |
| `category_fanout_max` | 5 | 턴당 최대 카테고리 수(프롬프트 상한 + 코드 절단) |
| `category_fanout_per_cat_limit` | 10 | leg 별 Spring `size`(≤30) |
| `category_fanout_merge_cap` | 30 | 병합 후 rerank 입력 상한 |

`category_distance_cut`은 **도입 안 함**(never-null 정책상 거리 컷 null 없음). 3s 타임아웃은
`spring_timeout_s` 재사용. `stream_first_token_timeout_s`는 로컬 미커밋 — 손대지 않음.

## 11. 관측(로그/메트릭)

카테고리 매핑 분기를 구조화 로그로: `category_mapped`(exact 직접) / `category_repaired`(최근접
보정) / `category_fallback_top1`(발화 폴백) / `category_hard_fail`(사유 포함). 리페어·폴백 빈도로
top-k 미스율·품질을 관측.

## 12. 계약·비범위

- **계약 무변경**: I-1 `categoryName: string|null`(leg마다 1개), SSE 경로 B(push=id배열,
  products.ready={sessionId,listId}, groups 없음). api-spec 개정 불필요.
- **LLM 예산**: decompose 1 + rerank 1 = 2회. 매핑은 LLM 0회 → `llm_call_limit=2` 유지.
- **비범위**: Case 3 기능(ShoppingItem·priority·예산·groups), `categoryId` 전환, 사전 자동
  재동기화(Spring↔사전 drift), alias/active 사전.

## 13. 테스트 전략 (TDD)

- **유닛**(`test_category_mapping.py`): never-null 5분기(exact/최근접/발화폴백/빈리스트정규화/
  하드실패degrade), 멀티 dedup·상한 절단, 로그 방출. embed·search·exact 주입형 fake.
- **유닛**(`test_decompose.py`): `categoryQueries` 파싱(단일/멀티/누락/null/절단).
- **유닛**(`test_recommendation.py` 확장): fan-out 병렬·병합·dedup·merge_cap, 일부 leg 실패,
  전량 실패→SEARCH_FAILED, 단일=기존 경로.
- **통합**(`@pytest.mark.integration`): 실 pg-catalog top-k·exact·최근접.
- fakes/stubs(`DEFAULT_DECOMPOSE`)에 `categoryQueries` 반영. 전 구간 ruff·pytest 통과.

## 14. OPEN (확인 대기)

- **OPEN-1 (BE category 형식)** 🔴 — Spring category 컬럼 실제 형식 미확정. `"top > mid"` 동일
  저장 가정으로 진행. 슬래시 결합 등이면 `_search_query_params`에 변환 한 줄. **구현 후 통합
  스모크(실제 검색 0건 여부)로 검증 필수.**

## 15. 후속 — 이슈 #59 본문 개정

이슈 #59 본문은 파일 기반·단일 카테고리·null 허용으로 쓰여 실제 설계(DB 방식·방식 A·
never-null·멀티 fan-out)와 어긋난다. 본 설계에 맞춰 이슈를 개정한다(배너 + 본문 교체 + 근거
댓글).
