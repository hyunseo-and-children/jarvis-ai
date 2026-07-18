"""애플리케이션 설정 (pydantic-settings).

프로젝트 원칙 "config 주입": 모든 튜너블(모델 ID, DB URL, 인증(JWKS/iss/aud),
Spring base URL, 검색 파라미터)을 환경변수로 주입하여 코드 변경 없이 교체 가능하게 유지한다.

[2026-07-15 확정] MVP 검색은 Spring 위임(POST /products/search)이며 벡터/카탈로그 미러/
enrichment/임베딩은 고도화(post-MVP)로 이동했다. 임베딩 필드는 고도화 대비 유지만 한다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

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


@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴. FastAPI 의존성/모듈에서 재사용한다."""
    return Settings()
