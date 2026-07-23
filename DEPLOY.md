# 배포 가이드 — jarvis-ai

배포 담당이 이 repo만 보고 배포할 수 있도록 정리한 단일 문서다.
아키텍처·계약 원본은 [README.md](README.md) / [docs/api-spec.md](docs/api-spec.md), 환경변수 원본은 [.env.example](.env.example).

## 0. 이 repo는 무엇이고, 어디에 배포하나

- **FastAPI / Python 3.12** AI 에이전트 서버(자비스). 프론트엔드(`toss-delta-final/jarvis-frontend`)·Spring 백엔드(`toss-delta-final/jarvis-backend`)는 별도 repo.
- FE가 Spring 발급 JWT로 **AI 서버를 직접 호출**(SSE)한다. AI는 상품 원본을 복제하지 않고 질의 시점에 Spring 검색을 위임하며, AI 생성물(search_doc·임베딩·프로필)만 자체 PostgreSQL에 둔다.
- **배포 트리거**: 배포팀 CD가 **`main` 머지(=`dev → main` 승격) 기준**으로 동작한다(백엔드 [`deploy.yml`](https://github.com/toss-delta-final/jarvis-backend/blob/main/.github/workflows/deploy.yml)과 동일 패턴). 브랜치 전략은 [README §Git 워크플로](README.md) 참조.
- **런타임 의존**: 이 서버는 **PostgreSQL ×2(pgvector) + Spring API + LLM/임베딩 API**에 붙어야 동작한다(아래 §2·§4).

## 1. 빌드 & 실행 (컨테이너)

```bash
docker build -t jarvis-ai:dev .
docker run -p 8000:8000 --env-file deploy.env jarvis-ai:dev
```

- 멀티스테이지 `uv` 빌드(python:3.12-slim), **non-root**(`jarvis`), embedding 그룹 포함. dev 의존성 제외.
- 컨테이너는 **`0.0.0.0:8000`** 에서 uvicorn 기동(`app.main:app`). 포트는 `EXPOSE 8000`.
- DB ×2가 먼저 떠 있어야 앱이 정상 부팅한다(app 부팅 시 pgvector 확장 + 스토어 스키마 자동 setup). 로컬 전체 스택은 `docker compose up`(앱 + pg-catalog 5433 + pg-profile 5434).

## 2. 환경변수 (`deploy.env`)

전체 목록·용도는 [.env.example](.env.example)(모든 값이 pydantic-settings 필드와 1:1). 아래는 **배포 시 반드시 확인**할 항목.

**필수 (운영 = `AUTH_MODE=jwks` 기준):**

| 키 | 설명 |
|---|---|
| `CATALOG_DB_URL` / `PROFILE_DB_URL` | PostgreSQL ×2 접속(pgvector 필요 — §4). 배포 인프라 값으로. |
| `AUTH_MODE` | 운영은 **`jwks`**(Spring 공개키 RS256 검증). `dev`는 서명 검증 없이 디코드 — **로컬 전용, 운영 금지**. |
| `JWKS_URL` | Spring `GET /.well-known/jwks.json`(배포된 Spring 주소 기준). |
| `JWT_ISSUER` / `JWT_AUDIENCE` | 토큰 iss/aud 검증값(기본 `jarvis-spring-auth` / `jarvis-fastapi-ai`). |
| `INTERNAL_API_TOKEN` | AI↔Spring `X-Internal-Token`. **백엔드 `app.internal.token` 과 동일 값**(불일치 시 검색·담기·주문 등 `/internal` 양방향 차단). `jwks` 모드에서 미설정 시 **기동 실패**. |
| `SPRING_BASE_URL` | 역호출 대상 Spring API 주소(검색·장바구니·주문·카탈로그 배치). |
| `OPENAI_API_KEY` | 기본 provider(`LLM_PROVIDER=openai`)일 때 필수. |
| `GOOGLE_API_KEY` | 임베딩(gemini-embedding-001) — 카탈로그 배치·검색 임베딩에 필요. |
| `CORS_ORIGINS` | FE 직접 호출 오리진(JSON 배열 문자열). **FE 운영 오리진 포함 필수**(예: `["https://<FE도메인>"]`). |

**LLM provider 토글**: `LLM_PROVIDER=openai`(기본) → `OPENAI_API_KEY`. `anthropic` 으로 바꾸면 → `ANTHROPIC_API_KEY`. 모델 id(`OPENAI_*_MODEL_ID`·`HAIKU/SONNET_MODEL_ID`)는 각 대시보드 실값으로.

**선택 (기본값 있음):** 상태저장 타임아웃/풀(`STATE_STORE_*`), 배치 주기(`CATALOG_BATCH_INTERVAL_S`=300), 검색·추천 튜너블(`TOP_K`·`EXPOSE_*`·`LLM_CALL_LIMIT` 등), 프로필 튜너블(`PROFILE_*`), 스트림 티켓 scope(`JWT_SCOPE` — C-1 실값 확정 후 운영 주입 권장).

## 3. ⚠️ 시크릿 — repo에 실제 값은 없다

repo에는 **키 목록(`.env.example`)만** 있고 실제 시크릿은 없다(커밋 금지). 배포 환경용으로 준비:

- `INTERNAL_API_TOKEN`: **백엔드팀과 동일 값으로 합의**(백엔드 `DEPLOY.md §3`에서 `openssl rand -hex 32`로 생성한 그 값). 양쪽이 달라지면 `/internal` 콜백이 막힌다.
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`: 각 provider 대시보드 발급값.
- **JWT 서명키는 이 서버에 없다** — AI는 Spring의 JWKS 공개키로 **검증만** 한다(서명은 Spring). 그래서 별도 private key 주입 불필요.
- 시크릿은 repo 밖 안전 채널로만 공유. 배포 환경에선 GitHub Environment/Actions Secrets 등 시크릿 저장소 사용 권장.

## 4. DB 준비 (필수) — PostgreSQL ×2 (pgvector)

두 DB 모두 **pgvector 확장**이 필요하다(`pgvector/pgvector:pg16` 이미지 권장). `catalog`·`profile`로 분리:

- **catalog** — 상품 AI 생성물(`products`: search_doc·embedding vector(1536)·extras + HNSW), `order_seed`, `categories`.
- **profile** — 프로필/스레드 상태, `processed_events`(session-end 멱등 lifecycle) 등.

**A. 컨테이너로 띄우는 경우(권장 — compose와 동일):** `pgvector/pgvector:pg16` 두 개를 각각 띄우고 init 스크립트를 `/docker-entrypoint-initdb.d`로 마운트하면 **빈 볼륨 최초 부팅 시 자동 생성**된다.
- catalog init: [`db/catalog/init/`](db/catalog/init/) (`00_products.sql` → `01_order_seed.sql` → `02_categories.sql`)
- profile init: [`db/profile/init/`](db/profile/init/) (`00_processed_events.sql` → `01_conversation_turns.sql` → `02_profile_session_activity.sql`)

**B. 관리형 PostgreSQL(RDS 등)인 경우:** pgvector 확장 가용 확인 후 위 init SQL을 순서대로 수동 적용. **기존 볼륨 업그레이드**는 [`db/catalog/migrations/`](db/catalog/migrations/)의 마이그레이션도 적용:
```bash
psql "$CATALOG_DB_URL" -f db/catalog/init/00_products.sql   # 이후 01, 02
psql "$PROFILE_DB_URL" -f db/profile/init/00_processed_events.sql   # 이후 01, 02
# 기존 볼륨: db/catalog/migrations/*.sql 을 날짜순 적용
```

> 앱은 부팅 시 pgvector 확장 + LangGraph 스토어 스키마를 idempotent 하게 자체 `setup()` 하고, `processed_events`도 앱 연결 시 idempotent migration 한다. 위 init/migration은 **상품·프로필 도메인 테이블**을 준비하는 것.

## 5. 헬스체크

`GET /health` → `{"status":"ok"}`. ALB/오케스트레이터 헬스체크 타겟으로 사용.

## 6. 네트워킹 / CORS

- FE가 **AI 서버를 직접 호출(SSE)** 하므로, `CORS_ORIGINS`에 **FE 운영 오리진을 반드시 포함**해야 한다(백엔드의 동일 오리진 프록시 방식과 다름 — AI는 직접 호출 레인).
- AI→Spring 역호출(검색·장바구니·주문·카탈로그 배치)은 `SPRING_BASE_URL` + `X-Internal-Token`으로 나간다. 전 구간 타임아웃 3s.
- `/internal/**` 는 서비스 토큰으로 보호되지만, 가능하면 인그레스에서 외부 노출을 차단 권장.

## 7. 배포 담당 체크리스트

- [ ] `docker build -t jarvis-ai .`
- [ ] `deploy.env` 작성 — §2 필수값, `AUTH_MODE=jwks`, `INTERNAL_API_TOKEN`은 백엔드와 동일, `CORS_ORIGINS`에 FE 운영 오리진
- [ ] PostgreSQL ×2(pgvector) 준비 + init/migration 적용(§4)
- [ ] 컨테이너 실행(`-p 8000:8000`) 후 `GET /health` = `{"status":"ok"}` 확인
- [ ] Spring(`SPRING_BASE_URL`)·JWKS(`JWKS_URL`) 도달 확인 — 검색·인증 레인 정상
- [ ] FE팀에 **공개 AI API URL(SSE)** 공유
- [ ] (공개 노출 시) `/internal/**` 인그레스 차단
