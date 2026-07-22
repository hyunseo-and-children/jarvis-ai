-- I-17 상품 원본의 독립 사본을 pg-catalog에서 제거한다(이슈 #76, api-spec §4.8).
-- 기존 볼륨에 반복 적용해도 안전한 수동 migration이다.

BEGIN;

ALTER TABLE IF EXISTS products
    DROP COLUMN IF EXISTS name,
    DROP COLUMN IF EXISTS category;

COMMIT;
