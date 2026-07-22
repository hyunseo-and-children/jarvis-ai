# SMOKE-SELLER-41 — supervisor 디스패치 실 LLM 스모크 체크리스트

> **버전**: v1.0.0 · **기준일**: 2026-07-19 · **상태**: 유효 — 재실행 가능한 수동 절차
> 유닛 테스트(237종)는 스텁 기준이라 **실 Haiku 가 대표 발화를 제대로 분류하는지**는
> 이 수동 스모크로 확인한다. Spring 없이 실행 가능 — 도구 호출은 "Error:" degrade 로
> 수렴하며, 여기서 보는 것은 **라우팅 정확도 + SSE 스트림 계약**이다.

## 0. 사전 조건

- `.env` 에 `ANTHROPIC_API_KEY` 설정 (SERVICE_TOKEN 은 미설정 = dev 스킵, 운영 검증 시엔 설정 후 `X-Internal-Token` 헤더 추가).
- 서버 실행: `uv run uvicorn app.main:app --reload`
- 별도 터미널: `uv run python scripts/smoke_seller_chat.py --all`

## 1. 판정 기준 (발화별 기대 결과)

| 발화 | 기대 분기(서버 로그 "판매자 라우팅:") | 기대 SSE |
|---|---|---|
| "지난달 매출 어때?" | analysis | 진행 token(계획 수립→워커→보고서) → 보고서 token → done. Spring 없음 → degrade finding 기반 부분 보고서여도 **done 으로 정상 종료**해야 함 |
| "전환율이 왜 떨어졌는지 분석해줘" | analysis | 위와 동일 |
| "감귤청 가격 12,900원으로 바꿔줘" | product | 대상 확인 불가(I-9 Error) → **clarification 되묻기 token → done** 이 정상 (draft 이벤트는 Spring 연동 후) |
| "신상품 등록해줘" | product | 필수 정보 되묻기 token → done (또는 draft{op:create, productId:null}) |
| "장바구니 전환율이 뭐야?" | general | 용어 설명 token 스트림 → done |
| "안녕" | general | 인사 token → done |
| `{"action":"confirm","draftId":"d-1"}` | (로그 없음 — 입구① 선판정) | "준비 중" 안내 token → done, **LLM 0회** |
| "경쟁사 매출 알려줘" | (로그 없음 — 입구② scope) | 거절 token → done, **LLM 0회** |

## 2. 통과 조건

1. 8건 전부 스트림이 `done` 또는 `error` 로 닫힌다(행/무한 대기 없음).
2. 라우팅 로그가 기대 분기와 일치(6건). 오분류 시: SUPERVISOR_PROMPT 예시 보강 또는 `SELLER_ROUTE_CONFIDENCE_MIN` 조정 — 결과를 이 문서에 기록.
3. confirm·scope 2건은 라우팅 로그가 **찍히지 않아야** 함(LLM 0회 증거).
4. analysis 예외 2경우 매핑 확인(선택): `ANTHROPIC_API_KEY` 를 일부러 깨고 분석 발화 → 사과 token + `error(INTERNAL)`.
5. first-token 체감: 분석 발화에서 첫 진행 token 이 ~10s 내 도착(`seller_route_timeout_s` + planner 시작).

## 3. 실행 기록

| 일시 | 실행자 | 결과 | 비고 |
|---|---|---|---|
| | | | |
