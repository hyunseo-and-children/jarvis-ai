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
