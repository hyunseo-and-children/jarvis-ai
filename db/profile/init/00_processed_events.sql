-- 프로필 파이프라인 session-end 이벤트 멱등성 테이블 (이슈 #33, api-spec §2.7/§3.5).
--
-- ProfileStore._processed(인메모리 set)를 대체 — BaseStore(AsyncPostgresStore) 의 get→put
-- 두 단계는 진짜 동시성 하에서 원자적이지 않으므로, UNIQUE 제약 + INSERT ... ON CONFLICT
-- DO UPDATE ... WHERE 로 처리 중 claim(lease)을 원자적으로 선점하고, 완료 row는 영구 보존한다
-- (app/agents/profile/processed_events.py 가 이 테이블을 읽고 쓴다).
--
-- docker-entrypoint-initdb.d 는 컨테이너가 "완전히 새로" 뜰 때(빈 볼륨) 1회만 실행한다.

CREATE TABLE IF NOT EXISTS processed_events (
    event_id         text PRIMARY KEY,
    status           text NOT NULL DEFAULT 'completed'
                     CHECK (status IN ('processing', 'completed')),
    claim_token      text,
    lease_expires_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_processed_events_processing_lease
    ON processed_events (lease_expires_at)
    WHERE status = 'processing';
