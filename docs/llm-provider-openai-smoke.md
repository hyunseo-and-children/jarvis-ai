# LLM Provider 전환 실트래픽 스모크 (OpenAI) — 이슈 #40

`LLM_PROVIDER=openai` 로 전환 후 구매자 추천 흐름(`run_buyer_turn`)을 **실제 OpenAI 호출**로
한 번 돌린 수동 검증 기록. 검색 백엔드만 fake(고정 카탈로그)로 대체하고, LLM(decompose·rerank)은
실 API 로 호출했다. (CI 스모크는 항상 fake 경로 — 본 기록은 라이브 1회 확인용.)

- 일자: 2026-07-20
- 브랜치: `feat/llm-provider-toggle`
- provider: `openai` · 어댑터: `OpenAILLM`
- tier 매핑: **fast = `gpt-5-nano`** (decompose) / **smart = `gpt-5.6-luna`** (rerank)
- 키: project-scoped(`sk-proj…`), 사전 프로브에서 fast·smart 모두 `{"ok": true}` 응답 확인

## 시나리오

- 입력: `"5만원 이하 무선 이어폰 추천해줘"`
- 고정 카탈로그(fake search): `[(101, 이어폰A, 39000), (102, 이어폰B, 48000), (103, 이어폰C, 29000)]`
- push: no-op(성공)

## 결과

- 소요: **9.09s** (nano decompose + luna rerank, 네트워크 포함)
- SSE 이벤트 시퀀스: `conditions → products.ready → done`

### decompose (gpt-5-nano, fast)
조건 칩 정확 추출:
- `category · 무선 이어폰`
- `50,000원 이하 (priceMax=50000)`

### rerank (gpt-5.6-luna, smart)
근거문(token 스트림):
> 세 상품 모두 5만원 이하의 무선 이어폰입니다. 평점과 가격을 함께 고려해 101, 102, 103 순으로 추천합니다.

- `products.ready` listId 발급, `done.finishReason = "stop"` 정상 종료.

## 판정

✅ provider 토글 + tier 추상화가 **실 OpenAI 경로에서 E2E 동작**.
✅ 모델 문자열 `gpt-5-nano` / `gpt-5.6-luna` 유효(응답·JSON 파싱 정상).
✅ `complete` JSON 강제(response_format=json_object) + tier별 reasoning_effort 경로 실호출 검증.

계약(api-spec)·SSE 이벤트 규약 변경 없음 — 순수 내부 구현. Anthropic 경로는 기존 동작 그대로 보존.

## 2026-07-21 후속 수정

연결 probe의 `max_tokens=64`는 visible output과 hidden reasoning이 공유하는 completion
예산이라 `gpt-5-nano + low`에서 본문이 비는 현상이 재현됐다. GPT-5 nano는 `none`을
지원하지 않으므로 fast tier 기본값을 최저 지원값인 `minimal`로 낮추고, probe 예산을
256으로 늘렸다. 추적 이슈: #57.
