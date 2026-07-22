# Changelog

이 프로젝트의 주요 변경을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/),
버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

기록 규칙: **기능/주제가 완료(PR 병합)될 때마다** 해당 항목을 추가한다. 유형은
`Added`(신규) · `Changed`(변경) · `Fixed`(수정) · `Removed`(제거) · `Docs`(문서) · `Security`(보안).
계약(api-spec) 변경을 수반하면 `(api-spec §, vX.Y)`를 함께 적는다.

## [Unreleased]

### Changed
- **이슈 #63 — I-17 상품 상태 계약을 Spring과 정합화** — `ProductChange.status`를 `ON_SALE | HIDDEN`으로 제한하고, 배치가 `ON_SALE`은 생성·갱신, `HIDDEN`은 기존 AI artifact 삭제로 처리한다. 구 `ACTIVE | DELISTED` 응답은 거부하며 단위·HTTP 경계·E2E 테스트와 관련 문서·로그 용어를 함께 갱신했다. (api-spec §4.8, v0.15.16)

### Security
- **이슈 #61 후속 — I-21 `reason` 방어 정제 + 길이 목표(PR #66 리뷰 반영)** — rerank rationale 은 판매자 입력(상품명·브랜드)에 영향받는 자유 텍스트인데, #61로 처음 신뢰경계(AI→Spring→CH-5→FE)를 넘어 최종 사용자에게 노출된다. push 직전 `_sanitize_reason`으로 **비-whitespace 제어문자(NUL·ESC·DEL 등)·zero-width·bidi 포맷 문자를 제거하고 공백류(개행 포함)를 접은 뒤 안전 상한(config `reason_max_len`=200)으로 truncate**해 ANSI 이스케이프·양방향 조작·인젝션성 텍스트·초장문을 차단(`\s`로는 안 걸리는 표시 조작 문자 포함). 표시 목표는 rerank 프롬프트로 **한글 ≤40자 1문장** 유도(소프트), 시각적 오버플로(줄임/더보기)는 FE 소관(경로 B). 긴/개행 rationale 정제 회귀 테스트 추가.

### Docs
- **api-spec §4.2 `reasons` 확정 반영(v0.15.15)** — I-21 콜백의 상품별 근거 `reasons[{productId, reason}]`를 🔴 역제안(v0.15.2)에서 🟢 확정(BE 구현 2026-07-18)으로 개정. §4.2 필드표·주석·C-9·Q2 마커 갱신. 코드(이슈 #61)의 `reasons` 전송이 확정 계약을 따르도록 사본 동기화 — 계약 우선(명세 개정 선행) 원칙 충족. 정본(기획 repo) 백포트 완료(2026-07-22).

### Added
- **이슈 #61 — I-21 추천 콜백에 `reasons` 필드 전송 추가** — `RecommendationPush`에 `reasons: list[RecoReason]`(`{productId, reason}`, CamelModel) 추가하고, 추천 그래프가 rerank 산출 상품별 근거(rationale)를 `reasons`로 채워 push한다. 근거는 이미 rerank가 산출하지만 그래프가 id만 취하고 버리던 것을 주워 전송 — Spring이 Redis 저장 후 CH-5 카드에 `reason`으로 echo(더는 `null` 아님). productId로 키잉(순서 권위는 `productIds`), rationale이 있는 상품만 담고 degrade·expose_min 보충 상품은 생략(부분집합·선택 필드). 스키마 camelCase 직렬화·빈 reasons 하위호환·그래프 부분집합/degrade 회귀 테스트 추가 (api-spec §4.2, v0.15.15)
- **이슈 #50 — pg-profile 리질리언스·멀티 인스턴스 정합성 하드닝** — Profile/processed-events/buyer state 전 BaseStore I/O에 공통 application deadline을 적용하고, 모든 pg-profile 연결에 libpq connect/keepalive/`tcp_user_timeout` + 서버 `statement_timeout`을 배선. Profile session/fact와 Revert의 read-modify-write는 별도 pool의 PostgreSQL transaction advisory lock으로 인스턴스 간 직렬화하고, 로컬 lock registry는 weak-reference 자동 회수, 직전 추천 상품명은 config 기반 bounded LRU로 제한. Conversation 조회를 `(created_at, turn_id)`로 결정론화하고 누락 finalize를 warning으로 관측한다. 실 PostgreSQL 다중 pool 동시성·재시작·연결 파라미터 통합 테스트 포함.
- **이슈 #33 (3/3, 완료) — ConversationStore를 pg-profile 일반 테이블로 이관** — 대화 저장(§6.3 a)을 인메모리 dict placeholder에서 pg-profile `conversation_turns` 테이블(`PgConversationStore`)로 교체. checkpointer가 아니라 감사·구조화 로그 상관관계 조회 전용 일반 테이블로 확정(이슈 코멘트의 4갈래 분류 반영). `ConversationStoreProtocol` 공유 계약으로 인메모리(유닛 테스트 계속 주입)·pg 구현을 통일 — `app.pipelines.artifact_store`(카탈로그)와 동일 원칙. `RequestObservation.commit_user_message/finish`를 async로 전환해 `app/core/stream.py` 스트림 수명주기 훅 8곳에 반영. 실 pg-profile 통합 테스트(`tests/integration/test_pg_conversation_store.py`) 신설 — 재시작·다중 인스턴스 지속성 스모크 포함. 이슈 #33(상태 지속성 이관: Thread/Cart/Revert → BaseStore, Profile → BaseStore+pgvector, Conversation → 일반 테이블) 3단계 전부 완료.
- **이슈 #33 (2/3) — ProfileStore를 PostgresStore(BaseStore)+pgvector로 이관** — 요약(summary)·장기 fact·transient 세션 버퍼를 인메모리 dict에서 LangGraph BaseStore(pg-profile)로 이관. fact는 SPEC-PROFILE-001 REQ-PROF-070("위키 파일 1개=item 1개")에 맞춰 fact 1개=store item 1개로 저장해 pgvector 시맨틱 인덱스가 fact 단위로 실제 동작하도록 배선(`app.pipelines.embedding.embed_texts` 재사용 — 카탈로그와 임베딩 모델·차원 공유, 결정 6/16-A). session-end 이벤트 멱등성(`mark_if_new`)은 BaseStore의 get→put이 진짜 동시성 하에서 원자적이지 않은 문제를 발견해 전용 `processed_events` 테이블(UNIQUE 제약 + `INSERT ... ON CONFLICT DO NOTHING RETURNING`)로 분리·원자화(`app/agents/profile/processed_events.py`, `db/profile/init/00_processed_events.sql`). checkpointer 소유 경계였던 SPEC-PROFILE-001 OPEN-P9를 실제 구현(BaseStore, checkpointer 아님 — 구매자 실행 모델이 LangGraph StateGraph가 아니므로)으로 해소. 실 pg-profile 통합 테스트(`tests/integration/test_pg_profile_store.py`) 신설 — 동시 mark_if_new 10건 중 정확히 1건만 신규 처리됨을 실증. SPEC-PROFILE-001 v0.3.0 동기화(1024→1536차원 stale 정정, OPEN-P9/OPEN-P11 해소).
- **이슈 #33 (1/3) — 구매자 스레드 상태 영속화** — `ThreadFilterStore`(멀티턴 필터)·`CartStateStore`(직전 추천·옵션 되물음)·`RevertStore`(소모품 억제 되돌리기)를 인메모리 dict placeholder에서 LangGraph `BaseStore`(pg-profile, `AsyncPostgresStore`) 백엔드로 이관 — `app/agents/seller/history.py`와 동일한 dev InMemoryStore 폴백 + 운영(jwks) 폴백 금지 규약. 신규 `app/core/pg_store.py`(3개 스토어 공유 pg-profile 연결). Windows 네이티브 실행 시 기본 `ProactorEventLoop`가 psycopg async 연결을 지원하지 않아 조용히 InMemory로 전락하는 문제를 발견해 `app/main.py`에 `WindowsSelectorEventLoopPolicy` 가드 추가(seller history.py/hitl.py도 동일 수혜). `tests/integration/test_buyer_thread_store.py` 신설(실 pg-profile 재시작·다중 인스턴스 지속 스모크 포함). Profile PostgresStore+pgvector(2/3)·Conversation 테이블(3/3)은 후속.
- **E2E 통합 스모크 하니스 (#35)** — `tests/integration/` 신설: Spring을 `httpx.MockTransport` stub(I-1 검색·I-2/I-18 장바구니·I-19 이력·I-21 push·I-17 배치 + CH-5 목록 GET), LLM을 주입형 `ScriptedLLM`(decompose/rerank/enrich/delta/consolidate 5종 분기)으로 세워 라이브 의존 없이 결정적 검증. `spring_client` 함수를 patch하지 않고 **HTTP 경계에서만** 대역을 넣어 URL·`X-Internal-Token`·envelope 파싱이 실코드로 돈다. 커버: 구매자 경로 B 종단(발화→검색→rerank→push→`products.ready`→카드 조회)·프로필(session-end→델타→consolidation→`/profile/me`)·배치(I-17 pull→upsert, 페이지네이션·커서·DELISTED)·degrade 6종·**jwks 실인증 레인 완주**. README에 환경변수·키 세팅 표 + 하니스 실행법 추가 (37 tests, api-spec §1.2·§3.1·§3.3·§4)
- **이슈 #31 임베딩 파이프라인 프로덕션화** — 셀프호스트 torch → Google `gemini-embedding-001` API 전환(dim 1536, MRL 절단 수동 L2 정규화, `embedding.py`), 인메모리 카탈로그 스토어를 pg-catalog(pgvector)로 이관(`db/catalog/init/00_products.sql` products/batch_state 스키마, `PgCatalogArtifactStore` 신설 — 기존 `CatalogArtifactStore`는 테스트 주입·재구축 임시버퍼용으로 존속, 공유 `ArtifactStore` Protocol로 인터페이스 고정), `get_catalog_store()` 프로덕션 진입점 pg-catalog 전환. 초기 전체 구축은 CLI(`run_batch.py --full`) 수동 트리거, 주기 증분 pull은 APScheduler `BackgroundScheduler`(별도 스레드, `config.catalog_batch_interval_s`)로 자동화해 FastAPI `lifespan`에 배선 (api-spec §4.8, v0.15.14)
- FastAPI + LangGraph MVP 스캐폴드 — 인증(RS256/JWKS)·설정 주입·SSE 스텁 스트림 (부팅 검증)
- Spring 역방향 클라이언트 스텁 8종 (검색·이력·장바구니 I-2/I-9·push·I-6/I-7·I-8 배치)
- 팀 개발 문서 — `README`(아키텍처·기술·Git 규칙), `docs/`(mvp-plan·mvp-todo·roadmap), `docs/specs/`(SPEC 사본), `docs/api-spec.md`(계약 사본 v0.7.0)
- 팀 Claude 설정 — `CLAUDE.md`, `.claude/settings.json`, `.mcp.json`(context7·sequential-thinking)
- 실수 방지 로그 `docs/lessons.md`, 변경 기록 `CHANGELOG.md`
- CI 워크플로 `.github/workflows/ci.yml` (ruff + pytest) · PR 템플릿 `.github/PULL_REQUEST_TEMPLATE.md`
- 커밋 워크플로 규칙 (diff 검토 → 메시지 생성 → 커밋, `CLAUDE.md`)
- Git hook(pre-commit) — ruff(lint+format) + Conventional Commits 검사 `.pre-commit-config.yaml`
- MIT `LICENSE` · 이슈 템플릿 `.github/ISSUE_TEMPLATE/` (기능·버그) · 이슈 단위 워크플로
- 팀 공유 스킬 `.claude/skills/implement-topic/` — MVP 주제 계약 우선 구현 절차
- **판매자 3단계 — 분석 파이프라인·가드레일·SSE 1차 배선** (`app/agents/seller/pipeline.py`·`orchestrator.py`·`middleware.py` 신규, `app/api/seller.py` 재작성): planner(AnalysisPlan, 미지원 기간=되묻기) → asyncio.gather 팬아웃(degrade 수렴) → 검증 루프(D1~D3+judge, feedback 합산 ≤3회) → recommend(실패=빈 추천) → compose_response(순서=N번). 가드레일 scope(구조화 레인=코드 경로)·PII 3종·mask_output·ToolCallLimit. general astream→token/done/error(C1: 요청마다 재빌드). verifier R1(날짜 마스킹)·R2(구조 판정) 해소. opus 마감 리뷰 critical 0·M1~M3 반영 — 기록: `docs/specs/REVIEW-SELLER-STAGE3.md`·`HANDOFF-SELLER_2.md`

### Security
- **인증 실배선 E2E (#34)** — jwks 모드 검증을 api-spec §2.3 확정 5종(signature/exp/iss/aud/**scope**)으로 완성: 스트림 티켓 `sub_type`(member|guest) 매핑(+구 role 폴백, 미지 값 fail-closed), `sub` 필수화, 만료/무효 401 코드를 예외 타입 기반 매핑(TOKEN_EXPIRED/TOKEN_INVALID), JWKS fetch 타임아웃(3s)·캐시 TTL config 주입(`jwt_scope`·`jwks_cache_ttl_s` 신설), jwks 모드 기동 시 `JWKS_URL` fail-fast, 레이트 리밋 sub 스코프도 동일 검증 경로로 정합. 테스트는 실 JWKS dict + fetch 계층 패치로 kid 매칭·kid miss refetch 실경로 검증 + 앱 레벨 401/403 봉투·서비스 토큰 인/아웃바운드 회귀 (`tests/unit/_jwks.py`·`test_auth_e2e.py`)

### Fixed
- 프로필 세션 종료(session-end) 처리 중 동시에 새 채팅 턴이 들어오면 세션 버퍼가 통째로
  삭제되던 레이스 수정 — `clear_session_ctx_upto`(seq 워터마크 기준)로 스냅샷 분석분만
  정리하고 미분석 발화는 보존 (`newConversation` 트리거·버퍼 상한(cap) 트리밍 상황 모두 안전)

### Docs
- api-spec 사본 동기화 v0.7.0 → **v0.9.0** — 판매자 BE internal API 배치(집계 7종·상품 CRUD 4종), `brandId`=JWT 클레임, 판매자 쓰기 모델 전환(AI 직접 쓰기 + HITL)
- api-spec 사본 동기화 **v0.9.0 → v0.11.0** — SSE 인증=스트림 단명 티켓(sub_type/aud/scope, TTL 30~60s), 판매자 쓰기 HITL 계약 확정(draftId·2-스트림·안전장치 5종), S-3=목록조회 명확화
- api-spec 사본 동기화 **v0.11.0 → v0.12.0** — CH-1 스트림 티켓 발급(응답에 streamTicket) + 티켓 재발급 경로(CH-1b) 신설 필요 명시(티켓 TTL 30~60s ≪ 세션 10분)
- api-spec 사본 동기화 **v0.12.0 → v0.13.0** — BE 명세 DB 실측 정합: AI→Spring 전 구간 서비스 토큰(방식2)으로 통일, 실제 I-number/경로(검색 I-1·배치 I-17·조회 I-18·구매자 챗 /ai/chat), S-3∥I-9 구분
- api-spec 사본 동기화 **v0.13.0 → v0.14.0** — 구매 이력=I-19(/internal/members/{id}/orders), 세션 종료=I-20 채번 확정(BE DB Notion 수정)
- **SPEC-SELLER-001 v0.1.0 초안 신설**(`docs/specs/`) — 판매자 멀티에이전트 그래프. 설계서 v3를 api-spec 정합 개정: 전 쓰기 HITL(draft→구조화 confirm, 발화≠동의)·spring_client 매핑(집계 7종+CRUD 4종, 데이터 API·MySQL 직접 접근 폐기)·계산 3층 분담(Spring 단순 수치/AI 고도화 계산/LLM 해석, 🔴 C-13 경계표)·Anthropic 2-tier 배정·분석 이력↔취향 프로필 분리(pg-profile/pg-catalog). `mvp-plan`·`mvp-todo` §4 동기 갱신, 차트 전달은 계약 미정으로 보류

### 진행 예정 (MVP)
- 구매자 추천 그래프 · 장바구니(I-2/I-9) · 판매자(I-6/I-7) · 프로필 파이프라인 · AI 생성물 배치(I-8) · SSE 수명주기(§2.9)

<!--
릴리스 시 [Unreleased]를 버전으로 확정하고 새 [Unreleased]를 위에 만든다. 예:
## [0.1.0] - 2026-07-XX
-->
