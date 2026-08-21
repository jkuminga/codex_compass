# MemoryGraph version selection

조사 기준일: 2026-08-21  
대상 사용법: `SQLiteBackend`와 `searchMemories()`를 직접 import하는 로컬 Recall wrapper

## 결론

Git submodule은 다음 릴리스에 고정하는 것을 권장한다.

- 릴리스 태그: `v0.13.0`
- submodule이 기록할 실제 커밋: `67fc4f97816aa75823ae2dd4eff49f150cc6f1da`
- annotated tag 객체: `8faa9db85125280cfa204e57a05c52b6bcd47ba0`

`v0.13.0`은 annotated tag이므로 태그 객체와 실제 소스 커밋의 SHA가 다르다. Git submodule에는 실제 커밋 `67fc4f...`가 기록된다. [공식 v0.13.0 릴리스](https://github.com/memory-graph/memory-graph/releases/tag/v0.13.0), [실제 릴리스 커밋](https://github.com/memory-graph/memory-graph/commit/67fc4f97816aa75823ae2dd4eff49f150cc6f1da)

## 추천 이유

1. `v0.13.0`은 조사 시점의 최신 정식 릴리스이며 prerelease가 아니다. 이 버전에서 프로젝트가 Python MCP 서버에서 TypeScript/Bun CLI로 전환됐고, SQLite 백엔드와 프로그램 방식의 라이브러리 export가 공식적으로 추가됐다. 이전 `v0.12.4`는 Python 세대라 현재 wrapper 설계와 맞지 않는다. [공식 변경 기록](https://github.com/memory-graph/memory-graph/blob/v0.13.0/CHANGELOG.md)

2. 우리가 필요한 `SQLiteBackend` 클래스와 공개 메서드 `searchMemories()`가 이 버전에 실제 구현되어 있다. `SQLiteBackend`는 Bun 내장 SQLite를 사용하며 `connect()`, `initializeSchema()`, `searchMemories()`를 제공한다. [SQLiteBackend 소스](https://github.com/memory-graph/memory-graph/blob/v0.13.0/ts/src/backends/sqlite.ts)

3. 공식 릴리스는 12개 테스트 파일, 97개 테스트, 242개 assertion 통과와 깨끗한 TypeScript typecheck를 명시한다. 저장소의 SQLite 전용 테스트는 연결·스키마 생성, 저장, 문자열 검색, 태그 검색, 수정, 삭제, 관계, 통계와 health check를 검증한다. [공식 릴리스 노트](https://github.com/memory-graph/memory-graph/releases/tag/v0.13.0), [SQLite 테스트](https://github.com/memory-graph/memory-graph/blob/v0.13.0/ts/tests/sqlite-backend.test.ts)

4. 로컬에서도 정확히 `v0.13.0`을 checkout한 뒤 Bun 1.3.9로 `bun install --frozen-lockfile`, `bun test`, `bun run typecheck`를 실행했다. 결과는 97/97 테스트 통과, 0 실패, typecheck 통과였다. 앞서 Harness_v2에서 수행한 SQLite 저장·Recall·관계 smoke test도 성공했다.

5. 정식 태그를 고정하면 upstream `main`의 추후 변경이 submodule에 자동 유입되지 않는다. 직접 내부 모듈을 import하는 현재 구조에서는 이 재현성이 특히 중요하다.

## 왜 최신 main 커밋이 아닌가

`v0.13.0` 직후 `b7ac26020611dfe54d75e2bd9c2a3b095c317451`에서 adversarial review로 발견된 23개 문제가 수정됐다. 여기에는 SQLite 검색의 `OFFSET` 반영과 관계 탐색 깊이 제한도 포함된다. 이 커밋 역시 로컬에서 97/97 테스트와 typecheck가 통과했다. [공식 수정 커밋](https://github.com/memory-graph/memory-graph/commit/b7ac26020611dfe54d75e2bd9c2a3b095c317451)

그럼에도 초기 pin은 `v0.13.0`을 권장한다.

- `b7ac260`은 아직 새 버전 태그로 릴리스되지 않은 커밋이다.
- 초기 Recall wrapper는 `offset`을 쓰지 않고, 작은 `limit`으로 첫 페이지의 `searchMemories()`만 호출한다.
- 초기 Recall 경로에서는 관계 BFS도 사용하지 않으므로 SQLite 관련 post-release 수정 두 가지가 필수 조건은 아니다.
- 최신 main보다 정식 릴리스 태그가 버전 의미와 재현성이 명확하다.

다만 `v0.13.1` 이상의 새 정식 릴리스에 `b7ac260` 수정이 포함되면 업그레이드를 우선 검토하는 것이 좋다.

## 구현 시 주의점

### 최상위 index를 import하지 않는다

`v0.13.0`의 `ts/src/index.ts`는 `SQLiteBackend`를 export하지만, 파일 끝에서 CLI `main()`을 무조건 실행한다. 또한 그 파일의 `VERSION` 값은 실수로 `0.12.4`에 머물러 있다. Wrapper가 이 파일을 import하면 원치 않는 CLI 실행이 발생할 수 있다. [최상위 index 소스](https://github.com/memory-graph/memory-graph/blob/v0.13.0/ts/src/index.ts)

따라서 다음 구체적인 모듈을 직접 import해야 한다.

```text
vendor/memory-graph/ts/src/backends/sqlite.ts
```

`SQLiteBackend`: MemoryGraph 기억을 프로젝트 전용 SQLite 파일에 저장하고 조회하는 백엔드 클래스다.

### Bun 버전을 명시한다

공식 패키지는 Bun `>=1.1.0`을 요구한다. 재현성을 더 높이려면 Harness의 개발 환경에서 실제 검증한 Bun 버전도 별도로 고정하거나 최소 지원 버전과 CI 검증 버전을 명시하는 편이 안전하다. [공식 package.json](https://github.com/memory-graph/memory-graph/blob/v0.13.0/ts/package.json)

### upstream CI 증거의 한계

`v0.13.0` 저장소에는 GitHub Actions workflow가 없다. 따라서 공식 릴리스에 적힌 테스트 결과와 이번 로컬 재실행을 근거로 삼았으며, submodule 연결 후 Harness 자체 테스트에서 SQLite 직접 import와 Recall wrapper 동작을 고정해야 한다.

## 최종 pin 값

```text
repository: https://github.com/memory-graph/memory-graph.git
path:       vendor/memory-graph
tag:        v0.13.0
commit:     67fc4f97816aa75823ae2dd4eff49f150cc6f1da
```
