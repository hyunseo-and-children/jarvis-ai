# Issue #72 Unicode 출력 하드닝 설계

## 상태

- 승인: 2026-07-23 대화에서 문맥 인식 sanitizer, skeleton 기반 마스킹, 상태 기반 스트리밍 보호 방향 승인
- 관련 이슈: [#72](https://github.com/toss-delta-final/jarvis-ai/issues/72)
- 발견 경로: [PR #69 Claude 리뷰](https://github.com/toss-delta-final/jarvis-ai/pull/69#discussion_r3628346893)
- 계약 영향: 없음. `docs/api-spec.md` §3.1·§3.2의 엔드포인트, SSE 이벤트, 필드, 오류 코드를 바꾸지 않는다.

## 목표

사용자 또는 Spring 신뢰경계를 넘는 텍스트에서 Unicode Variation Selector와 Tag 문자로 숨긴 데이터가 정제 및 시크릿 마스킹을 우회하지 못하게 한다. 동시에 등록된 Unicode variation sequence, emoji presentation/keycap sequence, CJK IVS, 지원하는 emoji tag sequence는 원문 그대로 보존한다.

## 현재 결함

`app/core/text.py::_CTRL`은 C0/C1, zero-width, bidi 포맷 문자만 제거하고 다음 범위를 통과시킨다.

- Variation Selector: `U+FE00–U+FE0F`, `U+E0100–U+E01EF`
- Tag: `U+E0000–U+E007F`

따라서 `Bearer abcdefgh<U+FE0F>ijklmnop1234`처럼 비가시 문자를 삽입하면 `mask_output()`의 ASCII 연속 패턴이 끊긴다. 반대로 세 범위를 `_CTRL`에 일괄 추가하면 `❤️`, `#️⃣`, 등록된 CJK IVS, England/Scotland/Wales subdivision flag가 손상된다.

판매자 general 레인은 LLM 청크마다 독립적으로 마스킹하므로 민감 패턴 또는 Unicode sequence가 청크 경계에서 나뉘는 경우도 현재 검사를 우회한다.

## 설계 결정

### 1. Unicode 데이터는 공식 등록 목록을 고정 생성한다

런타임 네트워크 호출이나 신규 의존성을 추가하지 않는다. 표준 라이브러리만 사용하는 생성 스크립트가 다음 고정 버전 데이터를 읽어 compact generated module을 만든다.

- Unicode 17.0.0 `StandardizedVariants.txt`
- Unicode 17.0.0 `emoji-variation-sequences.txt`
- IVD 2025-07-14 `IVD_Sequences.txt`

생성 결과는 `(base code point, selector index)`를 정렬된 32-bit key 배열로 직렬화하고 zlib+base85로 압축한다. 런타임은 이를 `array('I')`로 한 번 복원해 `bisect`로 등록 여부를 조회한다. 생성 모듈에는 입력 버전, URL, SHA-256, 레코드 수를 기록한다.

`StandardizedVariants.txt`의 Mongolian Free Variation Selector처럼 Issue #72 대상 범위 밖인 selector는 생성 데이터에서 제외한다.

### 2. Tag 지원 범위는 RGI 3종으로 제한한다

지원하는 tag sequence는 Unicode 17.0.0 `RGI_Emoji_Tag_Sequence`의 England, Scotland, Wales 세 개다. 임의의 구조적으로 well-formed tag sequence까지 허용하면 tag payload 운반 통로가 다시 열리므로 보존하지 않는다.

- 정확히 일치하는 RGI sequence: 전체 보존
- 고아 tag, 미종결 tag, 비지원 tag, 정상 sequence 뒤 추가 tag: visible base는 보존하고 비지원 tag 문자만 제거
- `U+E0000`, deprecated `U+E0001 LANGUAGE TAG`: 항상 제거

### 3. 문맥 sanitizer는 visible base를 보존한다

`app/core/unicode_security.py`가 Variation Selector와 Tag만 담당하는 순수 함수를 제공한다.

```python
def strip_invalid_invisible_sequences(text: str) -> str: ...
```

정책은 다음과 같다.

- 등록된 `(base, selector)` 한 쌍만 보존
- 고아 또는 미등록 selector는 제거
- 등록된 쌍 뒤 반복 selector는 제거
- RGI tag sequence만 보존
- 잘못된 tag sequence의 visible base는 보존하고 tag 문자만 제거

`app/core/text.py::_strip_unsafe()`와 `_strip_unsafe_multiline()`가 이 함수를 `_CTRL` 제거 전에 공통 적용한다. 정규화(NFC/NFKC)는 selector와 tag를 제거하지 못하며 정상 텍스트를 불필요하게 변경할 수 있어 해결책으로 사용하지 않는다.

### 4. 시크릿 탐지는 출력과 분리된 skeleton에서 수행한다

정상 variation sequence를 출력에서 보존하면 숫자+VS처럼 표준상 유효한 조합이 주민번호 패턴을 끊을 수 있다. 따라서 `mask_output()`은 검사 전용 skeleton과 원본 인덱스 매핑을 만든다.

- skeleton에서는 Variation Selector, Tag, 기존 `_CTRL` 대상 문자를 모두 제거
- whitespace run은 검사 목적으로 하나로 접되 원본 span 매핑 유지
- 기존 시크릿 정규식은 skeleton에 적용
- 탐지한 skeleton span을 원본 span으로 역매핑해 원본 전체를 `[민감 정보 차단]`으로 교체
- 민감 패턴이 없는 정상 `❤️`, `#️⃣`, CJK IVS, RGI tag flag는 원문 그대로 반환

`mask_output()`을 직접 호출해도 방어가 성립하도록 skeleton 생성은 `_strip_unsafe()` 선호출에 의존하지 않는다.

### 5. seller general 스트림은 상태 기반 guard를 사용한다

full-string 경로의 `_token()`은 sanitizer 후 `mask_output()`을 호출하는 현재 순서를 유지한다. 청크 경로에는 요청 단위 상태 객체를 둔다.

```python
class StreamingOutputGuard:
    def feed(self, text: str) -> list[str]: ...
    def flush(self) -> list[str]: ...
```

guard는 다음 상태를 유지한다.

- 직전 청크 끝의 잠재 variation base
- 진행 중인 RGI tag sequence prefix
- `sk-`, `Bearer`, 주민등록번호의 아직 확정되지 않은 prefix
- 최소 길이에 도달해 마스킹을 시작한 가변 길이 token의 continuation 상태
- 청크 경계 공백 중복 방지를 위한 이전 출력 상태

민감 패턴의 완전한 prefix가 사용자에게 먼저 노출되지 않도록 확정 전까지 bounded tail을 보류한다. match가 확정되면 marker 하나를 emit하고 token 문자 continuation은 delimiter까지 버린다. 스트림 종료 시 `flush()`가 남은 정상 텍스트를 emit한다.

기존 SSE 이벤트명과 순서는 유지한다. 정상 입력은 여전히 여러 `token` 이벤트로 스트리밍되며 전체 응답 버퍼링으로 후퇴하지 않는다.

### 6. 실행 정본과 표시값을 분리한다

- `app/agents/seller/hitl.py::validate_draft()`는 문맥 sanitizer만 적용한 실행 정본을 checkpoint에 저장한다.
- Spring create/update payload에는 표시용 마스킹 문자열을 넣지 않는다.
- `app/api/seller.py::_draft_event()`는 같은 정본/현재값의 표시 복사본에 `mask_output()`을 적용한다.
- 사용자 대면 buyer/profile/seller 출력과 AI→Spring 추천 reason push는 기존 `_strip_unsafe*` 호출부를 통해 문맥 sanitizer를 공유한다.
- 내부 전용 상수나 신뢰된 기계 필드까지 호출부를 반사적으로 확대하지 않는다.

## 테스트 전략

### 등록 sequence 보존

- `❤️`
- `#️⃣`
- `U+3402 U+E0100` 등록 CJK IVS
- England, Scotland, Wales RGI tag sequence
- 정상 sequence가 일반 한글/ASCII와 섞인 문장

### 비정상 sequence 제거

- 문자열 시작의 고아 VS
- ASCII 문자 뒤 미등록 VS
- 등록 pair 뒤 반복 VS
- supplemental VS로 인코딩한 연속 payload
- base 없는 tag run
- terminator 없는 tag run
- 비지원 subdivision tag
- 정상 RGI tag 뒤 추가 tag

### 마스킹

- VS/Tag를 삽입한 `sk-` API key
- VS/Tag를 삽입한 Bearer token
- 각 숫자 사이에 VS를 삽입한 주민등록번호
- 정상 emoji/CJK/tag sequence가 민감 패턴 없이 그대로 반환됨
- 민감 패턴 앞뒤 정상 Unicode sequence가 보존됨

### 스트리밍

- `Bearer`와 token body가 다른 청크
- token body와 VS/Tag가 다른 청크
- base와 selector가 다른 청크
- RGI tag sequence가 여러 청크
- 민감 token continuation 전체가 marker 하나로 축약
- 정상 청크의 공백·개행·이벤트 순서 회귀 없음

### 신뢰경계

- seller draft SSE는 마스킹됨
- confirm 후 Spring payload는 invalid invisible만 제거되고 표시 marker로 오염되지 않음
- profile markdown, recommendation reason/comment의 비정상 sequence 제거

## 비범위

- 시크릿 패턴 종류 자체 확대
- UTS #39 전체 confusable/homoglyph 대응
- ZWJ emoji 정책 변경
- 요청 입력 전체의 Unicode 제한
- API/SSE 계약 또는 오류 코드 변경
- 런타임 외부 Unicode 데이터 조회
