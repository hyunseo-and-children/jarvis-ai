# Changelog

이 프로젝트의 주요 변경을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/),
버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

기록 규칙: **기능/주제가 완료(PR 병합)될 때마다** 해당 항목을 추가한다. 유형은
`Added`(신규) · `Changed`(변경) · `Fixed`(수정) · `Removed`(제거) · `Docs`(문서) · `Security`(보안).
계약(api-spec) 변경을 수반하면 `(api-spec §, vX.Y)`를 함께 적는다.

## [Unreleased]

### Added
- pg-catalog `products` 임베딩 프로비넌스 컬럼(`embed_model·embed_dim·embed_task·normalized`) + `embedding_meta_complete` CHECK, 기존 볼륨용 마이그레이션(#65).
- `embed_texts(task_type=...)` 및 비대칭 임베딩 바인딩(질의=RETRIEVAL_QUERY / 문서=RETRIEVAL_DOCUMENT)(#65).
- **이슈 #79 — AI 내부 프로필 inactivity timeout** — 회원 발화 저장과 같은 pg-profile
  transaction에서 세션별 `last_activity_at`을 DB 시각으로 갱신하고, 10분 비활동 세션을 1분
  주기의 bounded `FOR UPDATE SKIP LOCKED` sweep으로 선점한다. Spring I-20(`logout`·
  `newConversation`)과 timeout은 고정키 claim으로 직렬화되는 공통 finalizer를 사용한다. Spring
  종료만 멱등키를 영구 완료하고, idle 처리는 재개 가능한 checkpoint로 claim을 해제하여 같은
  sessionId의 후속 발화를 다시 flush한다. 새 회원 발화는 같은 DB transaction에서 이전
  `PROCESSING`/`COMPLETED` 종료 generation을 무효화하고, terminal finalizer는 처리 중 갱신된
  activity를 `COMPLETED`로 덮지 않는다. scheduler는 라이브 스트림 슬롯을 점유하지 않으며,
  처리 동시성 상한, 전체 batch wave를 포괄하는 claim TTL 검증, claim lease/crash 복구,
  claim별 오류 격리, activity 완료 실패의 retryable 집계, LLM 실패 시 버퍼 보존을 포함한다.
  conversation/activity 양쪽 schema 초기화는 동일 advisory lock을 사용해 콜드스타트 DDL 경합을 막는다.
  I-20 입력 파생 단계의 내부 예외도 `retryable`/202로 강등해 best-effort 응답 계약을 유지한다.
  `tabClose` 신호나 추가 HTTP API는 도입하지 않았다. (api-spec §3.5, v0.15.19;
  SPEC-PROFILE-001 v0.4.0)

### Changed
- **이슈 #82 — 판매자 LLM을 공용 provider 토글에 연결** — 판매자 역할이 Anthropic 모델을 직접 고르던 경로를 `fast`/`smart` tier와 공용 resolver로 전환했다. 기본 OpenAI는 tier별 reasoning effort를 사용하고 `temperature`를 보내지 않으며, Anthropic 전환 시 기존 temperature 정책을 유지한다. 활성 provider 키 누락은 SDK 호출 전에 차단해 판매자 SSE `LLM_UNAVAILABLE`로 반환하고, 구조화 출력은 provider 간 동일한 `ToolStrategy` 계약을 유지한다. 와이어 계약 변경 없음.
- 런타임 I-17 배치·sample_100 로더가 임베딩 프로비넌스를 함께 적재(#65).
- **이슈 #63 — I-17 상품 상태 계약을 Spring과 정합화** — `ProductChange.status`를 `ON_SALE | HIDDEN`으로 제한하고, 배치가 `ON_SALE`은 생성·갱신, `HIDDEN`은 기존 AI artifact 삭제로 처리한다. 구 `ACTIVE | DELISTED` 등 미정의 값은 항목별로 skip하지 않고 페이지 전체를 fail-closed 처리해 artifact·커서를 유지하며, Spring 수정 후 같은 `since`부터 재처리한다. 단위·HTTP 경계·E2E 테스트와 관련 문서·로그 용어를 함께 갱신했다. (api-spec §4.8, v0.15.18)

### Security
- **이슈 #72 — Unicode Variation Selector·Tag 출력 하드닝** — 공식 Unicode 17.0.0·IVD 2025-07-14 등록 pair와 England/Scotland/Wales RGI Tag flag만 문맥적으로 보존하고, 고아·반복·비지원 은닉 payload는 제거한다. invisible-free skeleton 및 요청 단위 bounded 스트림 guard로 VS/Tag 삽입과 청크 분할을 이용한 API key·Bearer token·주민번호 마스킹 우회를 차단하되, Spring 실행 정본에는 표시용 차단 문구를 저장하지 않는다. 와이어 계약은 변경하지 않았다.
- **이슈 #67 — AI·판매자 영향 텍스트의 사용자 노출 정제 전수 적용** — `reason`의 위험 문자 제거를 공용 `_strip_unsafe`로 추출하고, 길이 캡 없이 rerank `overall_comment`에 재사용했다. 구매자 일반답변·조건/되돌리기 칩·장바구니 상품/옵션 문구, 판매자 `token`·`draft`, 프로필 조회의 LLM 마크다운까지 실제 SSE/HTTP 신뢰경계를 조사해 제어문자·zero-width·bidi 포맷 문자 제거와 공백 접기를 적용하되 보고서·마크다운·목록·상품 설명의 구조적 개행은 보존했다. 하드코딩 `action.message`와 현재 미구현 `budget`은 비오염 경로라 제외했으며 와이어 계약은 변경하지 않았다.
- **이슈 #61 후속 — I-21 `reason` 방어 정제 + 길이 목표(PR #66 리뷰 반영)** — rerank rationale 은 판매자 입력(상품명·브랜드)에 영향받는 자유 텍스트인데, #61로 처음 신뢰경계(AI→Spring→CH-5→FE)를 넘어 최종 사용자에게 노출된다. push 직전 `_sanitize_reason`으로 **비-whitespace 제어문자(NUL·ESC·DEL 등)·zero-width·bidi 포맷 문자를 제거하고 공백류(개행 포함)를 접은 뒤 안전 상한(config `reason_max_len`=200)으로 truncate**해 ANSI 이스케이프·양방향 조작·인젝션성 텍스트·초장문을 차단(`\s`로는 안 걸리는 표시 조작 문자 포함). 표시 목표는 rerank 프롬프트로 **한글 ≤40자 1문장** 유도(소프트), 시각적 오버플로(줄임/더보기)는 FE 소관(경로 B). 긴/개행 rationale 정제 회귀 테스트 추가.

### Docs
- **이슈 #92 후속 — 리뷰 게이트를 배포 경계로 이동** — `dev`는 **PR+CI 필수·사람 승인 리뷰 면제**(리뷰 0), 사람 1인 리뷰는 **`dev → main` 승격 PR**에서만 강제하도록 브랜치 보호·문서(README·CLAUDE.md)를 정합화. dev·main 모두 직접 push 금지·`lint-test` 필수는 유지.
- **이슈 #92 — `main`=배포 라인 고정 + `dev` 통합 브랜치 도입** — 배포팀 CD가 `main` push 기준 EC2 자동배포(`jarvis-backend/.github/workflows/deploy.yml` 패턴)임에 맞춰, 일상 개발을 통합 라인 `dev`로 모으고 `main`은 배포 라인으로 고정했다. README §Git 워크플로에 `main`(배포)+`dev`(통합)+topic 3계층·분기 기준 `dev`·`dev → main` 승격/핫픽스 절차를 반영하고, CLAUDE.md §Git의 브랜치·PR·worktree 분기 기준을 `dev`로 개정. `dev` 브랜치 보호(직접 push 금지·CI 필수·리뷰 1인)는 repo admin 웹 설정 필요.
- **api-spec §4.2 `reasons` 확정 반영(v0.15.15)** — I-21 콜백의 상품별 근거 `reasons[{productId, reason}]`를 🔴 역제안(v0.15.2)에서 🟢 확정(BE 구현 2026-07-18)으로 개정. §4.2 필드표·주석·C-9·Q2 마커 갱신. 코드(이슈 #61)의 `reasons` 전송이 확정 계약을 따르도록 사본 동기화 — 계약 우선(명세 개정 선행) 원칙 충족. 정본(기획 repo) 백포트 완료(2026-07-22).

### Added
- **이슈 #59 — 카테고리 하이브리드 분류(임베딩 보정 매핑 + 멀티 fan-out)** — decompose 가 자유 문자열로 내던 `filters.category` 를 제거하고, LLM 추측(`categoryQueries`)을 **임베딩으로 실재 DB 카테고리(canonical)에 보정**(방식 A: exact match → 임베딩 최근접(raw, 없으면 그 leg 의 query 앵커); canonical-or-null — 카테고리 신호가 없으면 강제하지 않고 무필터 검색)해 Spring I-1 에 실재 카테고리만 나가게 했다(가짜 `categoryName` 으로 인한 0건 방지). 매핑은 `(canonical, query)` leg 를 산출하고, 상황형 멀티 카테고리 질의("유럽여행 준비물")는 leg 마다 Spring I-1 을 **병렬 검색 후 round-robin 병합**(productId dedup·`category_fanout_merge_cap` 절단 — 한 카테고리가 rerank 입력을 독점하지 않게)한다. leg 별 `SpringUnavailable` 은 흡수하고 전량 실패만 `SEARCH_FAILED`, 매핑 결과가 없으면 단일 filters 검색으로 fallback. LLM 호출은 2회(decompose+rerank) 유지 — 매핑은 임베딩·DB 만(LLM 0회). 튜너블 `category_top_k`·`category_fanout_max`·`category_fanout_per_cat_limit`·`category_fanout_merge_cap` 주입(하드코딩 금지). 계약 무변경(I-1 `categoryName`·SSE 경로 B). 설계 `docs/specs/DESIGN-CATEGORY-HYBRID-59.md`. **OPEN-1(Spring `category` 컬럼 `"top > mid"` 통 전송 가정)은 통합 스모크 대기**. fan-out 병합/병렬/degrade·매핑 분기(exact·최근접·신호 없음→무필터·하드실패 degrade)·decompose `categoryQueries` 파싱 유닛 테스트 추가.
- **이슈 #61 — I-21 추천 콜백에 `reasons` 필드 전송 추가** — `RecommendationPush`에 `reasons: list[RecoReason]`(`{productId, reason}`, CamelModel) 추가하고, 추천 그래프가 rerank 산출 상품별 근거(rationale)를 `reasons`로 채워 push한다. 근거는 이미 rerank가 산출하지만 그래프가 id만 취하고 버리던 것을 주워 전송 — Spring이 Redis 저장 후 CH-5 카드에 `reason`으로 echo(더는 `null` 아님). productId로 키잉(순서 권위는 `productIds`), rationale이 있는 상품만 담고 degrade·expose_min 보충 상품은 생략(부분집합·선택 필드). 스키마 camelCase 직렬화·빈 reasons 하위호환·그래프 부분집합/degrade 회귀 테스트 추가 (api-spec §4.2, v0.15.15)
- **판매자 챗 화면 전환 신호 — `meta`/`progress` 이벤트 + `done.panel` (S-4, api-spec §3.2 v0.14.1, FE 계약 B)** — 판매자 대시보드(좌 채팅/우 패널)가 "우측을 바꿀지"를 판단하도록 3신호를 추가했다(판매자 스트림 전용, 구매자 계약 무변경): `meta{lane}`(매 스트림 첫 프레임 — analysis/product/general/confirm/apply/refused), `progress{text}`(분석 진행 로딩 — 최종 답변 `token` 과 분리), `done{finishReason,panel}`(패널 조치 — replace/keep/refresh). 레인×패널로 FE 요구 1~3(첫 질문 분할·분석 우측 출력·상품 CRUD 초안/HITL·무관 질문 유지)이 전부 결정된다. `_seller_stream` 6개 substream 에 배선, `_done()` 이 panel 을 싣도록 변경(구매자 `DoneData` 무변경). analysis 진행 문구를 `token`→`progress` 로 이관. `docs/specs/FE-CONTRACT-SELLER-CHAT.md` 에 분기별 요청→응답 시퀀스(성공·실패 전수) 문서화. 노션 S-4·api-spec §3.2 동기화. meta/panel 계약 테스트 3종 추가 — seller 282 통과·전체 574 통과·ruff clean. (api-spec §3.2)

### Fixed
- **이슈 #59 PR #73 리뷰 후속 — 카테고리 매핑 하드닝 3건** — (1) **임베딩 `task_type` 비대칭 바인딩**: 매핑 앵커(질의)는 `RETRIEVAL_QUERY`, categories 시드(문서)는 `RETRIEVAL_DOCUMENT` 로 저장소 공통 규약(#65)에 맞춰, 한쪽만 태깅되면 코사인이 왜곡돼 top-k 매칭 품질이 에러 없이 조용히 저하되던 잠재 불일치를 제거했다. (2) **절단 튜너블 방어**: `category_fanout_max`·`category_fanout_per_cat_limit`·`category_fanout_merge_cap` 에 `Field(ge=0)` 를 걸어, 음수 설정 시 Python slice 가 "뒤에서 N 개 제외"로 뒤집혀 "≤0 이면 정확히 0개" 절단 불변식이 조용히 깨지던 것을 원천 차단. (3) **decompose 빈 leg 사전 필터**: `_parse_category_queries` 가 `category_fanout_max` 절단 전에 신호(raw·query) 있는 leg 만 남겨, LLM 이 앞쪽에 빈 항목을 섞어내도 fanout 예산을 먹어 뒤쪽 실제 카테고리를 밀어내지 않게 했다. 설계 정본 `docs/specs/DESIGN-CATEGORY-HYBRID-59.md`(§4.2·§9·§10) 동기화. 회귀 테스트 추가. 계약 무변경.
- **이슈 #82 Claude Review 후속 — provider 설정 하위호환·오류 전파·관측성·meta-first 보강** — `Literal` 타입 제한은 유지하면서 Settings 입력 경계에서 provider 값을 소문자로 정규화해 기존처럼 `OpenAI`·`OPENAI`·`Anthropic` 환경변수도 허용하고, 미지원 값은 계속 기동 전에 거부한다. 분석 worker의 `LLMNotConfigured`도 부분 실패 finding으로 흡수하지 않고 API 경계까지 재전파해 `LLM_UNAVAILABLE` 계약을 유지한다. API 경계는 키나 예외 원문 없이 provider·lane·threadId만 오류 로그로 남기며, supervisor가 설정 오류로 분류 전에 실패해도 `meta{general}` 후 `error`를 보내 모든 판매자 스트림의 meta-first 계약을 지킨다. (SPEC-SELLER-001 v1.1.4)
- **이슈 #76 — I-17 소비자 복구·데이터 최소화 정합** — Spring의 `400 INVALID_CURSOR`를 일반 장애와 구분해 저장 커서가 무효면 `since="0"` 임시 스토어 전체 재구축으로 자동 복구한다(최초 커서 `0`에서 앞 페이지가 이미 커밋된 경우 포함). 재구축 실패 시에는 배치 시작 전 전체 상태로 되돌리는 대신, §4.8의 페이지 성공 후 커서 저장 규약에 따라 이미 성공한 마지막 페이지의 artifact·커서 체크포인트를 유지한다. I-17 원본 입력은 enrichment와 `search_doc` 생성에만 사용하도록 `CatalogArtifact`·pg-catalog·sample loader의 독립 `name`/`category` 사본을 제거하고 기존 볼륨용 idempotent migration을 추가했다. (api-spec §4.8)
- **판매자 draft SSE 의 `changes[].field` 를 camelCase 로 (S-4, FE 계약 C-1)** — `_draft_event` 가 내부 `ProductField`(snake_case)를 그대로 와이어에 실어 `stock_quantity`·`original_price`·`image_url` 이 규약(§2.2 camelCase) 위반으로 나가던 버그 수정. 나갈 때만 `to_camel` 로 변환(`stockQuantity`·`originalPrice`·`imageUrl`), 내부 DraftChange·Spring 쓰기(I-10/11)는 snake_case 유지. 8종 필드 회귀 테스트(`test_draft_changes_field_is_camelcase`) 추가. 부수로 C-2(draft.summary)·C-3(product 근거 token 없음)·C-4(productId 숫자)·C-5(draftId UUID)를 api-spec §3.2·노션 S-4 에 정합. seller 283 통과·전체 575 통과·ruff clean.

### Changed
- **판매자 챗 confirm 전송을 최상위 필드로 전환 + FE 계약 정합 (S-4, api-spec §3.2 v0.14.1)** — HITL 승인을 구 "message 문자열에 JSON 을 실어 파싱"(`pipeline.parse_confirm_message`)에서 **요청 본문 최상위 `action`/`draftId` 필드**로 전환. seller 전용 `SellerChatRequest`(`app/schemas/seller.py`)를 신설해 구매자 `ChatRequest` 는 그대로 두고, `_seller_stream` 이 `request.action == "confirm"` 로 판정한다(발화 ≠ 동의 [HARD] 는 스키마 구조로 강제 — `action=="confirm"` + `draftId` 누락은 `RequestValidationError`→400). `threadId` 필수 유지(A-3). FE↔서버 SSE 와이어 포맷(`event:` 없는 `data:{type,data}`)·confirm 형식을 노션 S-4·api-spec §3.2·`docs/specs/FE-CONTRACT-SELLER-CHAT.md` 3곳에 정합. 잔여(화면 전환용 `meta` 이벤트·draft `field` snake_case 버그 C-1 등)는 FE-CONTRACT §5 B/C/D/E 로 이관. seller 유닛 279 통과·전체 571 통과·ruff clean. (api-spec §3.2)

### Added
- **이슈 #50 — pg-profile 리질리언스·멀티 인스턴스 정합성 하드닝** — Profile/processed-events/buyer state 전 BaseStore I/O에 공통 application deadline을 적용하고, 모든 pg-profile 연결에 libpq connect/keepalive/`tcp_user_timeout` + 서버 `statement_timeout`을 배선. Profile session/fact와 Revert의 read-modify-write는 별도 pool의 PostgreSQL transaction advisory lock으로 인스턴스 간 직렬화하고, 로컬 lock registry는 weak-reference 자동 회수, 직전 추천 상품명은 config 기반 bounded LRU로 제한. Conversation 조회를 `(created_at, turn_id)`로 결정론화하고 누락 finalize를 warning으로 관측한다. 실 PostgreSQL 다중 pool 동시성·재시작·연결 파라미터 통합 테스트 포함.
- **이슈 #33 (3/3, 완료) — ConversationStore를 pg-profile 일반 테이블로 이관** — 대화 저장(§6.3 a)을 인메모리 dict placeholder에서 pg-profile `conversation_turns` 테이블(`PgConversationStore`)로 교체. checkpointer가 아니라 감사·구조화 로그 상관관계 조회 전용 일반 테이블로 확정(이슈 코멘트의 4갈래 분류 반영). `ConversationStoreProtocol` 공유 계약으로 인메모리(유닛 테스트 계속 주입)·pg 구현을 통일 — `app.pipelines.artifact_store`(카탈로그)와 동일 원칙. `RequestObservation.commit_user_message/finish`를 async로 전환해 `app/core/stream.py` 스트림 수명주기 훅 8곳에 반영. 실 pg-profile 통합 테스트(`tests/integration/test_pg_conversation_store.py`) 신설 — 재시작·다중 인스턴스 지속성 스모크 포함. 이슈 #33(상태 지속성 이관: Thread/Cart/Revert → BaseStore, Profile → BaseStore+pgvector, Conversation → 일반 테이블) 3단계 전부 완료.
- **이슈 #33 (2/3) — ProfileStore를 PostgresStore(BaseStore)+pgvector로 이관** — 요약(summary)·장기 fact·transient 세션 버퍼를 인메모리 dict에서 LangGraph BaseStore(pg-profile)로 이관. fact는 SPEC-PROFILE-001 REQ-PROF-070("위키 파일 1개=item 1개")에 맞춰 fact 1개=store item 1개로 저장해 pgvector 시맨틱 인덱스가 fact 단위로 실제 동작하도록 배선(`app.pipelines.embedding.embed_texts` 재사용 — 카탈로그와 임베딩 모델·차원 공유, 결정 6/16-A). session-end 이벤트 멱등성(`mark_if_new`)은 BaseStore의 get→put이 진짜 동시성 하에서 원자적이지 않은 문제를 발견해 전용 `processed_events` 테이블(UNIQUE 제약 + `INSERT ... ON CONFLICT DO NOTHING RETURNING`)로 분리·원자화(`app/agents/profile/processed_events.py`, `db/profile/init/00_processed_events.sql`). checkpointer 소유 경계였던 SPEC-PROFILE-001 OPEN-P9를 실제 구현(BaseStore, checkpointer 아님 — 구매자 실행 모델이 LangGraph StateGraph가 아니므로)으로 해소. 실 pg-profile 통합 테스트(`tests/integration/test_pg_profile_store.py`) 신설 — 동시 mark_if_new 10건 중 정확히 1건만 신규 처리됨을 실증. SPEC-PROFILE-001 v0.3.0 동기화(1024→1536차원 stale 정정, OPEN-P9/OPEN-P11 해소).
- **이슈 #33 (1/3) — 구매자 스레드 상태 영속화** — `ThreadFilterStore`(멀티턴 필터)·`CartStateStore`(직전 추천·옵션 되물음)·`RevertStore`(소모품 억제 되돌리기)를 인메모리 dict placeholder에서 LangGraph `BaseStore`(pg-profile, `AsyncPostgresStore`) 백엔드로 이관 — `app/agents/seller/history.py`와 동일한 dev InMemoryStore 폴백 + 운영(jwks) 폴백 금지 규약. 신규 `app/core/pg_store.py`(3개 스토어 공유 pg-profile 연결). Windows 네이티브 실행 시 기본 `ProactorEventLoop`가 psycopg async 연결을 지원하지 않아 조용히 InMemory로 전락하는 문제를 발견해 `app/main.py`에 `WindowsSelectorEventLoopPolicy` 가드 추가(seller history.py/hitl.py도 동일 수혜). `tests/integration/test_buyer_thread_store.py` 신설(실 pg-profile 재시작·다중 인스턴스 지속 스모크 포함). Profile PostgresStore+pgvector(2/3)·Conversation 테이블(3/3)은 후속.
- **E2E 통합 스모크 하니스 (#35)** — `tests/integration/` 신설: Spring을 `httpx.MockTransport` stub(I-1 검색·I-2/I-18 장바구니·I-19 이력·I-21 push·I-17 배치 + CH-5 목록 GET), LLM을 주입형 `ScriptedLLM`(decompose/rerank/enrich/delta/consolidate 5종 분기)으로 세워 라이브 의존 없이 결정적 검증. `spring_client` 함수를 patch하지 않고 **HTTP 경계에서만** 대역을 넣어 URL·`X-Internal-Token`·envelope 파싱이 실코드로 돈다. 커버: 구매자 경로 B 종단(발화→검색→rerank→push→`products.ready`→카드 조회)·프로필(session-end→델타→consolidation→`/profile/me`)·배치(I-17 pull→upsert, 페이지네이션·커서·`HIDDEN`)·degrade 6종·**jwks 실인증 레인 완주**. README에 환경변수·키 세팅 표 + 하니스 실행법 추가 (37 tests, api-spec §1.2·§3.1·§3.3·§4)
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
- **I-20 실패 안전 멱등 lifecycle** — `processed_events`를 단일 영구 마커에서
  `PROCESSING`(claim token+유한 lease) / `COMPLETED`로 분리. 요청 취소·내부 실패는 claim을
  cancellation-safe하게 해제하고, 프로세스 crash·해제 DB 실패 잔재는 lease 만료 후 재선점한다.
  delta 성공 뒤 consolidation 실패를 별도 `failed` 상태로 구분해 버퍼를 지우거나 완료 마킹하지
  않는다. 기존 볼륨은 앱 연결 시 idempotent 스키마 migration으로 완료 row를 보존한다.
- **session-end(I-20) 멱등키 = `(userId, sessionId)` 고정키** — BE 실측: session-end 는 세션을
  삭제하는 종료(`NEW_CONVERSATION`·`LOGOUT`)에만 오고 `tabClose`·`inactivityTimeout`은 발화되지
  않는다 → "하나의 `sessionId` = 하나의 논리적 종료"가 성립하므로 `session-end:{userId}:{sessionId}`
  고정키로 같은 통지 재전송(at-least-once)만 중복 처리한다. (한때 검토한 버퍼 내용 해시 방식은
  실재하지 않는 "재체크포인트" 방어라 폐기.) 신규 통지는 버퍼가 비어도 `accepted`로 기록하고,
  이후 동일 통지는 버퍼 상태와 무관하게 `duplicate`로 응답한다 (api-spec §2.7·§3.5, v0.15.17)
- **이슈 #62 — session-end(I-20) 계약 정렬** — `POST /events/session-end`가 상시 `400`을
  반환해 세션 종료 통지가 전부 실패하던 문제 수정. BE 실측 payload에 맞춰 `SessionEndEvent`에서
  `eventId`·`endedAt`를 제거하고 `userId`를 string → **number(BIGINT)**로 정정, `reason`은
  optional·enum 미강제·최대 64자. 멱등키를 `eventId` 필드 대신 **`session-end:{userId}:{sessionId}`
  파생 복합키**로 전환(같은 sessionId라도 userId가 다르면 서로 중복 아님). `userId`는 양의
  BIGINT 정수만 엄격히 받아 string/float/bool coercion을 거부한다 (api-spec §3.5·§2.7, v0.15.17)
- 프로필 세션 종료(session-end) 처리 중 동시에 새 채팅 턴이 들어오면 세션 버퍼가 통째로
  삭제되던 레이스 수정 — `clear_session_ctx_upto`(seq 워터마크 기준)로 스냅샷 분석분만
  정리하고 미분석 발화는 보존 (`newConversation` 트리거·버퍼 상한(cap) 트리밍 상황 모두 안전)

### Docs
- **판매자 챗 오류 계약·누락 계약 정합 (S-4, FE 계약 D·E) — 코드 무변경** — 오류표를 코드 실측에 맞춰 정정: confirm 실패(만료·미존재·소유불일치·중복·stale)는 HTTP 오류가 아니라 **200+안내 token+`done{panel:"keep"}`** 이므로 노션의 `409 DRAFT_EXPIRED`/`DRAFT_NOT_FOUND` 제거(D-1); `429 RATE_LIMITED` 는 `/seller/chat` 에 실제 적용됨을 확인해 유지(D-3, 초기 "미구현" 진단 정정); `409 STREAM_IN_PROGRESS`·`504 UPSTREAM_TIMEOUT` 를 노션에 추가(D-4). 누락 계약을 노션에 명시: 추천 적용("N번 적용해줘"→apply 레인, E-1)·draft 취소(별도 API 없음·TTL 만료, E-2)·scope 거절(E-3)·`field` camelCase 8종(E-4). api-spec §3.2 에 confirm-200·스트림 전 오류 목록 반영. FE-CONTRACT-SELLER-CHAT.md v1.0.0(A~E 전부 해소).
- **판매자 문서 정리 — SELLER-FINAL·SPEC-SELLER-001 v1.0.0 승격** — MVP(1~4-3단계) 완료로 역할을 다한 단계별 진행 기록 9종(`HANDOFF-SELLER`·`_2`·`_3` · `REVIEW-SELLER-STAGE2`·`STAGE3` · `DESIGN-SELLER-TOOLS-STAGE1` · `IMPL-PLAN-SELLER-001` · `REALIGN-SELLER-20260719` · `WORKFLOW-SELLER-STAGE3.png`)을 삭제. 내용은 `SELLER-FINAL-{WORKFLOW,TECH,RISKS,ROADMAP}`에 이미 흡수되어 있었고, 리포 밖에서 이들을 참조하는 곳은 없었다(코드·CLAUDE.md·mvp-plan 은 `SPEC-SELLER-001` 만 참조). 남긴 문서 6종에 `v1.0.0` 버전 헤더를 부여하고, 삭제 문서에만 정의돼 있던 BE·FE 확정 항목 `F1`·`F6` 을 `SELLER-FINAL-WORKFLOW` 머리말에 인라인 보존. `docs/specs/README.md` 에 SELLER-FINAL 5종 표를 신설(기존 표에 누락돼 있었음). 계약(api-spec) 변경 없음
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
