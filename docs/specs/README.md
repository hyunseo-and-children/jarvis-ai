# 소유 SPEC (동기화 사본)

그래프 노드 내부 로직의 상세 규칙. **정본은 기획 저장소 `.moai/specs/`**, 여기는 hk-final 개발용 사본이다. 외부 계약(SSE·엔드포인트·오류 코드)은 [../api-spec.md](../api-spec.md)가 상위 소스 — SPEC과 어긋나면 api-spec 우선.

| SPEC | 버전 | 다루는 것 | 코드 |
|---|---|---|---|
| [SPEC-RECOMMEND-001](SPEC-RECOMMEND-001.md) | v0.8.0 | 추천 그래프 — Case 1/2/3 분기, decompose/rerank 규칙, 예산 검증, dedup(14-F) | `app/agents/buyer/` |
| [SPEC-PROFILE-001](SPEC-PROFILE-001.md) | v0.2.0 | 프로필 — 승격 게이트 3조건, sleep-time 병합, /profile/me | `app/agents/profile/` |
| [SPEC-CATALOG-DATA-001](SPEC-CATALOG-DATA-001.md) | v0.1.2 | AI 생성물 — enrichment→임베딩 (⚠️ 동기화 방식은 api-spec §4.8 pull 배치가 우선) | `app/pipelines/` |
| [SPEC-SELLER-001](SPEC-SELLER-001.md) | v1.0.0 | 판매자 그래프 — supervisor 라우팅, 분석 서브그래프(워커 5종·검증 루프), 전 쓰기 HITL, 분석 이력 (⚠️ **본 저장소 최초 작성 — 정본 승격 필요**) | `app/agents/seller/` |

> SPEC은 로직 상세용. 계약(무엇을 주고받나)은 api-spec, 결정 배경(왜)은 기획 저장소 `product.md`.

## 판매자 MVP 현재 상태 (SELLER-FINAL, 2026-07-20 기준)

SPEC-SELLER-001 이 "무엇을 만들기로 했나"라면, 아래 4종은 **MVP(1~4-3단계) 완료 시점에 실제로 무엇이 어떻게 동작하는가**의 정본이다. 새 세션·리뷰어는 RISKS 부터 읽는다.

| 문서 | 버전 | 다루는 것 |
|---|---|---|
| [SELLER-FINAL-WORKFLOW](SELLER-FINAL-WORKFLOW.md) | v1.0.0 | 요청 수명주기 — Spring 패스스루 배치, supervisor 분기, HITL 왕복 |
| [SELLER-FINAL-TECH](SELLER-FINAL-TECH.md) | v1.0.0 | 기술 선택과 이유 — 스택, 모델 배정(2-tier), 영속화 2 DB |
| [SELLER-FINAL-RISKS](SELLER-FINAL-RISKS.md) | v1.0.0 | 미정(🔴) BE 확정 대기 B1~B7 · 검증 공백 — **먼저 읽을 것** |
| [SELLER-FINAL-ROADMAP](SELLER-FINAL-ROADMAP.md) | v1.0.0 | post-MVP 확장 백로그 — 시맨틱 캐시(E1)·RAG(E2) |
| [SMOKE-SELLER-41](SMOKE-SELLER-41.md) | v1.0.0 | 실 LLM 라우팅·SSE 수동 스모크 절차(`scripts/smoke_seller_chat.py`) |

> 단계별 진행 기록(HANDOFF·REVIEW-STAGE·DESIGN·IMPL-PLAN·REALIGN)은 위 문서로 내용이 흡수되어 2026-07-22 에 삭제했다 — 필요하면 git 히스토리에서 복구한다.
