-- 대화 저장(감사·조회 전용) 테이블 (이슈 #33, api-spec §6.3 a).
--
-- ConversationStore(인메모리 placeholder)를 대체 — checkpointer 가 아니다(재개용 아니라
-- 감사·구조화 로그 상관관계 조회용, app/core/conversation.py PgConversationStore 참고).
--
-- docker-entrypoint-initdb.d 는 컨테이너가 "완전히 새로" 뜰 때(빈 볼륨) 1회만 실행한다.

CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id         text PRIMARY KEY,
    sequence_id     bigserial NOT NULL,
    conversation_id text NOT NULL,
    user_id         text,
    role            text NOT NULL,
    user_text       text NOT NULL,
    assistant_text  text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'PENDING',
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- turns_for(conversation_id) 조회 + 실제 INSERT 순서 정렬용.
CREATE INDEX IF NOT EXISTS idx_conversation_turns_sequence
    ON conversation_turns (conversation_id, sequence_id);
