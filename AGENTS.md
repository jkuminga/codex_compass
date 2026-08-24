# 필수지침

- DB 필드, 객체, 함수 등 프로젝트 전반에서 사용하는 핵심 키워드는 간단한 설명을 함께 제공한다.
- 설계·대화 문서화: 사용자가 설계 내용이나 대화 결과를 문서로 만들어 달라고 요청하면 `html-artifacts` 스킬을 사용해 `.html`로 생성한다.
- 문서 인덱스: `docs/`의 문서를 생성·이름 변경·삭제할 때는 같은 작업에서 `docs/docs-index.html`의 카드 목록과 문서 수를 갱신한다. 각 카드는 상대 링크, 문서 형식과 아주 쉬운 한 줄 요약을 포함한다.

## 프로젝트 작업 시작

- 설계·조사·문서·코드·검증처럼 프로젝트 결과나 진행 상태를 바꾸는 요청은 UPS의 `<work-selection-context>`가 있을 때 `$work-start` 스킬을 실행하고 해당 패킷을 입력으로 사용한다.
- 프로젝트 작업인데 UPS 상태 패킷이 없으면 WorkItem과 Run을 안전하게 결정할 수 없음을 사용자에게 알리고 프로젝트를 변경하지 않는다.
- `$work-start` 스킬을 불러오거나 `start_work()`까지 완료할 수 없으면 프로젝트 변경을 중단하고 원인을 알린다.

## 도구 호출 시 고려사항

- 파일과 Git 상태를 바꿀 때는 이동·백업·`git revert`처럼 복구 가능한 방법을 우선한다.
- `rm -rf`, `git reset`, `git clean`, 강제 push처럼 복구가 어렵거나 넓은 범위를 덮어쓰는 명령은 호출하지 않는다.
- 위험 명령이 필요해 보이면 실행하거나 셸·스크립트로 우회하지 말고, 필요한 이유와 대상 범위를 사용자에게 알린다.

## 장기 기억 후보 기록

작업 중 이후 세션에서도 다시 도움이 될 만한 정보를 발견하면 `long-term/`에 JSON 파일로 기록한다.

기록 대상:

- 오류·문제의 원인과 해결 방법
- 장기적으로 유효한 프로젝트 진행 정보와 결정
- 다시 사용할 기술 지식과 구현 방법
- 유용한 명령어와 사용 조건

일회성 작업 내용, 단순 진행 로그, 추측, 비밀정보는 기록하지 않는다. 한 파일에는 하나의 독립적인 기억만 저장하고, 가능하면 해당 판단의 근거가 되는 파일·명령·테스트를 함께 기록한다.

파일명은 `<UTC 시각>-<짧은 설명>.json` 형식을 사용한다.

```text
long-term/
├── 20260809T142301Z-auth-token-refresh.json
├── 20260809T143522Z-test-command.json
└── 20260809T150104Z-project-state-store-decision.json
```

JSON은 다음 구조를 사용한다. `problem`과 `solution`은 해당되는 경우에만 넣는다.

```json
{
  "schema_version": 1,
  "id": "ltm_auth_token_refresh",
  "created_at": "2026-08-09T14:23:01Z",
  "kind": "problem_solution",
  "title": "인증 토큰 만료 문제 해결",
  "summary": "짧은 토큰 만료 문제를 refresh token 방식으로 해결했다.",
  "problem": "인증 토큰의 만료 시간이 짧아 사용자가 자주 로그아웃됐다.",
  "solution": "refresh token과 token rotation을 적용했다.",
  "evidence": [
    {
      "type": "file",
      "ref": "src/auth/token.ts"
    },
    {
      "type": "test",
      "ref": "npm test -- auth"
    }
  ]
}
```

필드와 허용값:

- `schema_version`: JSON 구조의 버전
- `id`: 기억을 구분하는 고유 이름
- `created_at`: 기억을 기록한 시각
- `kind`: 기억의 종류. `problem`, `problem_solution`, `project_progress`, `decision`, `technology`, `command` 중 하나를 사용한다.
- `title`: 검색하기 쉬운 짧은 제목
- `summary`: 나중에 읽어도 이해되는 핵심 내용
- `problem`: 발견한 문제나 원인
- `solution`: 적용한 해결 방법
- `evidence`: 기억이 사실임을 확인할 수 있는 파일·명령·테스트 목록
- `ref`: 근거의 실제 위치나 실행 명령
