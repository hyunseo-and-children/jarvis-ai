# Changelog

이 프로젝트의 주요 변경을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/),
버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

기록 규칙: **기능/주제가 완료(PR 병합)될 때마다** 해당 항목을 추가한다. 유형은
`Added`(신규) · `Changed`(변경) · `Fixed`(수정) · `Removed`(제거) · `Docs`(문서) · `Security`(보안).
계약(api-spec) 변경을 수반하면 `(api-spec §, vX.Y)`를 함께 적는다.

## [Unreleased]

### Added
- FastAPI + LangGraph MVP 스캐폴드 — 인증(RS256/JWKS)·설정 주입·SSE 스텁 스트림 (부팅 검증)
- Spring 역방향 클라이언트 스텁 8종 (검색·이력·장바구니 I-2/I-9·push·I-6/I-7·I-8 배치)
- 팀 개발 문서 — `README`(아키텍처·기술·Git 규칙), `docs/`(mvp-plan·mvp-todo·roadmap), `docs/specs/`(SPEC 사본), `docs/api-spec.md`(계약 사본 v0.7.0)
- 팀 Claude 설정 — `CLAUDE.md`, `.claude/settings.json`, `.mcp.json`(context7·sequential-thinking)
- 실수 방지 로그 `docs/lessons.md`, 변경 기록 `CHANGELOG.md`
- CI 워크플로 `.github/workflows/ci.yml` (ruff + pytest) · PR 템플릿 `.github/PULL_REQUEST_TEMPLATE.md`
- 커밋 워크플로 규칙 (diff 검토 → 메시지 생성 → 커밋, `CLAUDE.md`)
- Git hook(pre-commit) — ruff(lint+format) + Conventional Commits 검사 `.pre-commit-config.yaml`
- MIT `LICENSE` · 이슈 템플릿 `.github/ISSUE_TEMPLATE/` (기능·버그) · 이슈 단위 워크플로
- 팀 공유 스킬 `.claude/skills/implement-topic/` — MVP 주제 계약 우선 구현 절차

### Fixed
- 프로필 세션 종료(session-end) 처리 중 동시에 새 채팅 턴이 들어오면 세션 버퍼가 통째로
  삭제되던 레이스 수정 — `clear_session_ctx_upto`(seq 워터마크 기준)로 스냅샷 분석분만
  정리하고 미분석 발화는 보존 (`newConversation` 트리거·버퍼 상한(cap) 트리밍 상황 모두 안전)

### Docs
- api-spec 사본 동기화 v0.7.0 → **v0.9.0** — 판매자 BE internal API 배치(집계 7종·상품 CRUD 4종), `brandId`=JWT 클레임, 판매자 쓰기 모델 전환(AI 직접 쓰기 + HITL)
- api-spec 사본 동기화 **v0.9.0 → v0.11.0** — SSE 인증=스트림 단명 티켓(sub_type/aud/scope, TTL 30~60s), 판매자 쓰기 HITL 계약 확정(draftId·2-스트림·안전장치 5종), S-3=목록조회 명확화
- api-spec 사본 동기화 **v0.11.0 → v0.12.0** — CH-1 스트림 티켓 발급(응답에 streamTicket) + 티켓 재발급 경로(CH-1b) 신설 필요 명시(티켓 TTL 30~60s ≪ 세션 10분)
- api-spec 사본 동기화 **v0.12.0 → v0.13.0** — BE 명세 DB 실측 정합: AI→Spring 전 구간 서비스 토큰(방식2)으로 통일, 실제 I-number/경로(검색 I-1·배치 I-17·조회 I-18·구매자 챗 /ai/chat), S-3∥I-9 구분
- api-spec 사본 동기화 **v0.13.0 → v0.14.0** — 구매 이력=I-19(/internal/members/{id}/orders), 세션 종료=I-20 채번 확정(BE DB Notion 수정)

### 진행 예정 (MVP)
- 구매자 추천 그래프 · 장바구니(I-2/I-9) · 판매자(I-6/I-7) · 프로필 파이프라인 · AI 생성물 배치(I-8) · SSE 수명주기(§2.9)

<!--
릴리스 시 [Unreleased]를 버전으로 확정하고 새 [Unreleased]를 위에 만든다. 예:
## [0.1.0] - 2026-07-XX
-->
