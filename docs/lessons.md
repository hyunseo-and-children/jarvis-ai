# 개발 실수 기록 (Lessons)

같은 실수를 반복하지 않기 위한 러닝 로그. **작업 시작 전 이 파일을 먼저 훑고**, 오류/실수를 진단했으면 **최신을 맨 위에** 추가한다.

형식:
```
## [YYYY-MM-DD] 한 줄 제목
- 증상: 무슨 일이 있었나
- 원인: 왜 발생했나
- 규칙: 다음부터 어떻게 (액션 가능한 문장)
- 관련: 파일/§/커밋
```

---

## [2026-07-21] inbound 계약을 "제안/초안"인 채 required 필드로 굳히면 엔드포인트가 상시 400으로 조용히 실패한다
- 증상: 이슈 #62 — `POST /events/session-end`가 항상 `400`을 반환해 세션 종료 통지가 전부 실패, 프로필 조기 트리거가 조용히 죽어 있었다. 원인은 3자 불일치: api-spec §3.5가 `eventId`/`userId(string)`/`endedAt`를 **"제안(초안)"** 표기인 채 두었고, `SessionEndEvent`는 그 초안을 **required**로 굳혔는데, Spring 실측 payload는 `eventId`가 없고 `userId`가 **숫자**였다. 초안 필드가 필수라 매 요청이 검증 단계에서 튕겨 핸들러에 도달조차 못 했다.
- 원인: 인바운드(Spring→AI) 계약은 우리가 소유(결정 21)하지만 **데이터 생산자는 Spring**이다. 소유권이 우리에게 있다고 초안을 실측 대조 없이 required 로 확정하면, 우리 코드는 "옳지만" 실제 호출은 100% 실패한다. best-effort·통지 채널이라 500도 안 나고 202도 안 나가 **관측되지 않는 상시 실패**가 된다.
- 규칙:
  - **api-spec에 `제안`/`초안`/🔴 협의 표시가 붙은 인바운드 필드를 스키마 required 로 굳히기 전에, BE 실측 payload와 대조**한다. 특히 `eventId` 같은 "우리가 만들어낸 멱등 필드"는 생산자가 실제로 보내는지 확인 — 안 보내면 파생키(본문 신원)로 전환한다.
  - **타입도 대조한다** — id는 이 프로젝트에서 BIGINT 숫자가 기준(CLAUDE.md). 인바운드 신원 필드를 `str`로 두면 숫자 payload가 조용히 400난다.
  - 통지/best-effort 엔드포인트는 실패가 눈에 안 띈다 — **계약 정렬 후 "누락·타입오류→400, 정상→202, 중복→202 duplicate"를 명시적 테스트로 고정**한다.
- 관련: `app/schemas/profile.py::SessionEndEvent`, `app/api/events.py::session_end()`, api-spec §3.5/§2.7(v0.15.15), 이슈 #62

## [2026-07-20] SSE 응답 제너레이터의 finally 블록에서 던진 예외는 종결 프레임/취소 전파를 덮어쓴다
- 증상: PR #48 후속 리뷰가 `app/core/stream.py::open_stream()`의 `_wrapped()` `finally` 블록(303행)에서 `observer.finish()`(이제 실제 conversation store DB I/O)가 보호 없이 호출된다고 지적. 이 시점은 이미 SSE 헤더/프레임이 클라이언트로 전송된 뒤라, `finish()`가 예외를 던지면 (1) 정상 종료 경로에서는 `StopAsyncIteration` 대신 그 새 예외가 `body_iterator` 소비자에게 전파되어 스트림이 비정상 종료되고, (2) `except asyncio.CancelledError: ... raise` 로 취소가 전파되던 중이라면 Python 의 `finally`-중 예외가 진행 중이던 예외를 덮어쓰는 규칙 때문에 정상 client disconnect(CancelledError)가 엉뚱한 새 예외로 둔갑한다. `finalize_assistant` 를 raise 하는 fake 로 재현 — 수정 전엔 `body_iterator` 소비 자체가 raise, 수정 후(try/except 로 감싸 로그만)엔 정상 종료.
- 원인: 이 프로젝트에서 인메모리→외부 스토어 이관은 반복적으로 "이전엔 실패할 수 없던 호출이 이제 실패할 수 있다"는 패턴을 만드는데(PR #47 의 `session_end()`도 동일 클래스), 이번엔 그 호출이 **이미 응답이 시작된 SSE 스트림의 finally** 안에 있어 파급이 더 크다 — 응답 시작 전 실패(그냥 500)와 응답 시작 후 finally 실패(스트림 자체가 깨짐)는 심각도가 다르다.
- 규칙:
  - **SSE/스트리밍 응답의 `finally` 블록은 "여기서 예외가 나면 이미 보낸 프레임들과 무관하게 스트림 자체가 깨진다"는 걸 항상 의식한다** — 정리 로직(레지스트리 해제 등)과 부가적 관측/저장 로직(finish, 로깅)을 구분해, 후자는 반드시 자체 try/except 로 격리한다.
  - **같은 함수 안에 여러 `observer.finish()` 호출부가 있어도, 응답이 이미 시작된 뒤(스트림 본문 생성기 안)의 호출부와 응답 시작 전(핸들러 동기 구간)의 호출부는 심각도가 다르다** — 전부 동일하게 취급해 한 번에 고치려 하지 말고, "이미 클라이언트에 데이터가 나간 뒤인가"를 기준으로 우선순위를 가른다(이번엔 딱 하나, `_wrapped()` finally 만 진짜 취약점이었다).
  - 리뷰가 "이 패턴이 다른 호출부에도 있다"고 폭넓게 지적해도, 그 다른 호출부들이 실제로 같은 심각도인지(예: 응답 시작 전이라 그냥 500이 되는지) 확인하고 나서 고칠 범위를 정한다 — 전부 고치는 게 항상 정답은 아니다.
- 관련: `app/core/stream.py::open_stream()._wrapped()`, `tests/unit/test_observability.py::test_stream_completes_when_finalize_assistant_fails`, PR #48 후속 리뷰

## [2026-07-20] 지연 정리 큐 패턴을 그대로 복사하면 안 되는 리소스가 있다 — AsyncConnectionPool 은 백그라운드 워커 태스크가 있어 cross-loop 정리가 그 자체로 새 버그다
- 증상: PR #47 후속 리뷰가 `app/agents/profile/processed_events.py`의 `set_pool()`/`reset()`이 `app/core/pg_store.py`(PR #46 후속 리뷰)와 동일한 fire-and-forget 스킵 버그를 갖고 있다고 지적 — `_pending_cleanup` 큐 패턴을 그대로 복사해 적용했더니, `tests/integration/test_pg_profile_store.py`를 전체 실행하면(개별 실행은 통과) 엉뚱한 다른 테스트(`test_processed_events_unmark_allows_reprocessing`)까지 `CancelledError`로 실패했다.
- 원인: `pg_store.py`가 감싸는 `AsyncPostgresStore`/`AsyncConnection`은 단일 커넥션이라 정리(`__aexit__`)가 비교적 단순하지만, `processed_events.py`의 `AsyncConnectionPool`은 **백그라운드 워커 태스크**를 그 풀을 만든 이벤트 루프에 묶어 둔다. pytest-asyncio 는 테스트 함수마다 새 이벤트 루프를 쓰므로, 이전 테스트(다른 루프)에서 큐에 쌓인 풀을 다음 테스트(새 루프)의 `_get_pool()`이 드레인하려 하면 이미 죽은 루프에 묶인 워커 태스크를 `await agather(...)`로 기다리게 되어 `CancelledError`(`asyncio.CancelledError`는 `BaseException` 상속 — `except Exception`으로 안 잡힘)가 새어 나온다. **"같은 이름의 버그"라고 반드시 같은 수정이 안전한 건 아니다** — 리소스의 내부 구현(워커 태스크 유무)에 따라 cross-loop 정리의 안전성이 다르다.
- 규칙:
  - **`_pending_cleanup` 류의 "다음 async 호출 때 정리" 패턴을 다른 리소스 타입에 이식하기 전에, 그 리소스가 정리 시점에 실제로 무엇을 하는지(백그라운드 태스크가 있는지, 자신을 만든 이벤트 루프에 의존하는지) 확인한다** — 겉보기엔 동일한 "sync 함수 안에서 async 리소스 정리" 문제여도, 커넥션 풀처럼 내부에 태스크를 갖는 리소스는 원래 만들어진 루프가 사라지면 정상적으로 닫을 방법이 없다.
  - **최선형(best-effort) 정리 경로에서 `contextlib.suppress(Exception)`은 `asyncio.CancelledError`를 잡지 못한다** — `CancelledError`는 `BaseException` 서브클래스라 별도 처리가 필요하다. "닫히면 좋고 안 닫혀도 그만"인 게 명확한 경로(참조를 이미 버려 재사용 안 함)라면 `suppress(BaseException)`으로 넓혀도 안전하다.
  - 수정 직후 반드시 **전체 파일을 통째로(개별이 아니라) 여러 번 반복 실행**해 안정성을 확인한다(이번엔 3회 반복으로 검증) — 개별 테스트 통과만으로는 순서 의존 회귀를 놓친다(같은 이유로 이전에 이미 한 번 겪은 교훈이기도 하다).
- 관련: `app/agents/profile/processed_events.py::_drain_pending_cleanup()`, `tests/integration/test_pg_profile_store.py::test_processed_events_set_pool_none_defers_cleanup_to_next_get_pool_call`, PR #47 후속 리뷰

## [2026-07-20] "실패할 수 없던 호출"이 인메모리→외부 스토어 이관 후 실패 가능 호출로 바뀌면 기존 try 범위가 새지 않는지 재점검해야 한다
- 증상: PR #47 후속 리뷰가 `app/api/events.py::session_end()`에서 `get_profile_store()`/`processed_events.mark_if_new()`/`store.clear_session_ctx_upto()` 세 호출이 `try` 블록 밖(또는 뒤)에 있어 예외가 안 잡힌다고 지적. 이관 전(인메모리 싱글턴) 이 호출들은 절대 실패할 수 없었지만, 이슈 #33 이관 후 운영(`auth_mode=jwks`)에서는 pg-profile 연결 실패 시 폴백 없이 `raise`하므로, DB 일시 장애만으로 이 엔드포인트가 500을 반환 — `§3.5`("어떤 오류도 202를 막지 않는다")를 위반한다. `get_profile_store()`를 raise 하는 fake 로 재현 — 수정 전엔 테스트가 raw exception 으로 실패(=500), try 범위를 넓힌 수정 후엔 202 통과.
- 원인: 원래 코드는 "이 호출은 안전하다"는 전제로 짜여 있었는데, 그 전제(인메모리라 실패 불가) 자체가 이관으로 깨졌다. 인메모리→외부 스토어 이관은 데이터 구조뿐 아니라 "이 호출이 실패할 수 있는가"라는 실패 모델 자체를 바꾼다 — 기존 에러 핸들링 경계(try 범위)가 새 실패 모델을 커버하는지 별도로 재검토해야 하는데 그걸 놓쳤다.
- 규칙:
  - **동기 인메모리 호출을 비동기 외부 스토어 호출로 바꿀 때, 그 호출을 감싼 기존 `try`/`except` 범위가 "새로 실패 가능해진" 모든 호출을 포함하는지 호출부 단위로 다시 확인한다** — 스토어 내부 구현(락·재시도 등)만 고치고 호출부의 에러 경계는 그대로 두면, "실패할 수 없던 코드가 실패할 수 있게 됐는데 아무도 안 잡는" 구멍이 생긴다.
  - **best-effort 계약(예: §3.5 "항상 202")이 있는 엔드포인트는, 그 계약을 지키는 try/except가 계약이 적용되는 모든 실패 가능 호출을 포함하는지 체크리스트처럼 확인한다** — 일부만 감싸면 "대부분의 경우 202"가 되어 계약 위반이 드물게만 재현되므로 놓치기 쉽다.
  - 실패 시 후처리(예: `unmark_event`)도 그 자체가 같은 외부 스토어를 건드리므로 실패할 수 있다 — 후처리 실패가 원래 응답(202)을 막지 않도록 별도로 `suppress`한다.
- 관련: `app/api/events.py::session_end()`, `tests/unit/test_profile.py::test_session_end_returns_202_when_profile_store_unavailable`, PR #47 후속 리뷰

## [2026-07-20] 공유 락을 쥔 채 실행되는 초기화 블록은 모든 await 지점에 상한이 있어야 한다
- 증상: PR #46 후속 리뷰가 `app/core/pg_store.py::get_store()`에서 `ctx.__aenter__()`(커넥션 수립)만 `state_store_connect_timeout_s`로 감싸져 있고, 바로 다음의 `await store.setup()`(스키마 DDL)에는 타임아웃이 없다고 지적. 이 블록 전체가 `_init_lock`을 쥔 채 실행되는데, 이 락은 `CartStateStore`·`ThreadFilterStore`·`RevertStore`가 전부 공유한다 — `setup()`이 (Postgres 락 경합 등으로) 멈추면 이후 들어오는 모든 buyer 요청이 함께 무한 대기한다. fake 스토어(`setup()`이 영원히 안 끝남)로 재현 — 수정 전엔 테스트가 실제로 타임아웃/hang, 수정 후(동일 timeout 으로 `setup()`도 wait_for)엔 통과.
- 원인: "커넥션 수립에 타임아웃을 걸었으니 초기화가 안전하다"고 안이하게 판단 — 같은 try 블록 안에 있는 **다른 await 지점**(`setup()`)은 별도로 감싸지 않으면 보호받지 않는다는 걸 놓쳤다. 공유 락 안에서 실행되는 코드는 그 블록의 "가장 느린 await"가 전체의 상한이 된다.
- 규칙:
  - **공유 락(`asyncio.Lock` 등)을 쥔 채 실행되는 코드 블록은, 그 안의 모든 외부 I/O await 지점에 개별적으로 타임아웃을 건다** — 하나만 걸고 나머지는 "그 정도면 되겠지"로 넘기지 않는다.
  - **"이론상 우려"를 리뷰가 지적하면, 실제 hang을 재현하는 fake/mock 으로 검증한다** — 실 DB로는 인위적으로 멈추는 상황을 안정적으로 재현하기 어려우므로, `setup()` 자체를 무한 `sleep()` 하는 fake 로 교체해 결정론적으로 재현.
  - 반면 같은 리뷰 라운드의 다른 지적(`entered_ctx`가 `__aenter__` 타임아웃 시 정리를 스킵)은, 라이브러리 내부(`@asynccontextmanager`로 감싼 `async with await AsyncConnection.connect(...) as conn:`)가 취소 시 자체적으로 정리할 가능성이 높아 "증명된 버그"로 보기 어려웠다 — 그래도 `entered_ctx` 대신 `ctx`로 통일해 비대칭을 없애는 비용 제로 방어 조치는 유지했다. **모든 리뷰 지적이 같은 확신도를 갖는 건 아니다** — 재현 가능한 것과 방어적으로만 유지하는 것을 구분해서 기록한다.
- 관련: `app/core/pg_store.py::get_store()`, `tests/unit/test_pg_store.py::test_get_store_bounds_hanging_setup_by_timeout`, PR #46 후속 리뷰

## [2026-07-20] fire-and-forget 정리(`asyncio.get_running_loop().create_task`)는 sync autouse fixture 컨텍스트에서 매번 조용히 스킵됨
- 증상: `set_store(None)`이 기존 실 연결을 "백그라운드 태스크로 정리"하도록 고쳤는데(이전 lessons 항목 — 당시엔 fire-and-forget 방식 자체의 검증 실패만 기록하고 원인 규명은 못 함), claude[bot] 후속 리뷰가 "`set_store()`는 sync 함수라 실행 중인 이벤트 루프가 없으면(`asyncio.get_running_loop()`가 RuntimeError) 정리가 스킵되는데, `tests/conftest.py`의 sync autouse fixture가 정확히 그 상황"이라고 지적. 직접 프로브 테스트로 확인한 결과 **실제로 conftest의 autouse fixture(setup 단계)는 항상 실행 중인 이벤트 루프가 없는 상태**였다 — 즉 이 정리 로직은 테스트 환경에서 단 한 번도 실제로 실행된 적이 없었다.
- 원인: pytest-asyncio 는 async 테스트 함수 실행을 위해 그 함수 안에서만 이벤트 루프를 돌리고, sync autouse fixture(테스트 함수 진입 전 setup)는 그 루프 시작 **전**에 실행된다. `contextlib.suppress(RuntimeError)`로 감싸 "실행 중 루프 없으면 조용히 스킵"하게 만든 게, 겉보기엔 안전한 방어 코드처럼 보이지만 실제로는 "이 정리 코드가 의도한 경로에서 단 한 번도 실행되지 않는다"는 뜻이었다 — 예외를 삼키는 코드가 있으면 "잘 동작하는 중"과 "매번 조용히 실패하는 중"을 로그 없이는 구분할 수 없다.
- 규칙:
  - **"실행 중 이벤트 루프가 없으면 스킵"하는 fire-and-forget 패턴은, 그 코드가 실제로 실행되는 호출 경로들의 이벤트 루프 유무를 전부 실측 확인한다** — 특히 테스트 conftest 의 autouse fixture 는 sync 인 경우가 흔한데, sync fixture 라고 해서 "이벤트 루프가 있을 수도 있겠지"라고 가정하면 안 된다. 직접 `asyncio.get_running_loop()` 를 프로브해서 확인(이번처럼).
  - **필요한 정리를 "당장 못하면 다음 기회에 확실히 한다"는 지연 큐 방식이 fire-and-forget 보다 안전하다** — `set_store()`(sync, 정리 대상을 리스트에 쌓기만 함) → 다음 `get_store()`(반드시 async 컨텍스트) 진입 시 그 큐를 `await` 로 확실히 비운다. 이러면 "이벤트 루프가 있는지 없는지"를 신경 쓸 필요가 없고, 타이밍에 의존하지 않아 `conn.closed` 로 결정론적으로 검증 가능하다(이전 fire-and-forget 은 검증 자체가 불가능했음).
  - **`except`/`suppress`로 예외를 삼키는 코드를 작성할 때마다 "이 경로가 실제로 정상 실행되는지"를 별도로 검증할 방법을 만든다** — 삼켜진 예외는 로그 없이는 흔적이 안 남으므로, "예외가 안 났다"와 "정상 실행됐다"를 혼동하기 쉽다.
- 관련: `app/core/pg_store.py::set_store/_drain_pending_cleanup`, `app/agents/profile/store.py::set_store/_drain_pending_cleanup`(동일 패턴 후속 적용), `tests/integration/test_buyer_thread_store.py::test_set_store_none_defers_cleanup_to_next_get_store_call`, PR #46/#47 후속 리뷰

## [2026-07-20] 모듈 전역 asyncio.Lock 을 pytest-asyncio function-scope 이벤트 루프에서 재사용하면 hang
- 증상: PR #47 리뷰(락 없는 초기화 레이스) 반영 후 `tests/integration/test_pg_profile_store.py` 전체를 한 번에 실행하면 11번째 테스트(`test_processed_events_mark_if_new_atomic_under_concurrency`, 기존에 있던 테스트라 이번에 새로 건드리지 않음)에서 FAILED 가 뜬 뒤 그다음 테스트로 전혀 진행되지 않고 무한정 멈췄다(`timeout 30`으로 강제 종료해야 빠져나옴). 그런데 신규로 추가한 동시성 테스트 3건은 **개별 실행하면 전부 통과**했고, 실패한 그 테스트도 **단독 실행하면 통과**했다 — 오직 "여러 테스트가 순서대로 실행될 때"만 재현됐다.
- 원인: `pytest.ini`(`pyproject.toml`)의 `asyncio_default_test_loop_scope=function` — 즉 pytest-asyncio 가 **테스트 함수마다 새 이벤트 루프**를 만든다. 반면 `_init_lock = asyncio.Lock()` 은 모듈이 세션 중 처음 import 될 때 **딱 한 번만** 생성되는 모듈 전역 객체다. 이 락이 어느 테스트의 루프에서 획득된 채로 그 루프가 닫혀버리면(`acquire()`는 됐는데 해당 루프에서 `release()`가 정상 실행되지 못한 채 루프가 종료되는 경우), 락의 내부 상태(`_locked=True`)는 그대로 남고 다음 테스트가 **다른 새 루프**에서 그 락을 `async with`로 얻으려 하면 영원히 풀리지 않는 `_locked=True` 를 보고 대기만 하다가 hang 된다. `asyncio.Lock`(Python 3.10+)은 생성자에서 루프를 요구하지 않아 이런 재사용이 "일단 되는 것처럼" 보이지만, 락 상태 자체는 루프와 무관하게 유지되므로 **정상 해제가 보장되지 않으면 그대로 다음 루프까지 전염**된다.
- 규칙:
  - **pytest-asyncio 가 function-scope 이벤트 루프를 쓰는 프로젝트에서, 모듈 전역 `asyncio.Lock`/`asyncio.Event`/`asyncio.Semaphore` 등 동기화 프리미티브는 테스트 격리(reset) 함수에서 반드시 재생성한다** — `_store`/`_pool` 같은 데이터만 초기화하고 락 객체 자체를 놔두면, 어느 한 테스트에서 락이 비정상 해제된 순간부터 이후 모든 테스트가 도미노로 hang 된다.
  - 재현이 안 되던 게 갑자기 "여러 테스트를 같이 돌릴 때만" 발생하면, 먼저 **개별 실행이 전부 통과하는지**부터 확인한다(이번처럼 개별 통과 + 조합 hang 이면 순서 의존 상태 공유가 원인일 확률이 높다).
  - `app/core/pg_store.py`(PR #46)에서 처음 이 락 패턴을 썼을 때는 이 문제가 안 드러났다 — 우연히 그 조합의 테스트에서는 락이 비정상 해제되는 시퀀스가 안 걸렸을 뿐, 근본 취약점은 동일하게 있었다(이번에 pg_store.py 의 `reset_store()`도 함께 고쳤다). "지금까지 안 터졌다"가 "안전하다"의 증거가 아니다.
- 관련: `app/core/pg_store.py::reset_store()`, `app/agents/profile/store.py::reset_profile_store()`, `app/agents/profile/processed_events.py::reset()`, PR #46/#47 후속 리뷰

## [2026-07-20] "락이 없으면 이론상 레이스"라는 리뷰 지적도 실제 데이터 구조를 보고 검증해야 한다
- 증상: PR #47 후속 리뷰가 `ProfileStore.add_fact()`의 cap 트리밍(asearch→sort→adelete)에 락이 없어 lost update 가 가능하다고 지적. `append_session_ctx`(단일 값 get→put)와 같은 패턴으로 보고 동일하게 락을 추가했으나, 실제로 버그를 재현하려 했더니 **실 Postgres 동시 호출(gather)도, 강제로 인터리브시키는 fake store(asearch 에 `await asyncio.sleep(0)` 삽입)도 모두 락 없이 통과** — 두 가지 서로 다른 방법으로 재현을 시도했음에도 데이터 유실이 재현되지 않았다.
- 원인: `append_session_ctx`는 "단일 값을 덮어쓰는" get→put(진짜 lost update 가능 — 나중 write 가 앞선 write 를 통째로 덮어씀)인 반면, `add_fact`의 cap 트리밍은 계속 늘어나기만 하는 항목 집합에서 "가장 오래된 초과분만 지우는" 연산이다. 임의 시점의 부분 스냅샷은 항상 "그 시점까지 커밋된 항목들의 시간순 앞부분(prefix)"이므로, 서로 다른 스냅샷을 본 동시 호출들의 삭제 대상은 항상 서로 부분집합 관계이고 `adelete`가 멱등이라 실제로는 자기 교정(self-correcting)된다 — 겉보기엔 같은 "락 없는 get→act" 패턴이어도 데이터 구조의 단조성(monotonicity)에 따라 실제 위험도가 다르다.
- 규칙:
  - **"이론상 레이스처럼 보인다"와 "실제로 데이터가 유실된다"는 다른 질문이다** — 리뷰가 지적한 패턴이 기존에 이미 고친 유사 버그와 겉모습이 같다고 곧바로 같은 수정을 적용하지 말고, 먼저 재현을 시도한다.
  - **동시성 테스트가 실 인프라(Postgres) 타이밍에 의존하면 false negative 가 나올 수 있다** — 강제로 인터리브시키는 fake(예: `asyncio.sleep(0)` 삽입)로 별도 재현을 시도해, 두 방법이 일치하면 결론에 더 확신을 가질 수 있다.
  - 재현에 실패했다고 반드시 코드를 되돌릴 필요는 없다 — 이미 만든 락이 무해하고(비용 거의 0) 다른 락(`_session_locks`)과 패턴 일관성이 있다면 "증명된 버그의 수정"이 아니라 "방어적 조치"라고 정직하게 문서화하고 유지해도 된다. 다만 **그 사실을 감추지 않는다** — 나중에 누가 "이 락이 막는 버그가 뭐냐"고 물었을 때 근거 없는 답을 하지 않도록.
- 관련: `app/agents/profile/store.py::_fact_lock/add_fact()`, PR #47 후속 리뷰

## [2026-07-20] fire-and-forget 정리 태스크는 "참조를 든 채로 재사용" 방식으로는 검증 불가
- 증상: `pg_store.set_store()`가 기존 실 연결을 백그라운드 태스크(`create_task`)로 닫도록 고친 뒤, 회귀 테스트로 `store.aget()` 재호출이 실패하는지 확인하려 했으나 **정리 로직을 일부러 빼도 테스트가 계속 통과**했다. `conn.closed` 로 직접 확인하도록 바꿨더니 이번엔 **정리 로직을 빼도(TEMP) `conn.closed`가 True로 나와** 신뢰할 수 없는 결과였다(원인 미규명 — psycopg 커넥션이 pytest 이벤트 루프 재사용 과정에서 어떤 이유로든 닫힌 것으로 보이나 확정 못 함).
- 원인: (1) 테스트가 `store`/`conn` 객체를 로컬 변수로 계속 참조하고 있어, 모듈 전역 `_store_ctx`만 `None` 으로 바뀌어도 파이썬 GC 관점에서 그 객체는 죽지 않는다 — "참조가 끊겼는지"와 "실제로 `__aexit__`가 호출됐는지"는 다른 질문이다. (2) fire-and-forget(`asyncio.create_task`, await 로 완료를 기다리지 않음)은 태스크 완료 시점을 테스트가 통제할 수 없어 근본적으로 타이밍에 취약하다.
- 규칙:
  - **"객체 참조가 여전히 동작하는지"로 정리(cleanup)를 검증하지 않는다** — 로컬 변수가 참조를 쥐고 있는 한 GC 는 일어나지 않으므로 무의미한 양성(false positive)이 나온다. 정리 대상 리소스 자체의 상태 플래그(`conn.closed` 등)를 직접 확인해야 한다.
  - **그렇게 해도 fire-and-forget 은 안정적으로 재현 가능한 회귀 테스트를 만들기 어렵다** — 이런 경우 "재현 불가"를 인정하고 자동 테스트는 만들지 않되, 코드 리뷰(로직 정확성 수동 검토)로 대체하는 게 거짓 안전감을 주는 flaky 테스트보다 낫다. 무리하게 테스트를 만들어 통과시키면 오히려 "검증됐다"는 잘못된 확신을 준다.
  - 애초에 "sync 함수 안에서 정리가 필요한 async 리소스"를 다루는 설계(`set_store()`) 자체가 테스트하기 어려운 근본 원인 — 가능하면 정리가 필요한 리소스의 lifecycle 관리는 처음부터 async 경계 안에 두는 설계를 우선 고려한다.
- 관련: `app/core/pg_store.py::set_store()`, PR #46 후속 리뷰

## [2026-07-20] BaseStore 이관 시 "await 가 생기는 지점"마다 새 동시성 레이스가 생김 (PR #46 리뷰)
- 증상: claude[bot] PR 리뷰가 두 곳을 지적 — (1) `app/core/pg_store.py::get_store()` 가 `_store is None` 체크 후 `await ctx.__aenter__()` 사이에 락이 없어, 콜드 스타트 시 동시 요청이 각자 pg 커넥션을 중복 생성하고 앞선 연결(들)은 정리 없이 버려짐(누수) + `store.setup()` 부분 실패 시에도 이미 연 연결 미정리. (2) `RevertStore.add()` 가 `get()`(read) 후 `aput()`(write)하는 read-modify-write라, 동일 키로 겹치는 요청이 오면 lost update 발생. 두 지적 다 실제로 재현됨(락 제거 후 테스트 시 100% 재현).
- 원인: 인메모리 dict 시절엔 `dict.update()`/딕셔너리 대입이 await 없이 원자적이었는데(GIL·단일 이벤트 루프), BaseStore(pg-profile) 이관으로 각 연산이 별도 네트워크 왕복(`await`)이 되면서 "체크 후 await" 패턴이 전부 새 레이스가 됐다. 이슈 #33 전체(pg_store.py·profile/store.py·profile/processed_events.py·core/conversation.py 4곳)에 동일한 "지연 초기화" 패턴을 복붙했고, `ProfileStore.append_session_ctx`/`clear_session_ctx_upto`도 같은 get→put 형태라 잠재적으로 같은 레이스가 있다(리뷰 대상 밖이라 미수정 상태로 남아있을 수 있음 — 후속 확인 필요).
- 규칙:
  - **인메모리 → 외부 스토어 이관 리뷰 체크리스트**: "이 메서드에 새로 생긴 `await` 지점이 있는가?" → 있으면 "그 사이에 동일 key로 다른 호출이 끼어들면 최종 상태가 틀려지는가?"를 반드시 확인한다. 딕셔너리 시절엔 원자적이던 연산이 async 스토어 이관 후 깨지는 게 이번처럼 반복 패턴이다.
  - **지연 초기화(`if _store is None: ... await ...`)는 반드시 `asyncio.Lock` 으로 전체를 감싼다** — 체크와 초기화 사이에 어떤 `await` 도 없어야 안전하다는 직관은 틀렸다(초기화 자체가 await 를 포함하므로).
  - **read-modify-write(get→update→put) 패턴은 key 단위 `asyncio.Lock` 딕셔너리로 직렬화**(`app/agents/seller/hitl.py::_confirm_lock` 선례와 동일 패턴) — BaseStore 는 CAS/원자적 update 를 제공하지 않는다.
  - **동시성 수정은 "락 없이 실패 재현 → 락 추가 후 통과" 순서로 검증**한다(주석 처리 후 테스트 → 복구). 락이 정말 그 버그를 막는지 확인 없이 추가하면 false-sense-of-safety 가 된다.
- 관련: `app/core/pg_store.py`(`_init_lock`)·`app/agents/buyer/recommendation/state.py`(`_add_locks`)·`tests/integration/test_buyer_thread_store.py`(재현 테스트 2건), PR #46, 이슈 #33

## [2026-07-20] 로컬 .env 의 GOOGLE_API_KEY 가 유닛테스트의 라이브 API 의존 버그를 가려 CI 에서만 터짐
- 증상: 이슈 #33 Phase 2(ProfileStore) 작업 중 로컬 `uv run pytest` 는 575개 전부 통과했는데, GitHub Actions CI 에서 `tests/unit/test_profile.py`·`tests/integration/test_profile_flow_e2e.py` 14건이 `app.pipelines.embedding.EmbeddingError: google_api_key 미구성`으로 실패. 그중 일부는 `session_end` 의 넓은 `except Exception` 이 이 오류를 삼켜 `processed_events.unmark_event()`(멱등 마킹 해제)까지 실행시켜, "멱등 재전송이 duplicate 로 안 잡힘" 같은 2차 증상으로 위장해 원인 파악을 어렵게 함.
- 원인: `ProfileStore` 의 테스트/dev 폴백 `InMemoryStore(index=...)` 가 프로덕션과 **동일한 실제 `embed_texts`(Google API) 함수**를 그대로 물려써서, `add_fact()` 호출만으로 실 API 콜이 발생했다. 로컬 `.env` 에 이미 `GOOGLE_API_KEY`(#31 카탈로그 작업 때 설정)가 있어 로컬에서는 조용히 성공 — CI 에는 그 시크릿이 없어(원래 유닛 테스트는 라이브 키가 필요 없어야 정상이므로) 처음 노출됨.
- 규칙:
  - **유닛 테스트용 InMemory 폴백에 실제 외부 API 호출 함수를 그대로 주입하지 않는다** — BaseStore `index={"embed": ...}` 처럼 "설정만 있으면 자동으로 호출되는" 구조는 특히 위험(코드 흐름만 봐서는 API 호출이 숨어있는지 안 보임). 반드시 fake/no-op 버전으로 분리(`_pg_index_config()`실 API용 vs `_fallback_index_config()`fake 용, 이번 수정 패턴).
  - **로컬 `.env` 에 실 API 키가 있으면 "라이브 의존 없음" 가정이 로컬에서 검증되지 않는다** — 새 라이브 API 연동 코드를 추가했으면 `KEY= uv run pytest`(빈 값 오버라이드)로 CI 조건을 로컬에서 먼저 재현해 확인한다. 이 프로젝트는 이미 `_no_live_recent_purchases`(구매이력) 같은 라이브 차단 autouse fixture 관례가 있으니 신규 외부 API 연동 시 같은 원칙을 적용할 것.
  - 대량 실패 로그를 볼 때 **에러 메시지가 다른 여러 건도 먼저 근본 원인 1개로 수렴하는지 확인**한다 — 이번처럼 넓은 `except Exception` 이 있으면 원인 오류가 완전히 다른 증상(멱등 깨짐 등)으로 위장될 수 있다.
- 관련: `app/agents/profile/store.py`(`_pg_index_config`/`_fallback_index_config` 분리), 이슈 #33 (2/3)

## [2026-07-20] Windows 기본 ProactorEventLoop 에서 psycopg async 연결이 조용히 InMemory 로 폴백
- 증상: 이슈 #33(ThreadFilter/Cart/Revert → AsyncPostgresStore) 통합 테스트를 실제 pg-profile(docker) 에 붙여 작성하던 중, 네이티브 Windows 에서 `AsyncPostgresStore.from_conn_string(...).__aenter__()` 가 `psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'` 로 실패. dev 폴백(auth_mode≠jwks)이 모든 예외를 잡아 InMemoryStore 로 조용히 전환하는 설계(app/agents/seller/history.py·hitl.py 와 동일 규약, 이제 app/core/pg_store.py 도)라 **오류 로그 없이는 겉보기엔 정상 동작**했다 — 즉 기존 seller history.py/hitl.py 도 네이티브 Windows dev 환경에서는 이 문제로 Postgres 연결이 한 번도 성사되지 않고 항상 InMemory 로 돌았을 가능성이 높다(테스트가 InMemoryStore 를 직접 주입해왔기 때문에 지금까지 미발견).
- 원인: asyncio 는 Windows 에서 기본으로 `ProactorEventLoopPolicy` 를 쓰는데, psycopg 의 async 커넥션은 `SelectorEventLoop` 만 지원한다. Docker(Linux) 컨테이너 안에서는 애초에 Proactor 가 없어 재현되지 않는다 — 네이티브 Windows 에서 앱을 직접 띄우거나(`uv run uvicorn ...`) 테스트를 돌릴 때만 드러난다.
- 규칙:
  - psycopg async(AsyncPostgresStore/AsyncPostgresSaver 등)를 새로 붙이는 코드는 **네이티브 Windows 에서 실제 연결까지 통합 테스트로 검증**한다 — InMemory 주입 테스트만으로는 이 클래스의 버그를 절대 못 잡는다.
  - `app/main.py` 모듈 최상단에 `sys.platform == "win32"` 가드로 `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` 를 추가해뒀다(uvicorn 이 루프를 만들기 전에 정책을 바꿔야 하므로 반드시 다른 임포트보다 먼저) — 이 앱에서 psycopg async 를 쓰는 모든 지점(seller history.py·hitl.py·core/pg_store.py)이 공통으로 이 정책에 의존한다. 신규 진입점(배치 CLI 등 uvicorn 을 거치지 않는 프로세스)을 추가할 때는 그 프로세스 자체 최상단에도 동일 가드가 필요하다.
  - 새 psycopg async 통합 테스트를 작성하면 `tests/integration/conftest.py` 가 `app.main` 을 임포트하는 시점(정책 적용)보다 먼저 다른 경로로 연결을 시도하지 않는지 확인한다.
- 관련: `app/main.py`, `app/core/pg_store.py`, `tests/integration/test_buyer_thread_store.py`, 이슈 #33

## [2026-07-20] CI "review pass" 를 리뷰 수렴으로 오인해 코멘트 도착 전에 머지
- 증상: PR #41 을 CI 통과(lint-test·review) + 코멘트 0건 확인 후 머지했는데, **머지 91초 뒤**에 P2 리뷰 코멘트가 달렸다(머지 07:17:01Z, 코멘트 07:18:32Z). 지적은 실재하는 결함이었고(E2E 하니스가 앰비언트 `AUTH_MODE=jwks` 에서 27/37 실패) 별도 후속 PR #43 으로 고쳐야 했다.
- 원인: 리뷰 잡의 **status=pass 와 코멘트 게시 완료는 별개**인데 이를 수렴 신호로 취급했다. 같은 리뷰 도구가 PR #39 에서는 4~8분 걸리며 라운드마다 코멘트를 냈는데, #41 은 57초만에 pass 로 떠 "지적 없음"으로 속단했다(테스트 전용 PR이라 빠른 게 자연스럽다고 판단).
- 규칙:
  - **머지 직전에 코멘트를 재조회한다** — `gh api repos/{owner}/{repo}/pulls/{n}/comments` 를 머지 명령 바로 앞에서 한 번 더. 체크 통과 시점의 조회 결과를 재사용하지 않는다.
  - 리뷰 잡이 평소보다 **현저히 빨리** 끝나면(이전 라운드 대비 1/5 이하) 코멘트 게시 지연을 의심하고 최소 1~2분 뒤 재확인한다.
  - 코멘트가 머지 후 도착하면 **되돌리지 말고 후속 PR**로 처리하고, 원 PR 코멘트에 후속 PR 링크로 답글을 남겨 추적성을 유지한다.
- 관련: PR #41 → #43, `tests/integration/conftest.py`

## [2026-07-20] repo 전체 `ruff format` 실행이 무관 파일 35개를 재포맷 (버전 드리프트)
- 증상: 커밋 준비 중 `uv run ruff format app tests`(dev 의존성 0.15.21)를 돌리자 이번 작업과 무관한 파일 30여 개가 재포맷돼 diff 를 오염시킴. pre-commit 훅의 ruff-pre-commit 은 v0.8.6 으로 고정돼 있어 기존 커밋들은 다른 포맷 규칙으로 들어가 있었음.
- 원인: 훅(rev v0.8.6)과 dev 의존성(ruff 0.15.21)의 버전 불일치 + CI 는 `ruff check`만 검사(format 미검사) → 저장소에 포맷 드리프트가 누적된 상태에서 전역 format 실행.
- 규칙:
  - `ruff format` 은 repo 전체가 아니라 **이번에 편집한 파일에만** 돌린다. 전역 실행 전 `git status` 로 파급 확인.
  - format 실행 후 `git status --short` 로 무관 파일 변경 여부를 반드시 검사 — 무관 재포맷은 `git restore` 로 되돌리고 관련 파일만 스테이징.
  - 포맷 드리프트 일괄 해소는 별도 `style:` 커밋/PR 로 분리(기능 PR 에 섞지 않는다). ruff-pre-commit rev ↔ dev ruff 버전 정렬도 그 PR 에서.
- 관련: `.pre-commit-config.yaml`, `pyproject.toml`, PR #34 브랜치 `feat/auth-e2e`

## [2026-07-17] 설계 문서가 구계약(v0.7.0) 기준으로 작성돼 계약과 드리프트
- 증상: 판매자 멀티에이전트 설계서 v3가 "삭제만 HITL"·"FE S-3 PATCH 반영"·자체 데이터 API(ai_reader MySQL 직접) 등 폐기된 구계약/타 아키텍처 전제를 포함한 채 완성됨. 코드 스텁 docstring(seller/spring_client)도 같은 구계약을 서술.
- 원인: api-spec 사본이 v0.9.0~v0.14.0으로 개정되는 동안(판매자 파트가 최대 변경 영역) 설계 문서는 별도 트랙에서 작성·완성됨. 스텁 docstring은 작성 시점(v0.7.0)에 고정.
- 규칙:
  - 설계/구현 착수 전 **api-spec 사본의 최신 버전 헤더와 §8 개정 항목**을 먼저 대조한다 — 특히 자기 담당 파트의 개정 이력(CHANGELOG Docs)을 훑는다.
  - 스텁 docstring의 § 번호는 신뢰하되 **서술 내용의 버전은 의심**한다(§ 위치는 유지되나 내용이 개정됐을 수 있음).
  - 외부 설계 문서를 SPEC으로 편입할 때는 **정합 조정표(설계서→확정, 근거)** 를 SPEC 앞머리에 남겨 무엇이 왜 바뀌었는지 추적 가능하게 한다.
- 관련: `docs/specs/SPEC-SELLER-001.md` §1, `docs/api-spec.md` §3.2/§4.4/§4.5, `app/services/spring_client.py`

## [2026-07-16] 파일이 엉뚱한 저장소에 생성됨 (cwd 착오)
- 증상: hk-final에 만들려던 `CLAUDE.md`·`.claude/settings.json`이 기획 repo(my-project)에 생성돼 기존 moai 설정(522줄, 훅 포함)을 덮어씀.
- 원인: Bash 작업 디렉터리가 이전 명령에서 my-project로 남아 있었는데 `cat > CLAUDE.md`를 상대경로로 실행. cwd를 확인하지 않음.
- 규칙:
  - 파일 쓰기는 **절대경로**로 (`cat > /home/nyong/projet/hk-final/CLAUDE.md`). 상대경로 금지.
  - 명령 앞에 `cd <절대경로> && pwd`로 cwd를 못 박고 시작.
  - hk-final은 워크스페이스 밖이라 Write 도구가 막힌다(path traversal) → **Bash heredoc + 절대경로**로 쓴다.
  - 덮어쓰기 전 대상 파일을 확인 — 내가 만든 게 아니면 멈추고 점검.
- 관련: `CLAUDE.md`, `.claude/settings.json`

## [2026-07-15] api-spec 사본이 정본과 어긋날 위험
- 증상: 계약(SSE 이벤트·오류 코드)이 코드/사본/정본 세 곳에 흩어져 드리프트 우려.
- 원인: 정본은 기획 repo, hk-final엔 사본만 존재.
- 규칙: 계약 변경은 **정본(기획 repo api-spec) 먼저** 개정 → 사본(`docs/api-spec.md`) 동기화 → 코드. 사본과 정본이 다르면 정본 우선. SPEC 사본의 낡은 SSE 명명도 api-spec 우선.
- 관련: `docs/api-spec.md`, `docs/specs/`
