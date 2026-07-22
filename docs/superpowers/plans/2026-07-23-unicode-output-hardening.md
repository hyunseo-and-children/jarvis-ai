# Unicode 출력 하드닝 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 등록된 Unicode variation/tag sequence는 보존하면서 비정상 은닉 payload와 시크릿 마스킹 우회를 모든 사용자 노출 경계에서 차단한다.

**Architecture:** 공식 Unicode 17.0.0·IVD 2025-07-14 데이터를 compact generated lookup으로 고정하고, `app/core/unicode_security.py`가 full-string/stream 문맥 검사를 담당한다. 표시 계층은 invisible-free skeleton에서 민감정보를 탐지해 원본 span을 마스킹하며, seller general 스트림은 요청 단위 상태 guard로 청크 경계를 보존한다.

**Tech Stack:** Python 3.12, stdlib `urllib`/`hashlib`/`zlib`/`base64`/`array`/`bisect`, FastAPI SSE, pytest, ruff

## Global Constraints

- 계약 정본 `docs/api-spec.md` §3.1·§3.2의 엔드포인트, SSE 이벤트, 필드, 오류 코드는 변경하지 않는다.
- 신규 런타임 의존성을 추가하지 않는다.
- 런타임 네트워크에서 Unicode 데이터를 조회하지 않는다.
- Unicode 데이터는 17.0.0, IVD는 2025-07-14로 고정한다.
- 정상 `❤️`, `#️⃣`, 등록 CJK IVS, RGI England/Scotland/Wales tag flag를 보존한다.
- Spring 실행 정본에는 invalid invisible 제거만 적용하고 `[민감 정보 차단]`을 저장하지 않는다.
- 모든 production behavior는 해당 실패 테스트를 먼저 확인한 뒤 구현한다.
- 주석/docstring은 한국어, 식별자는 영어로 작성한다.
- 작업 경로는 `/home/nyong/inte-final/.worktrees/jarvis-ai-issue-72`만 사용한다.

---

## File Structure

- Create `scripts/generate_unicode_security_data.py` — 고정 공식 데이터 다운로드·파싱·검증·compact module 생성
- Create `app/core/_unicode_variation_data.py` — 생성된 source metadata와 압축된 등록 variation key
- Create `app/core/unicode_security.py` — 등록 조회, full-string sanitizer, streaming sequence sanitizer
- Modify `app/core/text.py` — 공용 text sanitizer에 문맥 검사 연결, skeleton/source mapping 제공
- Modify `app/agents/seller/middleware.py` — skeleton 기반 `mask_output`, `StreamingOutputGuard`
- Modify `app/api/seller.py` — general 스트림에서 요청 단위 guard feed/flush 사용
- Create `tests/unit/test_unicode_security.py` — 정상/악성 sequence와 generated-data 회귀
- Modify `tests/unit/test_seller_middleware.py` — VS/Tag 삽입 민감정보 마스킹 회귀
- Modify `tests/unit/test_seller_api.py` — 청크 경계 sanitizer/masker 회귀
- Modify `tests/unit/test_seller_hitl.py` — Spring 실행 정본과 표시 마스킹 분리 회귀
- Modify `tests/unit/test_profile.py` — profile markdown 경계 회귀
- Modify `tests/unit/test_recommendation.py` — buyer reason/comment 경계 회귀
- Modify `CHANGELOG.md` — Issue #72 보안 하드닝 기록

---

### Task 1: 공식 Unicode 데이터 생성기와 등록 조회

**Files:**
- Create: `tests/unit/test_unicode_security.py`
- Create: `scripts/generate_unicode_security_data.py`
- Create: `app/core/_unicode_variation_data.py`
- Create: `app/core/unicode_security.py`

**Interfaces:**
- Produces: `is_registered_variation_sequence(base: str, selector: str) -> bool`
- Produces: metadata constants `UNICODE_VERSION`, `IVD_VERSION`, `VARIATION_KEY_COUNT`, `SOURCE_SHA256`
- Consumes: fixed official data URLs from the approved design

- [ ] **Step 1: Write a failing policy test against the existing sanitizer**

```python
from app.core.text import _strip_unsafe


def test_strip_unsafe_removes_unregistered_variation_pair() -> None:
    assert _strip_unsafe("A\ufe0fB") == "AB"
```

This uses an existing public behavior surface, so RED is an assertion failure rather than a missing-module collection error.

- [ ] **Step 2: Run the test and confirm RED**

Run:

```bash
uv run pytest tests/unit/test_unicode_security.py::test_strip_unsafe_removes_unregistered_variation_pair -q
```

Expected: FAIL because the actual value is `"A\ufe0fB"`.

- [ ] **Step 3: Implement the generator, compact lookup, and minimal registered-pair scanner**

The generator must:

```python
UNICODE_VERSION = "17.0.0"
IVD_VERSION = "2025-07-14"
SOURCE_URLS = {
    "standardized": "https://www.unicode.org/Public/17.0.0/ucd/StandardizedVariants.txt",
    "emoji": "https://www.unicode.org/Public/17.0.0/ucd/emoji/emoji-variation-sequences.txt",
    "ivd": "https://www.unicode.org/ivd/data/2025-07-14/IVD_Sequences.txt",
}
```

- strip comments and parse the code-point field before `;`
- retain only two-code-point entries whose selector is in `FE00..FE0F` or `E0100..E01EF`
- deduplicate pairs across IVD collections
- encode `key = (base << 8) | selector_index`
- sort keys, pack them as big-endian unsigned 32-bit integers, zlib-compress, base85-encode
- record SHA-256 for each source and reject an empty/malformed source
- render deterministic Python with 100-column-safe string chunks

The runtime lookup must decode once into `array('I')`, byte-swap on little-endian hosts, and use `bisect_left` without a runtime network call. Add `strip_invalid_invisible_sequences()` just far enough to remove unregistered/orphan selector characters while preserving registered pairs, then wire it into `_strip_unsafe()`.

- [ ] **Step 4: Generate the pinned module and verify GREEN**

Run:

```bash
uv run python scripts/generate_unicode_security_data.py
uv run pytest tests/unit/test_unicode_security.py::test_strip_unsafe_removes_unregistered_variation_pair -q
```

Expected: PASS. Generated metadata reports Unicode `17.0.0`, IVD `2025-07-14`, and a non-zero record count.

- [ ] **Step 5: Add deterministic metadata checks**

```python
def test_generated_variation_data_is_pinned_and_nonempty() -> None:
    from app.core import _unicode_variation_data as data

    assert data.UNICODE_VERSION == "17.0.0"
    assert data.IVD_VERSION == "2025-07-14"
    assert data.VARIATION_KEY_COUNT > 30_000
    assert set(data.SOURCE_SHA256) == {"standardized", "emoji", "ivd"}
```

Run the targeted file and confirm PASS.

- [ ] **Step 6: Commit the data foundation**

Commit only the generator, generated module, runtime lookup, and their tests with a Conventional Commit plus Lore trailers.

---

### Task 2: Full-string Variation Selector/Tag 문맥 sanitizer

**Files:**
- Modify: `app/core/unicode_security.py`
- Modify: `app/core/text.py:7-31`
- Modify: `tests/unit/test_unicode_security.py`
- Modify: `tests/unit/test_recommendation.py:283-312`

**Interfaces:**
- Produces: `strip_invalid_invisible_sequences(text: str) -> str`
- Produces: `UnicodeSequenceStreamSanitizer.feed(text: str) -> str`
- Produces: `UnicodeSequenceStreamSanitizer.flush() -> str`
- Consumes: `is_registered_variation_sequence()` from Task 1

- [ ] **Step 1: RED — orphan/unregistered selector removal**

```python
@pytest.mark.parametrize(
    ("dirty", "clean"),
    [
        ("\ufe0f시작", "시작"),
        ("A\ufe0fB", "AB"),
        ("❤\ufe0f\ufe0e", "❤\ufe0f"),
        ("상품\U000e0158\U000e0155", "상품"),
    ],
)
def test_strip_unsafe_removes_orphan_unregistered_and_repeated_selectors(
    dirty: str, clean: str
) -> None:
    assert _strip_unsafe(dirty) == clean
```

Run the parametrized test and confirm current output still contains selectors.

- [ ] **Step 2: GREEN — minimal selector scanner**

Implement one-pass code-point scanning. A selector is preserved only when it immediately follows a visible base and the exact pair is registered. After consuming one registered pair, subsequent selector characters are orphaned and removed.

Wire `strip_invalid_invisible_sequences()` before `_CTRL.sub()` in both `_strip_unsafe` paths. Run the RED test and existing text tests.

- [ ] **Step 3: RED — registered normal sequence preservation**

```python
@pytest.mark.parametrize(
    "text",
    ["❤️", "#️⃣", "㐂\U000e0100"],
)
def test_strip_unsafe_preserves_registered_variation_sequences(text: str) -> None:
    assert _strip_unsafe(text) == text
```

Confirm the test protects the exact sequence, not only the rendered glyph.

- [ ] **Step 4: RED — Tag allow/deny policy**

Use constants for the three RGI strings and test:

```python
@pytest.mark.parametrize("text", [ENGLAND_FLAG, SCOTLAND_FLAG, WALES_FLAG])
def test_strip_unsafe_preserves_supported_rgi_tag_flags(text: str) -> None:
    assert _strip_unsafe(text) == text


def test_strip_unsafe_drops_ill_formed_and_unsupported_tag_payloads() -> None:
    assert _strip_unsafe("A\U000e0075\U000e0073\U000e007fB") == "AB"
    assert _strip_unsafe("🏴\U000e0075\U000e0073") == "🏴"
    assert _strip_unsafe(ENGLAND_FLAG + "\U000e0061") == ENGLAND_FLAG
```

- [ ] **Step 5: GREEN — exact RGI prefix scanner**

Preserve only exact supported sequences. For invalid/incomplete tag runs, emit the visible base and discard only tag code points. Do not reject the whole response.

- [ ] **Step 6: RED/GREEN — stream sequence state**

Test `UnicodeSequenceStreamSanitizer` directly:

```python
def test_stream_sanitizer_preserves_sequences_split_across_chunks() -> None:
    guard = UnicodeSequenceStreamSanitizer()
    parts = [guard.feed("좋아 ❤"), guard.feed("️ " + ENGLAND_FLAG[:3]), guard.feed(ENGLAND_FLAG[3:])]
    parts.append(guard.flush())
    assert "".join(parts) == "좋아 ❤️ " + ENGLAND_FLAG
```

Also split malicious selector/tag runs across chunks and assert removal. Implement pending-base/RGI-prefix state until both tests pass.

- [ ] **Step 7: Run focused regressions and commit**

```bash
uv run pytest tests/unit/test_unicode_security.py tests/unit/test_recommendation.py -q
```

Commit the contextual sanitizer as one independently reviewable behavior.

---

### Task 3: Invisible-free skeleton 기반 시크릿 마스킹

**Files:**
- Modify: `app/core/text.py`
- Modify: `app/agents/seller/middleware.py:131-146`
- Modify: `tests/unit/test_seller_middleware.py:79-97`

**Interfaces:**
- Produces: `_security_skeleton(text: str) -> SecuritySkeleton`
- `SecuritySkeleton.text: str`
- `SecuritySkeleton.source_span(start: int, end: int) -> tuple[int, int]`
- Preserves: `mask_output(text: str) -> str`

- [ ] **Step 1: RED — direct VS/Tag masking bypass**

```python
@pytest.mark.parametrize(
    "text",
    [
        "sk-abcdefgh\ufe0fijklmnop1234",
        "Bearer abcdefgh\U000e0061ijklmnop1234",
        "9\ufe0f9\ufe0f0\ufe0f1\ufe0f0\ufe0f1\ufe0f-1\ufe0f2\ufe0f3\ufe0f4\ufe0f5\ufe0f6\ufe0f7\ufe0f",
    ],
)
def test_mask_output_detects_secrets_through_invisible_characters(text: str) -> None:
    masked = middleware.mask_output(text)
    assert masked == middleware.MASK_REPLACEMENT
```

Confirm all cases fail under the current regex substitution.

- [ ] **Step 2: GREEN — skeleton/source-span mapping**

`_security_skeleton()` removes `_CTRL` targets, all Variation Selectors, and all Tag characters only from the inspection string. It records each retained code point's source start. `source_span()` ends at the next retained code point so trailing invisible characters within a match are included.

Gather all pattern matches against the skeleton, convert to source spans, merge overlapping spans, and replace in reverse source order. Do not mutate non-matching normal text.

- [ ] **Step 3: RED/GREEN — legitimate sequence fidelity**

```python
@pytest.mark.parametrize("text", ["정상 ❤️", "번호 #️⃣", "한자 㐂\U000e0100", ENGLAND_FLAG])
def test_mask_output_preserves_normal_unicode_sequences(text: str) -> None:
    assert middleware.mask_output(text) == text
```

Also verify normal sequence before/after a masked secret remains byte-for-byte identical.

- [ ] **Step 4: Run focused tests and commit**

```bash
uv run pytest tests/unit/test_seller_middleware.py tests/unit/test_unicode_security.py -q
```

Commit skeleton-based masking separately from streaming.

---

### Task 4: seller general 청크 경계 보호

**Files:**
- Modify: `app/agents/seller/middleware.py`
- Modify: `app/api/seller.py:85-104,160-202`
- Modify: `tests/unit/test_seller_api.py:43-107`

**Interfaces:**
- Produces: `StreamingOutputGuard.feed(text: str) -> list[str]`
- Produces: `StreamingOutputGuard.flush() -> list[str]`
- Consumes: `UnicodeSequenceStreamSanitizer`, `_strip_unsafe_multiline`, `mask_output`

- [ ] **Step 1: RED — Bearer/API key/RRN across chunks**

Add `_StubStreamAgent` cases where every pattern is split before the minimum match length. Join emitted token text and assert exactly one marker with no secret fragments that reconstruct the original token.

```python
def test_stream_masks_bearer_token_split_across_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _StubStreamAgent(
        [
            AIMessageChunk(content="키는 Bearer abcdefgh"),
            AIMessageChunk(content="\ufe0fijklmnop"),
            AIMessageChunk(content="1234 입니다"),
        ]
    )
    monkeypatch.setattr(seller_api, "build_general_agent", lambda today: agent)
    text = "".join(e["data"]["text"] for e in _collect(_request("키?")) if e["type"] == "token")
    assert text == "키는 [민감 정보 차단] 입니다"
```

- [ ] **Step 2: GREEN — bounded sensitive-prefix retention**

The guard must retain the longest suffix that can still become one of:

- `sk-` + fewer than 16 allowed token characters
- `Bearer` + whitespace + fewer than 16 allowed token characters
- a valid prefix of `\d{6}-[1-4]\d{6}`

When a variable-length token reaches 16 characters, emit one marker and consume further allowed token characters until a delimiter. Emit safe prefixes immediately; do not buffer the full response.

- [ ] **Step 3: RED/GREEN — Unicode sequence across chunks**

Add general-stream tests splitting heart+VS, CJK+supplemental VS, and an RGI tag flag across chunks. Join token text and assert exact preservation. Add an invalid split tag payload and assert it is removed.

- [ ] **Step 4: Preserve normal stream semantics**

Update `_general_stream()` to instantiate one guard per request, emit every fragment returned by `feed()`, emit `flush()` fragments before `done`, and retain existing error behavior.

Run existing tests proving:

- normal two-chunk response remains multiple incremental tokens
- boundary space is not lost
- tool-use blocks remain excluded
- `meta` first and `done` last

- [ ] **Step 5: Focused verification and commit**

```bash
uv run pytest tests/unit/test_seller_api.py tests/unit/test_seller_middleware.py -q
```

Commit the chunk-safe guard as a separate logical change.

---

### Task 5: 신뢰경계 회귀와 릴리스 기록

**Files:**
- Modify: `tests/unit/test_seller_hitl.py`
- Modify: `tests/unit/test_seller_api.py`
- Modify: `tests/unit/test_profile.py`
- Modify: `tests/unit/test_recommendation.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: unchanged `_strip_unsafe`, `_strip_unsafe_multiline`, `mask_output`, seller HITL flow
- Produces: regression evidence for each Issue #72 trust boundary

- [ ] **Step 1: RED/GREEN — seller execution/display split**

Extend the existing draft/confirm test with an invalid selector/tag payload plus a maskable Bearer token.

Assertions:

- SSE `draft.changes[].after` contains `[민감 정보 차단]`
- Spring `patch.description` contains the original visible Bearer token, not the marker
- Spring value contains no invalid VS/Tag characters
- a legitimate `❤️` sequence in the description remains present in Spring and SSE

- [ ] **Step 2: RED/GREEN — profile markdown**

Return markdown containing registered and invalid sequences from the profile reader stub. Assert `/profile/me` preserves registered sequences and removes invalid payload without changing the response schema.

- [ ] **Step 3: RED/GREEN — buyer recommendation boundary**

Cover `_sanitize_reason()` and `overall_comment` with registered/invalid sequences. Assert Spring push reasons and SSE token comments follow the same policy.

- [ ] **Step 4: Update changelog**

Add one `[Unreleased]` `Security` entry describing what and why. Do not claim an api-spec version change because the wire contract is unchanged.

- [ ] **Step 5: Run boundary test set and commit**

```bash
uv run pytest \
  tests/unit/test_unicode_security.py \
  tests/unit/test_seller_middleware.py \
  tests/unit/test_seller_api.py \
  tests/unit/test_seller_hitl.py \
  tests/unit/test_profile.py \
  tests/unit/test_recommendation.py -q
```

Commit boundary coverage and changelog together only if no production behavior changes remain uncommitted.

---

### Task 6: 전체 검증과 최종 검토

**Files:**
- Review all changed files

- [ ] **Step 1: Regenerate and require a clean generated diff**

```bash
uv run python scripts/generate_unicode_security_data.py
git diff --exit-code -- app/core/_unicode_variation_data.py
```

Expected: no generated drift.

- [ ] **Step 2: Format and lint**

```bash
uv run ruff check --fix
uv run ruff format
uv run ruff check
```

Expected: all checks pass; inspect any auto-fix before staging.

- [ ] **Step 3: Full test suite**

```bash
uv run pytest
```

Expected: all selected unit/non-infrastructure integration tests pass with zero failures.

- [ ] **Step 4: Diff/security review**

Check:

- no `.env`, token, secret, debug output, or runtime downloader introduced
- generated source URLs and hashes are pinned
- no api-spec/SSE field drift
- no display marker reaches Spring payload
- no user-facing target call site bypasses `_strip_unsafe*` or the seller guard
- no unrelated files changed

- [ ] **Step 5: Final commit if formatter or review changed files**

Use a Conventional Commit plus Lore trailers. `Tested:` must contain the fresh ruff and full pytest evidence.

- [ ] **Step 6: Report completion evidence**

Report worktree path, commits, changed files, generated Unicode versions, targeted/full test counts, and remaining risk: RGI tag support is intentionally limited to three sequences and ZWJ policy remains out of scope.
