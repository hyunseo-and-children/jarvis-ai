"""애플리케이션 설정 (pydantic-settings).

프로젝트 원칙 "config 주입": 모든 튜너블(모델 ID, DB URL, 인증(JWKS/iss/aud),
Spring base URL, 검색 파라미터)을 환경변수로 주입하여 코드 변경 없이 교체 가능하게 유지한다.

[2026-07-15] MVP 후보 검색은 Spring 위임(GET /internal/products/search, I-1)이며 상품 원본
컬럼의 AI측 사본(카탈로그 미러)은 두지 않는다.
[2026-07-20 정정] enrichment·임베딩(§4.8 I-17 배치)은 MVP 편입 확정 — 임베딩 검색 방식1·2를
SearchBackend로 구현해 골든셋 확정(api-spec §4.8 말미·§4.6, C-17). 구 "post-MVP" 표기 폐기.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 기반 전역 설정. 접두사 없이 대문자 필드명과 매핑된다."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Anthropic 2-tier LLM ──
    anthropic_api_key: str = ""
    haiku_model_id: str = "claude-haiku-4-5"
    sonnet_model_id: str = "claude-sonnet-5"

    # ── 셀프호스트 한국어 임베딩 (MVP, §4.8 배치 + 임베딩 검색) ──
    # [2026-07-20 MVP 편입] 방식1(pgvector 벡터검색)·방식2(재정렬) 백엔드가 사용. torch 는 --group embedding.
    embedding_model_id: str = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
    embedding_dim: int = 1024
    catalog_batch_page_size: int = 500  # I-17 배치 페이지 크기(§4.8, config 주입)
    catalog_vector_overfetch: int = 4  # 방식1 hydrate 후 필터·품절 제거 대비 벡터 여유조회 배수

    # ── PostgreSQL / pgvector ×2 ──
    # catalog: AI 생성물(extras/search_doc/임베딩, §4.8 I-17 배치 upsert) 호스트, profile: 프로필 스토어+대화 저장(§6.3).
    catalog_db_url: str = "postgresql://jarvis:jarvis@localhost:5433/catalog"
    profile_db_url: str = "postgresql://jarvis:jarvis@localhost:5434/profile"

    # ── Spring 연동 (역방향 호출, api-spec §4) ──
    spring_base_url: str = "http://localhost:8080"
    # AI→Spring internal 역호출 서비스 토큰 (X-Internal-Token, api-spec §2.3 v0.13.0).
    # 빈 값은 개발용 — Spring 미가동 시 검색/‑push 는 어차피 SpringUnavailable 로 degrade.
    internal_api_token: str = ""

    # ── CORS (FE 직접 호출, api-spec §2.7 / C-11) ──
    cors_origins: list[str] = ["http://localhost:3000"]

    # ── 인증 (api-spec §2.2, RS256 + JWKS 확정 2026-07-15) ──
    # dev : 서명 검증 없이 디코드 (헤더 없으면 게스트) — 로컬 개발 전용
    # jwks: Spring GET /.well-known/jwks.json 공개키로 RS256 검증 (kid→키, exp/iss/aud 확인)
    auth_mode: Literal["dev", "jwks"] = "dev"
    jwks_url: str | None = None
    jwt_issuer: str | None = "shopping-spring-auth"
    jwt_audience: str | None = "shopping-fastapi-ai"
    # 스트림 티켓 scope 검증값 (§2.3 v0.10.0 확정 검증 항목 — 제안값 chat:stream,
    # 실값은 C-1 확정 시 env 교체). None 이면 scope 검증 생략(전환기 호환).
    jwt_scope: str | None = "chat:stream"
    # JWKS tier-1 캐시 TTL(s) — 만료 전에는 kid miss 시에만 refetch(§2.3), 요청마다 왕복 금지.
    jwks_cache_ttl_s: float = 300.0

    # [통일 2026-07-20 rebase 합류] 서비스 토큰은 팀 규약 `internal_api_token` 단일 키.
    # 인바운드(§3.5 verify_service_token — 프로필 write I-20)와 아웃바운드
    # (spring_client — AI→Spring)가 같은 X-Internal-Token 값을 공유한다(아키텍처 07/17).
    # 구 seller 전용 키 service_token(인바운드)·internal_token(아웃바운드)은 폐기.
    # spring_timeout_s 도 팀 정의(아래 공통 블록)를 재사용한다 — 중복 정의 금지.

    # ── 판매자 분석 임계값 (app/agents/seller/calc.py 주입, 하드코딩 금지) ──
    seller_ma_window: int = 7  # 매출 이동평균 window(일)
    seller_anomaly_deviation_pct: float = 30.0  # 매출 이상판정 편차 임계(%)
    seller_conversion_drop_pct: float = 20.0  # 전환율 하락 이상 임계(%)
    seller_churn_inactive_days: int = 30  # 이탈 코호트 무활동 일수(I-16 inactiveDays 기본)
    seller_recent_days_default: int = 7  # normalize_period "최근 N일" 기본 N
    # safe_eval `**` 결과 자릿수 상한(DoS 방어) — 초과 식은 ValueError 로 거부(리뷰 반영).
    seller_calc_max_result_digits: int = 100
    # 도구 반환 상세도 상한(안 1+차등, 2026-07-17 사용자 확정) — 컨텍스트 폭주 방지.
    seller_summary_max_points: int = 60  # 시계열 상세 나열 상한(포인트 수)
    seller_summary_max_events: int = 5  # I-13/I-14 이벤트 kv 나열 상한(건)
    seller_list_default_limit: int = 20  # I-9 상품 목록 기본 limit(미지정 시)

    # ── 판매자 후속 단계 대비 선등록 (1단계 미소비, 하드코딩 재발 방지) ──
    seller_report_score_threshold: int = 21  # 보고서 검증 통과 점수(21/30)
    seller_report_max_retries: int = 3  # 검증 루프 상한
    seller_draft_ttl_minutes: int = 10  # HITL 미승인 draft 만료
    # 4-2 HITL 실행(hitl.py): confirm 시점 I-9 재조회(stale 검증)의 페이지 순회 상한 —
    # I-9 에 productId 필터가 없어 목록을 넘겨가며 찾는다(페이지 크기 = seller_list_default_limit).
    seller_draft_lookup_max_pages: int = 10
    # PostgresSaver(pg-profile) 초기 연결 대기 상한 — 초과 시 dev 는 InMemory 폴백.
    seller_checkpoint_connect_timeout_s: float = 5.0
    seller_history_recent_n: int = 5  # planner 최근 분석 이력 주입 건수
    # 4-3 분석 이력(history.py): 판매자당 보관 상한(초과분 오래된 것부터 폐기)과
    # 이력에 남길 보고서 요약 길이(전문 보존은 4-4 캐시 소관 — SPEC §9.1 "report 요약").
    seller_history_max_items: int = 20
    seller_history_report_max_chars: int = 500
    seller_tool_call_limit: int = 8  # ToolCallLimit 전역 한도(선택)
    seller_worker_timeout_s: float = 60.0  # 분석 워커 1종 실행 상한(3-3 팬아웃, §7 90s 목표 내)

    # ── 판매자 supervisor 라우팅 (4-1a, REALIGN §4 — 2026-07-19 확정) ──
    # confidence 미달 = analysis 보수 라우팅(SPEC 장치 ⑤). 장애 = general 폴백(사용자 결정).
    seller_route_confidence_min: float = 0.6  # 이 값 미만이면 analysis 로 보수 재지정
    seller_route_timeout_s: float = 10.0  # 라우팅 LLM 상한 — first-token 10s 목표 내(§2.9)

    # ── 판매자 모델 배정 (SPEC-SELLER-001 §8 — Anthropic 2-tier temperature) ──
    # 모델 ID 는 위 haiku_model_id/sonnet_model_id 가 단일 출처(중복 필드 금지).
    seller_haiku_temperature: float = 0.0  # supervisor·planner·워커 5종·judge (일관성 장치 ①)
    seller_sonnet_temperature: float = 0.2  # report·recommend (서술 품질)

    # ── 검색/추천 튜너블 (SPEC-RECOMMEND-001) ──
    search_default_limit: int = 30
    top_k: int = 30
    expose_min: int = 5
    expose_max: int = 8
    llm_call_limit: int = 2
    relaxation_max_rounds: int = 3

    # ── 장바구니 (이슈 #3, api-spec §4.1) ──
    # CART_OPTION_INVALID 재질문 상한 — 초과 시 action CART_ERROR(§4.1). 하드코딩 금지.
    cart_option_reask_max: int = 1

    # ── dedup (#4, api-spec §4.7 결정 14-F) ──
    # 최근 구매 제외 윈도우(일) — 이보다 오래된 구매는 제외 목록에서 뺀다(영구 제외 방지).
    dedup_recent_days: int = 90
    # 소모품 카테고리(결정 14-F 억제 대상) — MVP config 소스. 정본은 catalog 속성사전
    # (SPEC-CATALOG-DATA-001 REQ-CAT-013 소모품 boolean 플래그). 카테고리명은 BE categoryName 과 일치.
    consumable_categories: list[str] = []

    # ── 프로필 (SPEC-PROFILE-001) ──
    profile_recency_highlights: int = 3   # §5.1 최근 맥락 하이라이트 개수
    profile_gate_threshold: float = 0.5   # §6.3 승격 게이트 임계(salience·repetition EMA)
    profile_fact_char_cap: int = 200      # "기억해" hot-path fact 길이 상한(오탐·남용 방어)
    profile_max_facts: int = 200          # 사용자별 fact 개수 상한(무제한 누적 방어) — 최신 우선 유지
    profile_session_buffer_cap: int = 100 # 세션 transient 버퍼 발화 개수 상한(무제한 누적 방어)

    profile_summary_max_chars: int = 1000  # §5.1 요약 상한(생성 측 압축 재작성)
    # PII 로그 지문 pepper (§6.3 b) — 운영(jwks)은 실제 secret 주입 필수(아래 검증). 빈 값은 개발용.
    pii_hash_pepper: str = ""
    # 사용자 message 길이 상한 (api-spec §3.1 · PII·메모리 방어). 튜너블.
    chat_message_max_chars: int = 4000
    # sessionId/threadId 길이 상한 — 불투명 키가 registry·저장소·로그에 쌓이는 남용 방어.
    chat_key_max_chars: int = 200

    # ── SSE 스트림 수명주기 (api-spec §2.9, 값은 config 기본값·운영 조정 가능) ──
    # first-token: 첫 이벤트까지 상한. 초과 시 스트림 시작 전이면 504, 후면 in-stream error.
    stream_first_token_timeout_s: float = 10.0
    # 스트림 전체 상한. 초과 시 done(finishReason "stop")으로 정상 절단.
    stream_total_timeout_s: float = 90.0
    # disconnect 감지 폴링 간격 (취소 = 연결 종료, §2.9 b).
    stream_disconnect_poll_s: float = 0.5
    # AI→Spring 콜백 타임아웃 (§2.9 c, BE I-2 기준 통일). 실제 호출부에서 사용.
    spring_timeout_s: float = 3.0
    # AI→LLM 단일 호출 타임아웃 + 재시도 횟수 (§2.9 c).
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 1

    # ── 레이트 리밋 (api-spec §2.8, 토큰 sub 스코프, 인메모리·단일 인스턴스 전제) ──
    rate_limit_per_min: int = 10
    rate_limit_per_hour: int = 100
    # IP 백스톱 배수 — 토큰 sub 스코프를 회전 우회해도 클라이언트 IP 상한으로 남용 차단.
    # NAT 뒤 다수 정상 사용자 오탐을 줄이려 sub 상한보다 관대하게 둔다.
    rate_limit_host_multiplier: int = 5
    # 신뢰 리버스 프록시 뒤 배포 시 True — 클라이언트 IP 를 X-Forwarded-For 에서 읽는다.
    # append 형 프록시($proxy_add_x_forwarded_for)는 자사 프록시가 관측한 IP 를 **최우측**에
    # 붙이므로, 우측에서 신뢰 홉 수만큼 센 위치를 클라이언트 IP 로 쓴다(최좌측은 위조 가능).
    trust_forwarded_for: bool = False
    # 신뢰하는 프록시 홉 수(우측부터). 자사 프록시 1대면 1 = 최우측 값.
    forwarded_for_trusted_hops: int = 1


    @model_validator(mode="after")
    def _require_pepper_in_prod(self) -> "Settings":
        """운영(jwks)에서 PII pepper 미주입이면 기동 실패 — 조용히 약한 해시로 도는 것 방지."""
        if self.auth_mode == "jwks" and not self.pii_hash_pepper:
            raise ValueError("PII_HASH_PEPPER must be set when auth_mode=jwks")
        # inbound write 엔드포인트(§3.5) 서비스 토큰 — 운영은 필수(미설정 시 조용히 fail-open 방지).
        if self.auth_mode == "jwks" and not self.internal_api_token:
            raise ValueError("INTERNAL_API_TOKEN must be set when auth_mode=jwks")
        # jwks 모드의 검증 키 소스 — 미설정이면 전 요청 401 폭주라 기동 시점에 fail-fast(#34).
        if self.auth_mode == "jwks" and not self.jwks_url:
            raise ValueError("JWKS_URL must be set when auth_mode=jwks")
        return self


@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴. FastAPI 의존성/모듈에서 재사용한다."""
    return Settings()
