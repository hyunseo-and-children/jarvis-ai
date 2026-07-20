# FastAPI AI 서버 로컬 통합 가이드

> 기준: 2026-07-21 현재 워크트리. AI 서버는 자연어를 검색 조건과 행동으로 바꾸고, Spring의 최신 커머스 데이터를 이용해 SSE 응답을 생성한다.

구매자·판매자 분기별 event sequence는 [FastAPI SSE 응답 경우의 수](%28AI%29sse-response-catalog.md)에 별도로 정리했다.

## 1. AI 서버가 맡는 역할

- FE가 Spring에서 받은 RS256 stream ticket을 검증한다.
- 사용자 발화를 추천, 장바구니, 장바구니 조회, 일반 대화 intent로 분해한다.
- 상품 후보, 최근 구매, 장바구니, 판매자 데이터는 Spring `/internal/**`에서 조회한다.
- 추천 product ID를 Spring에 push한 뒤 FE에는 `products.ready`만 보낸다.
- 회원 대화와 session-end 이벤트를 바탕으로 프로필을 갱신한다.
- AI 생성물과 임베딩은 pg-catalog, 프로필/체크포인트는 pg-profile에 저장한다.

```text
FE :3000 ── POST /chat 또는 /seller/chat + stream ticket ──> AI :8000
AI :8000 ── /internal/** + X-Internal-Token ───────────────> Spring :8080
Spring :8080 ── /events/session-end + X-Internal-Token ───> AI :8000
AI ── pg-catalog :5433 / pg-profile :5434 / LLM provider
```

## 2. 이번에 맞춘 핵심 충돌

### 2.1 Spring stream ticket 계약으로 인증 기본값 수정

Spring 발급값과 AI 검증값이 달라 모든 서명 검증이 401이 될 수 있었던 부분을 통일했다.

| 항목 | 현재 값 |
|---|---|
| issuer | `jarvis-spring-auth` |
| audience | `jarvis-fastapi-ai` |
| scope | `chat:stream` |
| JWKS | `http://localhost:8080/.well-known/jwks.json` |

CORS에는 FE 현재 포트 3000과 구 Vite 포트 5173을 모두 허용한다.

관련 파일: `.env.example`, `app/core/config.py`, `tests/unit/_jwks.py`, `tests/unit/test_config_integration_defaults.py`.

### 2.2 AI ↔ Spring 서비스 token 이름 통일

AI의 단일 설정 키는 `INTERNAL_API_TOKEN`이다.

- AI → Spring: 모든 `/internal/**` 요청에 `X-Internal-Token`
- Spring → AI: `/events/session-end` 요청의 `X-Internal-Token` 검증

Spring `application-local.yml`의 `app.internal.token`과 값이 다르면 검색/push/cart는 401이 되고 추천이 실패하거나 `products.ready`가 나오지 않는다.

### 2.3 Spring 상품 검색 응답 envelope 호환

기존 AI parser는 다음 구형 형태만 기대했다.

```json
{"success":true,"data":{"items":[...]}}
```

현재 Spring의 `ApiResponse<List<...>>`는 다음 형태다.

```json
{"success":true,"data":[...]}
```

`_parse_search_response`가 현재 배열 형태와 구형 `items` 형태를 둘 다 수용하도록 수정했다. 이 충돌이 남아 있으면 Spring이 상품을 반환해도 AI는 후보 0건으로 해석한다.

관련 파일: `app/services/spring_client.py`, `tests/integration/_stubs.py`, `tests/unit/test_recommendation.py`.

### 2.4 Spring 최소 검색 응답에서 price를 optional로 변경

Spring I-1 검색은 필터·리랭킹용 최소 필드만 반환할 수 있고 화면 표시 가격은 CH-5 추천 목록 조회에서 다시 가져온다. 따라서 `SpringProduct.price`를 optional로 바꿔, 검색 응답에 표시 가격이 빠져도 전체 추천이 스키마 오류로 무너지지 않게 했다.

가격 범위와 재고/상태 필터의 권위는 Spring SQL이며 최종 화면 가격의 권위도 Spring 카드 API다.

### 2.5 sample_100 사전 임베딩 적재 도구 추가

`scripts/load_sample_100.py`는 `../sample_100/ai/documents.jsonl`을 검증하고 pg-catalog `products` 테이블에 멱등 upsert한다.

검증 항목:

- 문서 100건, product ID 중복 없음
- `products/*.json`과 ID 집합 일치
- `gemini-embedding-001`
- 1536차원
- `RETRIEVAL_DOCUMENT`
- `normalized=true`, L2 norm 약 1

이미 임베딩된 벡터를 넣으므로 이 작업 자체는 Google API를 다시 호출하지 않는다.

> ⚠️ **`sample_100` 번들은 이 repo에 포함되지 않는다.** 로더 기본 경로는 워크스페이스 형제 경로 `../sample_100`(`ai/documents.jsonl`·`products/*.json`)다. 번들을 그 위치에 두거나 `--documents`/`--products-dir` 로 명시 경로를 넘겨야 하며, 없으면 로더가 명확한 에러로 종료한다.

```bash
uv run python scripts/load_sample_100.py --dry-run
uv run python scripts/load_sample_100.py
```

### 2.6 테스트가 로컬 실키를 사용하지 않도록 격리

로컬 `.env`에 API key가 있어도 기본 unit/integration 테스트가 과금 API를 호출하지 않도록 `tests/conftest.py`에서 provider key를 비우고 `AUTH_MODE=dev`를 강제한다. 실제 외부 API smoke는 marker로 분리한다.

## 3. 실제 추천 요청 흐름

1. FE가 Spring에서 발급받은 ticket으로 `POST /chat`
2. AI가 ticket에서 `sub`, `sub_type`, 판매자라면 `brandId`를 도출
3. fast LLM이 intent와 `ProductSearchFilters` 생성
4. 기본 `SpringSearchBackend`가 `GET /internal/products/search` 호출
5. AI가 최근 구매 제외와 평점 하한을 사후 필터링
6. smart LLM이 후보 순서와 추천 이유 생성
7. `POST /internal/recommendations`로 최종 product ID push
8. push 성공 시에만 `products.ready` emit
9. FE가 Spring에서 카드 조회

주요 SSE 이벤트:

```text
conditions → token... → products.ready → done
```

검색 실패는 `error`, rerank 실패는 Spring 검색 순서 fallback, 추천 목록 push 실패는 `products.ready` 없이 안내 token과 `done`으로 종료한다.

## 4. 로컬 환경변수

### 최소 채팅 확인 모드

사전 적재 sample과 Spring SQL keyword/category 검색만 사용할 때:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=<secret>
SPRING_BASE_URL=http://localhost:8080
INTERNAL_API_TOKEN=<Spring app.internal.token과 동일한 값>
AUTH_MODE=dev
CORS_ORIGINS=["http://localhost:3000","http://localhost:5173"]
CATALOG_DB_URL=postgresql://jarvis:jarvis@localhost:5433/catalog
PROFILE_DB_URL=postgresql://jarvis:jarvis@localhost:5434/profile
EMBEDDING_MODEL_ID=gemini-embedding-001
EMBEDDING_DIM=1536
```

이 모드에서는 `OPENAI_API_KEY`가 채팅 LLM에 필요하다. `GOOGLE_API_KEY`는 사전 임베딩 적재에는 필요 없으며, 비어 있으면 증분 embedding scheduler만 비활성화된다.

### RS256/JWKS까지 확인하는 통합 모드

```dotenv
AUTH_MODE=jwks
JWKS_URL=http://localhost:8080/.well-known/jwks.json
JWT_ISSUER=jarvis-spring-auth
JWT_AUDIENCE=jarvis-fastapi-ai
JWT_SCOPE=chat:stream
PII_HASH_PEPPER=<local-secret>
INTERNAL_API_TOKEN=<Spring과 동일한 값>
GOOGLE_API_KEY=<secret>
```

현재 config는 `AUTH_MODE=jwks`에서 `PII_HASH_PEPPER`, `INTERNAL_API_TOKEN`, `JWKS_URL`, `GOOGLE_API_KEY`가 없으면 fail-fast한다. 따라서 **키 두 개만 넣으면 되는 것은 아니다**. 다만 미리 생성된 sample 100 벡터만 적재하는 명령은 Google API key 없이 실행할 수 있다.

## 5. 로컬 실행

```bash
cd jarvis-ai
uv sync
docker compose up -d pg-catalog pg-profile
cp .env.example .env   # 최초 1회, 이후 위 값 수정
# sample_100 번들을 ../sample_100 에 배치(또는 아래 명령에 --documents/--products-dir 지정) — repo 미포함
uv run python scripts/load_sample_100.py --dry-run
uv run python scripts/load_sample_100.py
uv run uvicorn app.main:app --reload --port 8000
```

확인:

```bash
curl http://localhost:8000/health
```

## 6. 최근 대화가 어색해질 수 있는 현재 제한

- **기본 검색은 아직 `SpringSearchBackend`** 다. Spring의 name/summary/attributes 부분 문자열 검색과 정형 필터를 사용하며, pg-catalog에 100개 embedding을 넣어도 라이브 요청은 자동으로 vector 검색을 쓰지 않는다.
- `EmbeddingRerankBackend`와 `VectorSearchBackend` 구현은 있으나 기본 backend로 선택되지 않았다. Vector 방식은 Spring ID hydrate 계약도 추가로 필요하다.
- buyer 멀티턴은 전체 대화 transcript가 아니라 누적 filter, 직전 추천, 옵션 되물음 상태 중심이며 현재 process memory placeholder다. 서버 재시작 시 사라진다.
- `conditions`는 확정 filter를 보여주지만 추천 결과가 0건이면 사용자에게 자연스럽게 조건을 완화하는 품질은 LLM 응답에 의존한다.
- `부츠컷 청바지` 같은 표현은 Spring의 category suffix/수식어 제거로 보완했지만, 모든 한국어 동의어를 해결하는 검색은 아니다.

이 때문에 “최근 대화가 약간 삐리하다”는 현상은 서버 연결 실패라기보다 **대화 메모리 범위와 기본 lexical/정형 검색의 한계**일 가능성이 높다. 진단할 때는 `products.ready` 존재 여부, Spring I-1 결과 건수, decompose filter를 함께 본다.

## 7. 유지보수 계약과 검증

### AI 팀이 이후 변경에서 지킬 계약

- 사용자/판매자 신원은 요청 body가 아니라 검증된 ticket claim에서만 가져온다.
- Spring `ApiResponse`의 `data` 모양이 list/object/page 중 무엇인지 contract test로 고정한다.
- 추천 ID push가 성공한 뒤에만 `products.ready`를 emit하며 상품 카드는 SSE에 싣지 않는다.
- Spring I-1 최소 후보 필드와 CH-5 표시 필드를 구분해 optional 필드를 과도하게 필수화하지 않는다.
- embedding 적재, embedding scheduler 활성화, live vector 검색 활성화는 서로 다른 상태로 취급한다.

### 실행 검증

```bash
uv run pytest
uv run ruff check
```

현재 통합 수정 기준 결과는 `577 passed, 11 deselected`, Ruff 통과다.
