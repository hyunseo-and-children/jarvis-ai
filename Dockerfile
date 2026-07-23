# 멀티스테이지 uv 빌드 (결정 13). 임베딩(google-genai·pgvector)은 main deps라 uv sync 로 설치
# — 셀프호스트 torch·`--group embedding` 폐기(api-spec §4.8 v0.15.14).

# ── builder ──
FROM python:3.12-slim AS builder

# uv 바이너리를 공식 이미지에서 복사한다.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 잠금 파일 기준 재현 가능 설치 — dev 제외(임베딩 의존성은 main deps).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 프로젝트 소스 복사 후 프로젝트 자체 설치.
# README.md 는 pyproject `readme` 필드라 wheel 빌드(hatchling) 시 필요.
COPY README.md ./
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ── final ──
FROM python:3.12-slim AS final

# 비루트 사용자.
RUN groupadd --system jarvis && useradd --system --gid jarvis --create-home jarvis

WORKDIR /app

# 가상환경과 소스만 반입.
COPY --from=builder --chown=jarvis:jarvis /app/.venv /app/.venv
COPY --from=builder --chown=jarvis:jarvis /app/app /app/app

ENV PATH="/app/.venv/bin:$PATH"

USER jarvis

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
