---
id: SPEC-PROFILE-001
version: 0.4.0
status: draft
created: 2026-07-10
updated: 2026-07-23
author: navis
priority: high
issue_number: 79
---

> ✅ **저장소 정본** — 외부 HTTP 계약(엔드포인트·필드·오류 코드)은
> [docs/api-spec.md](../api-spec.md)가 소유하고, 본 SPEC은 프로필 파이프라인의 내부 동작과
> 인수 기준을 소유한다. 두 문서가 어긋나면 계약은 api-spec을 먼저 고친 뒤 본 SPEC을 동기화한다.

# SPEC-PROFILE-001 — 사용자 프로필 파이프라인 (User Profile Pipeline: reader / builder / gate)

> 본 SPEC은 product.md Section 12-A **결정 16**(프로필 파이프라인 상세 설계)를 직접 입력으로 하여, 구매자 그래프(`POST /chat`)에 `profile_summary`를 공급하고 대화·구매 이력에서 프로필을 누적/갱신하는 **프로필 파이프라인**(`app/agents/profile/`의 `reader`/`builder`/`gate`)의 동작을 EARS 요구사항 수준으로 확정한다.
> 결정 16의 `profile_summary` 계약(하이브리드 단일 문자열·구조화 블록의 FilterSet 매핑 한정·최근 맥락 섹션·문자 기반 1,000자 config 상한·생성 측 집행·게이트 통과 미폐기 fact 한정·신규 회원 `None`), 물리 저장소(PostgresStore/LangGraph BaseStore·네임스페이스·카탈로그와 **완전 별도 인스턴스**[v0.2.0: 결정 16-A로 MVP는 **단일 인스턴스 + 별도 데이터베이스**로 개정]·pgvector·BaseStore 내장 semantic 인덱스 + 결정 6 임베딩 모델), 게이트 구현 분담(LLM 태깅 + 코드 계산), transient 3종 MVP, write 소스(대화 델타 + 구매 이력 미러), 저장된 세션 버퍼 기반 트리거(Spring 명시적 종료 + AI 내부 비활동 종료), hot-path "기억해", 마이페이지 GET only는 **구속 제약(binding)** 이며 본 SPEC에서 재논의하지 않는다.
> 결정 16이 상속하는 결정 4(OKF 위키 포맷)·결정 4-A(운영 정책 6항)도 변경 없이 구속 상속한다. 본 SPEC은 그 위에 델타 레코드/게이트 상태/Store item/GET API/`profile_summary` 섹션 레이아웃의 Pydantic 수준 스키마, 오류 처리, 인수 기준을 확정한다.

## HISTORY

- **v0.4.0 (2026-07-23, 이슈 #79)** — MVP 세션 종료 트리거 소유권을 확정했다. Spring I-20은 `logout`·`newConversation`만 전달하고 탭 닫기 신호는 제거한다. AI는 수락된 회원 발화 저장 시 pg-profile의 세션 활동 시각을 DB 서버 시각으로 갱신하고, 기본 10분 timeout/60초 sweep의 단일 인스턴스 스케줄러가 인덱스 기반 bounded batch로 비활성 세션을 선점한다. 내부 timeout은 HTTP 자기 호출 없이 I-20과 같은 finalizer·고정키 claim을 사용하되, idle 성공은 영구 종료가 아닌 재개 가능한 checkpoint로 claim을 해제한다. 새 활동은 completed activity를 active로 되돌리고 이전 종료 generation을 같은 transaction에서 무효화하며, terminal finalizer는 처리 중 새 activity를 completed로 덮지 않는다. scheduler는 라이브 스트림 슬롯을 점유하지 않는다. 처리 전 활동/활성 스트림 재확인, token+lease claim, claim별 실패 격리·crash 재시도와 명시적 종료 경합 인수 기준을 추가했다.
- **v0.3.0 (2026-07-20, 이슈 #33; v0.15.17 구현 보강)** — 저장소 이관 구현 완료 반영. (1) **임베딩 모델/차원 갱신**: 결정 6 "셀프호스트 1024차원"은 카탈로그 파이프라인이 이슈 #31 로 Google `gemini-embedding-001`(1536-dim, MRL 절단 수동 L2 정규화)로 전환되며 stale — REQ-PROF-074 자체가 "카탈로그와 모델 공유"를 요구하므로 프로필도 동일 모델/차원을 그대로 따른다(신규 계약 협의 아님, 기존 REQ 의 자연스러운 적용). §5.3 네임스페이스 주석·§1.3·§4 결정 6 행·REQ-PROF-074·§10 비용 문구 갱신(차원 1024→1536, 임베딩 비용 0 문구 삭제 — Google API 호출이라 토큰 비용 발생). (2) **checkpointer→BaseStore 로 구현 확정, OPEN-P9 해소**: session_context(구매자 스레드 상태 전반 — ThreadFilter/Cart/Revert/session_ctx)는 실제 LangGraph StateGraph 가 없는 구매자 실행 모델(단순 함수 호출 체인) 특성상 checkpointer 가 아니라 BaseStore(app/core/pg_store.py 공유 연결, 별도 인스턴스는 아니고 같은 pg-profile 물리 인스턴스 내 별도 store 객체) 로 구현됨 — write 소유는 구매자 그래프(app/agents/buyer/graph.py) 그대로. (3) **fact 저장 단위 확정**: REQ-PROF-070 "위키 파일 1개 = item 1개" 원칙을 그대로 적용해 fact 마다 개별 store item(uuid 키)으로 저장 — semantic 인덱스가 fact 단위로 실제 동작(요약/세션버퍼는 `index=False`). (4) **session-end 멱등 파생키 lifecycle**: 전용 `processed_events` 테이블에서 `session-end:{userId}:{sessionId}`의 PROCESSING(token+lease)과 COMPLETED를 분리한다. 실패·취소는 claim 해제, crash 잔재는 lease 재선점하며 성공 뒤에만 완료 마킹한다(app/agents/profile/processed_events.py, db/profile/init/00_processed_events.sql). (5) OPEN-P11 부분 해소: 서빙 형태는 FastAPI 프로세스 내 동기 SDK 호출(app.pipelines.embedding.embed_texts, google-genai)로 확정. 요구사항·스키마 구조·게이트 규칙은 무변경.
- **v0.2.0 (2026-07-15)** — 결정 16-A(저장소 물리 구성 개정) 반영. "카탈로그와 완전 별도 Postgres 인스턴스"를 MVP 기준 **단일 Postgres 인스턴스 안의 별도 데이터베이스 2개**(catalog/profile)로 개정 — 논리 분리 규율(DB 단위 분리로 cross-DB 조인 구조적 차단 + 계정 분리[search read-only/프로필 워커 profile 한정] + cross-DB 의존 금지)로 부하 격리 목적을 대체하고, 물리 인스턴스 분리는 고도화 승격 경로(연결 문자열 교체 수준)로 유예. REQ-PROF-072/073/074 개정, AC-PROF-21 개정, DoD·불변식 문구 갱신, OPEN-P9에 물리 결합 논점 소멸 주석. 그 외 요구사항·스키마 불변. 근거: 데모 규모에 격리할 부하 없음(2026-07-15 AWS 배포 구성 논의, product.md 결정 16-A).
- **v0.1.0 (2026-07-10)** — 최초 작성. 결정 16(프로필 파이프라인 상세 설계)을 파이프라인 EARS 명세로 구체화. `profile_summary` 섹션 레이아웃·델타 레코드·게이트 상태·Store item·GET API 스키마 초안 확정. reader(동기 read, LLM 0회)·builder(2단계: 세션별 델타 생성 → sleep-time consolidation)·gate(3조건 승격, LLM 태깅 + 코드 계산 분담) 계약 정의. 결정 16이 구속 상속하는 결정 4/4-A 및 관련 결정 5/6/8/9/10-A/12/14-F를 §4 참조 표에 반영. 결정 16 내부의 몇몇 판독 긴장(에피소딕 최근 맥락의 게이트 예외, 구매 신호의 명시성 부재와 3조건 AND 여부, checkpointer 소유 경계, GET 노출 범위)은 해소하지 않고 §9 OPEN 항목으로 명시 등록한다.

---

## 1. 개요 & 범위 (Overview & Scope)

### 1.1 목적

사용자의 대화·구매 이력에서 취향/맥락을 지속 누적·갱신하고(build), 그래프 진입 시 추천·재랭킹이 소비할 압축 취향 요약(`profile_summary`)을 저지연 동기 조회로 공급하며(read), 일시적 요청 오염 없이 검증된 선호만 프로필로 승격시키는(gate) **프로필 파이프라인**의 관찰 가능한 동작을 정의한다. 파이프라인은 요청 경로 안의 동기 `reader`와, 요청 경로 밖의 비동기 `builder`(세션별 델타 생성 → sleep-time consolidation) + `gate`(승격 판단)로 구성된다.

### 1.2 In Scope (본 SPEC이 확정하는 것)

- `reader`: 그래프 진입 시 `profile_summary`의 동기 조회 — store 단일 get, LLM 0회, 지연 크리티컬. 게스트·신규 회원은 `None`.
- `profile_summary` 생성 계약: 하이브리드 단일 문자열의 섹션 레이아웃(구조화 블록 / 산문 / 최근 맥락)과 구조화 블록의 필드 규약(FilterSet 매핑 가능 속성 한정: 가격 성향·선호/회피 브랜드·평점 성향·Layer 2 속성), consolidation 재작성에 의한 크기 집행.
- `builder` 1단계(델타 생성): 미처리 세션당 Sonnet 1회 호출로 명시성/현저성/transient 태그가 붙은 후보 델타 산출 + 세션별 관심 분포 스냅샷.
- `builder` 2단계(sleep-time consolidation): EMA 누적·승격 판정·recency-wins 충돌 해소·supersede 처리·중복 병합의 결정론적 코드(병합된 위키 파일의 텍스트 통합에만 LLM 사용, 신선도/승격 판단에는 LLM 미사용), 크기 상한 내 요약 재생성.
- `gate`: 3조건 승격 로직과 그 LLM 태깅/코드 계산 분담, 엔트로피 신호의 최소 세션 수 가드, 구매 신호 판정 경로(명시성 없음).
- 트리거: 저장된 세션 버퍼(정합성 기반), Spring best-effort 세션 종료 통지(idempotent), AI 내부 10분 비활동 스케줄러, sleep-time 스케줄링 config, 구매 이력 스캔.
- hot-path "기억해": fact 즉시 기록, 턴 중 요약 재생성 없음.
- 저장소 스키마: Store item 구조(valid_from/last_confirmed/superseded_by 포함 frontmatter 메타·confidence·type semantic|episodic), 네임스페이스 레이아웃, 임베딩 인덱스 구성.
- 마이페이지 표시용 `GET /profile/{user_id}` API(마크다운 passthrough).
- 오류 처리(델타/consolidation LLM 실패, store 불가용, activity claim 고착 — 데이터 날조 금지, MoAI 3회 재시도 원칙), 인수 기준, DoD.

### 1.3 의존성 (Dependencies — 본 SPEC 외부, 참조만)

이 파이프라인은 아래 컴포넌트가 제공하는 계약에 의존하나, 그 구현은 본 SPEC의 범위가 아니다.

| 의존 대상 | 제공하는 것 | 소유 |
|---|---|---|
| 구매자 그래프 (`agents/buyer/graph.py`) | 그래프 진입 시 `reader` 호출 지점, 진입 시 주입 대상(`profile_summary`) | 구매자 그래프 SPEC(별도) |
| 추천 서브그래프 (SPEC-RECOMMEND-001) | `profile_summary`를 read-only 문자열로 소비(REQ-REC-005/006, OPEN-9 해소 대상) | SPEC-RECOMMEND-001 |
| session_context / 프로필 세션 버퍼 (BaseStore) | 대화 스레드 상태와 회원 발화 버퍼의 영속 저장. 세션 중 write는 구매자 그래프 소관 | 구매자 그래프 SPEC(별도) — 본 SPEC은 finalizer에서 버퍼를 소비(§9 OPEN-P9) |
| 주문 이벤트 미러 (결정 9 채널 확장 / 14-F) | 사용자별 경량 구매 이력 미러(user_id·product_id·category·purchased_at) — read-only 조회. 추천 dedup(14-F)과 동일 미러 공유 | 카탈로그/주문 이벤트 SPEC(별도) |
| Spring 세션 종료 통지 (I-20) | `logout`·`newConversation` 명시적 종료의 조기 트리거(best-effort) — 유실 허용 | Spring / api-spec §3.5 |
| 임베딩 모델 (결정 6, v0.3.0 갱신) | BaseStore 내장 semantic 인덱스의 1536차원(Google `gemini-embedding-001`) 임베딩 계산(카탈로그 파이프라인과 모델 공유, 인스턴스는 별도) | `app.pipelines.embedding`(이슈 #31/#33) |
| 리뷰 분석 그래프 (결정 10-A) | (고도화) 작성자 취향 신호 공급 — 본 SPEC은 수신 계약 자리만 예약 | 리뷰 분석 SPEC(별도, 고도화) |

---

## 2. Exclusions (What NOT to Build)

[HARD] 본 파이프라인에서 **구현하지 않는** 항목을 명시한다.

- **EX-P1 추천 서브그래프 동작**: `profile_summary`를 소비하는 재랭킹·개인화 로직은 SPEC-RECOMMEND-001 소관(REQ-REC-005/006). 본 파이프라인은 그 문자열을 **생산·공급**하고, 소비 측은 이를 불투명 read-only 문자열로만 취급한다. 추천이 요약을 어떻게 쓰는지는 본 SPEC의 범위가 아니다.
- **EX-P2 리뷰 신호 수신 구현**: 리뷰 분석 그래프(결정 10-A)가 공급하는 작성자 취향 신호의 실제 수신·게이팅 구현은 **고도화 범위(MVP 비구현)**. 본 SPEC은 수신 계약 슬롯(write 소스 열거의 예약 항목)만 남기고 동작을 구현하지 않는다(REQ-PROF-024).
- **EX-P3 프로필 편집 PUT**: 마이페이지에서 사용자가 프로필을 직접 수정하는 PUT(사용자 수정 = confidence 최상급 병합, 결정 4-A 보강 6)은 **고도화 범위(MVP 비구현)**. MVP는 조회(GET)만 제공한다(REQ-PROF-082).
- **EX-P4 브라우저 탭 종료 신호·Spring 세션 수명 판정**: 탭 닫기 전용 FE→BE→AI 또는 FE→AI API를 만들지 않는다. AI의 10분 비활동 판정은 **프로필 버퍼 flush 시점**만 소유하며 Spring Redis 세션의 유효성·만료 응답을 대신 판정하지 않는다. 사용자가 제한 시간 안에 돌아와 발화하면 활동 시각이 갱신되고, 돌아오지 않으면 timeout이 처리한다.
- **EX-P5 주문 이벤트 미러 적재 계약**: 주문 이벤트 → AI 경량 미러의 **적재(ingestion) 계약·이벤트 채널**은 카탈로그/주문 이벤트 SPEC 소관(결정 9/14-F). 본 SPEC은 이미 적재된 미러를 **read-only로 스캔**만 한다(REQ-PROF-054).
- **EX-P6 완전한 시간 인식 지식 그래프·유저별 LoRA**: temporal KG 백엔드(Zep/Graphiti)와 파라메트릭/유저별 LoRA는 결정 4에서 **v2로 유예·명시 기각**됨. 본 SPEC은 OKF 위키(자연어 마크다운 + frontmatter) 논리 모델과 결정론적 recency-wins만 구현하며, 그래프 추론·파라메트릭 편집은 구현하지 않는다.
- **EX-P7 턴 중 요약 재생성**: `profile_summary` 재생성은 **sleep-time consolidation 전용**이다. 턴 중(요청 경로) 또는 "기억해" hot-path에서 요약을 재생성하지 **않는다**. 같은 세션 내에서 방금 기록된 fact의 즉시 반영은 추천 서브그래프의 멀티턴 filters 병합(SPEC-RECOMMEND-001 §6.7, 본 SPEC 비범위)이 커버한다(REQ-PROF-061).

---

## 3. 용어 (Glossary)

| 용어 | 정의 |
|---|---|
| `reader` | 그래프 진입 시 `profile_summary`를 store 단일 get으로 동기 조회하는 컴포넌트(`agents/profile/reader.py`). LLM 0회, 지연 크리티컬 |
| `builder` | 요청 경로 밖에서 프로필을 갱신하는 컴포넌트(`agents/profile/builder.py`). 2단계: (1) 세션별 델타 생성, (2) sleep-time consolidation |
| `gate` | 후보 델타의 프로필 승격 여부를 판정하는 로직(`agents/profile/gate.py`). LLM 태깅 + 코드 계산 분담(결정 16) |
| `profile_summary` | 그래프 진입 시 주입되는 압축 취향 요약. **하이브리드 단일 마크다운 문자열**(구조화 블록 + 산문 + 최근 맥락). 게스트·신규 회원 `None`(결정 16) |
| 델타(delta) | 한 세션에서 추출된 후보 프로필 변경 사항. 명시성/현저성/transient 태그(LLM)와 대상 파일·구조화 속성을 담음. 승격 전까지는 미확정 상태 |
| 승격(promotion) | 후보 델타가 3조건 게이트를 통과해 프로필 위키(store)에 반영되는 전이. sleep-time에 코드가 판정 |
| 3조건 게이트 | 반복성(EMA confidence 누적) · 현저성(salience) · 명시성(사용자 직접 진술)의 승격 게이트(결정 4-A). "기억해"는 명시성 hot-path 예외 |
| transient(일시적) | "이번엔 비싸도 돼", "엄마 선물" 같은 일회성/상황적 요청. session_context에만 기록하고 프로필 write 후보에서 배제(결정 4-A/16) |
| session_context | pg-profile BaseStore의 구매자 스레드 상태. transient 요청·세션 관찰이 누적되는 계층이며 세션 종료 시 장기 프로필로 자동 전파되지 않음 |
| consolidation | 여러 세션 델타를 위키에 병합·중복 제거·모순 해소하는 sleep-time 배치 처리. 텍스트 통합은 LLM, 신선도/승격 판단은 코드 |
| recency-wins | 최신성 충돌을 결정론적 코드(타임스탬프 비교)로 해소하는 정책. LLM에 최신성 판단을 위임하지 않음(결정 4-A, STALE arXiv:2605.06527) |
| supersede | fact 폐기 대신 `superseded_by`로 이력을 보존하는 처리. 삭제(delete)하지 않음(결정 4-A) |
| EMA | 지수이동평균. 반복 관측 시 confidence 누적·승격, 재확인 없는 선호는 감쇠(PAMU arXiv:2510.09720) |
| 관심 분포 스냅샷 | 세션별 카테고리 관심 분포의 스냅샷. 엔트로피 급증(선물/탐색 세션 신호) 코드 계산의 입력(결정 16 transient (b)) |
| 세션 활동 행 | `(user_id, session_id)`별 `last_activity_at`·상태·claim lease를 보관하여 bounded timeout sweep의 대상을 결정하는 pg-profile 레코드 |
| 미러(order mirror) | 주문 이벤트에서 파생된 AI 서버 측 경량 구매 이력 사본(user_id·product_id·category·purchased_at). 추천 dedup(14-F)과 공유 |
| Store item | PostgresStore(BaseStore)의 저장 단위. key = 위키 파일 경로, value = frontmatter 필드 + 마크다운 본문 |

---

## 4. 관련 결정 참조 (Related Decisions)

본 SPEC은 아래 확정 결정을 구속 제약으로 상속한다(product.md Section 12-A).

| 결정 | 내용 | 본 SPEC 반영 |
|---|---|---|
| 결정 4 | 프로필 저장 포맷 OKF 스타일 자연어 위키 + 경량 frontmatter, `profiles/{user_id}/` 번들(index.md + 지식 단위 파일), semantic/episodic 분리, consolidation 갱신, 진입 시 index.md + 압축 요약만 read | §5.3/§6.2/§6.8 전반 |
| 결정 4-A | 6항 보강 — (1) transient 격리, (2) 3조건 승격 게이트("기억해" 예외), (3) 2단 비동기 쓰기, (4) 임베딩 검색 인덱스 + frontmatter 구조화 필드, (5) valid_from/last_confirmed/superseded_by + recency-wins + EMA 감쇠 + supersede, (6) 마이페이지 노출/편집 | §6.3/§6.4/§6.5/§6.7/§6.8/§6.9 |
| 결정 16 | 프로필 파이프라인 상세 설계(본 SPEC의 직접 입력) — `profile_summary` 계약, 물리 저장소, 게이트 구현 분담, 트리거/Spring 인터페이스, write 소스. **SPEC-RECOMMEND-001 OPEN-9 해소** | 본 SPEC 전체 |
| 결정 5 | Haiku 4.5(경량) + Sonnet 5(상위). 프로필 델타 생성·consolidation = Sonnet, 캐시된 입력 ITPM 미차감(프롬프트 캐싱 권장) | REQ-PROF-020/031, §비기능 |
| 결정 6 (v0.3.0 갱신) | 임베딩 Google `gemini-embedding-001` 1536차원 — BaseStore 내장 semantic 인덱스가 소비. 카탈로그와 모델 공유, 인스턴스 별도 | REQ-PROF-074, §1.3 |
| 결정 8 | 비회원은 프로필 없음, AI 서버 무상태 — 게스트는 reader `None`, build 스킵 | REQ-PROF-003/041 |
| 결정 9 | 이벤트 기반 준실시간 동기화, 일 1회 보정 배치 패턴 — 주문 미러 채널의 근간 | §1.3, REQ-PROF-054 |
| 결정 10-A | 리뷰 분석 그래프가 (고도화) 작성자 취향 신호 공급 — 본 SPEC은 수신 계약 슬롯만 예약 | EX-P2, REQ-PROF-024 |
| 결정 12 / 이슈 #79 | 종료 트리거 소유권 분리 — Spring=`logout`·`newConversation`, AI=프로필 버퍼 10분 비활동 flush, 탭 닫기 신호 없음 | EX-P4, REQ-PROF-051/056~059 |
| 결정 14-F | 구매 이력 미러는 추천 dedup과 공유. 미러 이벤트 채널 계약은 카탈로그/주문 이벤트 SPEC 소유, 본 SPEC은 read-only 소비 | EX-P5, REQ-PROF-054 |

---

## 5. 인터페이스 정의 (Interface Definitions)

### 5.1 `profile_summary` 섹션 레이아웃

`reader`가 반환하고 추천·재랭킹이 소비하는 하이브리드 단일 마크다운 문자열의 논리 구조. 실제 타입은 `str | None`이며(SPEC-RECOMMEND-001 State `profile_summary: str | None` 무개정), 아래는 그 문자열의 **내부 섹션 규약**이다. `decompose`와 `rerank`에 **동일한 문자열**이 주입된다(결정 16). 구조화 블록 필드는 decompose의 `source == derived` 필터 유일 원천(SPEC-RECOMMEND-001 REQ-REC-047 연계)이다.

```
# (섹션 1) 구조화 블록 — FilterSet 매핑 가능 속성 한정
#   가격 성향(price disposition) / 선호·회피 브랜드 / 평점 성향 / Layer 2 속성
#   confidence 수치는 노출하지 않음(결정 16). 강도는 자연어로만.
# (섹션 2) 산문(prose) — rerank용 취향 서술. 자연어, confidence 미노출
# (섹션 3) 최근 맥락(recent context) — recency 윈도우 내 episodic 하이라이트 2~3개
```

논리 스키마(생성 측 계약을 명세하기 위한 표현이며, 소비 측은 문자열로만 취급):

```python
class ProfileSummarySections(BaseModel):
    """profile_summary 문자열의 논리 섹션 계약 (생성 측 검증용).
    최종 산출물은 이 세 섹션을 결합·압축한 단일 마크다운 str이다."""
    structured_block: StructuredPreferences  # 섹션 1 — FilterSet 매핑 속성만
    prose: str                               # 섹션 2 — 자연어 취향 서술
    recent_context: list[EpisodicHighlight]  # 섹션 3 — 최근 episodic 2~3개 (config)

class StructuredPreferences(BaseModel):
    # FilterSet(SPEC-RECOMMEND-001 §5.1) 매핑 가능 속성 한정 — 그 외 속성 금지
    price_disposition: str | None = None     # 가격 성향 (자연어, 예: "3~5만원대 선호")
    preferred_brands: list[str] = []
    avoided_brands: list[str] = []
    rating_disposition: str | None = None    # 평점 성향 (예: "4.5+ 위주")
    layer2_attributes: dict[str, Any] = {}   # Layer 2 속성 성향 (예: {"무선": true})
    # confidence 수치·내부 메타는 노출하지 않음 (결정 16)

class EpisodicHighlight(BaseModel):
    label: str                               # 최근 상황/구매 요지 (예: "지난주 유럽여행 준비")
    occurred_at: str                         # ISO-8601 (recency 윈도우 판정용)
    # episodic 하이라이트는 recency 윈도우 + salience로 선택 (§9 OPEN-P8 참조)
```

크기 상한: 문자 기반 기본 1,000자(`core/config.py` 주입, `summary.char_cap`). 집행은 **생성 측(consolidation) 압축 재작성**이며 소비 측 절단이 아니다(REQ-PROF-016). 게스트·신규 회원은 문자열 전체가 `None`(REQ-PROF-003).

### 5.2 델타 레코드 / 게이트 상태 스키마

`builder` 1단계가 생성하고 `gate`/2단계가 소비하는 후보 델타, 세션별 관심 분포 스냅샷, 그리고 코드가 관리하는 게이트 누적 상태.

```python
class ProfileDelta(BaseModel):
    """builder 1단계 산출 — 미처리 세션당 Sonnet 1회 호출로 생성된 후보 변경."""
    user_id: str
    thread_id: str                           # 원천 대화 스레드 (워터마크 대상)
    target_path: str                         # 대상 위키 파일 경로 (= Store item key)
    type: Literal["semantic", "episodic"]
    assertion: str                           # 자연어 사실 진술 (consolidation 입력)
    structured_attrs: dict[str, Any] = {}    # frontmatter 구조화 필드 후보
    source: Literal["conversation", "purchase", "review"]  # review는 고도화 예약(EX-P2)
    # LLM 태깅 (델타 생성 1회 호출 내 흡수, 추가 호출 없음 — 결정 16)
    explicitness: float                      # 명시성 (0.0~1.0). 구매 소스는 명시성 없음→낮음(§9 OPEN-P7)
    salience: float                          # 현저성 (0.0~1.0)
    transient: bool                          # 일시적 요청 여부 (true면 session_context 격리, 승격 배제)
    created_at: str                          # ISO-8601

class InterestDistributionSnapshot(BaseModel):
    """builder 1단계가 세션별로 남기는 관심 분포 스냅샷 (transient (b) 엔트로피 신호 입력)."""
    user_id: str
    session_id: str
    distribution: dict[str, float]           # 카테고리 → 관심 비중/빈도
    computed_at: str

class GateState(BaseModel):
    """코드가 sleep-time에 관리하는 승격 누적 상태 (LLM 아님)."""
    user_id: str
    preference_key: str                      # 승격 후보 선호 키 (예: "brand:소니")
    ema_confidence: float                    # 반복성 EMA 누적 (config α)
    observation_count: int
    last_salience: float
    promoted: bool = False
    valid_from: str | None = None            # 승격 시 부여 (결정 4-A 메타)
    last_confirmed: str | None = None        # 최근 재확인 시각
    superseded_by: str | None = None         # 폐기 대신 supersede 참조 (결정 4-A)
    updated_at: str
```

### 5.3 Store item 스키마 (PostgresStore / BaseStore)

위키 "파일" 1개 = Store item 1개. 네임스페이스는 `("profile" | "facts" | "episodes", user_id)`(결정 16). key = 위키 파일 경로, value = frontmatter 필드 + 마크다운 본문.

```python
class StoreItemValue(BaseModel):
    """PostgresStore item의 value 스키마. key(=위키 파일 경로)는 store가 관리."""
    # frontmatter 필드 (결정 4 포맷 + 결정 4-A 메타)
    type: Literal["semantic", "episodic"]    # 결정 4 (필수)
    tags: list[str] = []
    confidence: float | None = None          # 내부 메타 — profile_summary에는 미노출
    valid_from: str | None = None            # 결정 4-A (5)
    last_confirmed: str | None = None
    superseded_by: str | None = None
    structured_attrs: dict[str, Any] = {}    # 수치·구조 속성은 산문 대신 frontmatter (결정 4-A (4))
    # 본문
    body: str                                # 자연어 마크다운 (마이페이지 노출·임베딩 대상)

# 네임스페이스 규약 (결정 16)
#   ("profile",  user_id)  → index.md (압축 요약 진입점)
#   ("facts",    user_id)  → 지식 단위 semantic 파일 (budget.md, taste/fashion.md ...)
#   ("episodes", user_id)  → episodic 파일 (situations/travel-2026.md ...)
#
# 임베딩 인덱스 (결정 16, v0.3.0 갱신): BaseStore 내장 semantic 인덱스.
#   embed 대상 = fact 필드(1 fact = 1 item), 차원 = 1536 (결정 6, Google gemini-embedding-001).
#   embed 함수·차원은 core/config.py 주입 (하드코딩 금지). summary·session_ctx 는 index=False.
#
# 물리 배치 (결정 16/16-A): MVP는 단일 Postgres 인스턴스 내 별도 데이터베이스(catalog/profile).
#   profile DB에도 pgvector 확장 필요. 계정 분리(search read-only / 프로필 워커 profile 한정).
#   session_context(구매자 스레드 상태)는 profile 측 동거 확정(BaseStore, §9 OPEN-P9 해소, v0.3.0).
```

### 5.4 마이페이지 GET API 페이로드 스키마

```python
# GET /profile/{user_id}
# 마이페이지 표시용 — 자연어 마크다운 passthrough (결정 16, MVP는 GET only).
class ProfileViewResponse(BaseModel):
    user_id: str
    exists: bool                             # 프로필 존재 여부 (게스트·신규 회원 false)
    markdown: str | None                     # 사람이 읽는 프로필 마크다운. 미존재 시 None
    generated_at: str | None                 # 요약 생성 시각 (sleep-time consolidation 시각)
    # 주의: reader가 그래프 진입 시 반환하는 압축 profile_summary와 GET 노출 범위는
    #       서로 다를 수 있다(§9 OPEN-P10). GET은 사용자 투명성용(결정 4-A 보강 6),
    #       reader는 지연 크리티컬 압축 요약용.
```

---

## 6. 기능 요구사항 (Functional Requirements — EARS)

> 공통 규약(HARD): 모든 튜너블(요약 문자 상한, EMA α, 승격 임계, 엔트로피 임계, 최소 세션 수, recency 윈도우, 대화 보존 기간, sleep-time 배치 주기, 임베딩 차원 등)은 `core/config.py`에서 config 주입한다 — 하드코딩 금지(결정 16 고민 항목이 전부 config 주입으로 MVP 기본값 동작).

### 6.1 프로필 리더 (reader)

- **REQ-PROF-001** (Event-Driven): **When** 구매자 그래프가 진입(entry)하면, the `reader` **shall** 해당 `user_id`의 `profile_summary`를 PostgresStore에서 **단일 get 1회**로 동기 조회하여 반환한다.
- **REQ-PROF-002** (Ubiquitous): The `reader` **shall** 조회 시 LLM을 **호출하지 않는다**(요약 생성은 sleep-time 전용, REQ-PROF-035) — read 시점 LLM 호출 수는 0이며 지연 크리티컬 경로로 취급한다.
- **REQ-PROF-003** (State-Driven): **While** `user_id`가 부재(게스트)이거나 신규 회원이라 승격된 프로필이 없는 동안, the `reader` **shall** `profile_summary`를 `None`으로 반환한다 — 게스트와 신규 회원은 동일 경로이며(결정 16), 소비 측(SPEC-RECOMMEND-001 REQ-REC-006)이 개인화를 스킵한다.
- **REQ-PROF-004** (Ubiquitous): The `reader`가 반환하는 압축 요약과 마이페이지 `GET`(§6.9)이 반환하는 마크다운은 **노출 범위가 다를 수 있다** — `reader`는 §5.1의 압축 단일 문자열(지연 크리티컬)을, `GET`은 사용자 투명성용 사람이 읽는 마크다운을 반환한다. `reader`는 전체 번들을 로드하지 **않는다**(결정 4 읽기 정책, index.md + 압축 요약만).

### 6.2 `profile_summary` 생성 계약 (summary contract)

- **REQ-PROF-010** (Ubiquitous): The `profile_summary` **shall** 하이브리드 단일 마크다운 문자열이며 §5.1의 세 섹션(구조화 블록 / 산문 / 최근 맥락)을 포함한다.
- **REQ-PROF-011** (Ubiquitous): The 구조화 블록 **shall** FilterSet 매핑 가능 속성(가격 성향·선호/회피 브랜드·평점 성향·Layer 2 속성)만 담고, 그 외 속성을 담지 **않는다** — 이 블록은 decompose의 `source == derived` 필터 유일 원천이기 때문이다(SPEC-RECOMMEND-001 REQ-REC-047 연계).
- **REQ-PROF-012** (Ubiquitous): The 산문 섹션 **shall** rerank용 취향 서술을 자연어로 담되, confidence 수치·내부 메타를 노출하지 **않는다**(강도는 자연어로만).
- **REQ-PROF-013** (Ubiquitous): The 최근 맥락 섹션 **shall** recency 윈도우(config `summary.recency_window`) 내 episodic 하이라이트를 config 개수(기본 2~3개)로 담는다.
- **REQ-PROF-014** (Ubiquitous): The `reader`/생성기 **shall** `decompose`와 `rerank`에 **동일한** `profile_summary` 문자열을 주입한다 — 소비처별로 다른 요약을 생성하지 **않는다**(결정 16).
- **REQ-PROF-015** (Ubiquitous): The `profile_summary` **shall** 승격 게이트를 통과했고(§6.5) `superseded_by`가 없는(미폐기) fact만 반영한다(결정 16, 결정 14-B 항목 6 이행). *(단, 최근 맥락 섹션의 episodic 하이라이트가 반복성 게이트에 종속되는지 여부는 §9 OPEN-P8 참조 — 본 SPEC은 episodic 하이라이트를 recency+salience 선택으로 처리한다고 가정한다.)*
- **REQ-PROF-016** (Ubiquitous): The 요약 크기 상한 **shall** 문자 기반 config 값(`summary.char_cap`, 기본 1,000)으로 하며, 집행은 **생성 측(consolidation) 압축 재작성**으로 수행하고 소비 측 절단으로 처리하지 **않는다**(결정 16).
- **REQ-PROF-017** (State-Driven): **While** 신규 회원이라 승격 fact가 없는 동안, the 생성기 **shall** 요약을 억지로 생성하지 않고 `None`을 유지한다(콜드스타트 = 게스트와 동일, REQ-PROF-003).

### 6.3 빌더 1단계 — 델타 생성 (delta generation)

- **REQ-PROF-020** (Event-Driven): **When** sleep-time 배치가 미처리 세션(스레드)을 스캔하면, the `builder` 1단계 **shall** 세션당 Claude Sonnet 5(결정 5)를 **1회** 호출하여 후보 델타 목록(`ProfileDelta`)을 산출하고, 각 델타에 명시성/현저성/transient를 태깅한다 — 태깅은 이 델타 생성 호출 내에서 함께 수행하며 별도 LLM 호출을 두지 **않는다**(결정 16).
- **REQ-PROF-021** (Event-Driven): **When** 세션 델타를 생성하면, the `builder` 1단계 **shall** 그 세션의 관심 분포 스냅샷(`InterestDistributionSnapshot`)을 함께 저장하여 엔트로피 신호(§6.5)의 코드 계산 입력으로 남긴다.
- **REQ-PROF-022** (Ubiquitous): The `builder` 1단계 **shall** transient 신호 (a)(명시적 한정어·수혜자 전환)와 (c)(intent≠preference 라우팅)를 델타 생성 프롬프트 안에서 판정해 `ProfileDelta.transient`에 반영한다(결정 16 — LLM 흡수 신호).
- **REQ-PROF-023** (Unwanted): The `builder` **shall not** 턴 중(요청 경로)에 프로필 store에 write하지 않는다 — 관찰은 session_context 버퍼에만 누적하고, 델타 생성은 세션 종료 후(sleep-time 배치)에만 수행한다(결정 4-A 3, 2단 비동기).
- **REQ-PROF-024** (Optional): **Where** write 소스가 대화(`conversation`) 또는 구매(`purchase`)인 경우에 한하여, the `builder` **shall** 델타를 생성한다. 리뷰(`review`) 소스는 수신 계약 슬롯만 예약하며 MVP에서 델타 생성을 구현하지 **않는다**(EX-P2, 고도화 — 결정 10-A).
- **REQ-PROF-025** (Ubiquitous): The `builder` 1단계 **shall** 각 델타에 대상 위키 파일 경로(`target_path` = Store item key)와 `type`(semantic|episodic)을 부여한다 — 지속 취향/예산은 semantic, 최근 상황·구매는 episodic로 분류한다(결정 4 메모리 분할).

### 6.4 빌더 2단계 — sleep-time consolidation

- **REQ-PROF-030** (Event-Driven): **When** sleep-time 배치가 실행되면, the `builder` 2단계 **shall** 여러 세션 델타를 위키에 병합·중복 제거·모순 해소하며, 맹목적 append·overwrite를 하지 **않는다**(결정 4 갱신 정책).
- **REQ-PROF-031** (Ubiquitous): The `builder` 2단계 **shall** 병합된 위키 파일의 **텍스트 통합(재작성)에만** LLM(Sonnet, 결정 5)을 사용하고, 신선도·승격 판단에는 LLM을 사용하지 **않는다**(결정 16 — "판단은 AI, 계산·검증은 코드").
- **REQ-PROF-032** (Ubiquitous): The `builder` 2단계 **shall** 반복성 EMA 누적, 승격 판정, recency-wins 충돌 해소, supersede 처리, 중복 병합을 **결정론적 코드**로 수행한다(재현 가능).
- **REQ-PROF-033** (Unwanted): The `builder` 2단계 **shall not** 최신성 판단을 LLM에 위임하지 않는다 — 최신성 충돌은 `valid_from`/`last_confirmed` 타임스탬프 비교의 결정론적 recency-wins 코드로 해소한다(결정 4-A 5, STALE arXiv:2605.06527 근거).
- **REQ-PROF-034** (Unwanted): The `builder` 2단계 **shall not** fact를 삭제하지 않는다 — 폐기 대신 `superseded_by`로 supersede하여 이력을 보존한다. 재확인되지 않은 선호는 EMA 감쇠를 적용한다(결정 4-A 5).
- **REQ-PROF-035** (Event-Driven): **When** consolidation이 완료되면, the `builder` 2단계 **shall** `profile_summary`를 §6.2 계약(문자 상한 내 압축 재작성)에 따라 재생성한다 — 요약 재생성은 sleep-time에만 발생한다(결정 16).
- **REQ-PROF-036** (Ubiquitous): The `builder` 2단계 **shall** 승격 시 각 fact에 `valid_from`을, 재확인 시 `last_confirmed`를 갱신하여 감쇠·모순 해소 메타를 유지한다(결정 4-A 5).
- **REQ-PROF-037** (Ubiquitous): The `builder` 2단계 **shall** Spring I-20 조기 통지 또는 AI inactivity timeout이 공통 session finalizer를 호출할 때 실행한다. 별도 전역 sleep-time 주기나 `run_after_session_end` 이중 경로를 두지 않는다(결정 16, 이슈 #79).

### 6.5 게이트 (gate)

- **REQ-PROF-040** (Ubiquitous): The `gate` **shall** 승격을 3조건(반복성 EMA · 현저성 salience · 명시성)으로 판정하며, 구현을 **LLM 태깅**(명시성·현저성·transient — 델타 생성 1회 호출 내)과 **코드 계산**(EMA 누적·임계 비교·승격 확정·recency-wins — sleep-time)으로 분담한다(결정 16).
- **REQ-PROF-041** (State-Driven): **While** `user_id`가 게스트인 동안, the `gate`/`builder` **shall** 프로필 승격 및 build를 스킵한다(결정 8 — 비회원 프로필 없음, AI 서버 무상태).
- **REQ-PROF-042** (Unwanted): **If** 델타가 transient로 태깅되면, **then** the `gate` **shall** 해당 델타를 session_context에 격리하고 프로필 승격 후보에서 배제한다 — 세션 종료 시 폐기한다(결정 4-A 1).
- **REQ-PROF-043** (Optional): **Where** transient 신호 (b)(관심 분포 엔트로피 급증)를 판정하는 경우, the `gate` **shall** 세션별 분포 스냅샷(REQ-PROF-021)을 입력으로 코드가 엔트로피를 계산하되, 최소 세션 수(config `entropy.min_sessions`) 미달 시 이 신호를 **비활성**한다(이력 부족 시 노이즈 방지 가드, 결정 16).
- **REQ-PROF-044** (Event-Driven): **When** 델타 소스가 구매(`purchase`)이면, the `gate` **shall** 명시성 없이 반복성·현저성 중심으로 승격을 판정한다 — 구매는 행동 신호이므로 명시성 조건을 요구하지 않는다(결정 16). *(3조건이 strict AND인지 가중 앙상블인지의 정확한 의미론은 §9 OPEN-P7 — 본 SPEC은 명시성이 필수가 아닌 가중 신호라고 가정한다.)*
- **REQ-PROF-045** (Event-Driven): **When** 사용자가 명시적 "기억해" 명령을 발화하면, the `gate` **shall** 3조건 게이트를 우회하는 hot-path 예외로 처리한다(즉시 fact 기록은 §6.7) — 명시성 hot-path는 반복성·현저성 누적을 기다리지 않는다(결정 4-A 2).
- **REQ-PROF-046** (Ubiquitous): The `gate`의 모든 임계(EMA α, 승격 임계, 엔트로피 급증 임계, 최소 세션 수, salience 임계) **shall** `core/config.py`에서 config 주입한다 — 하드코딩 금지(결정 16 고민 항목).

### 6.6 트리거 · 스케줄 (triggers & scheduling)

- **REQ-PROF-050** (Ubiquitous): The 파이프라인 **shall** 델타 생성의 정합성 원천을 pg-profile에 영속 저장된 **회원 세션 버퍼**로 두며, Spring 통지 payload의 내용이나 전달 성공에 정합성을 의존하지 **않는다**.
- **REQ-PROF-051** (Event-Driven): **When** Spring이 `logout` 또는 `newConversation` 종료를 통지하면, the 파이프라인 **shall** 이를 best-effort 조기 트리거로 사용한다. I-20은 같은 `(userId, sessionId)` 재전송에 idempotent하며 `tabClose`·`inactivityTimeout`을 Spring wire 사유로 요구하지 않는다(api-spec §3.5).
- **REQ-PROF-052** (Ubiquitous): The 파이프라인 **shall** Spring I-20과 AI 내부 timeout을 하나의 session finalizer 및 고정키 `PROCESSING` claim으로 직렬화한다. Spring I-20 성공은 processed event를 영구 `COMPLETED`로 확정하지만, 처리 중 activity generation 또는 claim 소유권이 바뀌면 terminal 완료를 중단한다. AI idle 성공은 같은 claim을 해제하는 checkpoint로 끝내 동일 sessionId의 후속 활동을 다시 처리할 수 있어야 한다. 실패·취소 시 버퍼를 보존하고 activity claim을 `ACTIVE`로 되돌려 재시도를 허용한다.
- **REQ-PROF-053** (Ubiquitous): The 파이프라인 **shall** 비활동 기준(기본 600초), sweep 주기(기본 60초), 한 번의 조회 상한, 최대 동시 finalizer 수와 claim lease를 config 주입값으로 운용한다. activity claim lease는 `ceil(batch size / max concurrency) × 2단계 LLM 최악 예산`보다 길어 모든 batch wave의 대기·처리를 포괄해야 한다.
- **REQ-PROF-054** (Event-Driven): **When** sleep-time 배치가 실행되면, the 파이프라인 **shall** 구매 이력 미러(주문 이벤트 → AI 경량 미러)를 read-only로 스캔하여 구매 소스 델타 후보를 생성한다 — 미러의 적재 계약·이벤트 채널은 본 SPEC 소관이 아니다(EX-P5, 결정 14-F와 미러 공유).
- **REQ-PROF-055** (Unwanted): The 세션 종료 통지 엔드포인트 **shall not** 통지 payload의 `reason`을 프로필 처리 분기나 정합성 원천으로 신뢰하지 않는다. 실제 처리 입력은 저장된 회원 세션 버퍼이며 `reason`은 관측용이다(REQ-PROF-050 연계).
- **REQ-PROF-056** (Event-Driven): **When** 인증·검증을 통과한 회원 사용자 발화를 `conversation_turns`에 저장하면, the 대화 저장소 **shall** 같은 PostgreSQL transaction에서 `(user_id, session_id)`의 `last_activity_at`을 **DB 서버 시각**으로 upsert하고 해당 고정키의 이전 `PROCESSING`/`COMPLETED` processed event를 삭제한다. 프로필 세션 버퍼 저장은 그 뒤 별도 저장소에서 수행한다. 새 활동은 activity를 `ACTIVE`로 재개하고 진행 중/완료된 이전 종료 generation을 무효화한다. 게스트·판매자·거부되거나 대화 저장에 실패한 요청은 활동을 갱신하지 않는다.
- **REQ-PROF-057** (Event-Driven): **When** 비활동 sweep이 실행되면, the scheduler **shall** `last_activity_at <= DB now - timeout`이며 `status='ACTIVE'` 또는 lease가 만료된 `PROCESSING` 후보만 `(status, last_activity_at)` 인덱스로 조회하여 config batch size까지 `PROCESSING`으로 원자 선점한다. `conversation_turns` 전체 집계(`MAX(created_at)`)나 active 전 행 full scan을 하지 않는다. 이 job의 등록·실행은 I-17 및 `GOOGLE_API_KEY` 구성 여부와 독립적이어야 한다.
- **REQ-PROF-058** (State-Driven): **While** timeout 후보를 처리하는 동안, the scheduler **shall** token+lease로 행을 원자 선점하고 finalizer 진입 직전 DB 활동 시각과 in-memory 활성 스트림을 재확인한다. 이미 활성인 스트림·재활동·다른 유효 claim이 있으면 이번 처리를 건너뛴다. scheduler는 실제 stream registry 슬롯을 acquire하지 않아 finalizer 처리 중 새 정상 채팅을 `409 STREAM_IN_PROGRESS`로 거절하지 않아야 한다. activity 완료/소유권 기록이 실패하면 성공이 아니라 `retryable`로 집계하고 claim을 해제한다. timeout coroutine은 FastAPI 메인 event loop에서 실행하여 loop-bound pg-profile store와 활성 스트림 레지스트리를 background thread/별도 loop에서 직접 접근하지 않는다.
- **REQ-PROF-059** (Unwanted): The scheduler **shall not** 자기 `/events/session-end` 엔드포인트를 HTTP 호출하거나 탭 닫기 전용 API를 만들지 않는다. 내부 timeout과 Spring 통지는 공통 finalizer 및 고정키 claim으로 경합을 안전하게 흡수한다. idle 성공은 claim을 해제하고 새 활동이 activity를 재개하게 하며, 실패·프로세스 crash 뒤에는 lease 만료 또는 claim 해제로 재시도할 수 있어야 한다.

### 6.7 hot-path "기억해"

- **REQ-PROF-060** (Event-Driven): **When** 사용자가 명시적 "기억해" 명령을 발화하면, the 파이프라인 **shall** 해당 fact를 `manage_memory_tool` 경로로 store에 **즉시 기록**한다(결정 4-A 2 / 결정 16 hot-path).
- **REQ-PROF-061** (Unwanted): The hot-path "기억해" **shall not** 턴 중에 `profile_summary`를 재생성하지 않는다 — 요약 반영은 sleep-time 원칙을 유지하며, 같은 세션 내 즉시 효과는 추천 서브그래프의 멀티턴 filters 병합(SPEC-RECOMMEND-001 §6.7, 본 SPEC 비범위)이 커버한다(결정 16, EX-P7).
- **REQ-PROF-062** (Ubiquitous): The hot-path 기록 fact **shall** 통상 fact와 동일한 frontmatter 메타(valid_from/last_confirmed/type/confidence)를 부여받아, 이후 sleep-time consolidation에서 recency-wins·supersede 처리에 정상 참여한다.

### 6.8 저장소 스키마 (storage)

- **REQ-PROF-070** (Ubiquitous): The 파이프라인 **shall** 프로필을 PostgresStore(LangGraph BaseStore)에 저장하며, 위키 "파일" 1개 = Store item 1개(key = 위키 파일 경로, value = frontmatter 필드 + 마크다운 본문)로 매핑한다(결정 16, §5.3).
- **REQ-PROF-071** (Ubiquitous): The 파이프라인 **shall** 네임스페이스를 `("profile" | "facts" | "episodes", user_id)`로 구성한다 — `profile`은 index.md 압축 요약, `facts`는 semantic 지식 단위, `episodes`는 episodic 파일(결정 16).
- **REQ-PROF-072** (Ubiquitous, 결정 16-A 개정): The 프로필 store **shall** 카탈로그 검색 인덱스와 **별도의 데이터베이스**를 사용한다 — MVP는 단일 Postgres 인스턴스 내 DB 분리(catalog/profile) + 계정 분리(`search_service`는 catalog read-only, 프로필 워커는 profile 한정)이며, cross-DB 조인에 의존하지 **않는다**. 물리 인스턴스 분리는 부하 격리가 실제 필요해질 때의 고도화 승격 경로다(연결 문자열 교체 수준).
- **REQ-PROF-073** (Ubiquitous, 결정 16-A 개정): The 프로필 데이터베이스 **shall** pgvector 확장을 갖춘다(BaseStore 내장 semantic 인덱스가 요구 — catalog/profile 두 DB 각각 `CREATE EXTENSION`).
- **REQ-PROF-074** (Ubiquitous, v0.3.0 갱신): The BaseStore 내장 semantic 인덱스 **shall** fact 항목(1 fact = 1 Store item)을 결정 6의 Google `gemini-embedding-001` 1536차원 모델로 임베딩하며, 임베딩 함수·차원은 config 주입한다(하드코딩 금지, `embedding_model_id`·`embedding_dim`) — 카탈로그와 모델은 공유하되 데이터베이스는 별도다(결정 16-A). summary·session_ctx 항목은 semantic 인덱스 대상이 아니다(`index=False`).
- **REQ-PROF-075** (Ubiquitous, v0.3.0 확정): session_context(구매자 스레드 상태 — ThreadFilter/Cart/Revert/session_ctx) **shall** 프로필 인스턴스(pg-profile)에 동거하는 BaseStore 로 구현한다(§9 OPEN-P9 해소) — write 소유는 구매자 그래프(app/agents/buyer/graph.py)가 그대로 가진다.
- **REQ-PROF-076** (Ubiquitous): The 각 Store item **shall** frontmatter에 `type`(필수), `confidence`, `valid_from`/`last_confirmed`/`superseded_by`, `structured_attrs`(수치·구조 속성)를 담는다(결정 4 + 결정 4-A 4/5).
- **REQ-PROF-077** (Ubiquitous): The pg-profile `profile_session_activity` 테이블 **shall** `(user_id BIGINT, session_id TEXT)`를 primary key로 하고 `last_activity_at TIMESTAMPTZ`, `status(ACTIVE|PROCESSING|COMPLETED)`, `claim_token`, `lease_expires_at`을 저장한다. `(status, last_activity_at)` indexed access path를 제공하고 모든 시간 비교는 PostgreSQL 서버 시각을 사용한다.

### 6.9 마이페이지 API (`GET /profile/{user_id}`)

- **REQ-PROF-080** (Event-Driven): **When** `GET /profile/{user_id}` 요청이 도착하면, the API **shall** 해당 사용자의 사람이 읽는 프로필 마크다운을 `ProfileViewResponse`로 반환한다(자연어 마크다운 passthrough — 결정 4-A 보강 6 "노출" 이행, 결정 16 MVP GET only).
- **REQ-PROF-081** (State-Driven): **While** 대상 `user_id`가 게스트이거나 프로필이 없는 동안, the API **shall** `exists = false`, `markdown = null`을 반환하고 오류를 발생시키지 **않는다**.
- **REQ-PROF-082** (Unwanted): The API **shall not** 프로필 수정(PUT)을 제공하지 않는다 — 사용자 편집(수정 = confidence 최상급 병합)은 고도화 범위다(EX-P3, 결정 16).

### 6.10 오류 처리 관련 요구 (see §7)

- **REQ-PROF-090** (Unwanted): **If** `builder` 1단계 델타 생성 LLM 호출이 실패(오류/타임아웃)하면, **then** the 파이프라인 **shall** 해당 세션 버퍼를 삭제하거나 완료 처리하지 **않고**, MoAI 3회 재시도 원칙 하에서 재시도하며 델타를 날조하지 **않는다**.
- **REQ-PROF-091** (Unwanted): **If** `builder` 2단계 consolidation의 텍스트 통합 LLM 호출이 실패하면, **then** the 파이프라인 **shall** 기존 위키·기존 `profile_summary`를 보존(부분 갱신·손상 금지)하고 재시도하며, 실패한 병합을 다음 배치로 이월한다.
- **REQ-PROF-092** (Unwanted): **If** store(PostgresStore)가 불가용하면, **then** the `reader` **shall** `profile_summary`를 `None`으로 반환하여(추천은 게스트 경로로 정상 성립) 요청 경로를 막지 않고, `builder`는 write를 재시도 대상으로 이월한다 — 어느 경우에도 데이터를 날조하지 **않는다**.
- **REQ-PROF-093** (Unwanted): **If** 세션 활동 claim이 crash로 남으면, **then** the 파이프라인 **shall** lease 만료 후 보수적으로 재선점하며, 저장된 세션 버퍼를 조용히 건너뛰거나 삭제하지 **않는다**.
- **REQ-PROF-094** (Ubiquitous): The 모든 오류 처리 **shall** 노드별 재시도를 MoAI constitution의 최대 3회/작업 원칙 하에서 수행하고, 실패 시에도 프로필·요약·세션 버퍼·activity claim을 복구 가능한 상태로 유지한다(fail-safe).

---

## 7. 오류 처리 (Error Handling)

| 실패 지점 | 감지 | 처리 | 안전 불변식 |
|---|---|---|---|
| 델타 생성 실패 (1단계 Sonnet 오류/타임아웃) | LLM 호출 예외 | 최대 3회 재시도, 세션 버퍼 보존(다음 sweep/통지에서 재처리) | 델타 날조 금지, 저장 발화 유실 금지 |
| consolidation 텍스트 통합 실패 (2단계 Sonnet) | LLM 호출 예외 | 기존 위키·기존 요약 보존, 실패 병합 다음 배치 이월 | 부분 갱신·프로필 손상 금지 |
| store read 불가용 (reader) | store get 예외 | `profile_summary = None` 반환(추천 게스트 경로) | 요청 경로 블로킹 금지, `None` 외 값 날조 금지 |
| store write 불가용 (builder) | store put 예외 | write 재시도 이월(다음 배치) | 세션 버퍼·activity claim 복구 가능, 데이터 손실 금지 |
| 비활동 finalizer 실패·crash | 예외 또는 claim lease 만료 | 버퍼 보존, claim 해제 또는 lease 만료 후 재선점 | 실패를 `COMPLETED`로 확정하지 않음, 세션 버퍼 유실 금지 |
| 세션 활동 claim 고착 | lease 만료 감지 | 보수적 재선점(고정키 claim이 동시 실행 직렬화) | 저장 버퍼의 조용한 건너뜀 금지 |
| 임베딩 인덱스 실패 (BaseStore semantic) | embed/index 예외 | 텍스트 검색·링크 그래프로 degrade, 인덱스 재구축 이월 | 위키 본문 데이터 손상 금지 |
| `GET /profile/{user_id}` 미존재 사용자 | store 조회 결과 없음 | `exists = false`, `markdown = null` | 오류(4xx/5xx) 아닌 정상 응답 |

- 재시도 정책은 MoAI constitution의 최대 3회/작업 원칙 하에서 노드별 재시도를 기본으로 한다(구체 백오프 값은 구현 결정).
- 프로필·요약·세션 버퍼·activity claim은 어떤 실패에서도 손상되지 않고 재시도 가능한 상태(fail-safe)로 유지되어야 하며, 데이터 날조(존재하지 않는 fact·요약 생성)는 금지한다.

---

## 8. 인수 기준 (Acceptance Criteria)

모든 기준은 관찰 가능/테스트 가능해야 한다. Given-When-Then 형식.

- **AC-PROF-01 (리더 해피패스)**: **Given** 승격된 프로필이 있는 회원 `user_id`, **When** 그래프가 진입하면, **Then** `reader`는 store 단일 get **1회**로 `profile_summary`(str)를 반환하고, 이 과정에서 LLM 호출 수는 0이다(REQ-PROF-001/002).
- **AC-PROF-02 (게스트·신규 회원 None)**: **Given** `user_id` 부재(게스트) 또는 승격 fact가 없는 신규 회원, **When** 그래프가 진입하면, **Then** `reader`는 `profile_summary == None`을 반환하고 예외가 발생하지 않는다(REQ-PROF-003/017).
- **AC-PROF-03 (reader ≠ my-page 범위)**: **Given** 동일 회원, **When** `reader`가 그래프 진입 시 반환한 압축 요약과 `GET /profile/{user_id}`가 반환한 마크다운을 비교하면, **Then** 전자는 압축 단일 문자열(전체 번들 미로드)이고 후자는 사람이 읽는 마크다운으로, 노출 범위가 다를 수 있음이 관찰 가능하다(REQ-PROF-004).
- **AC-PROF-04 (요약 3섹션 구조)**: **Given** 승격 fact가 충분한 회원, **When** `profile_summary`가 생성되면, **Then** 구조화 블록·산문·최근 맥락 세 섹션이 모두 존재한다(REQ-PROF-010).
- **AC-PROF-05 (구조화 블록 FilterSet 한정)**: **Given** 프로필에 FilterSet 매핑 불가 속성(예: 자유 서술 취향)과 매핑 가능 속성(가격 성향·브랜드)이 섞여 있는 회원, **When** 요약이 생성되면, **Then** 구조화 블록에는 FilterSet 매핑 가능 속성만 나타나고 그 외 속성은 산문 섹션으로만 나타난다(REQ-PROF-011).
- **AC-PROF-06 (동일 문자열 주입)**: **Given** 임의의 추천 턴, **When** `decompose`와 `rerank`에 각각 `profile_summary`가 주입되면, **Then** 두 곳에 주입된 문자열은 **동일**하다(REQ-PROF-014).
- **AC-PROF-07 (문자 상한 생성 측 집행)**: **Given** 승격 fact가 많아 요약이 상한을 초과할 회원과 config `summary.char_cap = 1000`, **When** consolidation이 요약을 재생성하면, **Then** 결과 문자열 길이는 1,000자 이하이고, 초과분은 소비 측 절단이 아니라 생성 측 압축 재작성으로 처리된 것이 관찰 가능하다(REQ-PROF-016).
- **AC-PROF-08 (게이트 통과 미폐기 fact만)**: **Given** 승격되지 않은 후보 fact와 `superseded_by`가 설정된 폐기 fact가 함께 있는 회원, **When** 요약이 생성되면, **Then** 두 fact 모두 `profile_summary`에 나타나지 않는다(REQ-PROF-015).
- **AC-PROF-09 (세션당 델타 1회 호출 + 태깅)**: **Given** 미처리 세션 1개, **When** `builder` 1단계가 실행되면, **Then** 그 세션에 대한 Sonnet 호출은 1회이고, 산출 델타 각각에 explicitness/salience/transient 태그가 붙어 있으며, 태깅을 위한 추가 LLM 호출은 없다(REQ-PROF-020).
- **AC-PROF-10 (세션별 관심 분포 스냅샷)**: **Given** 델타가 생성된 세션, **When** 1단계가 완료되면, **Then** 해당 세션의 `InterestDistributionSnapshot`이 저장되어 엔트로피 신호의 입력으로 사용 가능하다(REQ-PROF-021).
- **AC-PROF-11 (턴 중 write 금지)**: **Given** 진행 중인 대화 턴, **When** 사용자 발화가 처리되면(비 "기억해"), **Then** 프로필 store에 대한 write는 발생하지 않고 관찰은 session_context에만 누적되며, 델타 생성은 세션 종료 후 배치에서만 발생한다(REQ-PROF-023).
- **AC-PROF-12 (EMA·승격·recency-wins 코드 결정론)**: **Given** 동일한 델타 집합, **When** consolidation을 두 번 실행하면, **Then** EMA 누적·승격 판정·recency-wins 결과가 동일하며(재현 가능), 이 판정에 LLM이 호출되지 않는다(REQ-PROF-032/033).
- **AC-PROF-13 (supersede not delete)**: **Given** 기존 fact와 그를 대체하는 최신 fact, **When** consolidation이 충돌을 해소하면, **Then** 구 fact는 삭제되지 않고 `superseded_by`가 설정되어 이력이 보존된다(REQ-PROF-034).
- **AC-PROF-14 (구매 신호 명시성 없이 승격)**: **Given** 명시성 신호가 없는 구매 소스 델타가 반복성·현저성을 충족, **When** 게이트가 판정하면, **Then** 해당 선호가 승격된다 — 명시성 부재가 승격을 원천 차단하지 않는다(REQ-PROF-044).
- **AC-PROF-15 (엔트로피 최소 세션 가드)**: **Given** config `entropy.min_sessions` 미달의 사용자, **When** 게이트가 transient (b) 엔트로피 신호를 판정하면, **Then** 이 신호는 비활성 처리되어 오탐(노이즈)이 발생하지 않는다(REQ-PROF-043).
- **AC-PROF-16 (transient 격리)**: **Given** "이번엔 비싸도 돼" 같은 일시적 발화, **When** 델타가 transient로 태깅되면, **Then** 해당 델타는 session_context에만 남고 프로필 승격 후보에서 배제되어 장기 프로필로 전파되지 않는다(REQ-PROF-042).
- **AC-PROF-17 (통지 유실 회수)**: **Given** Spring 세션 종료 통지가 유실된 회원 세션, **When** 마지막 저장 발화로부터 10분 뒤 비활동 sweep이 실행되면, **Then** 저장된 세션 버퍼를 회수해 델타를 생성한다 — 통지 유실이 델타 유실로 이어지지 않는다(REQ-PROF-050/056~059).
- **AC-PROF-18 (세션 종료 통지 idempotent)**: **Given** 동일 세션 종료 통지가 2회 이상 도착, **When** 엔드포인트가 이를 처리하면, **Then** 델타·프로필이 중복 처리되지 않는다(REQ-PROF-051).
- **AC-PROF-19 ("기억해" 즉시 기록, 턴 중 요약 재생성 없음)**: **Given** 사용자의 "이거 기억해줘" 발화, **When** 처리되면, **Then** fact가 store에 즉시 기록되지만 `profile_summary`는 턴 중에 재생성되지 않으며, 요약 반영은 다음 세션 finalization에 일어난다(REQ-PROF-060/061).
- **AC-PROF-20 (Store item 구조)**: **Given** 승격된 semantic fact, **When** store에 반영되면, **Then** 해당 item의 key는 위키 파일 경로이고 value는 frontmatter 필드(type/valid_from/last_confirmed/superseded_by/confidence/structured_attrs) + 마크다운 본문이며, 네임스페이스는 `("facts", user_id)`이다(REQ-PROF-070/071/076).
- **AC-PROF-21 (별도 데이터베이스 + 계정 분리, 결정 16-A)**: **Given** docker compose 구성, **When** 서비스를 기동하면, **Then** 단일 Postgres 서비스 안에 catalog/profile **별도 데이터베이스**(각각 pgvector 확장)가 관찰되고, `search_service` 계정은 profile DB에 접근 권한이 없으며 프로필 워커 계정은 catalog DB에 쓰기 권한이 없다(REQ-PROF-072/073).
- **AC-PROF-22 (GET 마크다운 passthrough)**: **Given** 프로필이 있는 회원, **When** `GET /profile/{user_id}`가 호출되면, **Then** `exists == true`, `markdown`은 사람이 읽는 자연어 마크다운이고 PUT 경로는 제공되지 않는다(REQ-PROF-080/082).
- **AC-PROF-23 (GET 미존재 처리)**: **Given** 게스트 또는 프로필 미보유 `user_id`, **When** `GET /profile/{user_id}`가 호출되면, **Then** `exists == false`, `markdown == null`이고 오류가 아닌 정상 응답이다(REQ-PROF-081).
- **AC-PROF-24 (consolidation LLM 실패 안전)**: **Given** 2단계 텍스트 통합 LLM이 강제 실패하도록 주입된 상태, **When** 배치가 실행되면, **Then** 기존 위키·기존 `profile_summary`와 세션 버퍼가 보존되고(부분 손상 없음) 실패 병합이 다음 배치로 이월되며 완료 마킹되지 않는다(REQ-PROF-091, §7).
- **AC-PROF-25 (reader store 불가용 → None 폴백)**: **Given** store read가 강제 실패하도록 주입된 상태, **When** 그래프가 진입하면, **Then** `reader`는 `profile_summary == None`을 반환해 요청 경로를 막지 않고, 추천은 게스트 경로로 정상 성립한다(REQ-PROF-092).
- **AC-PROF-26 (10분 경계와 재활동)**: **Given** timeout=600초인 회원 세션, **When** 마지막 활동이 599초 전이면 대상이 아니고 600초 이상이면 대상이며, 처리 전 새 발화가 저장되면 **Then** 활동 시각이 갱신되어 이번 timeout finalization은 실행되지 않는다(REQ-PROF-056~058).
- **AC-PROF-27 (bounded indexed sweep)**: **Given** 대량의 세션 활동 행과 batch size=N, **When** sweep 1회가 실행되면, **Then** 최대 N개만 claim하고 `(status,last_activity_at)` 인덱스를 사용하는 후보 쿼리가 실행되며 `conversation_turns` 전체 집계는 발생하지 않는다(REQ-PROF-057).
- **AC-PROF-28 (timeout·I-20 경합 멱등)**: **Given** 동일 `(userId,sessionId)`에 AI timeout과 Spring `logout`/`newConversation` 통지가 경합, **When** 둘이 동시에 finalizer에 진입하면, **Then** 델타·consolidation·버퍼 삭제는 논리적으로 한 번만 완료되고 한 경로는 `duplicate`로 수렴한다(REQ-PROF-052/059).
- **AC-PROF-29 (timeout 실패 복구)**: **Given** timeout finalizer가 델타/consolidation 중 실패하거나 프로세스가 crash, **When** claim이 해제되거나 lease가 만료된 뒤 다음 sweep이 실행되면, **Then** 보존된 버퍼가 재처리되고 실패 실행은 `COMPLETED`로 남지 않는다(REQ-PROF-052/058/059).
- **AC-PROF-30 (종료/checkpoint 후 같은 세션 재개)**: **Given** idle checkpoint 또는 Spring terminal 처리가 시작·완료된 sessionId, **When** 같은 sessionId의 새 회원 발화가 저장되거나 finalizer 처리 중 새 stream이 시작되면, **Then** 정상 채팅은 scheduler 때문에 409를 받지 않고 activity는 `ACTIVE`로 재개되며 이전 processed-event generation은 무효화된다. 처리 중 terminal finalizer는 새 activity를 `COMPLETED`로 덮지 않고, 새 버퍼는 다음 timeout/I-20에서 다시 처리된다(REQ-PROF-052/056/058/059).

### Definition of Done

- [ ] REQ-PROF-001~004, 010~017, 020~025, 030~037, 040~046, 050~059, 060~062, 070~077, 080~082, 090~094 전 항목이 테스트로 커버됨.
- [ ] AC-PROF-01~30 전 시나리오가 통과(pytest, integration은 docker compose 앱 + 단일 Postgres 서비스[catalog/profile 데이터베이스 2개, 각 pgvector] 구성 — 결정 16-A).
- [ ] `profile_summary` 섹션 레이아웃/델타 레코드/게이트 상태/Store item/GET API 스키마가 Pydantic 모델로 구현되고 스키마 계약 테스트 존재(`ProfileSummarySections`·`StructuredPreferences`·`ProfileDelta`·`GateState`·`StoreItemValue`·`ProfileViewResponse` 포함).
- [ ] 하드 불변식(reader LLM 0회·단일 get, 턴 중 write 금지, EMA/승격/recency-wins 코드 결정론, supersede-not-delete, 요약 문자 상한 생성 측 집행, 게이트 통과 미폐기 fact만 요약 반영, `decompose`·`rerank` 동일 문자열 주입) 회귀 테스트 존재.
- [ ] 게이트 분담(LLM 태깅 + 코드 계산, transient 3종 MVP, 구매 신호 명시성 없음, 엔트로피 최소 세션 가드, REQ-PROF-040~046, AC-PROF-14/15/16) 구현·테스트 존재.
- [ ] 저장된 세션 버퍼 기반 트리거(Spring I-20 best-effort·idempotent + AI 내부 inactivity scheduler, REQ-PROF-050~059, AC-PROF-17/18/26~29) 구현·테스트 존재 — 경계·재활동·경합·실패 복구 회귀 테스트 포함.
- [ ] 저장소(PostgresStore BaseStore·네임스페이스·카탈로그와 별도 데이터베이스 + 계정 분리[결정 16-A]·pgvector·BaseStore semantic 인덱스 + 결정 6 임베딩 모델·`profile_session_activity`, REQ-PROF-070~077, AC-PROF-20/21/27) 구현·테스트 존재.
- [ ] 마이페이지 `GET /profile/{user_id}`(마크다운 passthrough·게스트 처리, PUT 미제공, REQ-PROF-080~082, AC-PROF-22/23) 구현·테스트 존재.
- [ ] 오류 처리(델타/consolidation LLM 실패, store 불가용, claim 고착, 데이터 날조 금지, 3회 재시도, REQ-PROF-090~094, AC-PROF-24/25/29, §7) 회귀 테스트 존재 — 실패 시 프로필·요약·세션 버퍼 fail-safe 유지 검증 포함.
- [ ] 리뷰 신호 수신(EX-P2)·프로필 편집 PUT(EX-P3)은 MVP 비범위(고도화)임을 회귀 테스트에 반영(고도화 미구현 경계 — 수신 계약 슬롯만 예약).
- [ ] 모든 튜너블이 `core/config.py` 주입(하드코딩 금지)임을 검증하는 테스트 존재(요약 문자 상한·EMA α·승격 임계·엔트로피 임계·최소 세션 수·recency 윈도우·비활동 timeout/sweep/batch/concurrency/lease·임베딩 차원).
- [ ] §9의 미해결 항목이 후속 SPEC/이슈로 등록됨.

---

## 9. 미해결 / 후속 항목 (Open Questions & Follow-ups)

> **시점 관례** 🔴 — 아래 OPEN 항목은 MVP를 **막지 않는다**. 해당 기능은 **MVP에서 단순 기본값(config)으로 동작**하며, OPEN은 그 기본값의 **정밀 확정·튜닝(정량 목표·경계 재조정)만 MVP 이후**로 미룬 것이다. 스모크 검증은 SPEC-RECOMMEND-001 §6.12 평가 하니스(골든셋 + 유저 시뮬레이터)로 MVP 중에도 수행한다. 반면 "MVP 비구현" 기능은 §2 Exclusions(EX-*)에 별도로 명시한다(그쪽은 MVP에 동작 자체가 없음).

- **OPEN-P1 (요약 문자 상한 기본값)**: `summary.char_cap` 기본 1,000자가 세 섹션(구조화·산문·최근 맥락)을 담기에 적정한지는 데모 프로필 실측 후 조정(TBD). config 주입이므로 스키마 변경 없이 조정 가능(REQ-PROF-016, 결정 16).
- **OPEN-P2 (EMA α·승격 임계)**: 반복성 EMA α와 승격 confidence 임계의 정밀값은 골든셋/시뮬레이터 실측 후 확정(TBD). MVP는 config 기본값으로 동작(REQ-PROF-040/046, 결정 16).
- **OPEN-P3 (엔트로피 급증 임계·최소 세션 수)**: transient (b) 엔트로피 급증 임계와 `entropy.min_sessions` 가드값은 실측 후 확정(TBD). 이력 부족 시 노이즈 방지를 위해 MVP는 보수적 기본값(REQ-PROF-043, 결정 16).
- **OPEN-P4 (최근 맥락 recency 윈도우)**: 최근 맥락 섹션의 recency 윈도우와 하이라이트 개수(기본 2~3)는 실측 후 조정(TBD). config 주입(REQ-PROF-013, 결정 16).
- **OPEN-P5 (대화 보존 기간)**: 처리 전 세션 버퍼와 완료 대화의 보존 기간(`conversation.retention_period`)은 데모 규모·비용 실측 후 확정(TBD). config 주입(REQ-PROF-053, 결정 16).
- **OPEN-P6 (sleep-time consolidation 주기)**: consolidation 배치 주기와 "세션 종료 직후 실행" 옵션의 균형은 데모 차세션 반영 요구 실측 후 조정(TBD). **세션 비활동 판정값은 이 항목과 별개로 이슈 #79에서 기본 timeout 600초/sweep 60초로 확정**하며 config로 조정한다(REQ-PROF-037/053).
- **OPEN-P7 (3조건 게이트 AND vs 가중 앙상블 의미론)**: 결정 16은 구매 신호를 "명시성 없이 반복성·현저성 중심으로 판정"한다고 하나(REQ-PROF-044), 결정 4-A의 "3조건 게이트"가 3조건을 strict AND로 요구하는지 가중 앙상블(명시성은 기여 신호)인지 명시하지 않는다. 두 판독이 상충한다 — strict AND면 구매 신호가 명시성 부재로 **영원히 승격 불가**해 결정 16의 "구매도 write 소스" 의도와 모순되고, 가중 앙상블이면 "기억해" hot-path 예외(REQ-PROF-045)가 자연스럽다. 본 SPEC은 **가중 앙상블(명시성 필수 아님)** 을 가정하고 진행하나, 정확한 게이트 의미론과 가중치는 실측·확정 대상(TBD). 🔴 이는 판독 긴장이므로 상위 결정 계층에서 확인 필요.
- **OPEN-P8 (최근 맥락 episodic의 게이트 예외 경계)**: 결정 16은 요약이 "게이트 통과 미폐기 fact만" 반영한다고 하나(REQ-PROF-015), 동시에 최근 맥락 섹션은 recency 윈도우 내 **episodic 하이라이트 2~3개**를 담는다(REQ-PROF-013). episodic 하이라이트는 최근 단발 이벤트라 반복성(EMA) 조건을 구조적으로 충족하지 못한다 — "게이트 통과"(반복성 포함)와 "최근 episodic 포함"이 상충한다. 본 SPEC은 최근 맥락 섹션의 episodic 하이라이트를 **반복성 게이트가 아닌 recency 윈도우 + salience 선택**으로 처리한다고 가정하나, 이 예외의 정확한 경계(어떤 episodic이 요약에 오를 자격이 있는가)는 확정 대상(TBD). 🔴 판독 긴장, 상위 결정 계층 확인 필요.
- **[v0.3.0 해소] OPEN-P9 (session_context 소유·물리 배치 경계)**: 구매자 실행 모델이 실제 LangGraph StateGraph 가 아니라 단순 함수 호출 체인이라 "checkpointer"라는 메커니즘 자체를 적용할 수 없음이 이슈 #33 구현 중 확인됨 — 대신 BaseStore(app/core/pg_store.py, pg-profile 동거)로 구현했다. write 소유는 그대로 구매자 그래프(app/agents/buyer/graph.py) — 프로필 파이프라인은 read-only 소비만 한다(REQ-PROF-050/075 불변). 결정 16-A(단일 인스턴스)로 물리 결합 우려는 이미 소멸했었고, 이번에 스키마·구현 소유까지 확정됨.
- **OPEN-P10 (GET 마이페이지 노출 범위)**: 결정 16은 GET이 "자연어 마크다운"을 반환한다고만 하고 노출 범위(index.md 압축 요약만인지, 전체 지식 단위 번들을 조립한 마크다운인지)를 명시하지 않는다. reader(그래프 진입)는 압축 요약만 로드하나(결정 4 읽기 정책), 마이페이지는 사용자 투명성용(결정 4-A 6)이라 더 넓은 노출이 자연스럽다. 본 SPEC은 GET을 **사람이 읽는 프로필 마크다운(reader 압축 요약보다 넓을 수 있음)** 으로 가정하나, 정확한 조립 범위는 확정 대상(TBD). 🔴 기획 UX 확인 항목.
- **[v0.3.0 해소] OPEN-P11 (임베딩 서빙 형태 공유 의존)**: 결정 6 이 이슈 #31 로 확정됨(Google `gemini-embedding-001` API, FastAPI 프로세스 내 동기 SDK 호출, `app.pipelines.embedding.embed_texts`) — 프로필도 동일 함수를 그대로 재사용한다(REQ-PROF-074). 별도 경량 임베딩 서비스 분리는 채택되지 않았다.

---

## 비기능 요구사항 (Non-Functional Requirements)

> 하드 시간 추정을 두지 않는다. 지연은 상대적 예산/우선순위로 표현한다.

### 지연·경로 예산 가이드라인 (상대적)

- `reader`(store 단일 get, LLM 0회)는 **요청 경로에 포함되는 유일한 프로필 컴포넌트**이므로 지연 크리티컬 — 가장 가벼운 지연이어야 한다(최적화 우선순위 High). LLM·전체 번들 로드·다홉 링크 순회는 read 경로에서 금지(REQ-PROF-001/002).
- `builder` 1·2단계와 `gate`는 **요청 경로 밖**(sleep-time 배치)에서 실행되어 챗봇 응답 지연에 영향을 주지 않는다(결정 4-A 3, 2단 비동기).
- consolidation의 LLM 텍스트 통합(Sonnet)이 프로필 파이프라인의 지배적 비용원이나, 배치 처리라 응답 지연과 무관하다.

### 토큰/비용 가드레일

- **read 시점 LLM 0회**: reader는 LLM을 호출하지 않는다 — 요약은 sleep-time에 미리 계산(precompute)되어 store에 저장됨(Sleep-time Compute arXiv:2504.13171 근거, 결정 16).
- **델타 생성 세션당 1회**: builder 1단계는 미처리 세션당 Sonnet 1회 — 명시성·현저성·transient 태깅을 이 호출에 흡수하여 추가 호출을 두지 않는다(REQ-PROF-020, 결정 16).
- **consolidation LLM은 텍스트 통합 전용**: EMA·승격·recency-wins·dedup은 코드이며 LLM이 아니다(REQ-PROF-032, 결정 16).
- **모델 티어**: 델타 생성·consolidation = Sonnet 5(결정 5). 공유 시스템 프롬프트는 프롬프트 캐싱하여 ITPM 한도에서 제외(결정 5, 배치 부하 대비).
- **임베딩 비용**(v0.3.0 갱신): BaseStore semantic 인덱스는 Google `gemini-embedding-001` API(결정 6, 이슈 #31)를 호출하므로 토큰 비용이 발생한다 — fact 승격 시점에만 호출되어 빈도는 낮다(세션당 최대 수 건).
- **config 주입 기본값**: `summary.char_cap = 1000`, `summary.recency_window`·최근 맥락 개수, EMA α·승격 임계, `entropy.min_sessions`·엔트로피 급증 임계, 프로필 비활동 timeout(600초)·sweep(60초)·batch size(10)·concurrency(2)·claim lease(900초), 임베딩 차원(1536, v0.3.0 갱신)·embed 함수 — 전부 `core/config.py` 주입(하드코딩 금지, 결정 16/이슈 #79).

### 안전/일관성 불변식 (must-hold)

- reader는 LLM 0회·단일 get, 게스트·신규 회원 `None`(REQ-PROF-001/002/003, AC-PROF-01/02).
- 턴 중 프로필 write 금지, 델타 생성은 sleep-time 전용(REQ-PROF-023, AC-PROF-11).
- 최신성 충돌은 항상 코드 결정론적 recency-wins, LLM 최신성 판단 위임 금지(REQ-PROF-033, AC-PROF-12).
- fact 폐기 대신 supersede(이력 보존), 삭제 금지(REQ-PROF-034, AC-PROF-13).
- 요약 문자 상한은 생성 측 압축 재작성으로 집행, 소비 측 절단 아님(REQ-PROF-016, AC-PROF-07).
- 요약은 게이트 통과·미폐기 fact만 반영(REQ-PROF-015, AC-PROF-08 — 단 episodic 예외 §9 OPEN-P8).
- `decompose`·`rerank`에 동일 `profile_summary` 문자열 주입(REQ-PROF-014, AC-PROF-06).
- 델타 생성 정합성의 원천은 저장된 회원 세션 버퍼다. Spring 통지가 유실돼도 AI의 비활동 sweep이 회수한다. timeout과 명시적 종료의 경합은 공통 finalizer·고정키 claim으로 직렬화하며, idle은 재개 가능한 checkpoint이고 Spring I-20만 영구 멱등 완료한다(REQ-PROF-050~059, AC-PROF-17/28/30).
- 프로필 store는 카탈로그와 별도 데이터베이스 + 계정 분리, cross-DB 조인 금지 — MVP는 단일 인스턴스(결정 16-A)(REQ-PROF-072, AC-PROF-21).
- 어떤 실패에서도 프로필·요약·세션 버퍼·activity claim은 fail-safe 유지, 데이터 날조 금지(REQ-PROF-090~094, §7, AC-PROF-24/25/29).
