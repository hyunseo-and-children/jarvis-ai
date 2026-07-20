# FastAPI SSE 응답 경우의 수

> 기준: 2026-07-21 runtime. FE 렌더링까지 포함한 상세 표는 FE 저장소의 `(FE)ai-sse-response-cases.md`와 같은 시점의 계약을 기준으로 한다.

## 1. 전송 형식

모든 stream 내부 event는 다음 한 줄짜리 SSE data frame이다.

```text
data: {"type":"<event>","data":{...}}

```

구매자 `/chat` 활성 event:

```text
token / conditions / suggestions / action / products.ready / done / error
```

판매자 `/seller/chat` 활성 event:

```text
token / draft / done / error
```

## 2. 활성 event payload

| event | payload | 발생 조건 |
|---|---|---|
| `token` | `{text: string}` | 답변, 추천 코멘트, 되묻기, 진행 문구 |
| `conditions` | `{chips: [{field,label,value}]}` | 구매자 추천 검색 직전 |
| `suggestions` | `{chips: [{label,revert?,relaxation?,estCount}]}` | 현재는 최근 구매 소모품 category 되돌리기 |
| `action` | `{type,message,cartItemId?,reason?}` | 구매자 장바구니 담기 성공/실패 |
| `products.ready` | `{sessionId,listId}` | Spring 추천 목록 push 성공 뒤 |
| `draft` | `{draftId,op,productId,changes,summary}` | 판매자 상품 변경 초안 저장 성공 |
| `done` | `{finishReason: stop|zero_result}` | 정상/degrade 종료 |
| `error` | `{code,message}` | stream 내부 치명 오류 종료 |

`error.code`는 `LLM_TIMEOUT | LLM_UNAVAILABLE | SEARCH_FAILED | INTERNAL`이다. `action.reason`의 현재 runtime 값은 `PRODUCT_NOT_FOUND | CART_ERROR`다.

## 3. 구매자 sequence

| 상황 | sequence |
|---|---|
| 추천 성공 | `conditions → token* → suggestions? → products.ready → done(stop)` |
| 추천 0건 | `conditions → token → suggestions? → done(zero_result)` |
| 최근 구매 API 실패 | 정상 추천으로 degrade |
| rerank 실패 | 검색 순서 fallback 후 정상 추천 sequence |
| 목록 push 실패 | `conditions → token* → suggestions? → token(지연 안내) → done(stop)` |
| Spring 검색 실패 | `conditions → error(SEARCH_FAILED)` |
| LLM client 없음 | `error(LLM_UNAVAILABLE)` |
| decompose timeout | `error(LLM_TIMEOUT)` |
| 일반 대화 | `token → done(stop)` |
| 담기 성공 | `action(CART_ADDED) → done(stop)` |
| 담기 상품 불명확 | `token(되묻기) → done(stop)` |
| 옵션 필수/첫 invalid | `token(옵션 재질문) → done(stop)` |
| 옵션 invalid 한도 초과 | `action(CART_ADD_FAILED/CART_ERROR) → done(stop)` |
| 상품 없음 | `action(CART_ADD_FAILED/PRODUCT_NOT_FOUND) → done(stop)` |
| 기타 담기 실패 | `action(CART_ADD_FAILED/CART_ERROR) → done(stop)` |
| 장바구니 조회 | `token(목록/빈 상태/실패 안내) → done(stop)` |

`products.ready`는 카드가 아니라 상관키만 전달한다. 카드 표시 데이터는 Spring `GET /api/chat/lists/{listId}`가 소유한다.

## 4. 판매자 sequence

| 상황 | sequence |
|---|---|
| general 성공 | `token... → done(stop)` |
| scope 거절 | `token(거절문) → done(stop)` |
| 분석 성공/clarification/all-worker degrade | `token(진행)* → token(결과) → done(stop)` |
| 분석 planner/report 치명 실패 | `token(진행)* → token(사과) → error(INTERNAL|LLM_TIMEOUT)` |
| 상품 초안 성공 | `draft → done(stop)` |
| 상품 정보 부족/초안 검증 실패 | `token(되묻기) → done(stop)` |
| 상품 초안 치명 실패 | `error(INTERNAL|LLM_TIMEOUT)` |
| `N번 적용해줘` 성공 | `draft → done(stop)` |
| 적용 불성립 | `token(안내) → done(stop)` |
| confirm 결과 | `token(실행/만료/중복/미존재 결과) → done(stop)` |
| confirm Spring 실패 | `token(사과) → error(INTERNAL)` |

confirm 입력은 자유 문장이 아니라 다음 JSON 문자열만 승인된다.

```json
{"action":"confirm","draftId":"draft-id"}
```

## 5. stream 시작 전 HTTP 응답

stream 시작 전 실패는 SSE가 아닌 JSON이다.

```json
{"error":{"code":"TOKEN_INVALID","message":"인증 실패","requestId":"..."}}
```

| HTTP | code |
|---|---|
| 400 | `BAD_REQUEST` |
| 401 | `TOKEN_INVALID` 또는 `TOKEN_EXPIRED` |
| 403 | `FORBIDDEN` |
| 409 | `STREAM_IN_PROGRESS` |
| 429 | `RATE_LIMITED` |
| 500 | `INTERNAL` |
| 504 | `UPSTREAM_TIMEOUT` |

첫 event 이후 내부 예외는 `error(INTERNAL)`로, 전체 stream 상한 도달은 `done(stop)`으로 끝낸다. client disconnect는 별도 event 없이 generator를 취소한다.

## 6. runtime에 없는 예약/과거 event

- `budget`: spec 초안만 있고 현재 schema/graph 미구현
- `products`: 경로 B 이전 카드 직접 전송 event
- `metrics`, `analysis`, `productStats`, `productDiff`: FE/MSW 구 판매자 구조화 event
- 판매자 `action(PRODUCT_UPDATED)`: 현재 판매자 정본 event 집합에 없음

## 7. FE와 확인된 계약 공백

1. AI는 `suggestions`를 emit하지만 FE가 처리하지 않는다.
2. AI confirm은 JSON을 요구하지만 FE 버튼은 `[수정 확인] {draftId}`를 보낸다.
3. AI confirm 결과는 token뿐인데 FE diff settled 상태는 판매자 action을 기대한다.
4. `done.zero_result`가 FE 결과 panel을 비우지 않아 이전 상품이 남는다.
5. 판매자 분석은 token 산문인데 FE 차트/지표 panel은 legacy 구조화 event를 기다린다.

이 항목을 고칠 때는 AI emitter test와 FE reducer/render test를 함께 추가해야 한다.
