-- AI 생성물 카탈로그 (이슈 #31, api-spec §4.8 v0.15.14 — MVP 편입 확정 2026-07-20).
--
-- [변경] 2026-07-15 확정 당시엔 "고도화(post-MVP)로 이동"이었으나, §4.8 I-17 배치가
-- MVP 로 편입되고 임베딩 모델도 Google gemini-embedding-001(dim 1536)로 확정되며 되살아났다.
-- app/pipelines/pg_artifact_store.py(PgCatalogArtifactStore)가 이 테이블을 읽고 쓴다.
--
-- 상품 원본 컬럼(가격·재고·상품명 등)은 저장하지 않는다 — Spring 이 원본을 소유하고,
-- 여기는 AI 생성물(search_doc·embedding·extras)만 둔다(CLAUDE.md 원칙).
--
-- docker-entrypoint-initdb.d 는 컨테이너가 "완전히 새로" 뜰 때(빈 볼륨) 1회만 실행한다 —
-- 이미 만들어진 컨테이너의 볼륨에는 자동 반영되지 않는다(볼륨 재생성 또는 수동 psql 필요).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS products (
    product_id  bigint PRIMARY KEY,             -- Spring 원본 productId(BIGINT, CLAUDE.md 정합)
    search_doc  text NOT NULL,                  -- enrichment 결과 조립 텍스트(임베딩 입력, §4.8)
    embedding   vector(1536) NOT NULL,           -- Google gemini-embedding-001, 수동 L2 정규화됨
    extras      jsonb NOT NULL DEFAULT '{}',     -- enrichment 산출물(tags·attributes)
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- 방식1(VectorSearchBackend) 코사인 유사도 벡터검색 인덱스.
CREATE INDEX IF NOT EXISTS idx_products_embedding_hnsw
    ON products USING hnsw (embedding vector_cosine_ops);

-- I-17 배치 커서 영속화 (기존 인메모리 CatalogArtifactStore._cursor 대체, §4.8 자연 복구).
CREATE TABLE IF NOT EXISTS batch_state (
    id     smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- 단일 행(카탈로그 배치 1종)
    cursor text
);

INSERT INTO batch_state (id, cursor) VALUES (1, NULL)
    ON CONFLICT (id) DO NOTHING;
