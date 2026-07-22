# EVAL-OBS-PLAN-001 — 평가셋·베이스라인·관측 고도화 계획

> **버전**: v0.1.0 (초안) · **상태**: 제안 — 코드 미변경, 착수 승인 대기
> **작성**: 2026-07-22 · **동기**: 중간발표 수강생 피드백(RAG 프로젝트, 동일 평가자) 반영. 커머스 에이전트(jarvis-ai)로 번역.
> **소유 후보 코드**: `evals/`(신설) · `app/core/observability.py`(확장) · `app/core/config.py`(튜너블 주입) · `.github/workflows/ci.yml`
> **상위 계약**: 본 계획은 **와이어 계약(api-spec §3.x SSE·엔드포인트)을 바꾸지 않는다** — 전부 오프라인 하네스 + 로그 필드 확장이라 FE·Spring 계약 무영향. 계약 변경이 필요해지면 명세 개정을 먼저 한다(CLAUDE.md 계약 우선).
> **관련**: [SPEC-RECOMMEND-001](SPEC-RECOMMEND-001.md) · [SPEC-SELLER-001](SPEC-SELLER-001.md) · [SELLER-FINAL-RISKS](SELLER-FINAL-RISKS.md) · 통합 하네스(이슈 #35, `tests/integration/`).

---

## 0. 왜 지금 이 세 가지인가 (피드백 → 리스크)

동일 평가자가 RAG 발표 네 팀 **전부**에게 공통으로 때린 지점이 있고, 이번 커머스 에이전트 발표에서 **같은 질문이 반복**된다. 현재 jarvis-ai 코드에는 오프라인 평가셋·회귀 지표가 **전혀 없다**(`app/pipelines/compare.py`는 아티팩트 델타 비교용이지 검색/추천 평가가 아니다). 관측은 턴 단위 로그(`chat_request`)만 있고 **집계(p50/p95·비용·degrade율)가 없다**.

| 피드백 원문(요지) | 현재 공백 | 커버 파트 |
|---|---|---|
| "검색 서비스인데 왜 RAGAS가 핵심 지표?" / 지표 계산법 설명 | 커머스 지표 정의·계산식 없음 | **①** |
| "RAG 도입 효과 베이스라인 비교가 네 팀 전부 빠졌다" | 에이전트 vs 순진한 baseline 비교 없음 | **②** |
| "end-to-end 답변 정확도 미측정 (검색만 평가)" | 최종 추천/보고서 정확도 골든셋 없음 | **①** |
| "RAPTOR+파서 동시 교체로 confound, 원인 분리 안 됨" | 한 번에 한 축만 바꾸는 실험 규율 없음 | **②** |
| "질의 유형별 breakdown 없음 (단순조회 따로 안 봄)" | query_type 분해 지표 없음 | **①②** |
| "p50/p95 latency·쿼리당 비용 공개하라" | 집계·비용 산출 없음 | **③** |
| "운영/자동화·모니터링 — LLM은 probabilistic" | 회귀 감지 CI·degrade 집계 없음 | **①③** |
| "자체평가만으로 product fit 주장 어렵다(≥5명 검증)" | 사람 perception study 없음 | **①**(§1.6) |

**설계 원칙 3개**: (a) **결정론** — 라이브 LLM/Spring 의존 없이 재현. 이미 있는 `ScriptedLLM` + `httpx.MockTransport` Spring stub(이슈 #35 하네스)을 그대로 재활용한다. (b) **계약 불변** — 오프라인 + 로그 확장만. (c) **튜너블은 config 주입** — 임계·k·가격표를 `app/core/config.py` Settings 로(하드코딩 금지, CLAUDE.md).

---

## ① 골든 평가셋 + 커머스 지표 정의

### 1.1 핵심 메시지 — RAGAS가 아니다

jarvis는 RAG 문답이 아니라 **추천(경로 B) + 판매자 분석/HITL 쓰기** 서비스다. 따라서 대표 지표는 검색/추천 지표와 태스크 정확도이지 `answer_relevancy`가 아니다. 발표 appendix에 **"우리 서비스의 핵심 지표가 왜 이것인가 + 어떻게 계산되는가"** 한 장을 반드시 둔다(RAG 때 계산식 못 설명해서 깨진 팀 있음).

### 1.2 골든셋 레이아웃

```
evals/
  datasets/
    buyer_golden.jsonl      # 추천 케이스
    seller_golden.jsonl     # 분석/라우팅 케이스
    fixtures/
      catalog_snapshot.json # Spring I-1 검색 응답 고정본(결정론)
      spring_seller.json    # I-6/7/13~16 집계 고정본
  metrics.py                # 지표 계산 순수 함수(공식은 §1.4/1.5)
  run_buyer.py / run_seller.py
  ablation.py               # 파트 ②
  report.py                 # 표/CSV 출력
```

버전 관리: 골든셋은 리포에 커밋(리뷰 대상). 카탈로그 fixture는 실제 I-1/I-6 응답 스키마와 동일하게 만들어 `MockTransport`로 주입 → **HTTP 경계에서만 대역**(이슈 #35 원칙 유지: URL·`X-Internal-Token`·envelope 파싱이 실코드로 돈다).

### 1.3 buyer 골든 케이스 스키마

```jsonc
{
  "id": "buy-0007",
  "query": "5만원 이하 캠핑용 경량 버너 추천",
  "query_type": "conditional",           // simple | conditional | comparison | multi_constraint
  "expected_filters": {                   // decompose 정답(필터 추출 정확도용)
    "category": "camping/burner", "priceMax": 50000, "attrs": ["경량"]
  },
  "relevant_product_ids": [101, 205, 310],// 정답 집합(retrieval 지표 기준)
  "ideal_order": [205, 101, 310],         // rerank 이상 순서(nDCG gain)
  "budget": 50000,
  "profile_facts": ["백패킹 선호", "무게 민감"], // 개인화 ablation용
  "must_exclude": [999]                   // 예산초과·품절 등 하드제약 위반이면 실패
}
```

### 1.4 buyer 지표 — 정의와 **계산식**

리트리벌 층(검색·decompose 품질):

- **Recall@k** = |정답 ∩ 상위 k| / |정답|. k ∈ {5,10,20}(config `eval_buyer_k_list`).
- **Precision@k** = |정답 ∩ 상위 k| / k.
- **MRR** = mean(1 / 첫 정답의 순위). 첫 유효 추천이 얼마나 위에 오는지.
- **nDCG@k** = DCG@k / IDCG@k, DCG = Σ rel_i / log2(i+1). `ideal_order`로 IDCG 산출 — rerank 순서 품질의 핵심 지표.
- **Filter-Accuracy** = decompose가 뽑은 필터가 `expected_filters`와 일치하는 비율(필드별 정확·부분점수). "검색 서비스 대표 지표"에 대한 직접 답.

프로덕트/비즈니스 층(최종 산출 품질 — end-to-end):

- **Expose-Rate** = 노출 건수 ≥ `expose_min`(=5) 충족률. rerank degrade가 얼마나 자주 하드제약 폴백으로 빠지는지.
- **Hard-Constraint-Violation** = 예산초과·`must_exclude` 포함 노출 비율(**0 목표** — 값이 아니라 안전성 지표).
- **Rerank-Lift** = (rerank on의 nDCG) − (검색순서의 nDCG). rerank가 실제로 순서를 개선하는지(파트 ②와 공유).
- **Personalization-Lift** = (profile 주입 nDCG) − (profile=None nDCG). 개인화 효과(편향완화 perception study의 커머스판).

> RAGAS류(answer_relevancy 등)는 **쓰지 않는다**. 추천은 정답 집합 기반 순위 지표가 정합. 이유를 appendix에 1줄 명시.

### 1.5 seller 골든 케이스 + 지표

스키마(요지): `question`, `expected_lane`(analysis/product/general/apply/confirm/refused — 라우팅 정답), `period`, `spring_fixture_id`(고정 집계), `expected_findings`(수치 정답 — 예 `deviation_pct≈-42`), `expected_recommendations`(구조·건수).

지표:

- **Routing-Accuracy** = 정확 분류 / 전체. + **혼동행렬**(analysis↔general 오분류가 어디서 나는지). "모드를 사용자에게 안 떠넘기고 내부 라우팅"이라는 강점을 **숫자로** 증명(몇대몇 Fast/Thinking 지적의 대응).
- **Numeric-Grounding-Accuracy** = 보고서 내 수치가 `expected_findings`(=고정 fixture에서 코드로 산출한 정답)와 일치하는 비율. `verifier.check_numbers_grounded`를 정답 대조로 승격. → "end-to-end 정확도" 직접 답.
- **Verifier-Human-Agreement** = judge `ReportScore.total`(21/30 임계)과 사람 라벨의 상관/일치율. 자체 채점이 신뢰할 만한지 검증(자체평가 한계 대응).
- **Degrade-Disclosure-Correctness** = 데이터 결손 시 보고서가 degrade를 실제로 고지했는지(`check_degrade_disclosed` 정답 대조).
- **Calc-Layer 정확도(계산 3층 분리)** = Spring 수치 층 / AI 코드 판정 층 / LLM 해석 층 각각을 따로 채점. 🔴 C-13(Spring이 `isAnomaly`·`deviationPct`를 주는지 미확정, SELLER-FINAL-RISKS) confound를 실측으로 분리 — "RAPTOR+파서 동시교체"와 같은 함정 제거.

### 1.6 LLM-judge 보조 + 사람 검증(≥5)

- 정답이 모호한 서술 품질(groundedness·product-fit)은 LLM-judge를 **보조**로 둔다. 단 **1차 판정은 결정론 정답 대조**, judge는 참고치(RAG 때 "judge 자체평가만으론 product fit 주장 불가" 지적).
- **Perception study**: buyer 개인화 on/off 블라인드 A/B, seller 보고서 유용성 — 최소 5명, 만족도·재사용 의사. 발표에 정성 근거로 1장. (수치 지표로 안 잡히는 핵심가치 입증 수단.)

### 1.7 CI 회귀 게이트

`pytest -m eval`로 골든셋 실행 → `evals/report.py`가 지표 산출 → **회귀 임계**(config `eval_regression_*`) 하회 시 실패. "LLM은 probabilistic이라 회귀 감지 필수"라는 운영 코멘트에 대한 직접 답. 결정론 하네스라 CI에서 재현 가능.

---

## ② 베이스라인 ablation — 에이전트 도입 효과 증명

### 2.1 이미 갖춘 실험 인프라 (강점으로 내세울 것)

몇대몇이 칭찬받은 "Strategy 패턴으로 Retriever/Reranker 분리"를 **jarvis는 이미 갖고 있다**:

- `stream_recommendation(decision, ..., search=, push_fn=, profile=)` — 검색·push·프로필이 **주입**. 축을 끼우고 빼기 쉬움.
- 검색 백엔드 4종 플러그인(`search_service.py`): `SpringSearchBackend`(default) / `EmbeddingRerankBackend` / `VectorSearchBackend` + `search_catalog(backend=)`.
- rerank는 단일 노드(`rerank()`), 프로필은 `profile` 인자 → on/off가 값 하나.

→ ablation 매트릭스가 **거의 공짜**다. 이 점을 발표에서 "실험 인프라 수준 추상화"로 부각(평가자 선호 포인트).

### 2.2 buyer ablation 축 (한 번에 한 축 — confound 제거)

| 축 | 값 | 무엇을 증명 |
|---|---|---|
| **naive baseline** | ① 순진: 단일 LLM이 카탈로그 top-N 원문으로 답 (rerank·profile·decompose 전부 off) | **에이전트가 정말 필요한가**(평가자 필수 요구) |
| search backend | Spring / EmbeddingRerank / Vector | 검색 방식별 Recall·nDCG |
| rerank | off(검색순서) / on(Sonnet) | Rerank-Lift |
| profile | None / 주입 | Personalization-Lift |
| decompose | off(원쿼리→필터) / on | 복합질의에서의 이득(단순질의에선 손해 가능성 확인) |

규율: **같은 골든셋·같은 fixture에서 한 축만 토글**(RAPTOR+파서 동시교체 실패 재현 방지). 결과는 §1.4 지표로.

### 2.3 seller ablation 축

| 축 | 값 | 무엇을 증명 |
|---|---|---|
| **plain 집계 baseline** | LLM 해석 없이 Spring 수치만 반환 | 분석 에이전트가 원수치 대비 가치를 더하는가 |
| 검증 루프 | off / on(≤3회) | 보고서 품질에 검증 루프가 기여하는가(비용 대비) |
| 모델 tier | Haiku-only / Haiku+Sonnet 분리 | **2-tier 배정 근거**(비용/품질 trade-off, 몇대몇의 체인별 라우팅 칭찬 대응) |
| 워커 수 | 단일 워커 / 팬아웃 전체 | 팬아웃의 한계효용 |

### 2.4 질의 유형별 breakdown

모든 지표를 `query_type`(simple/conditional/comparison/multi_constraint)별로 분리 리포트. decompose·RAPTOR류가 단순조회에서 손해일 수 있다는 지적(4조)에 대한 답 — 유형별로 어느 축이 언제 이득인지 표로.

### 2.5 산출물 — "ablation 슬라이드 한 장"

`evals/ablation.py`가 축×지표 매트릭스를 markdown/CSV로 출력. 평가자가 **모든 팀에게 명시적으로 요구한** ablation 표를 그대로 만족. 각 셀에 지표값 + Δ(baseline 대비).

---

## ③ 집계 관측 대시보드 (p50/p95·비용·degrade율)

### 3.1 현재 상태와 공백

`observability.py`는 요청당 `chat_request` JSON 1줄(`latencyFirstToken`·`latencyTotal`·`model[]`·`promptTokens`·`completionTokens`·`errorType`·`streamStatus`·`role`)과 `emit_rejection`(429/409/504)만 남긴다. **집계·비용·레인 차원·degrade 표시가 없다.**

### 3.2 로그 레코드 확장 (기존 필드 유지, 차원만 추가)

`RequestObservation.finish()` 레코드에 추가:

- **`lane`**: 판매자 `meta.lane`(analysis/product/general/confirm/apply/refused), 구매자 `recommend`/`cart`/`fallback`. 레인별 집계의 키.
- **`degraded`(bool) + `degradeReason`**: buyer(SEARCH_FAILED·rerank_fallback·push_skipped) + seller(worker_degrade·partial_report·all_workers_failed·spring_write_failed)를 **단일 enum**으로 통일. degrade율의 소스.
- **`costUsd`**: `Σ(prompt_tokens×price_in + completion_tokens×price_out)`, 모델별 단가는 config 가격표(§3.4). `record_model_call`이 이미 model·tokens를 모으므로 finish에서 산출.
- **`toolCalls`**: 판매자 ToolCallLimit(=8) 대비 실제 호출 수(과호출 관측).

계약 무영향(내부 로그 필드). PII 규약 유지 — 원문 없음, 지문만.

### 3.3 집계 계층 (MVP → 확장 2단계)

**MVP(로그 기반, 인프라 0)**: `scripts/aggregate_observability.py` — JSON 라인 로그를 읽어 롤업 산출.
- 지연: **p50/p95/p99**(first-token·total) — 원샘플 정렬 백분위. `role`·`lane`·`model`별 그룹.
- 비용: 턴당 `costUsd` 평균·합계, 레인별.
- degrade율 = degraded 턴 / 전체, `degradeReason` 분포.
- error율 = errorType 있는 턴 / 전체(`emit_rejection` 포함), 코드별.
- **SLO 대비**: p95 first-token vs 10s(§2.9), p95 total vs 판매자 90s 목표 — 초과율 표기.
출력: markdown 요약 + CSV, 또는 정적 HTML 대시보드 1장(발표용).

**확장(post-MVP, 인프라 있으면)**: 인프로세스 롤링 백분위(t-digest)를 `/internal/metrics`(Prometheus 노출) → Grafana. 로그 파싱 없이 실시간. api-spec에 엔드포인트 추가가 필요하면 **명세 개정 먼저**.

### 3.4 비용 모델 (config 주입)

모델별 1K 토큰 단가표를 Settings로 — `model_price_in_per_1k: dict[str,float]`, `model_price_out_per_1k`. 하드코딩 금지. Haiku/Sonnet 실단가 주입 시 §2.3 seller 모델 tier ablation의 비용축과 **동일 소스** 사용(일관성).

### 3.5 이 파트가 답하는 것

"쿼리당 비용·p50/p95 공개"(RAPTOR 조), "운영에서 어떻게 모니터링·자동화하나"(청년정책 조 정책 스크래핑 추궁 = 운영 관점), "degrade가 실제로 얼마나 발동하나"(jarvis의 강점인 degrade 3층을 **숫자로** 입증).

---

## 4. config 추가(제안) — 전부 주입, 하드코딩 금지

```python
# 평가
eval_buyer_k_list: tuple[int, ...] = (5, 10, 20)
eval_regression_recall_at5_min: float = 0.60      # CI 게이트
eval_regression_routing_acc_min: float = 0.85
eval_regression_ndcg_at10_min: float = 0.55
# 관측/비용
model_price_in_per_1k: dict[str, float] = {...}   # 모델→입력단가(USD)
model_price_out_per_1k: dict[str, float] = {...}
slo_first_token_ms: int = 10000                    # §2.9
slo_total_seller_ms: int = 90000                   # 판매자 목표
slo_total_buyer_ms: int = 30000
```

## 5. 단계별 착수 순서 (이슈 단위)

1. **관측 확장(③ 로그 필드 + 집계 스크립트)** — 가장 저위험, 즉시 발표 자산(p95·비용·degrade율 표). 계약 무변경.
2. **buyer 골든셋 + 지표(①)** — fixture는 기존 MockTransport 재활용. Recall/nDCG/Filter-Acc.
3. **buyer ablation(②)** — DI 구조라 골든셋 위에 바로 얹힘. naive baseline 포함.
4. **seller 골든셋·라우팅 혼동행렬·계산 3층 분리(①)** → seller ablation(②).
5. **CI 회귀 게이트(`-m eval`)** + perception study(사람 ≥5).

각 단계 = 별도 이슈/브랜치(CLAUDE.md Git 규칙), `uv run pytest`·`ruff` 통과, 완료 시 CHANGELOG 갱신.

## 6. 테스트 계획

- 지표 함수(`evals/metrics.py`)는 **순수 함수** → 손계산한 소형 케이스로 단위 테스트(nDCG·MRR·Recall 공식 검증). "지표를 발표자가 설명 못 하면 사상누각"에 대한 코드 레벨 방어.
- 하네스 결정론: 동일 입력 2회 실행 = 동일 지표(ScriptedLLM 고정).
- 관측 집계: 합성 로그 라인 → p50/p95 기대값 대조.

## 7. 계약·리스크 메모

- 본 계획은 **와이어 계약 불변**. `/internal/metrics`(§3.3 확장) 도입 시에만 api-spec 개정 선행.
- 🔴 C-13(계산 3층 경계, SELLER-FINAL-RISKS)은 §1.5 Calc-Layer 정확도 측정으로 **정량 근거**를 만들어 협의를 앞당긴다.
- 골든셋 정답 라벨링은 사람 공수 필요 — buyer/seller 각 30~50케이스로 시작(소규모 hold-out이 무평가보다 훨씬 낫다).
