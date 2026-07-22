# pg-catalog 스키마 계약 정합 + 비대칭 임베딩 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pg-catalog `products` 테이블에 임베딩 프로비넌스(model·dim·task·normalized)를 저장·복원하고, 런타임 임베딩을 문서/질의 비대칭(RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY)으로 정합한다.

**Architecture:** `products` 스키마에 프로비넌스 4컬럼 + CHECK 추가(embedding은 NOT NULL 유지, 테이블명 `products`·HNSW 유지). `embed_texts`에 `task_type` 파라미터를 더하고, 프로덕션 기본 임베딩을 `functools.partial`로 바인딩해 문서 경로=DOCUMENT / 질의 경로=QUERY로 나눈다(주입형 콜러블·테스트 fake는 무변경). 프로비넌스는 `CatalogArtifact`에 실어 왕복시켜 "낡은 행" 판별 근거로 삼는다.

**Tech Stack:** Python 3.12, FastAPI, psycopg/psycopg_pool, pgvector, google-genai(gemini-embedding-001), pytest, ruff, Docker Compose(pg-catalog pg16).

## Global Constraints

- 계약 우선 — 스키마/필드 변경 시 CLAUDE.md·api-spec 원칙 준수. AI Postgres에는 AI 생성물만 저장(상품 원본 컬럼 사본 금지).
- 주석·docstring은 한국어, 코드 식별자는 영어.
- 튜너블 하드코딩 금지 — `app/core/config.py` 주입.
- 커밋 전 `uv run ruff check --fix && uv run ruff format` → `uv run pytest`(기본 실행은 `-m 'not smoke and not integration'`). 테스트 없이 커밋 금지.
- 커밋 메시지: Conventional Commits `<type>(<scope>): <subject>`, 본문에 "왜". co-author 트레일러 유지.
- `embedding` 컬럼은 **NOT NULL 유지**(계약 nullable과 의도적 divergence — 두 적재 경로가 항상 임베딩 선행 생성).
- `domain`/`category` 컬럼·extras GIN 인덱스는 **범위 밖**(reader 없음).
- 임베딩 프로비넌스 값(verbatim): `embed_model="gemini-embedding-001"`, `embed_dim=1536`, 문서 task=`"RETRIEVAL_DOCUMENT"`, 질의 task=`"RETRIEVAL_QUERY"`, `normalized=true`.

---

## File Structure

- `app/core/config.py` — 프로비넌스/task 상수 추가(Task 1).
- `app/pipelines/embedding.py` — `embed_texts(task_type=...)` (Task 2).
- `app/services/search_service.py` — 질의 임베딩 기본 바인딩=QUERY (Task 3).
- `app/pipelines/artifacts_batch.py` — 문서 임베딩 기본 바인딩=DOCUMENT (Task 3), 프로비넌스 기록(Task 6).
- `app/pipelines/artifact_store.py` — `CatalogArtifact` 프로비넌스 필드(Task 4).
- `app/pipelines/pg_artifact_store.py` — 프로비넌스 읽기/쓰기(Task 5).
- `db/catalog/init/00_products.sql` — 스키마 개정(Task 7).
- `db/catalog/migrations/20260723_add_embedding_provenance.sql` — 기존 볼륨 마이그레이션(Task 7, 신규).
- `scripts/load_sample_100.py` — 로더 INSERT 프로비넌스(Task 8).
- `docs/local-integration-guide.md`, `CHANGELOG.md` — 절차·릴리스 기록(Task 9).
- 테스트: `tests/unit/test_embedding.py`, `tests/unit/test_search_backends.py`, `tests/unit/test_artifacts_batch.py`, `tests/unit/test_artifact_store.py`, `tests/unit/test_load_sample_100.py`, `tests/integration/test_pg_artifact_store.py`.

---

### Task 1: config — 프로비넌스/task 상수 추가

**Files:**
- Modify: `app/core/config.py` (임베딩 설정 블록, 현재 `embedding_dim` 아래)
- Test: `tests/unit/test_config.py` (없으면 생성)

**Interfaces:**
- Produces: `Settings.embedding_task_document: str = "RETRIEVAL_DOCUMENT"`, `Settings.embedding_task_query: str = "RETRIEVAL_QUERY"`, `Settings.embedding_normalized: bool = True`. 기존 `embedding_model_id`, `embedding_dim` 유지.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_config.py`에 추가(파일 없으면 아래로 생성):
```python
from app.core.config import Settings


def test_embedding_provenance_defaults():
    s = Settings(_env_file=None)
    assert s.embedding_task_document == "RETRIEVAL_DOCUMENT"
    assert s.embedding_task_query == "RETRIEVAL_QUERY"
    assert s.embedding_normalized is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config.py::test_embedding_provenance_defaults -v`
Expected: FAIL (AttributeError / 속성 없음)

- [ ] **Step 3: Write minimal implementation**

`app/core/config.py`에서 `embedding_dim: int = 1536` 바로 아래에 추가:
```python
    embedding_task_document: str = "RETRIEVAL_DOCUMENT"  # 저장 문서 임베딩 task(비대칭 검색)
    embedding_task_query: str = "RETRIEVAL_QUERY"  # 질의 임베딩 task(문서와 달라야 함)
    embedding_normalized: bool = True  # MRL 절단 후 수동 L2 정규화 여부(embedding.py)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config.py::test_embedding_provenance_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format
git add app/core/config.py tests/unit/test_config.py
git commit -m "feat(config): #65 임베딩 프로비넌스·task 상수 추가"
```

---

### Task 2: embedding — `embed_texts`에 task_type 파라미터

**Files:**
- Modify: `app/pipelines/embedding.py:66-96` (`embed_texts`)
- Test: `tests/unit/test_embedding.py`

**Interfaces:**
- Consumes: (없음 — 순수 파라미터 추가)
- Produces: `embed_texts(texts: list[str], *, task_type: str | None = None) -> list[list[float]]`. `task_type`가 truthy면 `EmbedContentConfig`에 `task_type`을 전달, 아니면 현행대로 전달 안 함(하위호환).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_embedding.py`의 `_FakeModels`가 config를 캡처하도록 교체하고 테스트 2개 추가:
```python
class _CapturingModels:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.last_config = None

    def embed_content(self, *, model, contents, config):
        self.last_config = config
        return _FakeResponse(self._vectors)


class _CapturingClient:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.models = _CapturingModels(vectors)


def test_embed_texts_passes_task_type_when_given(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key", embedding_dim=3)
    monkeypatch.setattr(emb, "get_settings", lambda: settings)
    client = _CapturingClient([[3.0, 4.0, 0.0]])
    monkeypatch.setattr(emb, "_client", lambda api_key: client)

    emb.embed_texts(["q"], task_type="RETRIEVAL_QUERY")

    assert client.models.last_config.task_type == "RETRIEVAL_QUERY"


def test_embed_texts_omits_task_type_by_default(monkeypatch):
    settings = Settings(_env_file=None, google_api_key="test-key", embedding_dim=3)
    monkeypatch.setattr(emb, "get_settings", lambda: settings)
    client = _CapturingClient([[3.0, 4.0, 0.0]])
    monkeypatch.setattr(emb, "_client", lambda api_key: client)

    emb.embed_texts(["d"])

    assert getattr(client.models.last_config, "task_type", None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_embedding.py::test_embed_texts_passes_task_type_when_given -v`
Expected: FAIL (`embed_texts() got an unexpected keyword argument 'task_type'`)

- [ ] **Step 3: Write minimal implementation**

`app/pipelines/embedding.py`에서 시그니처와 config 조립을 교체:
```python
def embed_texts(texts: list[str], *, task_type: str | None = None) -> list[list[float]]:
    """텍스트 목록을 Google gemini-embedding-001 API 로 임베딩한다 (§4.8).

    config.embedding_dim 을 output_dimensionality 로 요청하고, 응답을 수동 L2 정규화한다.
    task_type 지정 시 비대칭 검색용으로 전달한다(문서=RETRIEVAL_DOCUMENT / 질의=RETRIEVAL_QUERY).
    google_api_key 미구성 시 곧바로 EmbeddingError — 배치·테스트는 embed 콜러블을 주입한다.
    """
    settings = get_settings()
    if not settings.google_api_key:
        raise EmbeddingError("embed_texts: google_api_key 미구성 — Google 임베딩 API 호출 불가")

    from google.genai import types  # noqa: PLC0415

    client = _client(settings.google_api_key)
    try:
        response = client.models.embed_content(
            model=settings.embedding_model_id,
            contents=list(texts),
            config=types.EmbedContentConfig(
                output_dimensionality=settings.embedding_dim,
                **({"task_type": task_type} if task_type else {}),
            ),
        )
        out = [_l2_normalize([float(x) for x in item.values]) for item in response.embeddings]
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001 - SDK 호출·응답 파싱 예외를 EmbeddingError 로 통일 매핑
        raise EmbeddingError(str(exc)) from exc

    for vec in out:
        if len(vec) != settings.embedding_dim:
            raise ValueError(f"임베딩 차원 불일치: {len(vec)} != {settings.embedding_dim}")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_embedding.py -v`
Expected: PASS (신규 2개 포함, 기존 정규화·미구성·차원검증 테스트도 통과)

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format
git add app/pipelines/embedding.py tests/unit/test_embedding.py
git commit -m "feat(embedding): #65 embed_texts task_type 파라미터(비대칭 검색)"
```

---

### Task 3: 호출부 비대칭 바인딩 (질의=QUERY / 문서=DOCUMENT)

**Files:**
- Modify: `app/services/search_service.py:20-24`(import), `:83`, `:120-129`(두 백엔드 `__init__` 기본 바인딩)
- Modify: `app/pipelines/artifacts_batch.py:16-21`(import), `:128`(기본 바인딩)
- Test: `tests/unit/test_search_backends.py`, `tests/unit/test_artifacts_batch.py`

**Interfaces:**
- Consumes: `embed_texts(task_type=...)` (Task 2), `Settings.embedding_task_query`·`embedding_task_document` (Task 1).
- Produces: 주입 없이 생성 시 `EmbeddingRerankBackend`·`VectorSearchBackend`의 기본 임베딩은 `partial(embed_texts, task_type=embedding_task_query)`; `run_artifacts_batch` 기본 임베딩은 `partial(embed_texts, task_type=embedding_task_document)`. **주입형 `embed`·테스트 fake의 시그니처는 무변경**(`(texts) -> vectors`).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_search_backends.py`에 추가:
```python
import functools

from app.pipelines import embedding as _embedding
from app.services.search_service import EmbeddingRerankBackend, VectorSearchBackend


def test_rerank_backend_default_embed_binds_query_task(monkeypatch):
    seen = {}

    def spy(texts, *, task_type=None):
        seen["task_type"] = task_type
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(_embedding, "embed_texts", spy)
    backend = EmbeddingRerankBackend()
    backend._embed(["질의"])
    assert seen["task_type"] == "RETRIEVAL_QUERY"


def test_vector_backend_default_embed_binds_query_task(monkeypatch):
    seen = {}

    def spy(texts, *, task_type=None):
        seen["task_type"] = task_type
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(_embedding, "embed_texts", spy)
    backend = VectorSearchBackend()
    backend._embed(["질의"])
    assert seen["task_type"] == "RETRIEVAL_QUERY"
```

`tests/unit/test_artifacts_batch.py`에 추가:
```python
import pytest

from app.pipelines import artifacts_batch as _batch
from app.pipelines import embedding as _embedding
from app.pipelines.artifact_store import CatalogArtifactStore
from tests.integration._stubs import ScriptedLLM  # 배치 enrichment LLM 대역


@pytest.mark.asyncio
async def test_batch_default_embed_binds_document_task(monkeypatch):
    seen = {}

    def spy(texts, *, task_type=None):
        seen["task_type"] = task_type
        return [[float(len(t)), 0.0, 1.0] for t in texts]

    monkeypatch.setattr(_embedding, "embed_texts", spy)

    async def fetch(cursor, size):
        from app.schemas.spring import ProductChange, ProductChangesPage

        return ProductChangesPage(
            changes=[
                ProductChange(
                    productId=1, status="ON_SALE", updatedAt="2026-07-20T00:00:00Z",
                    name="상품-1", description="설명", categoryName="여행용품", brandName="브랜드",
                )
            ],
            nextCursor=None, hasMore=False,
        )

    store = CatalogArtifactStore()
    await _batch.run_artifacts_batch(
        fetch=fetch, llm=ScriptedLLM(), store=store,
        settings=None, full_rebuild=False,
    )
    assert seen["task_type"] == "RETRIEVAL_DOCUMENT"
```
> 참고: `ProductChangesPage`/`ProductChange`의 정확한 필드·별칭은 `app/schemas/spring.py`를 확인해 맞춘다(camelCase 별칭 `productId`·`categoryName`·`brandName`). `ScriptedLLM`은 `tests/integration/_stubs.py` 재사용.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_search_backends.py::test_rerank_backend_default_embed_binds_query_task tests/unit/test_artifacts_batch.py::test_batch_default_embed_binds_document_task -v`
Expected: FAIL (기본 바인딩이 task_type을 안 넘김 → `seen["task_type"]`가 None)

- [ ] **Step 3: Write minimal implementation**

`app/services/search_service.py` — 파일 상단 import에 `import functools` 추가(`import asyncio` 옆). 두 백엔드 `__init__`의 기본 바인딩 교체:
```python
# EmbeddingRerankBackend.__init__ 내부
self._embed = embed or functools.partial(
    _embedding.embed_texts, task_type=get_settings().embedding_task_query
)
```
```python
# VectorSearchBackend.__init__ 내부 (기존 self._embed = embed or _embedding.embed_texts 교체)
self._embed = embed or functools.partial(
    _embedding.embed_texts, task_type=get_settings().embedding_task_query
)
```

`app/pipelines/artifacts_batch.py` — import에 `import functools` 추가. 기본 바인딩 교체(`:128`):
```python
    embed = embed or functools.partial(
        _embedding.embed_texts, task_type=settings.embedding_task_document
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_search_backends.py tests/unit/test_artifacts_batch.py -v`
Expected: PASS (신규 + 기존 백엔드/배치 테스트 모두 통과 — 주입형 fake는 무변경이라 영향 없음)

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format
git add app/services/search_service.py app/pipelines/artifacts_batch.py tests/unit/test_search_backends.py tests/unit/test_artifacts_batch.py
git commit -m "feat(search): #65 비대칭 임베딩 바인딩(질의=QUERY/문서=DOCUMENT)"
```

> **compare.py 주의(코드 변경 없음):** `compare_backends(embed=...)`는 `embed`를 필수 주입받는다(기본값 없음). eval의 정합성을 위해 **호출측이 `functools.partial(embed_texts, task_type=embedding_task_query)`를 주입**해야 한다. `compare.py` 자체는 수정하지 않는다. Task 9 문서에 이 규칙을 명시한다.

---

### Task 4: CatalogArtifact — 프로비넌스 필드

**Files:**
- Modify: `app/pipelines/artifact_store.py:18-24` (`CatalogArtifact`)
- Test: `tests/unit/test_artifact_store.py` (없으면 생성)

**Interfaces:**
- Produces: `CatalogArtifact(product_id, search_doc, embedding, extras={}, embed_model=None, embed_dim=None, embed_task=None, normalized=None)`. 새 4필드는 기본 `None`(기존 생성 호출·인메모리 store·테스트 fake 무변경).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_artifact_store.py`에 추가(파일 없으면 생성):
```python
from app.pipelines.artifact_store import CatalogArtifact


def test_artifact_provenance_defaults_none():
    a = CatalogArtifact(product_id=1, search_doc="d", embedding=[0.0])
    assert a.embed_model is None and a.embed_dim is None
    assert a.embed_task is None and a.normalized is None


def test_artifact_carries_provenance():
    a = CatalogArtifact(
        product_id=1, search_doc="d", embedding=[0.0],
        embed_model="gemini-embedding-001", embed_dim=1536,
        embed_task="RETRIEVAL_DOCUMENT", normalized=True,
    )
    assert a.embed_model == "gemini-embedding-001"
    assert a.embed_dim == 1536 and a.embed_task == "RETRIEVAL_DOCUMENT"
    assert a.normalized is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_artifact_store.py -v`
Expected: FAIL (`unexpected keyword argument 'embed_model'` / 속성 없음)

- [ ] **Step 3: Write minimal implementation**

`app/pipelines/artifact_store.py`의 `CatalogArtifact` 정의 교체:
```python
@dataclass
class CatalogArtifact:
    """상품 1건의 AI 생성물. 상품 원본 필드는 별도 컬럼으로 보관하지 않는다.

    임베딩 프로비넌스(embed_model·embed_dim·embed_task·normalized)는 벡터의 출처 메타로,
    모델 교체 후 낡은 행을 판별하는 근거다. 인메모리 생성 호환 위해 기본 None.
    """

    product_id: int
    search_doc: str
    embedding: list[float]
    extras: dict = field(default_factory=dict)
    embed_model: str | None = None
    embed_dim: int | None = None
    embed_task: str | None = None
    normalized: bool | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_artifact_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format
git add app/pipelines/artifact_store.py tests/unit/test_artifact_store.py
git commit -m "feat(catalog): #65 CatalogArtifact 임베딩 프로비넌스 필드"
```

---

### Task 5: PgCatalogArtifactStore — 프로비넌스 읽기/쓰기

**Files:**
- Modify: `app/pipelines/pg_artifact_store.py:33-41`(`_row_to_artifact`·`_SELECT_COLS`), `:57-73`(`upsert`), `:87-100`(`_replace_all`)
- Test: `tests/integration/test_pg_artifact_store.py` (integration 마커 — 실 pg 필요)

**Interfaces:**
- Consumes: `CatalogArtifact`의 프로비넌스 필드(Task 4).
- Produces: `_SELECT_COLS = "product_id, search_doc, embedding, extras, embed_model, embed_dim, embed_task, normalized"`; `_row_to_artifact`가 8-튜플 언팩; `upsert`·`_replace_all` INSERT가 프로비넌스 4컬럼을 artifact 값으로 기록.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_pg_artifact_store.py`에 추가(기존 fixture 패턴 재사용, `@pytest.mark.integration`):
```python
def test_provenance_roundtrip(store):  # 파일의 기존 `store` fixture(자동 clear/close) 재사용
    store.upsert(
        CatalogArtifact(
            product_id=42, search_doc="문서", embedding=_vec(1.0),
            embed_model="gemini-embedding-001", embed_dim=1536,
            embed_task="RETRIEVAL_DOCUMENT", normalized=True,
        )
    )
    got = store.get(42)
    assert got.embed_model == "gemini-embedding-001"
    assert got.embed_dim == 1536
    assert got.embed_task == "RETRIEVAL_DOCUMENT"
    assert got.normalized is True
```
> 파일 상단에 이미 `pytestmark = pytest.mark.integration`(모듈 전체), `store` fixture, `_vec()` 헬퍼, `CatalogArtifact` import가 있다 — 그대로 쓴다. 별도 `@pytest.mark.integration` 불필요.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose up -d pg-catalog` (한 번) → `uv run pytest -m integration tests/integration/test_pg_artifact_store.py::test_provenance_roundtrip -v`
Expected: FAIL (`get`이 프로비넌스를 복원 못 함 / `_row_to_artifact` 언팩 오류 또는 컬럼 없음)

- [ ] **Step 3: Write minimal implementation**

`app/pipelines/pg_artifact_store.py`:
```python
def _row_to_artifact(row: tuple) -> CatalogArtifact:
    product_id, search_doc, embedding, extras, embed_model, embed_dim, embed_task, normalized = row
    return CatalogArtifact(
        product_id=product_id,
        search_doc=search_doc,
        embedding=_to_list(embedding),
        extras=extras or {},
        embed_model=embed_model,
        embed_dim=embed_dim,
        embed_task=embed_task,
        normalized=normalized,
    )


_SELECT_COLS = (
    "product_id, search_doc, embedding, extras, "
    "embed_model, embed_dim, embed_task, normalized"
)
```
`upsert`의 INSERT 교체:
```python
            conn.execute(
                """
                INSERT INTO products
                    (product_id, search_doc, embedding, extras,
                     embed_model, embed_dim, embed_task, normalized, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (product_id) DO UPDATE SET
                    search_doc = EXCLUDED.search_doc,
                    embedding = EXCLUDED.embedding,
                    extras = EXCLUDED.extras,
                    embed_model = EXCLUDED.embed_model,
                    embed_dim = EXCLUDED.embed_dim,
                    embed_task = EXCLUDED.embed_task,
                    normalized = EXCLUDED.normalized,
                    updated_at = now()
                """,  # noqa: S608 - 컬럼 상수만 사용, 사용자 입력 없음
                (
                    artifact.product_id,
                    artifact.search_doc,
                    Vector(artifact.embedding),
                    Jsonb(artifact.extras),
                    artifact.embed_model,
                    artifact.embed_dim,
                    artifact.embed_task,
                    artifact.normalized,
                ),
            )
```
`_replace_all`의 INSERT 교체:
```python
            conn.execute(
                """
                INSERT INTO products
                    (product_id, search_doc, embedding, extras,
                     embed_model, embed_dim, embed_task, normalized, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    artifact.product_id,
                    artifact.search_doc,
                    Vector(artifact.embedding),
                    Jsonb(artifact.extras),
                    artifact.embed_model,
                    artifact.embed_dim,
                    artifact.embed_task,
                    artifact.normalized,
                ),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -m integration tests/integration/test_pg_artifact_store.py -v`
Expected: PASS (신규 round-trip + 기존 pg 스토어 테스트)
그리고 기본 실행 회귀 확인: `uv run pytest`
Expected: PASS (integration 제외되어도 import·구문 회귀 없음)

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format
git add app/pipelines/pg_artifact_store.py tests/integration/test_pg_artifact_store.py
git commit -m "feat(catalog): #65 pg 스토어 임베딩 프로비넌스 읽기/쓰기"
```

---

### Task 6: 런타임 배치가 프로비넌스 채움

**Files:**
- Modify: `app/pipelines/artifacts_batch.py:63-72` (`_process_change`의 `CatalogArtifact(...)` 생성)
- Test: `tests/unit/test_artifacts_batch.py`

**Interfaces:**
- Consumes: `Settings.embedding_model_id`·`embedding_dim`·`embedding_task_document`·`embedding_normalized`(Task 1), `CatalogArtifact` 프로비넌스 필드(Task 4).
- Produces: `_process_change`가 store에 upsert하는 `CatalogArtifact`에 프로비넌스가 설정 상수로 채워진다.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_artifacts_batch.py`에 추가(Task 3 테스트의 fetch·store 스캐폴딩 재사용):
```python
@pytest.mark.asyncio
async def test_batch_records_provenance_from_settings(monkeypatch):
    monkeypatch.setattr(
        _embedding, "embed_texts",
        lambda texts, *, task_type=None: [[1.0, 0.0, 0.0] for _ in texts],
    )

    async def fetch(cursor, size):
        from app.schemas.spring import ProductChange, ProductChangesPage

        return ProductChangesPage(
            changes=[
                ProductChange(
                    productId=7, status="ON_SALE", updatedAt="2026-07-20T00:00:00Z",
                    name="상품-7", description="설명", categoryName="여행용품", brandName="브랜드",
                )
            ],
            nextCursor=None, hasMore=False,
        )

    store = CatalogArtifactStore()
    await _batch.run_artifacts_batch(fetch=fetch, llm=ScriptedLLM(), store=store)

    art = store.get(7)
    assert art.embed_model == "gemini-embedding-001"
    assert art.embed_dim == 1536
    assert art.embed_task == "RETRIEVAL_DOCUMENT"
    assert art.normalized is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_artifacts_batch.py::test_batch_records_provenance_from_settings -v`
Expected: FAIL (art.embed_model 등이 None)

- [ ] **Step 3: Write minimal implementation**

`app/pipelines/artifacts_batch.py`의 `_process_change` 안 `store.upsert(...)` 교체:
```python
    store.upsert(
        CatalogArtifact(
            product_id=change.product_id,
            search_doc=doc,
            embedding=vec,
            extras=extras,
            embed_model=settings.embedding_model_id,
            embed_dim=settings.embedding_dim,
            embed_task=settings.embedding_task_document,
            normalized=settings.embedding_normalized,
        )
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_artifacts_batch.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format
git add app/pipelines/artifacts_batch.py tests/unit/test_artifacts_batch.py
git commit -m "feat(catalog): #65 런타임 배치가 임베딩 프로비넌스 기록"
```

---

### Task 7: 스키마 — 00_products.sql 개정 + 마이그레이션 파일

**Files:**
- Modify: `db/catalog/init/00_products.sql`
- Create: `db/catalog/migrations/20260723_add_embedding_provenance.sql`
- Test: `tests/integration/test_pg_artifact_store.py` (CHECK 제약 검증, integration)

**Interfaces:**
- Produces: `products`에 `embed_model text`·`embed_dim int`·`embed_task text`·`normalized boolean` + `CONSTRAINT embedding_meta_complete CHECK (embed_model IS NOT NULL AND embed_dim IS NOT NULL)`. `embedding` NOT NULL·HNSW·`batch_state` 유지.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_pg_artifact_store.py`에 CHECK 검증 추가:
```python
@pytest.mark.integration
def test_check_rejects_missing_provenance():
    import psycopg
    from app.core.config import get_settings

    with psycopg.connect(get_settings().catalog_db_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO products (product_id, search_doc, embedding) "
                    "VALUES (%s, %s, %s)",
                    (999999, "d", "[" + ",".join(["0"] * 1536) + "]"),
                )
```
> pgvector 텍스트 리터럴 형식(`[0,0,...]`)으로 embedding을 넣어 CHECK(프로비넌스 누락)만 위반하도록 한다.

- [ ] **Step 2: Run test to verify it fails**

빈 볼륨 재생성 후 실행:
```bash
docker compose down -v && docker compose up -d pg-catalog
```
Run: `uv run pytest -m integration tests/integration/test_pg_artifact_store.py::test_check_rejects_missing_provenance -v`
Expected: FAIL (아직 CHECK 없음 → INSERT가 성공해 `DID NOT RAISE`)

- [ ] **Step 3: Write minimal implementation**

`db/catalog/init/00_products.sql`의 `CREATE TABLE products` 블록 교체 + 상단 주석의 "상품명" 자기모순 문구 정리:
```sql
CREATE TABLE IF NOT EXISTS products (
    product_id  bigint PRIMARY KEY,             -- Spring 원본 productId(BIGINT, CLAUDE.md 정합)
    search_doc  text NOT NULL,                  -- enrichment 결과 조립 텍스트(임베딩 입력, §4.8)
    embedding   vector(1536) NOT NULL,          -- Google gemini-embedding-001, 수동 L2 정규화됨
    extras      jsonb NOT NULL DEFAULT '{}',    -- enrichment 산출물(tags·attributes)
    embed_model text,                           -- 임베딩 모델 id(프로비넌스 — 낡은 행 판별)
    embed_dim   int,                            -- 임베딩 차원
    embed_task  text,                           -- 저장 문서 task(항상 RETRIEVAL_DOCUMENT)
    normalized  boolean,                        -- MRL 절단 후 L2 재정규화 여부
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT embedding_meta_complete          -- 벡터가 있으면 출처도 반드시 기록
        CHECK (embed_model IS NOT NULL AND embed_dim IS NOT NULL)
);
```
(상단 주석에서 "상품명 등은 저장하지 않는다"는 유지하되, `name` 정의가 이미 없음을 확인. HNSW 인덱스·`batch_state`·`INSERT INTO batch_state` 블록은 그대로 둔다.)

`db/catalog/migrations/20260723_add_embedding_provenance.sql` 신규:
```sql
-- pg-catalog products 임베딩 프로비넌스 컬럼 추가(이슈 #65).
-- 기존 볼륨에 반복 적용해도 안전한 수동 migration. 배포 인스턴스는 이 파일로 in-place 적용한다.

BEGIN;

ALTER TABLE IF EXISTS products
    ADD COLUMN IF NOT EXISTS embed_model text,
    ADD COLUMN IF NOT EXISTS embed_dim   int,
    ADD COLUMN IF NOT EXISTS embed_task  text,
    ADD COLUMN IF NOT EXISTS normalized  boolean;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'embedding_meta_complete'
    ) THEN
        ALTER TABLE products ADD CONSTRAINT embedding_meta_complete
            CHECK (embed_model IS NOT NULL AND embed_dim IS NOT NULL);
    END IF;
END $$;

COMMIT;
```

- [ ] **Step 4: Run test to verify it passes**

빈 볼륨으로 새 init 적용:
```bash
docker compose down -v && docker compose up -d pg-catalog
```
Run: `uv run pytest -m integration tests/integration/test_pg_artifact_store.py -v`
Expected: PASS (CHECK 위반 발생 확인 + Task 5 round-trip)

마이그레이션 파일 검증(기존 볼륨 시나리오, 반복 적용 안전):
```bash
psql "postgresql://jarvis:jarvis@localhost:5433/catalog" -f db/catalog/migrations/20260723_add_embedding_provenance.sql
psql "postgresql://jarvis:jarvis@localhost:5433/catalog" -f db/catalog/migrations/20260723_add_embedding_provenance.sql
```
Expected: 두 번 모두 오류 없이 `COMMIT`(IF NOT EXISTS 멱등).

- [ ] **Step 5: Commit**

```bash
git add db/catalog/init/00_products.sql db/catalog/migrations/20260723_add_embedding_provenance.sql tests/integration/test_pg_artifact_store.py
git commit -m "feat(catalog): #65 products 프로비넌스 컬럼·CHECK + 마이그레이션"
```

---

### Task 8: 로더 — 프로비넌스 컬럼 INSERT

**Files:**
- Modify: `scripts/load_sample_100.py:71-89` (`upsert_documents`의 INSERT)
- Test: `tests/unit/test_load_sample_100.py`(dry-run 회귀), `tests/integration/test_pg_artifact_store.py` 또는 신규 integration(실 적재)

**Interfaces:**
- Consumes: jsonl의 `embed_model`·`embed_dim`·`embed_task`·`normalized`(이미 검증 중), Task 7 스키마.
- Produces: 로더 INSERT가 프로비넌스 4컬럼을 jsonl 값으로 upsert.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_pg_artifact_store.py`에 로더 적재 검증 추가(integration):
```python
@pytest.mark.integration
def test_loader_persists_provenance(tmp_path):
    import json

    from app.core.config import get_settings
    from scripts import load_sample_100

    doc = {
        "product_id": 123, "search_doc": "문서",
        "embedding": [0.0] * 1536, "extras": {"tags": ["여행"]},
        "embed_model": "gemini-embedding-001", "embed_dim": 1536,
        "embed_task": "RETRIEVAL_DOCUMENT", "normalized": True,
    }
    # L2 norm=1 검증 통과 위해 한 성분을 1.0으로
    doc["embedding"][0] = 1.0
    path = tmp_path / "documents.jsonl"
    path.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")

    documents = load_sample_100.load_documents(
        path, expected_count=1, expected_dim=1536, expected_model="gemini-embedding-001"
    )
    load_sample_100.upsert_documents(documents)

    store = PgCatalogArtifactStore(get_settings().catalog_db_url)
    got = store.get(123)
    assert got.embed_task == "RETRIEVAL_DOCUMENT" and got.normalized is True
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -m integration tests/integration/test_pg_artifact_store.py::test_loader_persists_provenance -v`
Expected: FAIL (로더 INSERT가 프로비넌스를 안 넣어 `get`이 None 반환 → embed_task None)

- [ ] **Step 3: Write minimal implementation**

`scripts/load_sample_100.py`의 `upsert_documents` INSERT 교체:
```python
                conn.execute(
                    """
                    INSERT INTO products
                        (product_id, search_doc, embedding, extras,
                         embed_model, embed_dim, embed_task, normalized, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (product_id) DO UPDATE SET
                        search_doc = EXCLUDED.search_doc,
                        embedding = EXCLUDED.embedding,
                        extras = EXCLUDED.extras,
                        embed_model = EXCLUDED.embed_model,
                        embed_dim = EXCLUDED.embed_dim,
                        embed_task = EXCLUDED.embed_task,
                        normalized = EXCLUDED.normalized,
                        updated_at = now()
                    """,
                    (
                        product_id,
                        row["search_doc"],
                        Vector(row["embedding"]),
                        Jsonb(row.get("extras") or {}),
                        row["embed_model"],
                        row["embed_dim"],
                        row["embed_task"],
                        row["normalized"],
                    ),
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -m integration tests/integration/test_pg_artifact_store.py::test_loader_persists_provenance -v`
Expected: PASS
회귀: `uv run pytest tests/unit/test_load_sample_100.py -v`
Expected: PASS (dry-run 검증 경로 무영향)

- [ ] **Step 5: Commit**

```bash
uv run ruff check --fix && uv run ruff format
git add scripts/load_sample_100.py tests/integration/test_pg_artifact_store.py
git commit -m "feat(catalog): #65 로더가 임베딩 프로비넌스 적재"
```

---

### Task 9: 문서화 — 절차 + CHANGELOG

**Files:**
- Modify: `docs/local-integration-guide.md` (스키마 진화·마이그레이션 절차, compare eval 주입 규칙)
- Modify: `CHANGELOG.md` (`[Unreleased]`)
- Test: 없음(문서)

**Interfaces:**
- Consumes: Task 1–8의 결과.

- [ ] **Step 1: local-integration-guide.md에 스키마 진화 절차 추가**

sample_100 적재 절 근처에 추가:
```markdown
### 스키마 진화(임베딩 프로비넌스, 이슈 #65)

`products`에 `embed_model·embed_dim·embed_task·normalized` 컬럼이 추가됐다.
- **새 볼륨**: `docker compose up -d pg-catalog`로 `00_products.sql`이 새 스키마를 생성 → 마이그레이션 불필요.
- **기존 볼륨/배포 인스턴스**: `down -v` 없이 in-place 적용
  `psql "$catalog_db_url" -f db/catalog/migrations/20260723_add_embedding_provenance.sql` (멱등, 반복 적용 안전).

오프라인 eval(`compare_backends`)에서 질의 임베딩은 `functools.partial(embed_texts, task_type=settings.embedding_task_query)`를 주입해 문서(RETRIEVAL_DOCUMENT)와 짝을 맞춘다.
```

- [ ] **Step 2: CHANGELOG.md `[Unreleased]`에 항목 추가**

```markdown
### Added
- pg-catalog `products` 임베딩 프로비넌스 컬럼(`embed_model·embed_dim·embed_task·normalized`) + `embedding_meta_complete` CHECK, 기존 볼륨용 마이그레이션(#65).
- `embed_texts(task_type=...)` 및 비대칭 임베딩 바인딩(질의=RETRIEVAL_QUERY / 문서=RETRIEVAL_DOCUMENT)(#65).

### Changed
- 런타임 I-17 배치·sample_100 로더가 임베딩 프로비넌스를 함께 적재(#65).
```

- [ ] **Step 3: 최종 회귀 검증**

Run: `uv run ruff check && uv run pytest`
Expected: PASS (기본 실행 — integration/smoke 제외)

- [ ] **Step 4: Commit**

```bash
git add docs/local-integration-guide.md CHANGELOG.md
git commit -m "docs(catalog): #65 스키마 진화 절차·CHANGELOG"
```

---

## Self-Review

**Spec coverage:**
- 스키마 프로비넌스 4컬럼 + CHECK, embedding NOT NULL, 테이블명/HNSW/batch_state 유지 → Task 7 ✓
- config 상수 → Task 1 ✓
- embed_texts task_type + 비대칭 바인딩(문서/질의) → Task 2·3 ✓ (compare는 주입 규칙 문서화 Task 9)
- CatalogArtifact + pg 스토어 프로비넌스 읽기/쓰기 → Task 4·5 ✓
- 런타임 배치 프로비넌스 기록 → Task 6 ✓
- 로더 프로비넌스 INSERT → Task 8 ✓
- 마이그레이션 파일 + 볼륨 절차 문서화 → Task 7·9 ✓
- domain/category·extras GIN 제외 → 전 Task에서 미도입(정합) ✓

**Placeholder scan:** 각 코드 스텝에 실제 코드 포함, "TBD/적절히 처리" 없음. `ProductChange`/fixture 필드는 실제 파일 확인 지시로 구체화.

**Type consistency:** `embed_texts(texts, *, task_type=None)`(Task 2)를 Task 3의 `functools.partial(embed_texts, task_type=...)`가 그대로 사용. `CatalogArtifact` 4필드명(Task 4)이 Task 5·6·8의 읽기/쓰기·`_SELECT_COLS` 순서와 일치. `_row_to_artifact` 8-튜플 순서 = `_SELECT_COLS` 순서 일치.
