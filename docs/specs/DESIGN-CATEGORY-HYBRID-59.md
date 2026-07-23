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
  ②a [멀티턴 승계 가드 — PR #73 #12/#19] 이번 턴에 카테고리 신호가 전혀 없고(categoryQueries 가
  │     비었거나 raw·query 모두 없는 leg 만) 이전 턴 카테고리(prior)가 있으면, 재매핑 없이 prior 를
  │     그대로 승계한다(리파인 턴 "더 저렴한 걸로"). raw 든 query 든 신호가 하나라도 있으면(신규
  │     상황형 질의 포함) ②b 매핑을 탄다 — prior 로 하이재킹하면 fan-out 이 죽어 #59 문제 재발.
  │
  ②b [카테고리 매핑 — 코드, LLM 0회] 각 추측 category(raw)마다:
  │     ├─ raw 가 DB에 exact match       → raw 사용
  │     ├─ raw 있으나 exact 아님          → embed(raw) → 최근접 1개 사용 (거리컷 없음, 항상 채택)
  │     ├─ raw==null·query 있음           → embed(그 leg 의 query) → top-1 (앵커 raw→query, #17)
  │     ├─ raw·query 모두 없음(빈 리스트)  → leg 없음 → 무필터 검색 (발화 강제 매핑 안 함, #22)
  │     └─ 하드 실패(embed/DB 다운)       → 빈 legs → filters.category=None (전체 검색, §5·#20)
  │     → canonical 리스트(순서보존 dedup, category_fanout_max 절단). 성공 흐름은 항상 ≥1 canonical.
  │     매핑 결과 없음/하드실패/호출 예외는 모두 빈 legs → filters.category=None (canonical-or-null, #13·#18·#20).
  │
  ③ fan-out 검색 — canonical 카테고리마다 Spring I-1 검색 leg 를 만들어 병렬 실행,
  │     결과를 병합(productId dedup + round-robin 인터리브 + merge_cap 절단).
  │
  ④ 병합 결과 → 기존 dedup(최근구매)·소모품억제·rerank(Sonnet 1회)·push(I-21 id배열)·products.ready.
```

각 leg 의 `categoryName` = canonical(full `"top > mid"`)을 변환 없이 통째로 전송(§4.1 · OPEN-1).

## 4. never-null 정책

BE I-1 검색이 카테고리로 상품을 정제하면 검색 품질이 크게 오른다. 따라서 **카테고리 신호가 있는
질의는 성공 시 항상 canonical 을 낸다**(never-null). 단 (a) **카테고리 신호가 아예 없는
category-agnostic 질의**("5만원 이하 아무거나")는 카테고리를 강제하지 않고 무필터로 두고(#22),
(b) **검증이 불가능한 실패**(하드 실패 등)는 canonical 이 아닌 null 로 degrade 한다 — 미검증 raw 를
내보내는 대신 카테고리를 빼고 keyword 로 검색한다(§5·#20):

- decompose는 애매한 질의("집들이 선물")도 best-guess를 내도록 유도(정말 모르면 category=null 이라도
  **query 를 실으면** 그 query 로 임베딩해 흡수, #17). raw·query 가 모두 없으면 카테고리 신호 없음(#22).
- **거리 컷 null 없음** — 최근접이 멀어도 채택한다(억지라도 canonical 을 보낸다). 잘못된
  카테고리로 0건이 나오면 graph의 기존 zero-result 안내로 흡수.
- **canonical-or-null 불변식**: Spring `categoryName` 엔 canonical 또는 null(생략)만 나가고, 미검증
  raw 는 어느 경로로도 새지 않는다. 매핑 결과 없음(#13)·하드 실패(§5·#20)·매핑 호출 예외(#18)
  모두 빈 legs → `filters.category=None`.

### 4.1 Spring 전송 형식 (통 전송)

사전 leaf 는 전부 2단계 `"top > mid"`(2056개 전수 확인). **mid 단독은 전역 유일하지 않다** —
136개 mid 가 여러 top 에 걸침(예 `"LG"` → `TV > LG` / `세탁기 > LG` …, 영향 leaf 약 20%).
따라서 mid 만 보내면 모호. **BE category 컬럼이 하나**이므로 **full `"top > mid"` 문자열을
`categoryName` 에 통째로 전송**한다.

**형식 가정(가):** BE 컬럼이 사전과 동일하게 `"top > mid"`로 저장돼 있다고 가정하고 **변환
없이 그대로** 보낸다. `spring_client._search_query_params` 가 이미 `filters.category` 를 그대로
`categoryName` 에 넣으므로 코드 변경 없음. → **OPEN-1**(BE 형식 검증; 슬래시 결합 등이면 변환
한 줄 추가).

## 5. 실패 degrade — leg 단위 격리 (canonical-or-null, PR #73 #20·리뷰)

임베딩 API(Google)나 카테고리 DB(pg-catalog)가 요청 시점에 죽으면 추측을 DB로 보정할 수
없다. 이때 **미검증 raw 를 canonical 처럼 내보내지 않는다** — 그 leg 는 canonical 없이 드롭해
`filters.category=None`(전 leg 드롭 시)으로 두고 **카테고리를 빼고 keyword·가격·브랜드로 검색**한다.
실패는 **leg 단위로 격리**한다(전면 degrade 아님):

- **exact 매치는 보존**: exact_lookup(DB 직접 조회)은 그 자체로 canonical 검증이라 임베딩 경로
  (embed/search)와 독립적이다. 임베딩 API 가 일시 오류여도 이미 확정된 exact 매치 leg 는 유지하고,
  임베딩 보정이 필요한 leg 만 드롭한다(exact_lookup·embed/search 를 각각 별도 try 로 격리).
- **leg별 search 격리**: fan-out gather 는 `return_exceptions=True` — leg 하나의 순간 실패(pg
  경합·타임아웃)가 정상 leg 까지 날리지 않게 그 leg 만 unmapped 드롭한다(§6 leg 격리와 일관).
- raw 는 검증이 필요할 만큼 자주 틀리므로(이 PR 존재 이유) 검증 불가 시 raw 를 보내면 가짜
  categoryName 으로 0건이 날 위험이 크다. `categoryName` 은 계약상 선택(string|null)이라, 빼면
  keyword 로 전체에서 넓게 찾아 **0건보다 안전**하다.
- graph 바깥 except(#18)·미매핑(#13)과 **동일한 canonical-or-null 처리** — Spring 엔 canonical
  (exact 또는 search 히트) 또는 null 만 나간다. 실패 경로가 일관된다.
- **트레이드오프**: keyword 도 없는 순수 카테고리 질의는 전 leg 드롭 시 매우 넓게 검색되나(0건은
  아님) 드문 케이스다. (구설계의 "raw 그대로 전송"은 PR 전제 — raw 는 자주 틀림 — 과 모순이라 폐기.)

이는 카테고리 매핑만의 degrade다. Spring 상품검색은 별개 서비스라 매핑 DB가 죽어도 검색
자체는 살아있을 수 있다.

## 6. 멀티 카테고리 fan-out (경량)

상황형 질의("유럽여행 준비물")는 decompose가 카테고리 여러 개를 추출한다. 각각 canonical
매핑 후 **카테고리마다 Spring I-1 검색을 병렬 실행**하고 결과를 병합한다.

- **leg 구성**: canonical 카테고리마다 `decision.filters`를 복사해 `category`·`keyword`(해당
  `query` 있으면 그걸로)·`limit=category_fanout_per_cat_limit`로 교체.
- **병렬**: `asyncio.gather`, 각 leg(`_leg`)는 실패를 leg 단위로 격리한다 — `SpringUnavailableError`
  뿐 아니라 예상외 예외도 삼켜 `None`(그 leg만 드롭, 로그 `search_leg_failed`). 한 leg의 미처리
  예외가 gather→스트림 상위로 전파돼 SSE 전체가 죽지 않게(단일검색·최근구매 조회도 동일 격리).
  `CancelledError`는 전파(협조적 취소 보존). AI→Spring 3s 타임아웃은 기존 `spring_timeout_s` 재사용.
- **병합**: productId dedup + round-robin 인터리브(한 카테고리가 rerank 입력 독점 방지) +
  `category_fanout_merge_cap` slice 절단(`merged[:cap]` — decompose `_parse`·`_dedup_truncate`와
  동일 규약, cap≤0이면 0개).
- 전량 leg 실패 → 기존 `SEARCH_FAILED`. 성공했으나 전체 0건 → 기존 zero-result 안내.

**비범위(Case 3 기능):** priority(필수/권장/선택), 예산 배분(budget), SSE `groups`(경로 B 평면
유지), 풀 `ShoppingItem` 스키마. 이 기능들은 코드에 없고(조사 확인) 별도 이슈 소관이다. 단일
카테고리 질의는 canonical 리스트 길이 1로 동일 경로를 탄다.

## 7. 멀티턴 canonical 승계

매핑 결과가 항상 canonical(§4)이므로 `prior_filters.category`에 저장·승계되는 값도 늘
canonical이다. **존재하지 않는 이전 카테고리는 승계되지 않는다** — 불변식으로 보장. 멀티
카테고리 턴은 대표 카테고리(canonical[0])만 `filters.category`로 승계(나머지는 턴 국소, 비범위
트레이드오프로 명시 — #14). 이 트레이드오프의 가시적 결과: fan-out 턴은 조건 칩에 검색한
카테고리 전부를 조인해 보여주지만, 바로 다음 리파인 턴("더 저렴한 걸로")의 실제 재검색은
canonical[0] 한 개로 좁혀진다 — 칩(N개)과 재검색(1개)이 어긋난다. multi-leg 상태를 스레드에
저장하면 해소되나 단일 `filters.category` 필드·칩 제거 왕복(field 단위)과 충돌해 #14 로 유보한다
(PR #73 리뷰).

**승계 트리거(코드 레벨 가드, §3 ②a, PR #73 #12/#19):** 카테고리 병합은 price/brand 처럼
decompose 프롬프트("PRIOR_FILTERS 병합")로도 유도하지만(#10a), Haiku 가 놓쳤을 때의 결정적
안전망으로 그래프가 처리한다 — **이번 턴 카테고리 신호가 전혀 없을 때만**(categoryQueries 가
비었거나 raw·query 모두 없는 leg 만) prior 를 재매핑 없이 그대로 승계한다. raw·query 중 하나라도
있으면 신규 검색 의도로 보고 ②b 매핑을 태운다(신규 상황형 질의를 prior 로 하이재킹 방지, #19).

**의도된 기본값 — "신호 없음"은 리파인으로 간주한다(한계 명시, PR #73 리뷰):** prior.category 가
있고 이번 턴에 카테고리 신호가 없으면 항상 **리파인(prior 유지)**으로 처리한다. 그러나 "신호 없음"은
세 의도에서 모두 나온다: (1) 리파인("더 저렴한 걸로" → 유지가 맞음), (2) 명시적 칩 제거(#21/#84),
(3) 자연어 카테고리-리셋("5만원 이하 아무거나" → #22 취지상 무필터가 맞음). 현재 가드는 셋을 못
가르고 전부 (1)로 처리하므로, 스레드 안에서 (3)은 prior 로 좁혀져 #22 무필터 규칙이 무력화된다.
셋을 가르려면 그래프 휴리스틱이 아니라 **decompose 가 발화 의미로 리파인/리셋 의도 신호(예:
`isRefinement`)를 산출**해야 한다 — 프롬프트·스키마 변경 + 리셋 규약(api-spec §3.1 칩 제거와 동일
뿌리)이라 **#84 로 이관**한다. 이 PR 은 첫 턴 무필터(#22)만 보장하고, 스레드 내 리셋 구분은 #84.

**canonical-or-null 불변식:** `filters.category` 에는 canonical(매핑 성공·prior 승계) 또는 None 만
들어간다 — 매핑 결과 없음(#13)·하드 실패(§5·#20)·매핑 호출 예외(#18) 모두 None. 미검증 raw 는
어느 경로로도 새지 않으므로, prior 승계도 항상 canonical 만 이어받는다(불변식이 모든 실패 경로에서
일관되게 성립).

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
| `category_top_k` | 5 | raw·query 앵커 최근접 조회 top-k |
| `category_fanout_max` | 5 | 턴당 최대 카테고리 수(프롬프트 상한 + 코드 절단) |
| `category_fanout_per_cat_limit` | 10 | leg 별 Spring `size`(≤30) |
| `category_fanout_merge_cap` | 30 | 병합 후 rerank 입력 상한 |
| `category_search_pool_max_size` | 10 | pg-catalog 검색 풀 max_size(fan-out 동시성 ≥ fanout, PR #73 리뷰) |

`category_distance_cut`은 **도입 안 함**(never-null 정책상 거리 컷 null 없음). 3s 타임아웃은
`spring_timeout_s` 재사용. `stream_first_token_timeout_s`는 로컬 미커밋 — 손대지 않음.

## 11. 관측(로그/메트릭)

카테고리 매핑 분기를 구조화 로그로: `category_mapped`(exact 직접) / `category_repaired`(최근접
보정) / `category_fallback_top1`(raw 없이 query 로 매핑) / `category_unmapped`(신호 있으나 히트 0건).
실패 격리(§5): `category_leg_search_failed`(leg별 search 예외·사유) / `category_embed_failed`(임베딩
경로 전면 실패·사유) / `category_exact_failed`(exact 조회 실패·사유). 리페어·폴백 빈도로 top-k
미스율·품질을, 실패 로그 빈도로 pg/embedding 장애를 관측.

## 12. 계약·비범위

- **계약 무변경**: I-1 `categoryName: string|null`(leg마다 1개), SSE 경로 B(push=id배열,
  products.ready={sessionId,listId}, groups 없음). api-spec 개정 불필요.
- **LLM 예산**: decompose 1 + rerank 1 = 2회. 매핑은 LLM 0회 → `llm_call_limit=2` 유지.
- **비범위**: Case 3 기능(ShoppingItem·priority·예산·groups), `categoryId` 전환, 사전 자동
  재동기화(Spring↔사전 drift), alias/active 사전.

## 13. 테스트 전략 (TDD)

- **유닛**(`test_category_mapping.py`): 매핑 분기(exact/raw 최근접/query 앵커 최근접/신호 없음→빈
  결과·무필터(#22)/하드실패degrade), 멀티 dedup·상한 절단, 로그 방출. embed·search·exact 주입형 fake.
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
