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
