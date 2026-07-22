# SELLER-FINAL — 워크플로우 명세서

> **버전**: v1.0.0 · **기준일**: 2026-07-20 · **상태**: MVP 확정
> 판매자 멀티에이전트 MVP(1~4-3단계) 완료 시점의 **요청 수명주기 정본**.
> 계약 근거: api-spec 사본 v0.14.0 §3.2·§4.4·§4.5, [SPEC-SELLER-001](SPEC-SELLER-001.md), BE·FE 확정(2026-07-17).
>
> 본문의 `F1`·`F6` 은 그 BE·FE 확정 항목 번호다 — **F1**: FE→AI 직접 호출 폐기, 판매자 챗은 Spring 패스스루(S-4). **F6**: 교환 제거(상태 11→9)·배송비 0원·`stock_quantity` 도입(시드 100, CHECK ≥0)·전량 취소 시 `orders.status=CANCELLED` 승격.
> 캐시·RAG(구 4-4)는 post-MVP 확장으로 이동(2026-07-20 사용자 결정) — SELLER-FINAL-ROADMAP 참조.

## 1. 시스템 배치 (F1 — Spring 패스스루)

```
FE ──(JWT)──> Spring ──(CH-6 세션 발급)──┐
                                          │ S-4: POST {AI}/seller/chat
                                          │ X-Internal-Token + X-Seller-Id/X-Brand-Id(🔴 제안)
                                          ▼
                                    FastAPI AI 서버 ──(SSE 무가공 패스스루)──> FE
                                          │
                                          └──(I-6/7/13/14/15/16 집계, I-9~12 CRUD, X-Internal-Token)──> Spring internal
```

- FE→AI 직접 호출 없음(nginx 미노출). AI행 호출은 CH-2·S-4·I-20 뿐, 전부 Spring 발신.
- 신원은 **절대 본문에서 받지 않는다** — Spring 주입 헤더(검증된 메아리 신원)에서만. 토큰 결손/불일치 401, 신원 헤더 결손 400 (`require_seller_internal`).
- AI→Spring 역호출은 전 구간 `X-Internal-Token` + 3s 타임아웃.

## 2. 입구 판정 순서 (`_seller_stream` — 순서가 계약)

| 순서 | 판정 | 방식 | 결과 |
|---|---|---|---|
| ① | confirm (`{"action":"confirm","draftId":"…"}`) | 코드 JSON 파싱, LLM 0회 | HITL 실행 레인(§5) |
| ①.5 | 추천 적용 ("N번 적용해줘" 정형 발화) | 코드 정규식 전체매칭, LLM 0회 | 추천 적용 레인(§6) |
| ② | scope 위반 (경쟁사·타 판매자 등) | 코드 패턴(check_scope), LLM 0회 | 거절 token → done |
| ③ | supervisor 3분기 라우팅 | Haiku t=0 + 코드 후처리 | analysis / product / general |

라우팅 코드 후처리(4-1a): supervisor 장애(예외·타임아웃·비정형) = **general 폴백** + warning /
confidence < 0.6 (`seller_route_confidence_min`) = **analysis 보수 재지정**(원분류 analysis 는 유지).

## 3. analysis 레인 — 분석 파이프라인

```
질문 → [이력 주입] → planner(Haiku) → resolve_plan(코드: 기간 환산) 
     → 워커 팬아웃(asyncio.gather, 1~5종) → report(Sonnet) ⇄ 검증 루프 ≤3회
     → recommend(Sonnet) → compose → save_history → SSE token → done
```

1. **이력 주입(4-3)**: pg-profile 에서 최근 5건을 planner **입력 메시지**에 주입(프롬프트 불변). 조회 실패 시 주입 없이 계속.
2. **planner**: 워커 선택 + 기간 정규 어휘 재표현만. 날짜 환산은 코드(`calc.normalize_period`) — "이번 달" 등 미지원 표현·clarification 은 되묻기 token → done.
3. **워커 5종**(sales_anomaly·conversion·behavior·churn·abuse): Haiku t=0, Spring 집계 콜백 도구만(쓰기 도구 구조적 부재), `AnalysisFinding` 구조화 출력. 진행 token 을 유형별 방출.
4. **검증 루프**: 결정론 검사(수치 정합 등) + judge(Haiku) 채점 21/30 — 판정·재작성 지시는 전부 코드. 루프 소진/LLM 장애 시 마지막 보고서 미달 채택(degrade).
5. **recommend**: `RecommendationSet`(≤5건, **목록 순서 = "N번" 계약**). 실패는 빈 추천 degrade — 보고서를 죽이지 않는다.
6. **save_history**: 질문·유형·기간·보고서 요약(500자)·구조화 추천을 pg-profile 저장. 실패는 warning 후 계속.

**degrade 수렴 3층**: 도구 실패("Error:") → 워커 자신이 degrade finding / 워커 예외·타임아웃 → 코드가 degrade finding, 부분 보고서 계속 / 전 워커 실패 → 사과 token → done. **예외 전파는 2경우만**(planner 장애·1차 보고서 실패) → 사과 token + `error(LLM_TIMEOUT/INTERNAL)`.

## 4. product 레인 — draft 제안 (HITL 스트림 1)

```
질문 → product_agent(Haiku, 조회 도구만 — A안) → DraftProposal
     → validate_draft(코드 선검증) → start_draft(checkpoint 저장 + interrupt)
     → SSE draft{draftId, op, productId, changes[], summary} → done
     → FE: diff 카드 + [적용]/[취소]
```

- **A안 구조**: draft 생성 에이전트는 쓰기 도구를 볼 수 없다 — "발화 ≠ 동의"가 프롬프트가 아니라 구조로 보장.
- **validate_draft**(emit 전 코드 선검증): 수치 캐스팅(콤마·단위 관용)·update/delete 의 productId 필수·update 의 changes 필수·create 필수 3필드(name/price/stockQuantity)·create 의 image_url/status 금지(C4/D3). 불성립 = 되묻기 token(실행 불가능한 draft 를 FE 에 보여주지 않는다).
- draftId·신원·created_at 은 **코드 발급**. clarification 발화(대상 모호 등)는 되묻기 token → done.

## 5. confirm 레인 — HITL 실행 (스트림 2, LLM 0회)

```
confirm{draftId} → 코드 검사: 존재 → 소유(brandId) → 멱등 → TTL(10분)
                → 그래프 resume → I-9 재조회 stale 검증 → I-10/11/12 실행 → token(결과) → done
```

| 검사 | 실패 시 |
|---|---|
| 존재 (checkpoint 조회) | "찾을 수 없음" token → done |
| 소유 (draft.brandId == 요청 신원) | **미존재와 동일 문구**(존재 비노출) — draft 는 살아있음 |
| 멱등 (이미 실행됨) | "이미 처리됨 + 이전 결과" token → done (재실행 0회) |
| TTL (`seller_draft_ttl_minutes`=10) | 만료 안내 token → done |
| stale (before ≠ I-9 현재값, **stock 제외**) | 불일치 필드·현재값 안내 + 되묻기 → done (draft 종결) |
| Spring 장애 | 사과 token + error — **checkpoint 는 interrupt 에 잔존, 재confirm 가능** |

- 실행 주체는 **코드**: checkpoint 의 draft 를 그대로 op별 매핑(update→I-11 PATCH, create→I-10 POST, delete→I-12 DELETE soft). "보여준 diff == 실행하는 쓰기"가 구조로 보장, 실행 시점 LLM 0회.
- stock_quantity 는 주문 재고 차감(F6)으로 자연 변동 → stale 비교 제외, 변동 사실은 결과 안내에 현재값 표기.
- 삭제는 soft(status=HIDDEN) — HITL(그래프) + soft(데이터) 이중 방어.

## 6. 추천 적용 레인 — "N번 적용해줘" (4-3, LLM 0회)

```
"1번 적용해줘" → 정규식 N 추출 → 최신 이력 recommendations[N-1] 조회(재해석 금지)
              → before = I-9 현재값으로 draft 변환 → §4 흐름 합류(checkpoint→draft emit)
              → 이후 confirm 은 §5 와 동일
```

되묻기 경로: 이력 없음 / N 범위 밖(유효 범위 안내) / changes 없는 유형(promotion 등) / 상품 미발견.
비정형 변형 발화("아까 그 두번째 거 적용해줘")는 정규식 미일치 → supervisor→product→clarification 경로가 받는다.

## 7. general 레인

Haiku + 읽기 도구(매출·주문·상품 목록·계산기), 자유 텍스트 astream → token 스트림. 요청마다 재빌드(today 주입 stale 방지). 출력은 mask_output 청크 단위 적용.

## 8. SSE 이벤트 계약 (판매자)

| 이벤트 | 페이로드 | 비고 |
|---|---|---|
| `token` | `{text}` | 진행 안내·보고서·되묻기·결과 전부 |
| `draft` | `{draftId, op, productId(int\|null), changes[{field,before,after}], summary}` | 스트림당 최대 1회 |
| `done` | `{finishReason:"stop"}` | 정상 종료 유일 신호 |
| `error` | `{code, message}` | code ∈ LLM_TIMEOUT/LLM_UNAVAILABLE/INTERNAL |

와이어는 camelCase. 상품 카드는 SSE 에 싣지 않는다(경로 B — 구매자 파트).
