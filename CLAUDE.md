# jarvis-ai — 팀 개발 규칙

자비스(Jarvis) 에이전틱 커머스 AI 서버. Python 3.12 + FastAPI + LangGraph + PostgreSQL ×2 + Anthropic 2-tier(Haiku 4.5 / Sonnet 5). FE가 Spring 발급 JWT로 직접 호출한다.

## 계약 우선 (가장 중요)

- 계약 정본 `docs/api-spec.md`. 계약(엔드포인트·SSE 이벤트·필드·오류 코드)을 바꾸려면 **명세 개정이 먼저** — 코드에서 임의 변경 금지.
- 와이어 포맷은 **camelCase** — Pydantic `CamelModel`(by_alias) 규약 유지 (`app/schemas/`).
- 상품·옵션·장바구니·주문 id는 **숫자(BIGINT)** — DB 스키마 기준(product/product_option/cart_item/order). 회원·판매자 id도 숫자(JWT `sub`). **게스트 id는 UUID 문자열**(guest.id CHAR(36)). SSE는 상품 카드/id를 싣지 않는다(경로 B).
- **신원은 절대 요청 본문에서 받지 않는다** — AI가 검증한 JWT `sub`에서 도출 (IDOR 방지).
- 인증 레인: **AI→Spring internal 호출은 전부 `X-Internal-Token` 서비스 토큰**(검색·이력·장바구니 담기 I-2·조회 I-18·집계·CRUD·목록 push) — 본문/쿼리 신원은 AI가 JWT `sub`에서 도출(BE 실측 정합). SSE는 스트림 단명 티켓(RS256/JWKS). AI→Spring 타임아웃은 전 구간 3s.
- SSE 이벤트: 구매자 `token/conditions/action/suggestions/budget/products.ready/done/error`, 판매자 `token/draft/done/error`. 상품 카드는 SSE에 싣지 않는다(경로 B).

## 명령어

- 의존성: `uv sync` (임베딩 그룹: `uv sync --group embedding`)
- DB: `docker compose up -d pg-catalog pg-profile` (catalog 5433 / profile 5434)
- 실행: `uv run uvicorn app.main:app --reload`
- 테스트: `uv run pytest` / 린트: `uv run ruff check`
- Git hook 설치(1회): `uv run pre-commit install` — 커밋 시 ruff + 커밋 메시지 형식 자동 검사(`.pre-commit-config.yaml`).
- 자동 정리: `uv run ruff check --fix && uv run ruff format` — 린트 자동 수정 + 포맷.
- 스킬: **`/implement-topic [주제]`** — MVP 주제 하나를 계약 우선으로 구현(계약 읽기→TDD→ruff/pytest→커밋). `.claude/skills/`.
- 문서 참고: **context7 MCP** 연결됨(`.mcp.json`) — LangGraph·FastAPI 등 라이브러리 API는 추측 말고 context7로 최신 문서 조회.

## 컨벤션

- 스텁은 `NotImplementedError` + api-spec § 참조 주석 유지 — § 번호가 코드↔명세 링크다. 로직 상세는 `docs/specs/`.
- 튜너블 하드코딩 금지 — `app/core/config.py` 주입.
- 주석·docstring은 한국어, 코드 식별자(함수/변수/클래스)는 영어.
- `order_seed`는 폐기 예정 — 신규 코드에서 참조 금지 (`GET /internal/members/{id}/orders`(I-19)로 대체).
- AI Postgres에는 **AI 생성물(extras·search_doc·임베딩)만** 저장 — 상품 원본 컬럼 사본 금지.

## Git

- 브랜치: **`main`(배포·보호) + `dev`(통합·보호) + `<type>/<topic>` topic**. `main` push = 배포팀 CD(EC2 자동배포)라 `main`은 실서버 라인. 동시 기능은 각자 **`dev`에서 딴** 별도 `feat/`. 장수 브랜치 금지.
- 작업은 **이슈 단위** — 기능/버그를 이슈로 등록 후 브랜치·PR 연결(`Closes #N`). mvp-todo 주제와 이슈를 맞춘다.
- 커밋: Conventional Commits `<type>(<scope>): <subject>`. 계약 변경은 명세 개정 커밋을 먼저/함께.
- PR: **일상 개발은 `dev` 대상** + 최소 1인 리뷰 + `uv run pytest`·`ruff` 통과. **배포는 `dev → main` 승격 PR** 로만. `main` 직접 push·직접 PR(승격 외) 금지. 상세는 README.
- worktree 분기 기준도 `dev` — `git checkout dev && git pull` 후 분기(배포분·미완성 코드 딸림 방지).

**커밋 워크플로 (기능 구현 후)**:
1. `git diff` 로 변경 전체를 검토 — 의도치 않은 변경·디버그 코드·시크릿(.env)·잔여 스텁이 없는지 확인.
2. **`uv run ruff check --fix && uv run ruff format`** 로 린트 자동 정리 (pre-commit hook가 되막지 않도록 미리).
3. `uv run pytest` 통과 확인 (테스트 없이 커밋 금지).
4. **diff 내용을 근거로** Conventional Commit 메시지 생성 — `<type>(<scope>): <subject>` + 본문(왜). 추측이 아니라 실제 변경에서 뽑는다.
5. 관련 파일만 스테이징 → 커밋. 무관한 변경은 **별도 커밋**으로 분리(한 커밋 = 한 논리 단위).
6. 주제 완료 시 CHANGELOG.md 갱신, 계약 변경 시 정본 `docs/api-spec.md` 개정을 같은/선행 커밋에.
- Claude Code 보조 커밋은 co-author 트레일러를 남긴다.

## 실수 방지 (Lessons) — 하네스

- 작업 시작 전 [`docs/lessons.md`](docs/lessons.md)를 훑는다 — 과거에 밟은 실수를 다시 밟지 않기 위해.
- **오류/실수를 진단했으면 즉시 `docs/lessons.md`에 기록**(증상·원인·규칙, 최신을 맨 위에). 고치고 끝내지 말고 재발 방지 규칙까지 남긴다.
- 파일 쓰기는 **절대경로 + `cd <경로> && pwd`로 cwd 확인** 후 실행 (경로 착오로 다른 repo를 덮어쓴 전례 있음 — lessons 참조).

## 변경 기록 (Changelog) — 하네스

- **기능/주제가 완료(PR 병합)될 때마다** [`CHANGELOG.md`](CHANGELOG.md) `[Unreleased]`에 항목 추가 (Added/Changed/Fixed/Removed/Docs/Security).
- 계약(api-spec) 변경을 수반하면 `(api-spec §, vX.Y)`를 함께 적는다.
- "무엇을 왜" 한 줄로 — 커밋 메시지와 중복되어도 CHANGELOG는 사람이 훑는 릴리스 관점 요약이다.

## 금지

- `.env`·시크릿 파일 읽기/커밋 금지.
- 테스트 실행 없이 "완료" 보고 금지 — `uv run pytest` 결과를 근거로 제시할 것.
- 계약 관련 스키마/엔드포인트를 명세 개정 없이 수정 금지.
