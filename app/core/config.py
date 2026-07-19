"""애플리케이션 설정 (pydantic-settings).

프로젝트 원칙 "config 주입": 모든 튜너블(모델 ID, DB URL, 인증(JWKS/iss/aud),
Spring base URL, 검색 파라미터)을 환경변수로 주입하여 코드 변경 없이 교체 가능하게 유지한다.

[2026-07-15 확정] MVP 검색은 Spring 위임(POST /products/search)이며 벡터/카탈로그 미러/
enrichment/임베딩은 고도화(post-MVP)로 이동했다. 임베딩 필드는 고도화 대비 유지만 한다.
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

    # ── 셀프호스트 한국어 임베딩 (고도화, post-MVP) ──
    # MVP 검색은 Spring 위임이라 미사용. 고도화(pgvector 백엔드) 대비 설정만 유지.
    embedding_model_id: str = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
    embedding_dim: int = 1024

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

    # ── 이벤트 채널 서비스 토큰 (DEPRECATED) ──
    # /events/* 는 고도화(post-MVP)로 이동해 제거됨. 고도화 재도입 대비 필드만 남긴다.
    service_token: str | None = None

    # ── 검색/추천 튜너블 (SPEC-RECOMMEND-001) ──
    search_default_limit: int = 30
    top_k: int = 30
    expose_min: int = 5
    expose_max: int = 8
    llm_call_limit: int = 2
    relaxation_max_rounds: int = 3

    # ── 프로필 (SPEC-PROFILE-001, 내부 전용) ──
    profile_summary_max_chars: int = 1000
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
        return self


@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴. FastAPI 의존성/모듈에서 재사용한다."""
    return Settings()
