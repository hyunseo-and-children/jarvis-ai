# SELLER-FINAL — 핵심 기술 명세서

> **버전**: v1.0.0 · **기준일**: 2026-07-20 · **상태**: MVP 확정
> 판매자 멀티에이전트 MVP 의 기술 선택과 그 **이유** 정본. 워크플로우는 [SELLER-FINAL-WORKFLOW](SELLER-FINAL-WORKFLOW.md),
> 미결·리스크는 [SELLER-FINAL-RISKS](SELLER-FINAL-RISKS.md), 확장은 [SELLER-FINAL-ROADMAP](SELLER-FINAL-ROADMAP.md) 참조.

## 1. 스택

| 층 | 기술 | 버전/비고 |
|---|---|---|
| 런타임 | Python 3.12 + FastAPI + uv | SSE = StreamingResponse |
| 에이전트 | langchain `create_agent` + LangGraph | langgraph 1.2.9 / langchain 1.3.14 — StateGraph 수작업 조립 금지(HITL 그래프만 예외) |
| LLM | Anthropic 2-tier | Haiku 4.5(라우팅·계획·워커·judge, t=0) / Sonnet 5(보고서·추천, t=0.2) |
| 영속화 | PostgreSQL ×2 | pg-catalog(5433, AI 생성물·post-MVP pgvector) / pg-profile(5434, checkpoint·분석 이력) |
| 검증 | pytest 293종(스텁) + ruff | 실 LLM 스모크는 별도(SMOKE-SELLER-41) |

## 2. 모델 배정 (SPEC §8 — 일관성 장치 ①)

| 역할 | 모델 | temp | 출력 계약 |
|---|---|---|---|
| supervisor | Haiku | 0 | `RouteDecision` (Literal 3분기 + confidence) |
| analysis_planner | Haiku | 0 | `AnalysisPlan` (워커 선택 + 기간 정규 어휘) |
| 분석 워커 5종 | Haiku | 0 | `AnalysisFinding` |
| report judge | Haiku | 0 | `ReportScore` (축 3×10점, total 은 코드 property) |
| report_agent | Sonnet | 0.2 | 자유 텍스트 (검증 루프 통과 필요) |
| recommend_agent | Sonnet | 0.2 | `RecommendationSet` (순서 = "N번" 계약) |
| product_agent | Haiku | 0 | `DraftProposal` (조회 도구만 — A안) |

## 3. 핵심 설계 원칙 4가지

1. **계약값은 코드**: draftId·total 점수·기간 환산·판정(통과/폴백/멱등/TTL)은 전부 코드 소관. LLM 은 제안만 한다. 구조화 출력은 `ToolStrategy` + Literal/ge·le 로 스키마 수준에서 강제(장치 ⑤).
2. **발화 ≠ 동의, 구조로 보장**: draft 생성 에이전트에 쓰기 도구가 없고(A안), 승인은 구조화 confirm 신호만, 실행은 코드 매핑. LLM 이 실행 인자를 만들 수 있는 지점이 없다.
3. **degrade 우선**: 부가 기능(추천·이력·마스킹 대상 외)이 죽어도 주 기능(보고서·스트림 종료)은 산다. 예외 전파는 "내보낼 것이 없는" 2경우로 한정.
4. **튜너블은 Settings 주입**: 임계·상한·타임아웃 하드코딩 금지 — `app/core/config.py` 단일 출처.

## 4. HITL 실행 (4-2) — 기술 상세

- **그래프**: 단일 노드 StateGraph — `draft 저장 → interrupt(승인 대기) → resume 시 코드 실행`. 노드의 interrupt 이전 구간은 재실행되므로 부수효과 금지.
- **checkpointer**: `AsyncPostgresSaver`(pg-profile). **thread_id = `seller-draft:{draftId}`** — draftId↔checkpoint 바인딩 그 자체. dev 폴백: 연결 실패 시 `InMemorySaver` + 경고 1회, 운영(auth_mode=jwks) 폴백 금지.
- **강의 패턴과의 차이(의도적)**: 06_Middleware/02-HITL-V1 의 `HumanInTheLoopMiddleware`(LLM 도구 호출 interrupt→approve 재개)는 실행 인자가 LLM 산물이라 채택하지 않음. `interrupt`/`Command(resume)` 원리는 동일 사용.
- **안전장치 5종 구현 지점**: ①draftId=thread_id ②confirm 코드 선판정 ③완료 스레드 재confirm 멱등 안내 ④brandId 대조(존재 비노출) + Spring authz 최종 방어 ⑤created_at 기준 TTL 코드 판정.
- **stale 검증**: confirm 시점 I-9 재조회 → changes[].before 대조. int 필드는 정수 비교(표기 차이 오탐 방지), stock_quantity 제외(F6 자연 변동). I-9 에 productId 필터가 없어 페이지 순회(`seller_list_default_limit` × `seller_draft_lookup_max_pages`).
- **장애 원자성**: 실행 중 Spring 예외는 노드 밖으로 전파 → resume 체크포인트 미커밋 → draft 는 interrupt 에 잔존 → 재confirm 으로 재시도 가능.

## 5. 분석 이력 (4-3) — 기술 상세

- **저장소**: `AsyncPostgresStore`(pg-profile), 네임스페이스 `("sellers", {sellerId})` 키 `analysis_history` 에 **최신순 목록 1건**. per-item 키 대신 단일 목록인 이유: "가장 최근"과 "최근 N건"이 원자적 1회 읽기, store.asearch 정렬 비보장 회피(정렬을 코드가 소유). 상한 20건·보고서 요약 500자(Settings).
- **planner 주입**: 최근 5건을 입력 메시지 `[최근 분석 이력]` 블록으로 — 프롬프트 불변.
- **추천 적용**: 정규식 전체매칭("N번 (추천)(을/를) 적용…") → recommendations[N-1] → before 를 I-9 현재값으로 채워 DraftProposal → 4-2 validate_draft 재사용. **경로 전체에 LLM 0회.**

## 6. 보안·신원

- **인바운드**: `X-Internal-Token` 상수시간 비교(secrets.compare_digest) — 결손/불일치 401. Spring 주입 메아리 신원 `X-Seller-Id`/`X-Brand-Id`(🔴 헤더명 BE 확정 대기) 결손 400. dev(토큰 미설정)는 스킵 + 경고 1회.
- **IDOR 방지**: 신원은 `SellerContext`(dataclass) → `ToolRuntime[SellerContext]` 로 도구에 주입 — LLM 에게 보이지 않아 남의 brandId 조회/쓰기를 만들 수 없다. 도구 시그니처에 신원 인자 없음.
- **가드레일 미들웨어(장치 ⑥)**: scope(경쟁사 등 코드 선차단) → PII 정제(입력) → mask_output(출력, 시크릿 패턴). confirm 소유 불일치는 존재 비노출 거절.
- **아웃바운드**: AI→Spring 전 구간 `X-Internal-Token` + brandId 는 검증된 신원에서만.

## 7. 주요 Settings (튜너블 단일 출처)

| 키 | 기본 | 용도 |
|---|---|---|
| seller_route_confidence_min / seller_route_timeout_s | 0.6 / 10s | 라우팅 보수 재지정·상한 |
| seller_worker_timeout_s | 60s | 워커·planner·report 개별 상한 |
| seller_report_score_threshold / seller_report_max_retries | 21 / 3 | 검증 루프 |
| seller_draft_ttl_minutes | 10 | HITL 대기 만료 |
| seller_list_default_limit / seller_draft_lookup_max_pages | 20 / 10 | I-9 조회·stale 탐색 상한 |
| seller_checkpoint_connect_timeout_s | 5s | PG saver/store 초기 연결 |
| seller_history_recent_n / _max_items / _report_max_chars | 5 / 20 / 500 | 이력 주입·보관·요약 |

## 8. 테스트 체계

- **유닛 293종**(스텁 기준, LLM·PG·HTTP 0): 스키마 계약·라우팅 후처리·degrade 매핑·HITL 안전장치 5종·이력·SSE 와이어 포맷. `tests/unit/conftest.py` 가 전 테스트에 InMemory 백엔드를 자동 주입 — 로컬 PG 가동 여부와 무관하게 결정적.
- **실 LLM 스모크**(SMOKE-SELLER-41, 미실행 — API 키 미발급): 라우팅 정확도 6건 + LLM 0회 경로 2건 + 스트림 종료 보장. ⚠️ confirm 행 기대값은 4-2 반영 갱신 필요("준비 중" → "찾을 수 없습니다").
- 커밋 게이트: `uv run ruff check --fix && uv run ruff format` → `uv run pytest` → Conventional Commit.
