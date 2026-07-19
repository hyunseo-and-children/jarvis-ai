---
id: SPEC-CART-001
version: 0.2.4
status: draft
created: 2026-07-17
author: navis
priority: high
issue_number: null
---

> ⚠️ **신규 SPEC — 대응 정본 없음**: 다른 `SPEC-XXX-001` 문서들과 달리, 본 문서는 기획 저장소에 대응하는 정본이 아직 없다. `docs/PRD-RECOMMEND-PROFILE-AGENT.md` §3.3(핵심 기능)/§5.4(데이터 모델)/§7(API)/§10-F(리스크)의 장바구니 서술을 EARS 요구사항 수준으로 구체화한 **신규 초안**이며, 팀 검토 후 기획 저장소에 정본으로 등록하는 것을 권장한다(PRD §10-F 리스크 해소 목적).
> 외부 **계약**(엔드포인트·SSE 이벤트·필드·오류 코드)의 상위 소스는 여전히 **api-spec v0.15.8**([docs/api-spec.md](../api-spec.md))이다 — 본 SPEC과 어긋나면 api-spec을 따른다.

# SPEC-CART-001 — 장바구니 서브그래프 (Cart Subgraph)

> 본 SPEC은 결정 7(장바구니는 AI가 의도만 확정하고 실행은 Spring에 위임)을 EARS 요구사항 수준으로 구체화한다. "담아줘" 자연어에서 (상품·옵션·수량) 의도를 추출하고, `POST /internal/cart/items`(I-2)·`GET /internal/cart`(I-18)로 Spring에 위임해 실행·조회하며, 결과를 SSE `action`/`token` 이벤트로 사용자에게 알리는 장바구니 서브그래프의 동작을 확정한다.

## HISTORY

- **v0.2.4 (2026-07-19)** — PR #17 리뷰(별도 사항) + PR #21과의 병합: `productName`/`optionName`(C-16)이 BE I-18 문서(2026-07-18)로 필수 포함 확정됨을 반영해 REQ-CART-036·OPEN-CART-2를 완전히 해소(v0.2.3/PR #21 시점엔 이 부분만 미확정으로 남아 있었음). `CART_QUERY_INVALID`(I-18 400) 오류 처리를 §7에 신설. `reason`·options 구조를 SPEC에 값으로 직접 중복 기재하지 않고 api-spec §4.1 참조로 전환(§5.3, REQ-CART-025/051) — REQ-CART-025엔 PR #21의 방어적 스킵 문구를 유지. 경로 B productId 요구사항은 PR #21이 붙인 `REQ-CART-001a` 번호를 그대로 채택(중복 REQ-CART-008 제거). PRD-RECOMMEND-PROFILE-AGENT.md에도 동일 내용 반영, api-spec 참조 버전 v0.15.8 유지.
- **v0.2.3 (2026-07-19, PR #21)** — api-spec v0.15.8 동기화(#17 리뷰 반영): (1) `OUT_OF_STOCK` **폐기**(v0.15.5 C-3 해소 — 담기 재고검증 없음, 재고 차감=주문 시점) → 담기 실패 **2종**(`PRODUCT_NOT_FOUND`/`CART_ERROR`); §5.3·REQ-CART-015·051·EX-CART-2·§7 오류표·OPEN-CART-1 반영. (2) **옵션 스키마 확정**(BE 2026-07-18) — `CART_OPTION_REQUIRED` → `error.detail.options: [{optionId, name, extraPrice}]`, REQ-CART-025·OPEN-CART-2 해소. (3) **경로 B productId 해소 요구사항 신설**(REQ-CART-001a) — SSE에 카드가 없어 productId 를 직전 추천(last_reco) 문맥에서 확정하고 미노출 상품 담기를 차단(코드 구현 정합). 참조 버전 v0.15.3→v0.15.8.
- **v0.2.2 (2026-07-19)** — 팀 피드백(PRD.pdf) 반영: `product_id`/`option_id`를 `str`→`int`(BIGINT)로, `guest_id`를 `int`→`str`(UUID)로 수정(정본 §2.6 확정 — product/product_option id는 숫자, guest.id는 CHAR(36) UUID). §5.2 응답과 §5.3 SSE 페이로드 간 `cartItemId` 타입 불일치를 `int`로 통일. OPEN-CART-5(`productId` 타입)를 해소 처리. api-spec 참조 버전 v0.14.0→v0.15.3.
- **v0.2.1 (2026-07-18)** — BE 팀 공유 문서로 재고(`stock_quantity`) 도입·주문 실패 조건 확인, 장바구니 실패 사유 개수(4종→3종 추정) 확인 요청을 반영해 OPEN-CART-1 갱신. BE가 이 확인을 **LLM팀(우리) 책임**으로 명시함에 따라 OPEN-CART-1을 "Spring 대기" 성격에서 "우리가 답해야 하는 확인 요청"으로 재정의.
- **v0.2.0 (2026-07-17)** — 의도 추출 모델을 **Claude Haiku 4.5로 확정**(OPEN-CART-4 해소). 근거: `decompose`(추천 서브그래프)와 동일한 성격 — 복잡한 추론·종합 판단 없이 발화에서 (상품·옵션·수량) 3개 필드만 뽑는 경량 구조화 추출이며, 오히려 Case 판별까지 겸하는 `decompose`보다 단순하다. 이 프로젝트의 2-tier 모델 배정 원칙(결정 5 — Haiku=경량 추출/분해, Sonnet=종합 판단·생성)을 그대로 따른 결정. REQ-CART-007 개정, §9 OPEN-CART-4 해소로 이동.
- **v0.1.0 (2026-07-17)** — 최초 작성. `PRD-RECOMMEND-PROFILE-AGENT.md` §3.3/§5.4/§7/§10-F를 EARS 요구사항으로 구체화. 근거 문서: `mvp-plan.md` §2, `mvp-todo.md` §2, `api-spec.md` §3.1(3)/§4.1/§4.9, `app/agents/buyer/cart/__init__.py` 스텁 docstring, Notion "📡 API 명세서" DB(I-2/I-18 경로·메서드·인증 확정, 2026-07-16 대조).

---

## 1. 개요 & 범위 (Overview & Scope)

### 1.1 목적

구매자 그래프 내에서 "담아줘"류 장바구니 의도를 처리하는 **장바구니 서브그래프**의 동작을 정의한다. AI는 (상품·옵션·수량) 의도만 확정하고, 장바구니 담기의 실행·검증·수량 합산은 전적으로 Spring에 위임한다(결정 7) — AI는 커머스 DB에 직접 write하지 않는다.

### 1.2 In Scope

- 의도 추출: 발화에서 상품·옵션·수량을 구조화(`CartIntent`).
- 담기 실행 위임: `POST /internal/cart/items`(I-2)에 단건 위임, Case 3 묶음은 상품별 반복 호출.
- 옵션 되물음 멀티턴: `CART_OPTION_REQUIRED`/`CART_OPTION_INVALID` 응답에 따른 재질문·재담기.
- 담기 전 보유 조회: `GET /internal/cart`(I-18)로 기존 보유 확인, 합산 안내 생성(합산 실행은 Spring).
- 장바구니 질의 응답: "장바구니에 뭐 있어?" 발화에 I-18 조회 후 자연어 응답.
- 게스트 담기 허용: `userId`/`guestId` 분기.
- 결과 통지: SSE `action` 이벤트(`CART_ADDED`/`CART_ADD_FAILED`).

### 1.3 의존성 (Dependencies — 본 SPEC 외부, 참조만)

| 의존 대상 | 제공하는 것 | 소유 |
|---|---|---|
| 구매자 그래프 intent router | cart 서브그래프로의 진입 결정 | 구매자 그래프 SPEC(별도, 미작성) |
| `POST /internal/cart/items`(I-2) | 담기 실행·검증·수량 합산 | Spring(BE), api-spec §4.1 |
| `GET /internal/cart`(I-18) | 보유 조회 | Spring(BE), api-spec §4.9 |
| 인증(JWT `sub`/`role`) | 신원 도출(userId/guestId) | 공통 인프라(`app/core/auth.py`) |
| SSE 스트림 수명주기 | 동시 스트림 제한·취소·타임아웃 | 공통 인프라(api-spec §2.9) |

---

## 2. Exclusions (What NOT to Build)

[HARD] 본 서브그래프에서 **구현하지 않는** 항목을 명시한다.

- **EX-CART-1 담기 실행·검증·수량 합산**: `POST /internal/cart/items` 호출 이후의 실제 DB write, 옵션 소속 검증, 동일 상품·옵션 수량 합산은 전적으로 **Spring 소관**(결정 7). 본 서브그래프는 위임 호출만 한다.
- **[폐기, v0.15.5] EX-CART-2 재고 실시간 검증 로직**: 담기(I-2) 시점 재고 검증은 아예 없다 — 재고 차감·품절 판정은 **주문(O-1) 시점에만** 발생한다(v0.15.5 확정). `OUT_OF_STOCK` 코드 자체가 폐기됐으므로, 본 서브그래프가 예약해둘 자리도 더 이상 없다.
- **EX-CART-3 결제·주문 전환**: 장바구니에서 결제로 넘어가는 흐름(주문 생성 등)은 별도 orders 플로우 소관, 본 서브그래프 비범위.
- **EX-CART-4 장바구니 화면 렌더링**: 우측 패널·장바구니 페이지 UI는 FE/Spring 소관(경로 B와 동일 원칙 — AI는 표시 필드를 갖지 않는다).
- **EX-CART-5 옵션 스키마 자체 설계**: `optionId`·표시명의 정확한 구조는 Spring(BE) I-2 문서 소관이며(2026-07-18 확정, REQ-CART-025), 본 SPEC은 그 스키마를 소비만 한다.

---

## 3. 용어 (Glossary)

| 용어 | 정의 |
|---|---|
| `CartIntent` | 발화에서 추출된 (상품·옵션·수량) 구조화 의도. 영속 저장 안 됨, 그래프 state 내 일시적 |
| 옵션 되물음(option re-ask) | 옵션 필수 상품에 `optionId` 없이 담으려 할 때, I-2가 `400 CART_OPTION_REQUIRED`를 반환하면 `token` 텍스트로 옵션을 재질문하는 멀티턴 |
| 서비스 토큰(`X-Internal-Token`) | AI→Spring internal 그룹 호출의 인증 레인. 사용자 JWT 포워딩과 다름(§2.3 레인 c) |
| 합산 안내 | 담기 전 I-18로 기존 보유를 조회해 "이미 담겨 있어 N개로 늘렸어요"류 문구를 생성하는 것. 합산 자체의 실행 권위는 Spring |
| degrade(조회 실패) | I-18 조회가 실패해도 I-2 담기는 정상 진행하는 정책 — 조회는 안내용일 뿐 담기의 전제조건이 아님 |
| `last_reco` | 스레드 스코프로 보관하는 직전 추천 후보 목록(`productId`, `name`). 경로 B에서 "그거 담아줘" 같은 발화의 `productId`를 해소하는 유일한 소스 — 신규 담기는 이 목록에 있는 productId만 허용한다 |

---

## 4. 관련 결정 참조 (Related Decisions)

| 결정/근거 | 내용 | 본 SPEC 반영 |
|---|---|---|
| 결정 7 | 장바구니는 AI가 의도만 확정, 실행은 Spring 위임 | 본 SPEC 전체, EX-CART-1 |
| 결정 8 (개정 필요) | 원래 "장바구니·구매는 회원 전용"이었으나 BE I-2 문서(2026-07-10)로 게스트 담기 허용 확정 — 결정 8 개정 레코드 필요(api-spec §8 항목7, 아직 미등록) | REQ-CART-040~042, §9 OPEN-CART-3 |
| api-spec §4.1 | I-2 요청/응답/오류 코드 계약 | §5.2, §6.2/6.3 |
| api-spec §4.9 | I-18 요청/응답 계약, 두 용도(질의 응답·보유 확인) | §5.2, §6.4/6.5 |
| api-spec §3.1 (3) | SSE `action` 이벤트 페이로드 | §5.3, §6.6 |
| api-spec §2.3/§2.6 | 신원은 JWT `sub` 도출, 요청 본문 신뢰 금지(IDOR 방지) | REQ-CART-002 |

---

## 5. 인터페이스 정의 (Interface Definitions)

### 5.1 `CartIntent` (내부, 비영속)

```python
class CartIntent(BaseModel):
    product_id: int
    option_id: int | None = None
    quantity: int = 1   # 1~99
```

### 5.2 I-2 / I-18 요청·응답 (api-spec §4.1/§4.9)

```python
# I-2 요청 (AI → Spring)
class AddToCartRequest(BaseModel):
    user_id: int | None       # userId, guestId 중 정확히 하나만 채워짐
    guest_id: str | None
    product_id: int
    option_id: int | None
    quantity: int

class AddToCartResponse(BaseModel):
    success: bool
    data: dict   # { "cartItemId": int } — 성공 시

# I-18 응답 (AI → Spring)
class CartItem(BaseModel):
    cart_item_id: int
    product_id: int
    product_name: str | None      # 필수 포함 확정(BE 2026-07-18, REQ-CART-036)
    option_id: int | None
    option_name: str | None       # 필수 포함 확정(BE 2026-07-18, REQ-CART-036)
    quantity: int
    price: float | None
```

### 5.3 SSE `action` 페이로드 (FE 대면, camelCase)

```python
# 성공
{ "type": "action", "data": { "type": "CART_ADDED", "cartItemId": int } }

# 실패
{ "type": "action", "data": { "type": "CART_ADD_FAILED", "reason": str } }
# reason 허용값은 api-spec §4.1을 따른다 — SPEC에 값을 직접 중복 기재하지 않음(2026-07-19)
```

---

## 6. 기능 요구사항 (Functional Requirements — EARS)

> 공통 규약(HARD): 수량 상한(1~99)·재시도 횟수·타임아웃 등 모든 튜너블은 `core/config.py`에서 config 주입한다 — 하드코딩 금지.

### 6.1 의도 추출 (intent extraction, REQ-CART-001~007)

- **REQ-CART-001** (Event-Driven): **When** intent router가 사용자 발화를 장바구니 의도로 분류하면, the cart 서브그래프 **shall** 발화에서 (상품·옵션·수량)을 `CartIntent`로 추출한다.
- **REQ-CART-001a** (Unwanted, 경로 B, 2026-07-19): **If** 추출된 `product_id`가 직전 추천 결과(스레드 범위 `last_reco`)에 없으면, **then** the cart 서브그래프 **shall** I-2를 호출하지 않고 "추천을 먼저 받아보시면 담아드릴게요"류 안내로 종결한다 — 경로 B(SSE에 상품 카드 미탑재)라 LLM이 발화 속 임의 숫자를 상품으로 오추출해 추천되지 않은 상품을 담는 것을 방지한다(옵션 되물음 진행 중인 `pending`은 이미 검증된 상품이라 예외).
- **REQ-CART-002** (Ubiquitous): The cart 서브그래프 **shall** 신원(`userId` 또는 `guestId`)을 요청 본문이 아니라 AI가 검증한 JWT `sub` 클레임에서 도출한다 — FE가 보낸 신원 값을 신뢰하지 않는다(IDOR 방지, api-spec §2.3).
- **REQ-CART-003** (Optional): **Where** 사용자가 수량을 명시하지 않으면, the cart 서브그래프 **shall** 기본 수량 1로 처리한다.
- **REQ-CART-004** (Unwanted): The cart 서브그래프 **shall not** config 상한(기본 1~99) 밖의 수량으로 I-2를 호출하지 않는다.
- **REQ-CART-005** (State-Driven): **While** Case 3(다중 니즈) 상황에서 여러 상품을 담아야 하는 동안, the cart 서브그래프 **shall** 상품별로 I-2를 **개별 반복 호출**한다 — 묶음 담기 API는 없다(단건 계약).
- **REQ-CART-006** (Ubiquitous): The cart 서브그래프 **shall** 항목별 성공/실패를 자연히 분리해, 각 담기 시도마다 개별 `action` 이벤트를 emit한다(REQ-CART-005 연계).
- **REQ-CART-007** (Ubiquitous, 2026-07-17 확정): 의도 추출 **shall** **Claude Haiku 4.5**를 사용한다 — `decompose`(추천 서브그래프)와 동일한 경량 구조화 추출 성격(복잡한 추론·종합 판단 불필요)이며, 2-tier 모델 배정 원칙(결정 5)을 따른다. 모델 식별자는 `core/config.py` 주입(하드코딩 금지, 향후 모델 교체 시 config만 변경).

### 6.2 담기 실행 (add to cart, REQ-CART-010~016)

- **REQ-CART-010** (Event-Driven): **When** `CartIntent`가 확정되면, the cart 서브그래프 **shall** `POST /internal/cart/items`(I-2)를 `X-Internal-Token` 서비스 토큰으로 호출한다(api-spec §4.1).
- **REQ-CART-011** (Ubiquitous): The cart 서브그래프 **shall not** 장바구니 데이터에 직접 write하지 않는다 — 담기 실행·검증·수량 합산은 전적으로 Spring이 수행한다(결정 7, EX-CART-1).
- **REQ-CART-012** (Event-Driven): **When** I-2가 200 성공을 반환하면, the cart 서브그래프 **shall** 응답의 `cartItemId`를 사용해 SSE `action`(`CART_ADDED`)을 emit한다.
- **REQ-CART-013** (Event-Driven): **When** I-2가 `404 PRODUCT_NOT_FOUND`를 반환하면, the cart 서브그래프 **shall** `action`(`CART_ADD_FAILED`, `reason: "PRODUCT_NOT_FOUND"`)을 emit한다.
- **REQ-CART-014** (Event-Driven): **When** I-2가 `401 INTERNAL_TOKEN_INVALID`를 반환하면, the cart 서브그래프 **shall** 이를 운영 오류로 처리해 사용자에게는 `action`(`CART_ERROR`)로 안내하고, 서버 로그/알림을 남긴다 — 내부 원인(토큰 불일치)을 사용자에게 노출하지 않는다.
- **[폐기, v0.15.5] REQ-CART-015**: 담기(I-2) 시점에는 재고 부족 코드 자체가 존재하지 않는다 — 재고 차감·품절 판정은 주문(O-1) 시점에만 일어난다. 본 요구사항은 더 이상 유효하지 않다(EX-CART-2 폐기 연계).
- **REQ-CART-016** (Ubiquitous): The cart 서브그래프 **shall** AI→Spring 호출에 공통 인프라 타임아웃(3s)을 적용한다(mvp-plan.md §0).

### 6.3 옵션 되물음 멀티턴 (option re-ask, REQ-CART-020~025)

- **REQ-CART-020** (Event-Driven): **When** I-2가 `400 CART_OPTION_REQUIRED`(옵션 목록 포함)를 반환하면, the cart 서브그래프 **shall** 실패 `action`을 emit하지 **않고**, `token` 텍스트로 옵션을 재질문한다(예: "어떤 색상으로 담을까요?").
- **REQ-CART-021** (Event-Driven): **When** 사용자가 다음 턴에서 옵션을 답하면, the cart 서브그래프 **shall** 그 답을 `optionId`로 해석해 I-2를 재호출한다(재담기).
- **REQ-CART-022** (Event-Driven): **When** I-2가 `400 CART_OPTION_INVALID`(옵션이 해당 상품 소속 아님)를 반환하면, the cart 서브그래프 **shall** options 목록을 다시 제시하며 **1회 재시도**로 되물음한다.
- **REQ-CART-023** (Unwanted): **If** `CART_OPTION_INVALID` 되물음 재시도(REQ-CART-022) 후에도 실패하면, **then** the cart 서브그래프 **shall** 반복 재시도하지 않고 `action`(`CART_ADD_FAILED`, `reason: "CART_ERROR"`)으로 종료한다.
- **REQ-CART-024** (Ubiquitous): 되물음 중인 상태(대상 상품·옵션 후보 목록)는 그래프 state(thread checkpointer)에 임시 보관하며, 프로필 store에 영속하지 **않는다**.
- **REQ-CART-025** (Ubiquitous, 2026-07-19 확정): options 목록 **shall** api-spec §4.1이 정의하는 구조를 따른다(정확한 필드는 본 SPEC에 중복 기재하지 않음). 되물음 문구 생성은 그 구조의 표시명 필드(`name`)를 사용하며, 형식 이상 항목은 방어적으로 건너뛴다.

### 6.4 담기 전 보유 조회 (pre-add lookup, REQ-CART-030~033)

- **REQ-CART-030** (Optional): **Where** 담기 실행 전 기존 보유 확인이 가능하면, the cart 서브그래프 **shall** `GET /internal/cart`(I-18)를 호출해 동일 상품·옵션 보유 여부를 조회한다.
- **REQ-CART-031** (Event-Driven): **When** I-18 조회 결과 동일 상품·옵션이 이미 존재하면, the cart 서브그래프 **shall** "이미 담겨 있어 N개로 늘렸어요"류 안내 문구를 생성한다 — 단 **합산의 실행 권위는 Spring**이며, AI는 수량을 직접 계산하지 않는다.
- **REQ-CART-032** (Unwanted): **If** I-18 조회가 실패(타임아웃·오류)하면, **then** the cart 서브그래프 **shall** 보유 안내 없이 I-2 담기를 **정상 진행**한다 — 조회 실패가 담기를 막지 **않는다**(degrade).
- **REQ-CART-033** (Ubiquitous): I-18 호출은 담기(I-2) 호출과 **독립적으로 실패해도 무방**하며, 순서상 담기 전에 시도하되 필수 선행 조건은 아니다.

### 6.5 장바구니 질의 응답 (cart query, REQ-CART-034~036)

- **REQ-CART-034** (Event-Driven): **When** 사용자가 "장바구니에 뭐 있어?"류 발화를 하면, the cart 서브그래프 **shall** `GET /internal/cart`(I-18)를 조회하고 결과를 **별도 SSE 이벤트 없이 `token` 텍스트**로 자연어 응답한다.
- **REQ-CART-035** (State-Driven): **While** 장바구니가 비어 있는 동안, the cart 서브그래프 **shall** 오류가 아닌 정상 응답으로 "장바구니가 비어 있다"는 취지를 안내한다(`items: []`는 200 정상).
- **REQ-CART-036** (Ubiquitous, 2026-07-19 확정): 자연어 응답 생성에는 `productName`(상품명)이 필요하며, I-18 응답에 `productName`/`optionName`이 **필수 포함**됨이 BE 문서로 확정됐다(2026-07-18).

### 6.6 게스트 처리 (guest handling, REQ-CART-040~042)

- **REQ-CART-040** (State-Driven): **While** 사용자 역할(`role`)이 `guest`인 동안, the cart 서브그래프 **shall** `guestId`로 I-2/I-18을 호출해 담기·조회를 정상 허용한다 — 게스트 차단(`GUEST_NOT_ALLOWED`)은 **폐기**됐다(2026-07-10 BE 개정, api-spec §4.1).
- **REQ-CART-041** (Ubiquitous): 로그인 유도는 결제 시점에 **FE가 담당**하며, 본 서브그래프는 담기 단계에서 로그인을 요구하지 **않는다**.
- **REQ-CART-042** (Ubiquitous): 게스트 담기 허용은 기존 "장바구니·구매는 회원 전용" 결정(결정 8)과 상충하므로, 별도 결정 개정 레코드가 필요하다(§9 OPEN-CART-3, api-spec §8 항목7) — 본 SPEC은 BE 확정 계약을 따르되 이 개정 자체는 본 SPEC 범위가 아니다.

### 6.7 결과 통지 (SSE action, REQ-CART-050~052)

- **REQ-CART-050** (Ubiquitous): The cart 서브그래프 **shall** 담기 결과를 SSE `action` 이벤트로 통지하며, 이벤트 타입은 `CART_ADDED` | `CART_ADD_FAILED` 2종으로 고정한다(api-spec §3.1 (3)).
- **REQ-CART-051** (Ubiquitous, 2026-07-19 개정): `CART_ADD_FAILED`의 `reason` 필드 **shall** api-spec §4.1이 정의하는 허용 값 집합 안에서만 채워진다 — 정확한 값 목록은 본 SPEC에 중복 기재하지 않고 api-spec을 단일 소스로 따른다(리뷰 반영).
- **REQ-CART-052** (Unwanted): 옵션 되물음(REQ-CART-020)과 장바구니 질의 응답(REQ-CART-034)은 `action` 이벤트를 **사용하지 않는다** — `token` 텍스트로만 처리한다(REQ-CART-020/034 재확인).

---

## 7. 오류 처리 (Error Handling)

| 실패 지점 | 감지 | 처리 | 안전 불변식 |
|---|---|---|---|
| I-2 `CART_OPTION_REQUIRED` | 400 응답 | 되물음 멀티턴(REQ-CART-020) | 실패 action emit 금지 |
| I-2 `CART_OPTION_INVALID` | 400 응답 | 되물음 1회 재시도 후 실패 시 CART_ERROR(REQ-CART-022/023) | 무한 재시도 금지 |
| I-2 `PRODUCT_NOT_FOUND` | 404 응답 | `action`(CART_ADD_FAILED, PRODUCT_NOT_FOUND) | 후보 날조 금지 |
| I-2 `INTERNAL_TOKEN_INVALID` | 401 응답 | `action`(CART_ERROR) + 서버 로그, 내부 원인 미노출 | 사용자에 내부 오류 상세 노출 금지 |
| I-2 재고 부족 | — | **해당 없음** — 담기 재고검증 없음, `OUT_OF_STOCK` 폐기(v0.15.5) | 담기는 재고로 실패하지 않음 |
| I-18 조회 실패 | 타임아웃/오류 | 보유 안내 생략, 담기는 정상 진행(degrade, REQ-CART-032) | 조회 실패가 담기를 막지 않음 |
| I-18 `CART_QUERY_INVALID` | 400 응답(userId/guestId 둘 다 없거나 둘 다 있음) | REQ-CART-002(신원은 항상 JWT `sub`에서 도출)를 따르면 설계상 발생하지 않아야 하는 상태 — 발생 시 I-18 실패로 취급해 degrade(REQ-CART-032) | 신원 도출 로직 결함의 조기 발견 신호로만 사용, 사용자 노출 없음 |
| AI→Spring 타임아웃(3s) | 공통 인프라 | 재시도 정책은 공통 인프라 원칙(MoAI 3회) 따름 | — |

---

## 8. 인수 기준 (Acceptance Criteria)

- **AC-CART-01 (담기 해피패스)**: **Given** 옵션 없는 상품과 로그인 사용자, **When** "이거 담아줘" 발화, **Then** I-2가 단건 호출되고 성공 시 `action`(`CART_ADDED`, `cartItemId`)이 emit된다(REQ-CART-010/012).
- **AC-CART-02 (옵션 되물음 → 재담기)**: **Given** 옵션 필수 상품, **When** 옵션 없이 담기 시도, **Then** 실패 `action` 없이 `token`으로 재질문하고, 사용자가 옵션을 답하면 `optionId`로 해석해 재담기가 성공한다(REQ-CART-020/021).
- **AC-CART-03 (옵션 무효 1회 재시도)**: **Given** 잘못된 옵션 매칭, **When** `CART_OPTION_INVALID` 반복, **Then** 1회 재시도 후 `action`(`CART_ADD_FAILED`, `CART_ERROR`)로 종료한다(REQ-CART-022/023).
- **AC-CART-04 (게스트 담기 허용)**: **Given** `role == guest`, **When** 담기 시도, **Then** `guestId`로 I-2가 호출되고 차단 없이 성공한다(REQ-CART-040).
- **AC-CART-05 (조회 실패 degrade)**: **Given** I-18 강제 실패, **When** 담기 시도, **Then** 보유 안내 없이 I-2 담기는 정상 진행된다(REQ-CART-032).
- **AC-CART-06 (묶음 담기 개별 호출)**: **Given** Case 3 다중 상품, **When** 여러 상품을 한 번에 담아야 하는 상황, **Then** 상품별로 I-2가 개별 호출되고 항목별 `action`이 각각 emit된다(REQ-CART-005/006).
- **AC-CART-07 (장바구니 질의 응답)**: **Given** 담긴 상품이 있는 상태, **When** "장바구니에 뭐 있어?" 발화, **Then** 별도 이벤트 없이 `token`으로 목록이 자연어 응답된다(REQ-CART-034).
- **AC-CART-08 (신원 IDOR 방지)**: **Given** 임의의 담기/조회 요청, **When** 처리되면, **Then** `userId`/`guestId`는 요청 본문이 아니라 JWT `sub`에서 도출된 값이다(REQ-CART-002).
- **AC-CART-09 (수량 상한)**: **Given** config 수량 상한(1~99), **When** 범위 밖 수량이 추출되면, **Then** I-2 호출 전 상한 내로 처리된다(REQ-CART-004).
- **AC-CART-10 (추천 외 상품 담기 차단)**: **Given** 직전 추천 목록에 없는 `productId`, **When** 담기 시도, **Then** I-2를 호출하지 않고 안내 문구로 종결한다(REQ-CART-001a).

### Definition of Done

- [ ] REQ-CART-001~052 전 항목이 테스트로 커버됨.
- [ ] AC-CART-01~10 전 시나리오가 통과(pytest, Spring 목(mock) 또는 계약 테스트).
- [ ] `CartIntent`/`AddToCartRequest`/`CartItem`/SSE `action` 스키마가 Pydantic 모델로 구현되고 스키마 계약 테스트 존재.
- [ ] 하드 불변식(AI 직접 write 금지, 신원 JWT 도출, 되물음 시 action 미emit, 조회 실패 degrade, 묶음은 개별 호출) 회귀 테스트 존재.
- [ ] §9의 미해결 항목이 후속 Spring 협의/이슈로 등록됨.
- [x] 재고 코드(`OUT_OF_STOCK`) 폐기, options 스키마, productName/optionName 포함 여부 — 전부 확정 반영 완료(§9 참조).

---

## 9. 미해결 / 후속 항목 (Open Questions & Follow-ups)

- **OPEN-CART-3 (결정 8 개정 필요)**: 게스트 담기 허용이 기존 "장바구니·구매는 회원 전용" 결정과 상충 — 별도 개정 결정 레코드 필요(api-spec §8 항목7, 아직 미등록으로 보임).
- **OPEN-CART-6 (서비스 토큰 발급·교환 방식)**: `X-Internal-Token`의 발급/교환 절차가 미확정.

**[v0.2.0 해소] OPEN-CART-4 (의도 추출 모델 배정)**: Claude Haiku 4.5로 확정(REQ-CART-007) — `decompose`와 동일한 2-tier 배정 원칙(결정 5) 적용.

**[해소] OPEN-CART-5 (`productId` 타입)**: 숫자(BIGINT)로 확정(2026-07-18, S-1 연계) — 전 구간 string 통일 원칙은 폐기.

**[해소, v0.15.5] OPEN-CART-1 (재고 코드 부재)**: `OUT_OF_STOCK` 폐기 확정 — 담기(I-2) 시점 재고 검증 자체가 없고, 재고 차감·품절 판정은 주문(O-1) 시점에만 발생한다(BE Notion I-2·C-2 오류코드에도 부재 확인).

**[해소] OPEN-CART-2 (옵션·조회 응답 스키마)**: `CART_OPTION_REQUIRED`의 options 목록은 `{optionId, name, extraPrice}`로(BE 2026-07-18, api-spec §4.1), I-18 응답의 `productName`/`optionName`은 필수 포함으로 각각 확정됐다(BE I-18 문서, 2026-07-18) — REQ-CART-025/036 갱신.

---

## 비기능 요구사항 (Non-Functional Requirements)

- **지연**: AI→Spring 호출(I-2/I-18)은 공통 인프라 타임아웃 3s를 따른다. 장바구니 흐름은 LLM 호출이 의도 추출 1회뿐이라 추천 파이프라인보다 지연 예산이 가볍다.
- **비용 가드**: 의도 추출은 요청당 1회(추정 Haiku, OPEN-CART-4). 되물음 재시도(REQ-CART-022)는 최대 1회로 제한 — 무한 재시도 금지.
- **안전/일관성 불변식(must-hold)**:
  - AI는 장바구니 데이터에 직접 write하지 않는다(REQ-CART-011).
  - 신원은 항상 JWT `sub` 도출, 요청 본문 신뢰 금지(REQ-CART-002).
  - 되물음·질의 응답은 `action` 이벤트를 쓰지 않는다(REQ-CART-052).
  - I-18 조회 실패가 I-2 담기를 막지 않는다(REQ-CART-032).
  - 묶음 담기는 항상 상품별 개별 호출(REQ-CART-005).
- **개인정보**: 신원 도출 원칙은 프로젝트 전역 규칙(요청 본문 신뢰 금지)과 동일하게 적용한다.

---

## 참조 (References)

- [`PRD-RECOMMEND-PROFILE-AGENT.md`](../PRD-RECOMMEND-PROFILE-AGENT.md) §3.3/§5.4/§7/§10-F — 본 SPEC의 직접 입력
- [`api-spec.md`](../api-spec.md) §3.1 (3)/§4.1/§4.9/§8 항목7 — 외부 계약 정본
- [`mvp-plan.md`](../mvp-plan.md) §2, [`mvp-todo.md`](../mvp-todo.md) §2 — MVP 범위·체크리스트
- `app/agents/buyer/cart/{graph,state}.py` — 실제 구현(이슈 #3, PR #16) — 본 SPEC의 REQ-CART-001a·`last_reco`는 이 구현체와 대조해 문서화함
- Notion "📡 API 명세서" DB — I-2/I-18 경로·Method·인증 레인 확정 소스(2026-07-16 대조)
- BE 팀 공유 문서(2026-07-18) — 재고(`stock_quantity`) 도입·주문 실패 조건, 장바구니 실패 사유 개수(4종→3종) 확인 요청 근거(OPEN-CART-1)
- BE 팀 공유 문서(2026-07-18, "챗봇 장바구니 조회") — I-18 `productName`/`optionName` 필수 포함, `CART_QUERY_INVALID` 오류 코드, options 스키마 확정 근거(OPEN-CART-2)
