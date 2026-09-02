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

## 대화에서 완성 WI를 직접 등록하는 경우

사용자가 현재 대화의 내용을 바탕으로 “이 작업을 WI로 만들어 달라”고 명시하면 Draft를 거치지 않고 `$work-item-create` 스킬을 사용한다. 스킬은 대화에서 제목·목표·종류·우선순위·다음 행동·완료 조건을 정리한 뒤 `create_ready_work_item()`을 한 번 호출하고 `get_work_context()`로 저장 결과를 확인한다.

`create_ready_work_item()`은 실행 계획과 Acceptance Criteria를 하나의 DB 트랜잭션으로 저장하고, `is_draft = false`, `status = ready`인 WorkItem을 만든다. 이 경로는 미래 작업을 등록하는 것만 하므로 Run은 생성하지 않는다. 제목이나 목표를 정할 근거가 부족할 때만 생성 전에 짧게 확인한다.

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
- [x] 웹 콘솔 첫 연결 범위를 구현한다.
  - Draft 생성 모달 → SQLite 저장 → 목록의 Draft 배지 표시
- [x] 대화에서 명시한 완성 WI를 실행 없이 직접 등록한다.
  - `$work-item-create` → `create_ready_work_item()` → `ready` 상태 저장

## 웹 콘솔 구조 결정

Python `FastAPI` 기반의 로컬 단일 웹 앱으로 만든다. `FastAPI`는 브라우저 요청을 받아 화면과 HTTP API를 제공하는 Python 웹 서버다.

API와 정적 화면을 한 서버에서 함께 제공한다. 별도 React/Vite 프로젝트나 별도 Node 서버는 현재 만들지 않는다. 웹 API와 MCP 도구는 모두 `state_store.py`를 호출하며, 이 모듈은 SQLite 규칙을 가진 공통 저장소다. 화면은 HTML·CSS·JavaScript로 만들고, 데이터 저장·조회는 Python API가 맡는다.

```text
src/harness/control_center/
  app.py               # FastAPI 앱과 라우트를 조립
  work_items_api.py    # Draft 생성·WI 목록·WI 상세 API
  static/              # 브라우저 화면용 HTML·CSS·JavaScript
```

`src/stitch_harness_v2_control_center_mockup/`은 구현 대상이 아니라 화면 디자인 참조용으로 유지한다.

로컬 웹 콘솔은 저장소 루트에서 `bin/harness-control-center`를 실행하고 브라우저로 `http://127.0.0.1:8765`에 접속한다. `HARNESS_CONTROL_CENTER_PORT` 환경 변수로 포트를 바꿀 수 있다.

## 첫 HTTP API 계약

API 계약은 웹 화면이 보내는 JSON과 FastAPI 서버가 돌려주는 JSON의 약속이다. URL은 로컬 도구에 맞게 `/api`로 시작하고, 필드 이름은 Python·SQLite와 같은 `snake_case`를 사용한다.

### Draft 생성

`POST /api/draft-work-items`

```json
{
  "title": "웹 콘솔 첫 화면 구현",
  "kind": "implementation",
  "goal": "Draft WI를 생성하고 목록에서 확인할 수 있게 한다.",
  "description": "선택 입력. Stitch 목업을 기준으로 한다."
}
```

`title`, `kind`, `goal`은 필수이고 `description`은 선택이다. 웹은 `is_draft`, `status`, `priority`를 보내지 않는다. 서버의 `create_draft_work_item()`이 이를 `is_draft=true`, `status=backlog`, `priority=normal`으로 고정한다.

성공 시 `201 Created`와 생성된 WorkItem을 돌려준다.

```json
{
  "work_item": {
    "id": "WI-...",
    "title": "웹 콘솔 첫 화면 구현",
    "kind": "implementation",
    "goal": "Draft WI를 생성하고 목록에서 확인할 수 있게 한다.",
    "description": "선택 입력. Stitch 목업을 기준으로 한다.",
    "is_draft": true,
    "status": "backlog",
    "priority": "normal",
    "feature_id": null,
    "next_action": null,
    "created_at": "..."
  }
}
```

### WI 목록 조회

`GET /api/work-items`

목록에는 카드에 필요한 요약 정보만 돌려준다. AC, Run 같은 상세 정보는 포함하지 않는다.

```json
{
  "work_items": [
    {
      "id": "WI-...",
      "title": "웹 콘솔 첫 화면 구현",
      "kind": "implementation",
      "status": "backlog",
      "priority": "normal",
      "is_draft": true,
      "feature_id": null,
      "feature_title": null,
      "goal": "Draft WI를 생성하고 목록에서 확인할 수 있게 한다.",
      "created_at": "..."
    }
  ]
}
```

목록 필터는 목업의 `Status`, `Kind` 두 섹션만 만든다. `Draft`는 DB의 실제 상태값이 아니라 Status 섹션의 가상 필터다.

| 화면 Status 필터 | DB 조회 조건 |
| --- | --- |
| `Draft` | `status = backlog` AND `is_draft = true` |
| `Backlog` | `status = backlog` AND `is_draft = false` |
| `Ready` 등 나머지 상태 | 해당 `status` 값 |

따라서 `is_draft`는 응답에는 그대로 포함하지만, URL에는 별도 `is_draft` 필터를 만들지 않는다. 예를 들어 `GET /api/work-items?status=draft&kind=implementation`의 `draft`는 위의 두 DB 조건으로 변환한다.

### WI 상세 조회

`GET /api/work-items/{work_item_id}`

오른쪽 상세 패널용 API다. 기존 `get_work_item_context()`의 데이터를 재사용한다.

```json
{
  "work_item": {
    "id": "WI-...",
    "title": "웹 콘솔 첫 화면 구현",
    "kind": "implementation",
    "goal": "...",
    "description": "...",
    "is_draft": true,
    "status": "backlog",
    "priority": "normal",
    "next_action": null
  },
  "feature": null,
  "acceptance_criteria": [],
  "running_run": null,
  "recent_runs": []
}
```

Draft는 `next_action`, AC, Run이 비어 있는 것이 정상이다.

## Draft 생성 오류 처리

Draft 생성 모달은 실패해도 닫지 않고, 사용자가 쓴 값도 그대로 유지한다. 사용자는 오류를 고친 뒤 다시 저장할 수 있다.

| HTTP 상태 | 의미 | 화면 처리 |
| --- | --- | --- |
| `422` | 필수 입력값 또는 `kind` 값이 잘못됨 | 해당 입력칸 아래에 오류를 표시한다. |
| `409` | 입력은 맞지만 DB 규칙과 충돌함 | 모달 상단에 짧은 전체 오류를 표시한다. |
| `503` | SQLite 또는 로컬 서버를 지금 사용할 수 없음 | 모달 상단에 “잠시 후 다시 시도해 주세요”를 표시한다. |
| `500` | 예상하지 못한 서버 오류 | 내부 정보 없이 일반 오류 문구를 표시한다. |

오류 응답은 화면이 일관되게 처리할 수 있도록 아래 형태를 사용한다. `fields`는 입력칸별 오류가 있을 때만 넣는다.

```json
{
  "error": {
    "code": "validation_error",
    "message": "입력값을 확인해 주세요.",
    "fields": {
      "title": "제목은 비워둘 수 없습니다."
    }
  }
}
```

성공하면 모달을 닫고, 서버가 반환한 Draft를 목록 맨 위에 바로 추가한 뒤 해당 WI의 상세 패널을 연다. 별도 알림(toast), 자동 저장, 재시도 대기열은 첫 버전에 넣지 않는다.

## 웹 콘솔 구현 전 결정 체크리스트

- [x] 웹 앱과 HTTP API를 둘 기술 및 폴더 구조를 정한다.
- [x] 첫 API 3개의 요청·응답 JSON 계약을 정한다.
- [x] WI 목록에 표시할 필드와 첫 필터 범위를 정한다.
- [x] Draft 생성 요청이 실패했을 때 화면에 보여 줄 오류 방식을 정한다.
