# FE 계약 명세 — S-4 `POST /seller/chat` (판매자 대시보드)

> **버전**: v1.0.0 · **작성**: 2026-07-22 · **상태**: **A~E 전부 해소** — 코드·노션·api-spec 정합 완료. FE 착수 가능.
> **대상 독자**: FE. 이 문서 하나로 판매자 챗 대시보드를 붙일 수 있도록 분기별 요청/응답 JSON·성공/실패를 전부 담았다.
> **대조 기준**: 노션 「📡 API 현재」 S-4 페이지 · 리포 `docs/api-spec.md` §3.2 · **실제 코드**
> (`app/api/seller.py` · `app/agents/seller/hitl.py` · `app/schemas/chat.py` · `app/agents/seller/schemas.py`)
> **이 문서는 코드 실측이 기준이다** — 노션·api-spec 과 코드가 어긋나는 지점은 §5에 전부 나열했다.
> 본 문서는 제안이며 코드·노션을 수정하지 않았다.

---

## 0. 세 줄 요약

1. **A-1~A-3 해소됨(2026-07-22).** 이제 노션·api-spec·코드가 정합한다 — (a) SSE 는 `event:` 없는 `data:{type,data}` 포맷으로 통일, (b) confirm 은 최상위 `action`/`draftId` 필드, (c) `threadId` 필수 명시. 아래 §2·§3·§5-A 는 해소 후 기준으로 갱신됨.
2. **화면 전환 신호 구현됨(B, 2026-07-22).** 모든 스트림의 첫 프레임은 `meta{lane}`(어느 레인인지), 종료 프레임 `done{panel}`(우측 패널을 replace/keep/refresh 중 무엇을 할지)다. 분석 진행 상태는 `progress`(로딩), 최종 답변은 `token`으로 분리된다. FE 요구 1~3 이 이 세 신호로 결정된다 — §1.2·§1.4·§3 참조.
3. HITL 자체는 코드가 명세보다 **더 엄격하게** 구현돼 있다(소유·멱등·TTL·stale 5중). 다만 실패를 HTTP 4xx 가 아니라 **정상 스트림의 안내 문구**로 돌려준다 — FE 분기 설계가 달라진다(§4.3, §5-D).

---

## 1. 판매자 화면 플로우

### 1.1 화면 전환 상태 기계

```
┌────────────────────────────────────────────────────────────────┐
│ [S0] 초기 대시보드 — 채팅 입력창 중앙                          │
└───────────────────────────┬────────────────────────────────────┘
                            │ 판매자가 첫 질문 입력
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ [S1] 분할 레이아웃 — 좌: 채팅  /  우: 대시보드                 │
│      (요구사항 1. 이후 레이아웃은 되돌아가지 않는다)           │
└───────────────────────────┬────────────────────────────────────┘
                            │ 매 턴마다 스트림 수신
                            ▼
              ┌─────────────┴─────────────┐
              │ meta{lane} → … → done{panel}│  ← 서버가 명시 (B, 구현됨)
              └─────────────┬─────────────┘
       ┌──────────────┬─────┴─────┬──────────────┐
       ▼              ▼           ▼              ▼
   [S2] 분석      [S3] 초안     [S4] 실행결과  [S5] 유지
   panel=replace  panel=replace panel=refresh  panel=keep
   우측=분석리포트 우측=diff카드  우측=결과+갱신 우측 그대로
       │              │             ▲
       │              │  [적용] 클릭 │
       │              └─────────────┘
       │                  confirm 요청 = 새 스트림
```

**FE 는 우측 패널을 `done.panel` 하나로 결정한다** — 추측하지 않는다:

- `panel == "replace"` → 우측을 이번 스트림의 산출물로 교체(분석 리포트 / diff 카드).
- `panel == "keep"` → 우측 그대로(대화·되묻기·거절·오류).
- `panel == "refresh"` → 쓰기가 반영됨 → 우측 대시보드/상품목록을 **재조회**(이 스트림은 상품 데이터를 싣지 않는다).

`meta.lane` 은 첫 프레임이라 **로딩 상태를 즉시** 띄우는 용도다(예: analysis → 우측에 "분석 중" 스켈레톤). 최종 배치 권한은 `done.panel`.

### 1.2 요구사항 ↔ 레인·신호 매핑

| FE 요구 | 트리거 발화 예 | `meta.lane` | 이벤트 시퀀스 | `done.panel` | 우측 패널 |
|---|---|---|---|---|---|
| 1. 첫 질문 → 분할 | (무엇이든) | (있음) | `meta` … | — | 첫 `meta` 수신 시 레이아웃 전환 |
| 2. 분석 화면 | "지난달 매출 어때?" | `analysis` | `meta`→`progress`×N→`token`→`done` | `replace` | **분석 리포트로 교체** |
| 2. 분석 되묻기 | "매출 분석"(기간 없음) | `analysis` | `meta`→`token`→`done` | `keep` | 유지(되묻기는 대화) |
| 3. 등록/수정/삭제 | "1번 상품 27500원으로" | `product` | `meta`→`draft`→`done` | `replace` | **diff 카드 + [적용]/[취소]** |
| 3. 추천 적용 | "2번 적용해줘" | `apply` | `meta`→`draft`→`done` | `replace` | **diff 카드** |
| 3. 적용 결과 | `{action:"confirm"}` | `confirm` | `meta`→`token`→`done` | `refresh`(성공)/`keep` | **결과 안내 + 목록 재조회** |
| 3. 화면 무관 | "배송비 정책 뭐야?" | `general` | `meta`→`token`×N→`done` | `keep` | **유지** |
| — | "오늘 날씨 어때?" | `refused` | `meta`→`token`→`done` | `keep` | **유지** |

> ✅ 이제 `meta.lane`(레인) + `done.panel`(패널 조치)로 모든 분기가 **FE 에서 명확히 구분**된다. 애매하던 analysis vs general vs 거절이 lane 으로, 분석-리포트 vs 되묻기가 panel 로 갈린다.

### 1.3 HITL 2-스트림 (요구사항 3)

SSE 1스트림 = 응답 1회이므로 승인 대기를 한 연결에 물지 않는다.

```
[스트림 1 · 제안]
  FE → POST /seller/chat  { sessionId, threadId, message: "1번 상품 27500원으로 내려줘" }
  AI ← meta  { lane: "product" }
     ← draft { draftId, op, productId, changes[], summary }
     ← done  { finishReason: "stop", panel: "replace" }
  ※ 서버는 checkpoint(pg-profile)에 draft 를 저장하고 interrupt 상태로 대기

[사용자] diff 카드에서 [적용] 클릭

[스트림 2 · 승인·실행]  ← 완전히 새로운 HTTP 요청
  FE → POST /seller/chat  { sessionId, threadId, action: "confirm", draftId: "..." }
  AI ← meta  { lane: "confirm" }
     ← token { text: "변경을 반영했습니다 (productId=101)." }
     ← done  { finishReason: "stop", panel: "refresh" }   // 성공 시 refresh
  ※ 서버가 코드로 존재→소유→멱등→TTL 검사 후 resume → I-10/I-11/I-12 호출
  ※ [취소] 는 서버 호출 없음 — FE 가 카드만 닫는다. 미승인 draft 는 TTL 만료.

[취소]  서버 호출 없음 — FE 가 카드만 닫는다(별도 cancel API 없음). 미승인 draft 는 TTL 로 만료.
```

### 1.4 분기별 요청 → 응답 시퀀스 (FE 핵심 — 성공·실패 전수)

모든 요청은 `POST {AI_SERVER}/seller/chat` (헤더 §2). 아래는 **보내는 JSON → 받는 SSE 시퀀스**다. 모든 스트림은 `meta` 로 시작하고 `done`(정상) 또는 `error`(오류)로 끝난다.

#### (A) 일반 통계·분석 — analysis

요청:
```json
{ "sessionId": "s1", "threadId": "t1", "message": "지난달 매출 어때?" }
```
성공(리포트):
```
meta      { "lane": "analysis" }
progress  { "text": "매출 이상 분석 중…" }        // 0회 이상, 로딩
progress  { "text": "보고서 작성 중…" }
token     { "text": "6월 매출은 전월 대비 12%…" }  // 최종 리포트 1건
done      { "finishReason": "stop", "panel": "replace" }   // → 우측 패널에 리포트
```
되묻기(기간 불명 등 — 화면 안 바뀜):
```
meta   { "lane": "analysis" }
token  { "text": "어느 기간을 분석할까요?" }
done   { "finishReason": "stop", "panel": "keep" }   // 우측 유지, 대화로
```
실패:
```
meta   { "lane": "analysis" }
token  { "text": "죄송합니다. 분석 처리 중 문제가…" }   // 사과
error  { "code": "LLM_TIMEOUT" | "INTERNAL", "message": "…" }   // done 없음, 패널 유지
```

#### (B) 상품 등록·수정·삭제 — product (스트림 1)

요청:
```json
{ "sessionId": "s1", "threadId": "t1", "message": "1번 상품 27500원으로 내려줘" }
```
성공(초안 성립):
```
meta   { "lane": "product" }
draft  { "draftId": "…", "op": "update", "productId": 101,
         "changes": [{ "field": "price", "before": "29000", "after": "27500" }],
         "summary": "가격을 27,500원으로 인하" }
done   { "finishReason": "stop", "panel": "replace" }   // → 우측에 diff 카드 + [적용]/[취소]
```
초안 불성립(대상 모호·필수 누락 등 — 화면 안 바뀜):
```
meta   { "lane": "product" }
token  { "text": "어느 상품을 변경할지 특정하지 못했습니다…" }
done   { "finishReason": "stop", "panel": "keep" }
```
실패:
```
meta   { "lane": "product" }
error  { "code": "LLM_TIMEOUT" | "INTERNAL", "message": "…" }
```

#### (C) 초안 승인 — confirm (스트림 2, [적용] 클릭)

요청(최상위 `action`/`draftId`, `message` 없음):
```json
{ "sessionId": "s1", "threadId": "t1", "action": "confirm", "draftId": "a3f1c2d4-…" }
```
성공(실제 반영):
```
meta   { "lane": "confirm" }
token  { "text": "변경을 반영했습니다 (productId=101)." }
done   { "finishReason": "stop", "panel": "refresh" }   // → 우측 목록/대시보드 재조회
```
실패적 결과(전부 HTTP 200, 안내 문구 — §4.3): 만료·미존재·소유불일치·중복(멱등)·stale
```
meta   { "lane": "confirm" }
token  { "text": "초안이 만료됐습니다(유효 N분)…"  // 또는 "이미 처리된 승인…" 등 }
done   { "finishReason": "stop", "panel": "keep" }   // 변경 없음 → 유지
```
서버 통신 실패(초안 유지, 재시도 가능):
```
meta   { "lane": "confirm" }
token  { "text": "상품 서버와 통신이 원활하지 않아… 초안은 유지되니 다시…" }
error  { "code": "INTERNAL", "message": "상품 서버 통신에 실패했습니다." }
```

#### (D) 추천 적용 — apply ("N번 적용해줘")

요청:
```json
{ "sessionId": "s1", "threadId": "t1", "message": "2번 적용해줘" }
```
성공/불성립 시퀀스는 **(B) product 와 동일** — 성립 시 `draft`+`done{panel:"replace"}`, 불성립 시 `token`+`done{panel:"keep"}`, `meta.lane` 만 `"apply"`.

> 매칭은 문장 전체가 정형("N번 적용해줘")일 때만. "2번 상품에 할인 적용해줘"처럼 여분 토큰이 있으면 apply 가 아니라 일반 라우팅(analysis/product)으로 간다.

#### (E) 일반 대화 — general (화면 안 바뀜)

요청:
```json
{ "sessionId": "s1", "threadId": "t1", "message": "배송비 정책이 뭐야?" }
```
```
meta   { "lane": "general" }
token  { "text": "판매자님의 배송비…" }   // 스트리밍 증분, 여러 개
token  { "text": " 정책은…" }
done   { "finishReason": "stop", "panel": "keep" }
```

#### (F) 도메인 밖 — refused (화면 안 바뀜)

요청:
```json
{ "sessionId": "s1", "threadId": "t1", "message": "오늘 날씨 어때?" }
```
```
meta   { "lane": "refused" }
token  { "text": "판매 관련 질문만 도와드릴 수 있습니다…" }
done   { "finishReason": "stop", "panel": "keep" }
```

#### 스트림 시작 전 거부(요청 자체가 틀림 — SSE 아님, HTTP 오류 봉투)

`meta` 조차 못 받는다. HTTP 상태 + JSON 봉투로 온다(§4.1): `400`(필드 누락·`action=="confirm"`인데 `draftId` 없음)·`401`(토큰)·`403`(seller 아님)·`409`(동일 sessionId 동시 스트림).

---

## 2. 요청 스키마 (실측)

`app/schemas/seller.py::SellerChatRequest` — 구매자 `ChatRequest` 를 확장한 **판매자 전용** 모델(구매자 계약 무변경).

```
POST {AI_SERVER}/seller/chat
Authorization: Bearer {SELLER_JWT}      # seller_id + brand_id 클레임 필수
Content-Type: application/json
Accept: text/event-stream
```

### 2.1 (a) 일반 요청 — 통계·상품·잡담 공통

```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "threadId": "thread-001",
  "message": "지난달 매출 어때?"
}
```

| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `sessionId` | string | **예** | 길이 상한 `chat_key_max_chars` |
| `threadId` | string | **예** | ✅ 노션 예시에 추가됨(A-3). 누락 시 400 |
| `message` | string | **예** | 길이 상한 `chat_message_max_chars` |

- 신원(`sellerId`·`brandId`)은 본문에 **없다** — JWT 클레임에서만 도출(IDOR 방지). 코드도 동일(`require_seller`).

### 2.2 (b) 승인 요청 — [확정 2026-07-22, A-2]

```json
{
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "threadId": "thread-001",
  "action": "confirm",
  "draftId": "a3f1c2d4-5e6f-7890-abcd-ef1234567890"
}
```

**`action`·`draftId` 는 최상위 필드다.** seller 전용 `SellerChatRequest`(`app/schemas/seller.py`)가 이 필드를 받는다 — 구매자 `ChatRequest` 는 그대로다.

| 필드 | 타입 | 필수 | 비고 |
|---|---|---|---|
| `action` | `"confirm"` | 승인 시 | 이 값이 유일한 승인 신호 |
| `draftId` | string | `action=="confirm"` 이면 **필수** | 스트림1 `draft.draftId`. 누락·공백이면 **400** |
| `message` | string | 아니오 | 승인 요청에서는 비워도 된다(`""`). 승인은 발화가 아님 |
| `threadId`·`sessionId` | string | **예** | 일반 요청과 동일하게 필수 |

판정: 서버(`_seller_stream`)는 `request.action == "confirm"` 이면 곧장 confirm 레인으로 간다(LLM 0회). 자유 텍스트 message("응 바꿔")는 `action` 필드가 없으므로 **절대 승인이 아니다** — "발화 ≠ 동의" [HARD] 는 스키마 구조로 강제된다. `action=="confirm"` 인데 `draftId` 가 비면 스키마 validator 가 **400 BAD_REQUEST** 로 거른다(조용한 무시 없음).

### 2.3 (c) 추천 적용 — "N번 적용해줘" (E-1, 노션 반영됨)

분석 응답이 행동 추천을 포함한 뒤, 정형 발화로 추천을 draft 로 변환한다.

```json
{ "sessionId": "...", "threadId": "...", "message": "2번 적용해줘" }
```

정규식 `^\s*(\d{1,3})\s*번(?:\s*추천)?(?:\s*[을를])?\s*적용\s*(?:해\s*(?:줘|주세요|줘요)?|부탁해요?|하기)?\s*[.!?~]*\s*$` — **문장 전체**가 일치해야 한다. "2번 상품에 할인 적용해줘" 처럼 여분 토큰이 있으면 일반 라우팅으로 간다. 결과는 §3.3 `draft` 이벤트(= product 레인과 동일 합류).

---

## 3. 응답 스키마 — SSE (실측)

### 3.1 프레임 포맷 — [확정 2026-07-22, A-1] 노션·코드 정합

**코드가 내보내는 실제 바이트** (`app/api/seller.py::_sse`, `app/agents/buyer/_frames.py` 공통):

```
data: {"type":"token","data":{"text":"지난달 매출은…"}}\n\n
```

**`event:` 라인이 없다.** 모든 이벤트가 익명 `message` 이벤트로 오고, 종류는 payload 의 `type` 필드로 구분한다.

```js
// ❌ 동작하지 않음 — 노션 표기(event: token)를 그대로 믿은 구현
es.addEventListener('token', handleToken);   // 절대 발화하지 않음
es.addEventListener('draft', handleDraft);   // 절대 발화하지 않음

// ✅ 실제 코드에 맞는 구현
let lane = null;
es.onmessage = (e) => {
  const { type, data } = JSON.parse(e.data);
  switch (type) {
    case 'meta':     lane = data.lane; prepareLane(lane); break;   // 첫 프레임
    case 'progress': showLoading(data.text); break;                // 분석 로딩(답변 아님)
    case 'token':    appendToken(data.text); break;                // 대화/보고서 본문
    case 'draft':    renderDiffCard(data); break;                  // diff 카드
    case 'done':     finish(data.panel); break;                    // panel: replace|keep|refresh
    case 'error':    showError(data.code, data.message); break;    // 종결(뒤에 done 없음)
  }
};
```

> 참고: `POST` + 커스텀 헤더가 필요하므로 브라우저 표준 `EventSource` 는 쓸 수 없다(GET·헤더 불가). `fetch` + `ReadableStream` 수동 파싱 또는 `@microsoft/fetch-event-source` 류가 필요하다.

### 3.2 이벤트 6종 요약

| 이벤트 | 언제 | data | 개수 |
|---|---|---|---|
| `meta` | **첫 프레임(항상)** | `{ lane }` | 정확히 1 |
| `progress` | 분석 진행 상태(로딩, 답변 아님) | `{ text }` | 0+ (analysis 만) |
| `token` | 대화/보고서/결과 본문 | `{ text }` | 0+ |
| `draft` | 상품 초안(diff 카드) | `{ draftId, op, productId, changes[], summary }` | 0~1 |
| `done` | 정상 종료 | `{ finishReason, panel }` | 0~1 (error 시 없음) |
| `error` | 스트림 내부 오류(종결) | `{ code, message }` | 0~1 |

`products.ready` · `conditions` · `suggestions` · `budget` · `action` 은 **구매자 전용** — 판매자 스트림엔 오지 않는다.

### 3.3 `meta` — 첫 프레임(레인)

```json
{ "type": "meta", "data": { "lane": "analysis" } }
```

`lane` ∈ `analysis` ｜ `product` ｜ `general` ｜ `confirm` ｜ `apply` ｜ `refused`. **모든 판매자 스트림의 첫 프레임**이다. FE 는 이걸로 (1) 첫 질문 시 분할 레이아웃 전환, (2) 레인별 로딩 상태(예: analysis → 우측 "분석 중" 스켈레톤)를 즉시 준비한다. 최종 패널 배치는 `done.panel` 이 확정한다.

### 3.4 `progress` — 분석 로딩(답변 아님)

```json
{ "type": "progress", "data": { "text": "매출 이상 분석 중…" } }
```

`analysis` 레인에서만, 최종 답변 **전에** 0회 이상 온다. **답변이 아니라 진행 표시**다 — FE 는 임시 로딩 텍스트로 렌더하고, 최종 `token` 이 오면 대체한다. (다른 레인엔 오지 않는다.)

### 3.5 `token`

```json
{ "type": "token", "data": { "text": "지난달 매출은 전월 대비 12% 감소했어요." } }
```

레인별 emit 특성이 다르다 — FE 렌더링에 영향.

| 레인 | token 성격 | 개수 | 배치(done.panel) |
|---|---|---|---|
| `general` | LLM 스트리밍 증분 (타이핑 효과 가능) | 다수, 부분 문자열 | chat (keep) |
| `analysis`(report) | 최종 리포트 **전문 1건** (진행은 `progress` 로 분리) | 1 | **우측 패널 (replace)** |
| `analysis`(되묻기) | 되묻기 문구 1건 | 1 | chat (keep) |
| `product`/`apply`(되묻기) | 되묻기·검증 실패 문구 1건 | 0~1 | chat (keep) |
| `confirm` | 실행 결과 안내 **1건** | 1 | chat (+ refresh) |
| `refused` | 거절 문구 1건 | 1 | chat (keep) |

analysis 의 최종 리포트는 **한 덩어리로 도착**한다(스트리밍 증분 아님) — `done.panel=="replace"` 면 그 token 텍스트를 우측 패널에 렌더한다.

### 3.6 `draft` — 실측 페이로드

`app/api/seller.py::_draft_event`

```json
{
  "type": "draft",
  "data": {
    "draftId": "a3f1c2d4-5e6f-7890-abcd-ef1234567890",
    "op": "update",
    "productId": 101,
    "changes": [
      { "field": "stockQuantity", "before": "100", "after": "50" }
    ],
    "summary": "재고를 50개로 조정"
  }
}
```

| 필드 | 타입 | 실측 비고 |
|---|---|---|
| `draftId` | string | 서버 발급 **UUID v4**. 노션 예시 `"draft-8f21"` 은 형식만 예시 |
| `op` | `"create"｜"update"｜"delete"` | I-10 / I-11 / I-12 매핑 |
| `productId` | **number ｜ null** | 숫자(BIGINT). `create` 는 `null` |
| `changes[].field` | string | ✅ **camelCase** (C-1 수정 2026-07-22) — 규약 §2.2 |
| `changes[].before` | string | `create` 는 `""`. 수치도 문자열 |
| `changes[].after` | string | 수치도 문자열 |
| `summary` | string | diff 카드 부제용 한 줄 요약. api-spec §3.2·노션에 반영됨 |

`field` 로 올 수 있는 값 8종 — **와이어(camelCase) 기준**:

```
name  price  originalPrice  description  category  imageUrl  status  stockQuantity
```

(서버 내부 `ProductField`·Spring 쓰기는 snake_case 이지만, FE 로 나가는 draft 이벤트에서는 camelCase 로 변환된다. FE 는 camelCase 만 본다.)

`status` 값은 `ON_SALE` ｜ `HIDDEN` 만 허용. `delete` 는 `status: ON_SALE → HIDDEN` 1건으로 표현된다(soft delete 가시화).

`op: "create"` 제약 (`hitl.validate_draft`):
- **필수** `name`·`price`·`stockQuantity` — 누락 시 draft 불성립 → 되묻기 token
- **금지** `imageUrl`·`status` — 포함 시 draft 불성립 → 되묻기 token

### 3.7 `done` — 종료 + 패널 조치

```json
{ "type": "done", "data": { "finishReason": "stop", "panel": "replace" } }
```

| 필드 | 값 | 의미 |
|---|---|---|
| `finishReason` | `"stop"` 단일 | 판매자 스트림은 `zero_result` 미사용 |
| `panel` | `"replace"` | 이번 스트림 산출물로 우측 패널 교체(분석 리포트 / diff 카드) |
| `panel` | `"keep"` | 우측 그대로(대화·되묻기·거절) |
| `panel` | `"refresh"` | 쓰기 반영됨 → 우측 대시보드/상품목록 재조회 |

> `panel` 은 판매자 전용 필드다(구매자 `done` 엔 없음). `error` 로 끝나면 `done` 이 오지 않으므로, **오류 시 FE 는 패널을 유지(keep)** 하고 오류만 표시한다.

### 3.8 `error`

```json
{ "type": "error", "data": { "code": "INTERNAL", "message": "일시적인 오류가 발생했습니다." } }
```

`code`: `LLM_TIMEOUT` ｜ `LLM_UNAVAILABLE` ｜ `SEARCH_FAILED` ｜ `INTERNAL`

**`error` 는 종결 이벤트다 — 뒤에 `done` 이 오지 않는다.** FE 는 `done` 또는 `error` 중 **하나**를 스트림 종료 신호로 처리해야 한다. `error` 로 끝나면 패널은 유지(keep)한다.

---

## 4. 성공·실패 케이스 전수 (코드 실측)

### 4.1 스트림 시작 **전** 실패 — HTTP 상태 + JSON 봉투

봉투: `{ "error": { "code": "...", "message": "...", "requestId": "..." } }` (`app/core/errors.py::error_envelope`).

| HTTP | code | 조건 | 코드 위치 |
|---|---|---|---|
| 400 | `BAD_REQUEST` | `threadId` 누락 · `message` 길이 초과 · `action=="confirm"` 인데 `draftId` 누락/공백 | `SellerChatRequest` 검증 → `core.errors._validation_exception_handler` |
| 401 | `TOKEN_EXPIRED` | 토큰(스트림 티켓) 만료 → 재발급 후 재시도 | `deps.get_identity` |
| 401 | `TOKEN_INVALID` | 서명·형식·scope 불일치, 토큰 없음 | `deps.get_identity` |
| 403 | `FORBIDDEN` | `seller_id` 클레임 없음(`"seller scope required"`) 또는 `brandId` 클레임 없음 | `deps.require_seller` |
| 409 | `STREAM_IN_PROGRESS` | 같은 `sessionId` 로 이미 활성 스트림 존재(동시 요청) | `core.stream.open_stream` |
| 429 | `RATE_LIMITED` | 요청 과다 — 기본 **10회/분·100회/시**(config `rate_limit_per_min`/`_per_hour`), sub 스코프 + IP 백스톱 | `core.ratelimit.rate_limit_middleware` |
| 504 | `UPSTREAM_TIMEOUT` | first-token/전체 타임아웃(§2.9) — 상류(LLM) 응답 지연 | `core.stream.open_stream` |

> ✅ **400·429 확정**: 400 은 `RequestValidationError`→400 봉투 매핑(FastAPI 기본 422 아님), 429 는 `/seller/chat` 에 실제 적용됨(`_LIMITED_PATHS`). 초기 진단의 "429 미구현" 은 오판이었다.
> ✅ **confirm 실패(만료·미존재·소유불일치·중복·stale)는 HTTP 오류가 아니다** — 전부 **200 + 안내 token + `done{panel:"keep"}`** (§4.3). 노션의 409 `DRAFT_EXPIRED`/`DRAFT_NOT_FOUND` 는 발생하지 않으므로 노션에서 제거함(D-1).

### 4.2 스트림 **중** 실패 — `error` 이벤트

| 레인 | 상황 | 이벤트 시퀀스 |
|---|---|---|
| general | LLM 타임아웃 | `error{LLM_TIMEOUT}` |
| general | 그 외 예외 | `error{INTERNAL}` |
| analysis | 타임아웃 | `token`(사과) → `error{LLM_TIMEOUT}` |
| analysis | planner 장애·1차 report 실패 | `token`(사과) → `error{INTERNAL}` |
| product | draft 생성 타임아웃 | `error{LLM_TIMEOUT}` |
| product | checkpoint 저장 실패 | `error{INTERNAL}` |
| confirm | Spring 장애 | `token`(재시도 안내) → `error{INTERNAL}` |
| 추천 적용 | Spring 장애 | `token`(안내) → `error{INTERNAL}` |

### 4.3 confirm 결과 5종 — **전부 HTTP 200 + `token` + `done`** 🔴

`hitl.confirm_draft` → `ConfirmOutcome.status`. FE 는 HTTP 코드로 분기할 수 없고, 성공·실패가 **모두 자연어 안내 문구**로 온다.

| status | 조건 | 안내 문구(요지) | HTTP |
|---|---|---|---|
| `executed` | 정상 반영 | "변경을 반영했습니다 (productId=101)." | 200 |
| `not_found` | draftId 미존재 **또는 소유 불일치** | "해당 승인 요청을 찾을 수 없습니다…" | 200 |
| `expired` | `created_at` + `seller_draft_ttl_minutes` 경과 | "초안이 만료됐습니다(유효 N분)…" | 200 |
| `already_done` | 이미 실행된 draftId 재전송(멱등) | "이미 처리된 승인 요청입니다 — 중복 실행하지 않았습니다…" | 200 |
| `stale` | confirm 시점 I-9 재조회 결과 `before` 불일치 / 상품 미발견 | "초안 작성 이후 상품 정보가 변경되어 반영을 중단했습니다…" | 200 |

**소유 불일치는 `not_found` 와 같은 문구로 응답한다** — 타 판매자에게 draft 존재 여부를 노출하지 않기 위한 의도된 설계.

`stale` 검증 세부: `stock_quantity` 는 비교에서 **제외**한다(주문 재고 차감으로 자연 변동, F6). 대신 변동이 감지되면 결과 안내에 현재값을 덧붙인다.

> **FE 영향**: 성공/실패를 프로그램적으로 구분할 수단이 현재 없다. §5-D 제안.

### 4.4 draft 불성립 — `token` + `done` (200)

| 조건 | 문구(요지) |
|---|---|
| 대상 상품 미특정 (`update`/`delete` 인데 productId 없음) | "어느 상품을 변경할지 특정하지 못했습니다…" |
| `update` 인데 changes 비어있음 | "무엇을 어떻게 바꿀지 파악하지 못했습니다…" |
| `create` 에 `image_url`/`status` 포함 | "상품 등록 시에는 이미지·상태를 함께 지정할 수 없습니다…" |
| `create` 에 `name`/`price`/`stock_quantity` 누락 | "상품 등록에는 상품명·가격·재고 수량이 필요합니다…" |
| 값 캐스팅 실패 (예: price="비싸게") | "'price' 값 '비싸게' 을(를) 해석하지 못했습니다…" |
| LLM 이 `clarification` 반환 (대상 모호 등) | LLM 이 생성한 되물음 |

전부 `draft` 이벤트가 **오지 않고** `token` → `done` 이다. FE 는 diff 카드를 띄우면 안 된다.

---

## 5. 수정이 필요한 항목 (우선순위)

### A. ✅ 해소 완료 (2026-07-22)

| # | 항목 | 결정·조치 | 반영 위치 |
|---|---|---|---|
| **A-1** | SSE 프레임 | `event:` 라인 없는 `data:{type,data}` 포맷으로 확정(구매자 포함 리포 전체 규약). **노션 SSE 예시를 코드 포맷에 맞춰 수정**. 코드 무변경 | 노션 S-4 ✅ / FE-CONTRACT §3.1 |
| **A-2** | confirm 전송 | 최상위 `action`/`draftId` 필드로 확정. seller 전용 `SellerChatRequest` 신설(구매자 `ChatRequest` 무변경), `message`-JSON 파싱(`parse_confirm_message`) 제거. `action=="confirm"` + `draftId` 누락은 400 | `app/schemas/seller.py`·`app/api/seller.py`·`app/agents/seller/pipeline.py` / api-spec §3.2 / 노션 S-4 |
| **A-3** | `threadId` | 필수 유지(구매자 계약 일관). 노션 요청 예시 (a)(b) 에 `threadId` 추가 | 노션 S-4 / api-spec §3.2 |

> **테스트**: `tests/unit/test_seller_chat_request.py` 신설(스키마 계약 7종) + `test_seller_api.py`·`test_seller_router.py` 갱신. seller 스위트 279 통과, 전체 유닛 571 통과, ruff clean.

### B. ✅ 해소 완료 (2026-07-22) — 화면 전환 신호

3개 신호를 판매자 스트림 전용으로 추가했다(구매자 계약 무변경):

| 신호 | 위치 | 역할 |
|---|---|---|
| `meta{lane}` | 첫 프레임 | 레인 즉시 통지 → 레이아웃 전환·로딩 준비 (§3.3) |
| `progress{text}` | analysis 중 | 진행 상태를 최종 답변(`token`)과 분리 (§3.4) |
| `done{panel}` | 종료 | 우측 패널 조치 확정: replace/keep/refresh (§3.7) |

`lane` × `panel` 매핑(§1.2)으로 FE 요구 1~3 이 전부 결정된다: 분석↔잡담↔거절은 `lane` 으로, 분석-리포트↔되묻기는 `panel` 로 구분된다. B-2(done 에 lane) 대신 B-1(meta)+B-3(progress)을 채택해 로딩 스켈레톤·조기 전환까지 가능하게 했다.

**반영 위치**: `app/api/seller.py`(`_meta`/`_progress`/`_done(panel)` + 6개 substream) / 노션 S-4 / api-spec §3.2 / 본 문서 §1.4(분기별 시퀀스)·§3. `tests/unit/test_seller_api.py` 갱신 + meta/panel 계약 테스트 3종 추가 — seller 282 통과·전체 574 통과·ruff clean.

> **미해결(다음)**: analysis 의 최종 리포트가 스트리밍 증분이 아니라 단일 token 이라 긴 리포트에서 체감 지연이 있다. 증분 스트리밍은 파이프라인 구조 변경이 필요해 별도 과제.

### C. ✅ 해소 완료 (2026-07-22)

| # | 항목 | 조치 |
|---|---|---|
| **C-1** | `changes[].field` snake_case | ✅ **코드 수정** — `_draft_event` 가 `to_camel` 로 변환(`stock_quantity`→`stockQuantity` 등). 내부(Spring 쓰기)는 snake_case 유지. 8종 필드 회귀 테스트(`test_draft_changes_field_is_camelcase`) 추가 |
| **C-2** | `draft.summary` 누락 | ✅ **명세 반영** — api-spec §3.2·노션 draft 예시·필드표에 `summary` 추가 |
| **C-3** | product 근거 token | ✅ **명세를 코드에 맞춤** — api-spec §3.2·노션에서 "token(근거)→draft" 를 "draft(요약=`summary`)" 로 정정. 코드는 근거 token 미발행이 정상 |
| **C-4** | `productId` 타입 | ✅ **노션 정정** — "문자열"→숫자(BIGINT). api-spec·코드와 일치 |
| **C-5** | `draftId` 형식 | ✅ **노션 정정** — 서버 발급 UUID v4 임을 명시(예시 `draft-8f21` 은 형식 예시) |

### D. 🟡 오류 계약

### D. ✅ 해소 완료 (2026-07-22)

| # | 항목 | 조치 |
|---|---|---|
| **D-1** | draft 만료·미존재 | ✅ **노션에서 409 `DRAFT_EXPIRED`/`DRAFT_NOT_FOUND` 제거** — confirm 실패는 200+안내(§4.3). 성공/실패는 `done.panel`(refresh/keep)로 구분되므로 별도 `confirmStatus` 필드는 불필요(코드 무변경) |
| **D-2** | 400 `BAD_REQUEST` | ✅ 일치 확정 — `RequestValidationError`→400 매핑(§4.1). 초기 진단의 "422" 는 오판 |
| **D-3** | 429 `RATE_LIMITED` | ✅ **구현 확인** — `/seller/chat` 에 실제 적용(`core.ratelimit`, 10/분·100/시 + IP 백스톱). 노션 유지가 맞다. 초기 진단의 "미구현" 은 오판 |
| **D-4** | 409 `STREAM_IN_PROGRESS`·504 `UPSTREAM_TIMEOUT` | ✅ **노션에 추가** — 동시 요청(409)·상류 타임아웃(504). FE 가 실제로 만나는 오류 |

### E. ✅ 해소 완료 (2026-07-22)

| # | 항목 | 조치 |
|---|---|---|
| **E-1** | 추천 적용 발화 | ✅ **노션에 추가** — "N번 적용해줘"(정형)→`apply` 레인→draft. §1.4(D)·§2.3 |
| **E-2** | draft 취소 | ✅ **노션에 추가** — 별도 API 없음. FE 가 카드만 닫고 미승인 draft 는 TTL 만료 |
| **E-3** | scope 차단 | ✅ **노션에 추가** — 도메인 밖 질문은 `meta{refused}`+거절 token+`done{keep}`(LLM 0회) |
| **E-4** | `field` 8종 목록 | ✅ **노션 정정** — 5종→camelCase 8종(C-1 과 함께) |

---

## 6. 권장 처리 순서

1. ✅ **A-1·A-2·A-3 완료(2026-07-22)** — 요청/응답·confirm·threadId 정합.
2. ✅ **B 완료(2026-07-22)** — `meta`/`progress`/`done.panel` 로 화면 전환 신호 구현. FE 요구 1~3 은 이 문서 §1.4·§3 으로 구현 가능.
3. ✅ **C 완료(2026-07-22)** — C-1(field camelCase) 코드 수정, C-2~C-5 명세 정정.
4. ✅ **D·E 완료(2026-07-22)** — 오류표 정정(confirm-200·429 구현 확인·409/504 추가) + 누락 계약(추천 적용·취소·scope) 노션 반영. **코드 변경 없음**(전부 문서 정합).

> **A~E 전부 해소.** FE 는 이 문서 + 노션 S-4 로 판매자 챗 대시보드를 붙일 수 있다. 남은 것은 상류 재대조뿐(§7).

---

## 7. 미확인 항목

- 노션 「📡 API 현재」 DB 에서 S-4 의 `프론트 연결` 체크박스가 **`__NO__`** — FE 연동 미시작 상태로 기록돼 있다.
- 본 문서는 `docs/api-spec.md` **v0.14.0 로컬 사본** 기준이다. 기획 repo 정본(v0.15.4+)에서 §3.2 가 개정됐다면 재대조가 필요하다.
- `role == "seller"` 클레임 이름·형식은 api-spec C-1 에서 여전히 🔴 BE 확인 대기. 코드는 `seller_id`·`brand_id` 클레임 존재 여부로 판정한다.
