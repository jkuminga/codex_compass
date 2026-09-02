# Draft WorkItem 결정사항

## 목적

사용자가 웹 콘솔에서 해야 할 일을 메모하듯 가볍게 만들고, Codex가 나중에 실행 가능한 WorkItem으로 구체화한다.

Draft WorkItem은 정보가 빠진 작업이 아니라, 아직 실행 계획과 완료 조건을 다듬지 않은 WorkItem이다.

## 생성 시 입력

| 항목 | 필수 | 설명 |
| --- | --- | --- |
| `title` | 예 | 작업의 한 줄 제목 |
| `kind` | 예 | 작업 성격. 기존 enum에서 사용자가 선택한다. 예: `research`(조사), `implementation`(구현), `decision`(설계·결정) |
| `goal` | 예 | 이 작업으로 얻고 싶은 결과 |
| `description` | 아니오 | 배경, 아이디어, 참고사항을 자유롭게 남기는 메모 |

웹 콘솔에서는 `kind`를 드롭다운으로 고른다. 이후 fzf 입력 흐름을 추가한다면 방향키 선택을 우선하고, 어렵다면 번호 입력 방식으로 제공한다.

## 저장 규칙

기존 `work_items` 테이블에 아래 두 컬럼을 추가한다.

| 컬럼 | 값 | 설명 |
| --- | --- | --- |
| `description` | `NULL` 허용 | 선택 메모. 작성하지 않아도 Draft WI를 만들 수 있다. |
| `is_draft` | `true` 또는 `false` | Codex의 구체화 전인지 나타내는 표시값. 추측으로 판단하지 않고 이 값으로 명시한다. |

Draft를 만들 때의 기본값은 다음과 같다.

```text
is_draft = true
status = backlog
priority = normal
feature_id = NULL
next_action = NULL
acceptance criteria = 없음
```

## Codex 구체화 이후

Codex는 Draft의 제목·종류·목표·선택 메모를 읽고 필요한 경우 다음 정보를 채운다.

- `priority`: 다른 작업과 비교한 우선순위
- `feature_id`: 속할 큰 기능 묶음(Feature)
- `next_action`: 바로 시작할 수 있는 다음 행동
- Acceptance Criteria(AC): 완료 조건 목록

구체화가 끝나면 `is_draft = false`로 바꾸고, 실행 준비가 되었으면 `status = ready`로 바꾼다.

## 아직 하지 않는 것

- 사용자가 모든 WI 필드와 AC를 직접 입력하게 만들지 않는다.
- `description`이 비어 있다고 Draft 여부를 추측하지 않는다.
- fzf에서 새 WI를 만드는 확장 기능은 웹 콘솔의 Draft 생성 흐름을 재사용하는 후속 작업이다.

## 다음 구현 체크리스트

- [x] `work_items`에 `description`과 `is_draft` 컬럼을 추가한다.
  - `description`: 선택 메모
  - `is_draft`: Codex 구체화 전인지 나타내는 표시값
- [x] Draft 생성 규칙을 구현한다.
  - `status = backlog`, `is_draft = true`, AC 없음
- [x] Draft 구체화 규칙을 구현한다.
  - Codex가 `priority`, `next_action`, AC를 채운다.
  - 구체화가 끝나면 `is_draft = false`로 바꾼다.
  - 실행 준비가 끝났을 때만 `status = ready`로 바꾼다.
- [x] Draft 전용 상태 저장소·MCP 도구를 구현한다.
  - `create_draft_work_item()`: 사용자가 입력한 제목·종류·목표·선택 메모로 Draft를 만든다.
  - `refine_draft_work_item()`: Codex가 실행 계획과 완료 조건을 보강한다.
- [ ] 웹 콘솔 첫 연결 범위를 구현한다.
  - Draft 생성 모달 → SQLite 저장 → 목록의 Draft 배지 표시

## 웹 콘솔 구현 전 결정 체크리스트

- [x] 웹 앱과 HTTP API를 둘 기술 및 폴더 구조를 정한다.
  - Python `FastAPI` 기반의 로컬 단일 웹 앱으로 만든다.
  - `FastAPI`: 브라우저 요청을 받아 화면과 HTTP API를 제공하는 Python 웹 서버다.
  - API와 정적 화면을 한 서버에서 함께 제공한다. 별도 React/Vite 프로젝트나 별도 Node 서버는 현재 만들지 않는다.
  - 웹 API와 MCP 도구는 모두 `state_store.py`를 호출한다. `state_store.py`는 SQLite 규칙을 가진 공통 저장소 모듈이다.
  - 화면은 HTML·CSS·JavaScript로 만들고, 데이터 저장·조회는 Python API가 맡는다.
  - 권장 구조:

    ```text
    src/harness/control_center/
      app.py               # FastAPI 앱과 라우트를 조립
      work_items_api.py    # Draft 생성·WI 목록·WI 상세 API
      static/              # 브라우저 화면용 HTML·CSS·JavaScript
    ```

  - `src/stitch_harness_v2_control_center_mockup/`은 구현 대상이 아니라 화면 디자인 참조용으로 유지한다.
- [ ] 첫 API 3개의 요청·응답 JSON 계약을 정한다.
  - Draft 생성
  - WI 목록 조회
  - WI 상세 조회
- [ ] WI 목록에 표시할 필드와 첫 필터 범위를 정한다.
- [ ] Draft 생성 요청이 실패했을 때 화면에 보여 줄 오류 방식을 정한다.
