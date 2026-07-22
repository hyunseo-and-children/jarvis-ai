# pg-catalog 스키마 계약 정합 + 비대칭 임베딩 정합 — 설계

- 이슈: toss-delta-final/jarvis-ai #65
- 브랜치: `fix/catalog-schema-alignment`
- 날짜: 2026-07-23

## 목표

pg-catalog `products` 테이블을 AI 전달 페이로드(`sample_100/ai/schema.sql` + `documents.jsonl` 100건)와 정합시키되, **오늘 reader가 있는 것만** 담는다. 임베딩 프로비넌스(모델·차원·task·정규화)를 행마다 저장·복원해 "낡은 행(모델 교체 후)" 판별 근거를 확보하고, 런타임 임베딩을 문서/질의 **비대칭(RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY)** 으로 정합해 오프라인 골든셋과 짝을 맞춘다.

## 배경 · 탐색 결과

- 현재 `db/catalog/init/00_products.sql`에는 **`name` 컬럼이 이미 없다**(이슈 #65 핵심①은 사실상 완료 상태 — 재확인만).
- export팀이 만든 `sample_100/ai/schema.sql`이 사실상의 목표 계약. 우리 스키마는 두 가지를 **의도적으로 divergence**한다:
  - 테이블명 `products` 유지(계약은 `product_document`) — 코드 `FROM products` 충격 최소화.
  - `embedding` **NOT NULL 유지**(계약은 nullable).
- `documents.jsonl`은 `product_id, domain, category, extras, search_doc, embedding, embed_model, embed_dim, embed_task, normalized`를 싣는다.
- `embed_texts`(app/pipelines/embedding.py)는 **문서와 질의가 공유**한다. 현재 `task_type`을 전혀 보내지 않아 대칭 임베딩이다. 골든셋 문서는 오프라인에서 `RETRIEVAL_DOCUMENT`로 임베딩되어, 질의(default)와 짝이 어긋난다.
- MVP 기본 검색 백엔드는 `SpringSearchBackend`(임베딩 미사용). 임베딩은 방식2 rerank(`EmbeddingRerankBackend`)·방식1(`VectorSearchBackend`, 미착수)·eval(`compare.py`)에서 사용.

## 스코프에서 제외한 것 (근거)

reader가 없는 것은 넣지 않는다(YAGNI). 데이터는 `documents.jsonl`에 남아 있어 필요 시 컬럼 추가 + 멱등 재적재로 언제든 복구 가능하므로 영구 손실이 없다.

- **`domain` / `category` 컬럼 제외.** AI DB에서 domain/category를 읽거나 필터링하는 코드가 없다(`_SELECT_COLS`는 4컬럼만, 벡터검색은 embedding만, 카테고리 필터는 Spring 소관). domain의 유일한 잠재 소비처는 미착수 방식1의 전역 벡터검색 도메인 스코핑인데, 그마저 query→domain 분류기가 별도로 필요하다. 방식1 승격 시 BE 소스(I-17 페이로드 확장)로 end-to-end 설계한다.
- **extras GIN 인덱스 제외.** extras를 JSONB로 필터·존재검색하는 reader가 없다.

## 최종 결정 요약

| 항목 | 결정 |
|---|---|
| `name` | 이미 제거 — 재확인 |
| 테이블명 | `products` 유지 (계약과 의도적 divergence) |
| `embedding` | NOT NULL 유지 (계약과 의도적 divergence) |
| 프로비넌스 4종 | 추가 + 도메인 모델에 실어 읽기/쓰기 |
| `domain`/`category` | 제외 |
| extras GIN | 제외 |
| HNSW | 현행 `vector_cosine_ops` 유지 |
| task_type | 비대칭 정합 (문서=DOCUMENT / 질의=QUERY) |

### embedding을 NOT NULL로 두는 이유

products에 행이 들어오는 두 경로(로더 `scripts/load_sample_100.py`, 런타임 배치 `artifacts_batch.py`)가 **모두 임베딩을 먼저 만든 뒤 INSERT**하므로 NULL 행을 만드는 코드 경로가 없다(YAGNI). NOT NULL은 임베딩 누락 시 INSERT에서 즉시 실패(시끄러운 실패)하지만, NULL 허용이면 그 행이 벡터검색에서 조용히 누락된다. 단계형/비동기 재임베딩 파이프라인이 실제 로드맵에 오르면 그때 `NOT NULL` 제거 + CHECK를 `embedding IS NULL OR (...)` 형태로 바꾸는 한 줄 마이그레이션으로 전환한다.

## 상세 설계

### 1. 스키마 — `db/catalog/init/00_products.sql`

```sql
CREATE TABLE IF NOT EXISTS products (
    product_id  bigint PRIMARY KEY,
    search_doc  text NOT NULL,
    embedding   vector(1536) NOT NULL,
    extras      jsonb NOT NULL DEFAULT '{}',
    embed_model text,
    embed_dim   int,
    embed_task  text,
    normalized  boolean,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT embedding_meta_complete
        CHECK (embed_model IS NOT NULL AND embed_dim IS NOT NULL)
);
```

- HNSW 인덱스(`idx_products_embedding_hnsw`, `vector_cosine_ops`)·`batch_state` 테이블은 현행 유지.
- 상단 주석의 "상품명 저장 안 함" 자기모순 흔적 정리.

### 2. 설정 — `app/core/config.py`

```python
embedding_task_document: str = "RETRIEVAL_DOCUMENT"
embedding_task_query: str = "RETRIEVAL_QUERY"
embedding_normalized: bool = True
# embedding_model_id, embedding_dim 은 기존 유지
```

### 3. 비대칭 임베딩 — `app/pipelines/embedding.py` + 호출부

`embed_texts`에 `task_type` 파라미터 추가(하위호환 default `None` → 현행 동작 유지):

```python
def embed_texts(texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
    ...
    config=types.EmbedContentConfig(
        output_dimensionality=settings.embedding_dim,
        **({"task_type": task_type} if task_type else {}),
    )
```

호출부 정합:
- `app/pipelines/artifacts_batch.py`(문서 임베딩) → `settings.embedding_task_document`
- `app/services/search_service.py:98·138`(질의 임베딩) → `settings.embedding_task_query`
- `app/pipelines/compare.py:79`(eval 질의) → `settings.embedding_task_query`
- `app/agents/profile/store.py` → **손대지 않음**(default `None` 유지, 카탈로그 밖 별도 서브시스템)

### 4. 도메인 모델 + 스토어 — `artifact_store.py` / `pg_artifact_store.py`

프로비넌스는 유동값이므로 행마다 저장하고 **읽어들인다**(무효화 판별 근거).

- `CatalogArtifact`에 필드 추가(기존 생성 호환 위해 default `None`):
  `embed_model: str | None`, `embed_dim: int | None`, `embed_task: str | None`, `normalized: bool | None`.
- `pg_artifact_store.py`:
  - `_SELECT_COLS`에 4컬럼 추가, `_row_to_artifact` 언팩 확장.
  - `upsert`·`_replace_all` INSERT에 프로비넌스 컬럼 추가 — artifact가 실어온 값을 기록(상수 하드코딩 아님).
- 런타임(`artifacts_batch.py`)은 `CatalogArtifact` 생성 시 프로비넌스를 설정 상수로 채운다:
  `embed_model=settings.embedding_model_id`, `embed_dim=settings.embedding_dim`,
  `embed_task=settings.embedding_task_document`, `normalized=settings.embedding_normalized`.
- pg에서 읽으면 실제 저장값이 복원되어 현재 설정과 다를 수 있다(= 무효화 판별의 핵심).

### 5. 로더 — `scripts/load_sample_100.py`

이미 검증 중인 4개 필드를 INSERT에 추가(jsonl 값 그대로 영속):

```sql
INSERT INTO products (product_id, search_doc, embedding, extras,
                      embed_model, embed_dim, embed_task, normalized, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (product_id) DO UPDATE SET ...
```

### 6. 스키마 진화 — 마이그레이션 파일 (init과 병행)

`docker-entrypoint-initdb.d`는 빈 볼륨에서 1회만 실행되므로 기존 볼륨엔 자동 반영되지 않는다. **배포(deploy.yml)가 곧 도입되므로** `down -v`(볼륨 파기)는 프로덕션에서 못 쓰고, 스키마는 in-place `ALTER TABLE`로 진화시켜야 한다. 이슈 #76이 세운 마이그레이션 파일 관례(`ALTER TABLE IF EXISTS ... IF NOT EXISTS`, 기존 볼륨 반복 적용 안전)를 따른다.

- **신규**: `db/catalog/migrations/20260723_add_embedding_provenance.sql`
  ```sql
  BEGIN;
  ALTER TABLE IF EXISTS products
      ADD COLUMN IF NOT EXISTS embed_model text,
      ADD COLUMN IF NOT EXISTS embed_dim   int,
      ADD COLUMN IF NOT EXISTS embed_task  text,
      ADD COLUMN IF NOT EXISTS normalized  boolean;
  -- CHECK 제약: 존재 여부 확인 후 조건부 추가(제약명 중복 회피).
  DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'embedding_meta_complete') THEN
          ALTER TABLE products ADD CONSTRAINT embedding_meta_complete
              CHECK (embed_model IS NOT NULL AND embed_dim IS NOT NULL);
      END IF;
  END $$;
  COMMIT;
  ```
- `00_products.sql`은 새 볼륨용으로 함께 갱신(둘은 동일 최종 스키마를 만들어야 함 — 드리프트 주의).
- 기존 볼륨 적용: `psql "$catalog_db_url" -f db/catalog/migrations/20260723_add_embedding_provenance.sql`.
- README/로컬 통합 가이드에 절차 명시.

> **Future work**: 카탈로그 마이그레이션은 현재 수동 psql이다(자동 러너는 profile/state store 전용). 배포 파이프라인 도입 시 profile store의 "앱 연결 시 idempotent migration" 패턴을 카탈로그에도 도입해 배포가 자동 적용하도록 하는 것을 검토(별도 이슈).

## 테스트 (TDD)

- (integration) 스키마 CHECK: `embed_model` NULL로 INSERT 시 실패.
- (unit) `embed_texts(task_type=...)`가 `EmbedContentConfig`에 task_type을 전달하고, `task_type=None`이면 전달하지 않음(fake client로 config 캡처).
- (unit) 호출부별 올바른 task 전달: artifacts_batch=DOCUMENT, search_service/compare=QUERY.
- (unit/integration) 스토어 라운드트립: 프로비넌스를 쓰고 `get`/`all`로 복원.
- (unit) 로더가 프로비넌스 4종 영속 — 기존 `tests/unit/test_load_sample_100.py` 확장.
- `uv run ruff check` 통과, `uv run pytest` 통과.

## 완료 조건

- [ ] `00_products.sql` 개정(프로비넌스 4컬럼·CHECK, embedding NOT NULL, 테이블명/HNSW/batch_state 유지, 주석 정리)
- [ ] `config.py` 프로비넌스/task 상수 추가
- [ ] `embed_texts` task_type 파라미터 + 실제 전송
- [ ] 호출부 비대칭 정합(문서=DOCUMENT / 질의=QUERY / eval=QUERY, profile 무변경)
- [ ] `CatalogArtifact` + `pg_artifact_store` 프로비넌스 읽기/쓰기 정합
- [ ] `load_sample_100.py` INSERT 프로비넌스 컬럼 추가
- [ ] 마이그레이션 파일 `20260723_add_embedding_provenance.sql` 추가 + 절차 문서화
- [ ] `ruff check` / `pytest` 통과
- [ ] PR 본문에 `Closes #65`

## Future work (이번 스코프 밖)

- `domain` 필터 기반 벡터검색은 방식1(`VectorSearchBackend`) 승격 시 도입. domain의 정식 소스는 BE I-17 페이로드 확장(api-spec §4.8 `ProductChange`에 domain 추가) — 크로스팀 계약 협상 필요.
- 단계형/비동기 재임베딩 파이프라인 도입 시 `embedding` NULL 허용으로 전환.
