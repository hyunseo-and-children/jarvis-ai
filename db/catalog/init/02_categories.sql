-- 카테고리 사전 (이슈 #59 — 발화→categoryName 매핑, 임베딩 top-k + LLM 택일 하이브리드).
--
-- 발화를 카테고리로 매핑할 때: 질의 임베딩으로 이 테이블의 embedding 과 코사인 top-k 후보를
-- 뽑고(방식1 VectorSearchBackend 재사용), 그 소수 후보만 LLM 에 주고 최종 택일한다.
-- 확정된 canonical category(원본 "top > mid" leaf)만 Spring I-1 categoryName 으로 전달한다.
--
-- 원본 leaf 문자열은 AI 생성물이 아니라 매핑 기준 사전이다 — Spring 카탈로그의 실제
-- 카테고리 트리에서 파생한 값으로, products.embedding 과 같은 gemini-embedding-001(dim 1536,
-- 수동 L2 정규화) 파이프라인을 재사용해 embedding 을 채운다.
--
-- 시드(행 생성)와 임베딩 구축은 2단계로 분리한다 — 먼저 category 행을 넣고(embedding NULL),
-- 임베딩 배치가 embedding·embedding_model 을 채운다.
--
-- docker-entrypoint-initdb.d 는 컨테이너가 "완전히 새로" 뜰 때(빈 볼륨) 1회만 실행한다 —
-- 이미 만들어진 컨테이너의 볼륨에는 자동 반영되지 않는다(볼륨 재생성 또는 수동 psql 필요).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS categories (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- 내부 대리키(BIGINT, CLAUDE.md 정합)
    category         text NOT NULL UNIQUE,          -- 원본 "top > mid" leaf(사전 자연키, 재시드 멱등)
    embedding        vector(1536),                   -- gemini-embedding-001, 수동 L2 정규화(2단계 배치서 채움, NULL 허용)
    embedding_model  text,                           -- 임베딩 provenance — 모델/차원 변경 시 재생성 판단 근거
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- 임베딩 top-k 코사인 유사도 검색 인덱스(방식1 VectorSearchBackend, products 와 동일 관례).
CREATE INDEX IF NOT EXISTS idx_categories_embedding_hnsw
    ON categories USING hnsw (embedding vector_cosine_ops);
