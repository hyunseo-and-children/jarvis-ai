# Seller LLM Provider Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 판매자 LangGraph 에이전트가 기본 OpenAI 또는 Anthropic을 `LLM_PROVIDER`로 선택하고, 구매자와 동일한 provider/tier 해석 및 `LLM_UNAVAILABLE` degrade 규칙을 사용하도록 만든다.

**Architecture:** 역할은 provider 모델명이 아닌 `fast`/`smart` tier만 선택한다. `app/core/llm.py`의 순수 resolver가 Settings에서 provider·model ID·API key·reasoning effort를 해석하고, buyer wrapper·buyer telemetry·seller `BaseChatModel` factory가 그 결과를 공유한다. Seller factory는 OpenAI에 `reasoning_effort`만, Anthropic에 기존 temperature만 전달하며 명시적 `ToolStrategy`는 유지한다.

**Tech Stack:** Python 3.12, Pydantic Settings, LangChain 1.3.14, langchain-openai 1.3.5, langchain-anthropic 1.4.8, LangGraph 1.2.9, FastAPI SSE, pytest, ruff

## Global Constraints

- 계약 정본 `docs/api-spec.md`의 엔드포인트·SSE 이벤트·필드·오류 코드는 변경하지 않는다. 기존 허용 코드 `LLM_UNAVAILABLE`을 seller에 올바르게 적용한다.
- 내부 모델 정책을 바꾸기 전에 `docs/specs/SPEC-SELLER-001.md`를 provider 중립 `fast`/`smart` 기준으로 먼저 개정한다.
- 와이어 포맷은 camelCase, 신원은 JWT에서만 도출하며 이 작업은 요청/응답 스키마를 변경하지 않는다.
- provider는 운영 설정이며 요청별 runtime-configurable field로 노출하지 않는다.
- OpenAI에는 `temperature` 키를 전달하지 않고 tier별 `reasoning_effort`를 전달한다. Anthropic에는 기존 temperature를 전달하고 `reasoning_effort`를 전달하지 않는다.
- Structured output은 provider 간 동작 정합을 위해 기존 명시적 `ToolStrategy`를 유지한다.
- 신규 의존성은 추가하지 않는다.
- 모든 동작 변경은 실패 테스트를 먼저 실행한 뒤 최소 구현으로 통과시킨다.

---

### Task 1: Provider-neutral seller specification

**Files:**
- Modify: `docs/specs/SPEC-SELLER-001.md`
- Modify: `docs/specs/SELLER-FINAL-WORKFLOW.md`
- Modify: `docs/specs/SELLER-FINAL-TECH.md`
- Modify: `docs/specs/SELLER-FINAL-RISKS.md`
- Modify: `docs/specs/SELLER-FINAL-ROADMAP.md`

**Interfaces:**
- Consumes: api-spec §2.9/§3.2의 기존 `LLM_TIMEOUT`·`LLM_UNAVAILABLE` 계약
- Produces: 역할→`fast`/`smart`, OpenAI/Anthropic별 생성 파라미터 정책

- [ ] **Step 1: SPEC 버전을 v1.1.0으로 올리고 모델 배정 표를 provider 중립으로 개정한다**

  역할 표에 `product_agent`를 포함하고 다음 정책을 명시한다.

  ```text
  fast: supervisor, planner, worker, judge, product
  smart: report, recommend
  OpenAI: temperature 미전달, fast=minimal, smart=medium
  Anthropic: fast temperature=0.0, smart temperature=0.2
  ```

- [ ] **Step 2: seller 현재 상태 문서의 Haiku/Sonnet 전용 표현을 tier/provider 표현으로 동기화한다**

  워크플로우는 `fast`/`smart`를 주 표기로 쓰고 괄호 안에 provider별 실제 모델을 설명한다. 실 LLM 미검증 리스크는 Anthropic 단일 키가 아니라 provider별 스트리밍·ToolStrategy 스모크 필요로 바꾼다.

- [ ] **Step 3: 문서에 남은 seller Anthropic 전용 표현을 검색한다**

  Run:
  ```bash
  rg -n -i "Anthropic 2-tier|Haiku t=0|Sonnet t=0\.2|ANTHROPIC_API_KEY 미발급" docs/specs/SPEC-SELLER-001.md docs/specs/SELLER-FINAL-*.md
  ```

  Expected: provider 정책을 설명하기 위한 의도적 언급 외에 단일-provider 전제 없음.

### Task 2: Shared provider/tier resolver

**Files:**
- Modify: `tests/unit/test_llm_provider.py`
- Modify: `app/core/config.py`
- Modify: `app/core/llm.py`
- Modify: `app/agents/buyer/graph.py`
- Modify: `app/agents/buyer/recommendation/graph.py`

**Interfaces:**
- Produces: `LLMProvider`, `ModelTier`, `ResolvedModel`, `LLMNotConfigured`, `resolve_provider_model(settings, tier)`
- Consumes: `Settings.llm_provider`, provider별 모델 ID/API key/reasoning effort

- [ ] **Step 1: resolver 계약 실패 테스트를 작성한다**

  `tests/unit/test_llm_provider.py`에 다음을 고정한다.

  ```python
  resolved = resolve_provider_model(
      _settings(llm_provider="openai", openai_api_key="openai-key"),
      "fast",
  )
  assert resolved.provider == "openai"
  assert resolved.model_id == "gpt-5-nano"
  assert resolved.api_key == "openai-key"
  assert resolved.reasoning_effort == "minimal"
  ```

  Anthropic fast/smart, OpenAI smart, 잘못된 tier, 활성 provider 키 누락도 별도 테스트로 작성한다. 키 누락은 `LLMNotConfigured`를 기대한다.

- [ ] **Step 2: resolver 테스트가 현재 코드에서 실패하는지 확인한다**

  Run:
  ```bash
  uv run pytest -q tests/unit/test_llm_provider.py
  ```

  Expected: `resolve_provider_model`/`LLMNotConfigured` import 또는 동작 부재로 FAIL.

- [ ] **Step 3: Settings provider 타입과 resolver를 최소 구현한다**

  `app/core/config.py`:

  ```python
  LLMProvider = Literal["openai", "anthropic"]
  llm_provider: LLMProvider = "openai"
  ```

  `app/core/llm.py`:

  ```python
  ModelTier = Literal["fast", "smart"]

  @dataclass(frozen=True)
  class ResolvedModel:
      provider: LLMProvider
      tier: ModelTier
      model_id: str
      api_key: str = field(repr=False)
      reasoning_effort: str | None = None

  class LLMNotConfigured(LLMError):
      pass

  def resolve_provider_model(settings: Settings, tier: ModelTier) -> ResolvedModel:
      ...
  ```

  잘못된 tier는 `LLMError`, 활성 provider 키 누락은 `LLMNotConfigured`로 구분한다.

- [ ] **Step 4: buyer factory와 telemetry를 resolver에 연결한다**

  `get_llm()`은 fast/smart 두 결과를 해석해 기존 `AnthropicLLM`/`OpenAILLM`을 만들고 `LLMNotConfigured`일 때 기존 계약대로 `None`을 반환한다. `Settings.model_for_tier()` 중복 매핑을 제거하고 buyer observer는 `resolve_provider_model(settings, tier).model_id`를 기록한다.

- [ ] **Step 5: resolver 및 buyer provider 테스트를 통과시킨다**

  Run:
  ```bash
  uv run pytest -q tests/unit/test_llm_provider.py tests/unit/test_recommendation.py
  ```

  Expected: PASS.

### Task 3: Seller BaseChatModel provider adapter

**Files:**
- Modify: `tests/unit/test_seller_models.py`
- Modify: `app/agents/seller/models.py`

**Interfaces:**
- Consumes: `resolve_provider_model(settings, tier)`
- Produces: provider-aware `init_seller_model(role) -> BaseChatModel`

- [ ] **Step 1: SDK를 호출하지 않는 provider matrix 실패 테스트를 작성한다**

  `init_chat_model`을 recorder로 monkeypatch하여 다음을 검증한다.

  ```python
  assert ROLE_TIER["supervisor"] == "fast"
  assert ROLE_TIER["report"] == "smart"
  assert call["model_provider"] == "openai"
  assert call["model"] == settings.openai_fast_model_id
  assert call["reasoning_effort"] == "minimal"
  assert "temperature" not in call
  ```

  Anthropic fast/smart는 각각 기존 temperature를 받고 `reasoning_effort`가 없어야 한다. OpenAI smart는 `medium`을 받아야 한다. 키 누락은 SDK recorder 호출 없이 `LLMNotConfigured`를 발생시켜야 한다.

- [ ] **Step 2: seller model 테스트가 실패하는지 확인한다**

  Run:
  ```bash
  uv run pytest -q tests/unit/test_seller_models.py
  ```

  Expected: `ROLE_TIER` 값과 hard-coded Anthropic 호출 인자가 기대와 달라 FAIL.

- [ ] **Step 3: provider-aware cached factory를 최소 구현한다**

  캐시 키는 다음 실효 설정 전체를 받는다.

  ```python
  _cached_model(
      provider,
      model_id,
      api_key,
      temperature,
      reasoning_effort,
      timeout,
      max_retries,
  )
  ```

  OpenAI 분기에서는 `temperature` 키를 만들지 않고 Anthropic 분기에서는 `reasoning_effort` 키를 만들지 않는다. `init_chat_model(model=..., model_provider=...)`로 명시한다.

- [ ] **Step 4: seller model 테스트를 통과시킨다**

  Run:
  ```bash
  uv run pytest -q tests/unit/test_seller_models.py tests/unit/test_seller_workers.py
  ```

  Expected: PASS.

### Task 4: Seller LLM configuration error mapping

**Files:**
- Modify: `tests/unit/test_seller_router.py`
- Modify: `tests/unit/test_seller_api.py`
- Modify: `app/agents/seller/orchestrator.py`
- Modify: `app/api/seller.py`

**Interfaces:**
- Consumes: `LLMNotConfigured`
- Produces: seller SSE `error.data.code == "LLM_UNAVAILABLE"`

- [ ] **Step 1: supervisor가 설정 오류를 general fallback으로 삼키지 않는 실패 테스트를 작성한다**

  ```python
  _patch(monkeypatch, _StubSupervisor(exc=LLMNotConfigured("openai")))
  with pytest.raises(LLMNotConfigured):
      _route()
  ```

- [ ] **Step 2: seller 통합 스트림의 미구성 오류 실패 테스트를 작성한다**

  `route_question`이 `LLMNotConfigured`를 던지게 하고 `_seller_stream` 결과가 `error` 이벤트이며 code가 `LLM_UNAVAILABLE`인지 검증한다. 일반 `RuntimeError` 빌드 실패가 계속 `INTERNAL`인 기존 테스트도 보존한다.

- [ ] **Step 3: 새 오류 테스트가 실패하는지 확인한다**

  Run:
  ```bash
  uv run pytest -q tests/unit/test_seller_router.py tests/unit/test_seller_api.py
  ```

  Expected: 설정 오류가 general fallback 또는 uncaught 예외가 되어 FAIL.

- [ ] **Step 4: 설정 오류만 선택적으로 재전파하고 SSE에 매핑한다**

  `route_question()`:

  ```python
  except LLMNotConfigured:
      raise
  except Exception:
      return general_fallback
  ```

  `_seller_stream()`은 supervisor 호출에서 이를 잡아 기존 계약의 `LLM_UNAVAILABLE` error를 내보내고 종료한다. `_general_stream()` 등 직접 빌드 경계에서도 같은 전용 예외는 `INTERNAL`보다 먼저 처리한다.

- [ ] **Step 5: seller router/API 테스트를 통과시킨다**

  Run:
  ```bash
  uv run pytest -q tests/unit/test_seller_router.py tests/unit/test_seller_api.py
  ```

  Expected: PASS.

### Task 5: Documentation and verification

**Files:**
- Modify: `app/agents/seller/workers.py`
- Modify: `app/agents/seller/schemas.py`
- Modify: `app/agents/seller/orchestrator.py`
- Modify: `app/agents/seller/pipeline.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: completed provider-neutral implementation
- Produces: current code behavior matching documentation and release note

- [ ] **Step 1: 코드 docstring과 README의 Anthropic 단일-provider 표현을 정리한다**

  역할 설명은 `fast`/`smart`를 주 표기로 사용하고 provider별 정책을 필요한 곳에서만 괄호로 설명한다. README 환경변수 표는 활성 provider에 맞는 키가 필요하다고 설명한다.

- [ ] **Step 2: CHANGELOG `[Unreleased] / Added`에 Issue #82를 기록한다**

  한 줄에 “판매자 모델 팩토리도 `LLM_PROVIDER`와 공용 tier resolver를 사용해 OpenAI 기본/Anthropic 전환 및 미구성 degrade를 구매자와 정합”이라고 기록한다. api-spec 버전 표기는 추가하지 않는다.

- [ ] **Step 3: 수정된 동작의 집중 테스트를 실행한다**

  Run:
  ```bash
  uv run pytest -q \
    tests/unit/test_llm_provider.py \
    tests/unit/test_seller_models.py \
    tests/unit/test_seller_workers.py \
    tests/unit/test_seller_router.py \
    tests/unit/test_seller_api.py
  ```

  Expected: PASS.

- [ ] **Step 4: ruff 자동 수정과 포맷을 실행한다**

  Run:
  ```bash
  uv run ruff check --fix && uv run ruff format
  ```

  Expected: exit 0.

- [ ] **Step 5: 전체 테스트를 실행한다**

  Run:
  ```bash
  uv run pytest
  ```

  Expected: 모든 선택 테스트 PASS, 라이브 키가 필요한 smoke만 기존 marker에 따라 deselect.

- [ ] **Step 6: diff와 시크릿·잔여 하드코딩을 검토한다**

  Run:
  ```bash
  git diff --check
  git diff --stat
  git diff
  rg -n 'init_chat_model\(f?"anthropic:' app/agents/seller app/core
  ```

  Expected: whitespace 오류 없음, seller hard-coded provider 없음, `.env`/시크릿 변경 없음.

- [ ] **Step 7: 논리 단위 커밋을 만든다**

  SPEC/계획 개정을 먼저 커밋하고, 구현·테스트·CHANGELOG를 두 번째 커밋으로 만든다. Conventional Commit과 Lore trailers를 함께 사용한다.

  ```text
  docs(seller): make model tiers provider-neutral

  Constraint: SPEC-SELLER-001 must change before provider behavior
  Confidence: high
  Scope-risk: narrow
  Tested: documentation consistency search
  ```

  ```text
  feat(seller): honor the shared LLM provider toggle

  Constraint: preserve ToolStrategy and existing SSE wire contract
  Rejected: runtime-configurable model | provider is deployment configuration, not request input
  Confidence: high
  Scope-risk: moderate
  Tested: ruff and full pytest suite
  ```

## Self-Review

- Spec coverage: provider/tier mapping, temperature/reasoning split, key-missing degrade, cache, telemetry, structured output, docs and tests are each assigned to a task.
- Placeholder scan: no TODO/TBD steps; every behavior-changing step names an exact test and command.
- Type consistency: `LLMProvider`, `ModelTier`, `ResolvedModel`, `LLMNotConfigured`, `resolve_provider_model(settings, tier)` are defined in Task 2 and consumed unchanged by later tasks.

---

### Task 6: Claude Review follow-up — provider 값 대소문자 하위호환

**Review:** PR #88 unresolved thread `PRRT_kwDOTZymn86THE25`

**Decision:** 반영한다. 기존 `str` 설정과 `.lower()` 분기는 `OpenAI`/`Anthropic`처럼
대소문자가 섞인 환경변수 값을 허용했는데, `Literal` 전환이 이 동작을 조용히 깨뜨렸다.
타입 제한과 unknown-provider fail-fast는 유지하되 Settings 입력 경계에서 값만 소문자로
정규화한다. 공백 허용은 기존 보장사항이 아니므로 추가하지 않는다.

**Files:**
- Modify: `docs/specs/SPEC-SELLER-001.md`
- Modify: `tests/unit/test_llm_provider.py`
- Modify: `app/core/config.py`
- Modify: `CHANGELOG.md`

- [x] **Step 1: SPEC v1.1.1에 대소문자 비구분·unknown 거부 계약을 먼저 기록한다**
- [x] **Step 2: `OpenAI`/`ANTHROPIC` 입력이 canonical lowercase로 저장되는 실패 테스트를 추가한다**
- [x] **Step 3: 알 수 없는 provider가 계속 `ValidationError`인지 기존 테스트로 잠근다**
- [x] **Step 4: `field_validator(mode="before")`에서 문자열 값에만 `.lower()`를 적용한다**
- [x] **Step 5: 집중 테스트 → ruff → 전체 pytest 순서로 검증한다**
- [x] **Step 6: CHANGELOG에 하위호환 복구를 기록하고 Lore 커밋 후 push한다**
- [x] **Step 7: 리뷰 스레드에 변경·테스트를 답변하고 resolve한다**

### Task 7: Claude Review follow-up — worker configuration error 전파

**Review:** PR #88 unresolved thread `PRRT_kwDOTZymn86THQ_3`

**Decision:** 반영한다. `LLMNotConfigured`는 특정 데이터 소스의 일시 실패가 아니라 모든
worker가 공유하는 배포 설정 오류다. 현재 planner가 먼저 같은 fast tier를 만들기 때문에
대부분 선행 실패하지만, worker tier 분리나 직접 호출에서도 `run_workers`가 이 오류를 degrade
finding으로 바꾸면 API의 `LLM_UNAVAILABLE` 계약이 깨진다. gather 병렬성은 유지하고 결과 수렴
시 configuration error를 다른 예외보다 먼저 재전파한다.

**Files:**
- Modify: `docs/specs/SPEC-SELLER-001.md`
- Modify: `tests/unit/test_seller_orchestrator.py`
- Modify: `app/agents/seller/orchestrator.py`
- Modify: `CHANGELOG.md`

- [x] **Step 1: SPEC v1.1.2에 provider 미구성은 worker degrade 대상이 아님을 기록한다**
- [x] **Step 2: 정상 worker와 `LLMNotConfigured` worker가 섞여도 예외가 재전파되는 RED 테스트를 추가한다**
- [x] **Step 3: gather 결과에서 `LLMNotConfigured`를 finding 변환 전에 재전파한다**
- [x] **Step 4: 일반 예외·timeout의 기존 partial degrade 테스트를 함께 실행한다**
- [x] **Step 5: ruff·전체 pytest·diff 검토 후 Lore 커밋과 push를 수행한다**
- [ ] **Step 6: 리뷰 답변·resolve 후 새 CI/Claude Review를 다시 확인한다**
