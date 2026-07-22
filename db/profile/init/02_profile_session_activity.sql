-- 회원 프로필 세션 inactivity 판정용 activity 테이블 (이슈 #79).
--
-- user message INSERT와 같은 transaction에서 last_activity_at=DB now()를 touch한다.
-- 스케줄러는 (status, last_activity_at) 인덱스로 bounded claim하고, crash 잔재는
-- lease_expires_at 만료 뒤 재선점한다. COMPLETED sessionId는 재활성화하지 않는다.

CREATE TABLE IF NOT EXISTS profile_session_activity (
    user_id          bigint NOT NULL,
    session_id       text NOT NULL,
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    status           text NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'processing', 'completed')),
    claim_token      text,
    lease_expires_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_profile_session_activity_due
    ON profile_session_activity (status, last_activity_at, user_id, session_id);
