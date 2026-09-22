# 웹 콘솔 Runs 화면 설계

## 1. 목적

Runs 화면은 에이전트가 수행한 작업 실행 기록을 빠르게 훑어보는 화면이다.

`Run`은 하나의 WorkItem을 처리하기 위해 수행한 한 번의 실행 시도다. 사용자가 각 Run을 깊이 분석하는 경우는 많지 않을 것으로 예상하므로, Work Items 화면처럼 필터·목록·상세 정보를 세 개의 세로 컬럼으로 나누지 않는다.

화면의 핵심 목적은 다음 두 가지다.

- 최근에 어떤 Run이 실행되었는지 확인한다.
- 각 Run이 실행 중인지, 성공했는지, 비정상적으로 종료됐는지 빠르게 확인한다.

## 2. 참고 목업

화면 구현 시 다음 디렉터리의 목업을 참고한다.

```text
design/mockups/mockup_web_console_run_ui/
```

목업에는 목록 화면과 상세 팝업 화면이 포함되어 있다. 다만 상세 팝업은 실제 Harness가 보유한 정보보다 많은 정보를 가정하고 있으므로 그대로 구현하지 않는다. 색상, 간격, 테이블 구성 등 시각적 방향을 참고하는 용도로만 사용한다.

## 3. 전체 화면 구성

Runs 화면은 다음과 같이 구성한다.

1. 화면 상단에 2~3줄 규모의 가로형 필터 영역을 둔다.
2. 필터 아래에 Run 목록을 테이블 형태로 나열한다.
3. 별도의 상세 팝업이나 우측 상세 패널은 만들지 않는다.
4. 사용자가 목록의 개별 Run 행을 클릭하면 해당 행 아래에 상세 영역이 펼쳐진다.
5. 목록 행의 가장 오른쪽에는 실행 명령을 담은 Actions 컬럼 대신 펼침 상태를 나타내는 chevron만 둔다.

행을 다시 클릭하면 펼쳐진 상세 영역을 닫는다. 동시에 여러 행을 펼칠지 한 행만 펼칠지는 구현 과정에서 현재 UI 구조에 맞춰 결정할 수 있지만, 기본적으로는 한 번에 한 Run을 확인하는 단순한 동작을 우선한다.

## 4. 목록에 항상 표시할 정보

Run 목록의 기본 컬럼은 다음과 같다.

| 컬럼 | 설명 |
| --- | --- |
| Status | Run의 현재 상태. `running`, `succeeded`, `failed`, `interrupted`, `cancelled`를 상태 배지로 표시한다. |
| Run ID | Run을 구분하는 식별자다. 긴 값은 축약할 수 있으며 클릭으로 복사하는 동작을 제공할 수 있다. |
| Work Item | Run이 속한 WorkItem의 제목과 짧은 ID를 표시한다. |
| Intent | 해당 Run에서 수행하려던 작업의 요약이다. 목록에서는 한 줄로 제한하고 긴 내용은 말줄임표로 처리한다. |
| Started | `started_at`, 즉 Run이 시작된 시각을 표시한다. |
| Duration | 종료된 Run의 실제 경과 시간을 표시하고, 실행 중인 Run에는 진행 중 표현을 표시한다. |

최종 목록 컬럼은 다음 형태다.

```text
Status | Run ID | Work Item | Intent | Started | Duration
```

### 목록에서 제외할 정보

- `Actor`는 표시하지 않는다. 현재 `runs` 테이블에 실행 주체를 직접 저장하는 필드가 없고, 상태 이벤트에서 간접적으로 추출해야 하므로 목록 정보로 사용하지 않는다.
- `Actions` 컬럼은 표시하지 않는다. 현재 Run을 수정·재시작·삭제하는 웹 기능이 없으며, 행 클릭으로 상세 정보를 펼치는 동작과도 중복된다.
- 행 오른쪽의 chevron은 별도 Action이 아니다. 접힌 상태에서는 아래 방향, 펼친 상태에서는 위 방향으로 표시해 행이 확장 가능하다는 점과 현재 상태를 알려준다.
- `Origin Source`는 표시하지 않는다. 현재 Run에 CLI, 웹 콘솔 등의 생성 출처를 저장하는 필드가 없다.

## 5. Duration 표시 규칙

`Duration`은 Run의 실행 시간을 나타내는 표시값이며 DB에 별도 컬럼으로 저장하지 않는다.

### 종료된 Run

종료된 Run은 다음 두 시각의 차이로 실행 시간을 계산한다.

```text
duration = ended_at - started_at
```

표시는 `3m 42s`, `1h 12m`처럼 읽기 쉬운 고정값으로 제공한다.

### 실행 중인 Run

실행 중인 Run에는 `started_at`부터 현재 시각까지 증가하는 실시간 타이머나 스톱워치를 사용하지 않는다.

웹 콘솔은 Run 상태를 지속해서 polling하거나 실시간 구독하지 않기 때문에, 최초 조회 이후 Run이 종료되어도 브라우저가 이를 알지 못할 수 있다. 이 상태에서 타이머를 계속 증가시키면 실제 종료 시간보다 큰 잘못된 시간이 표시될 수 있다.

따라서 실행 중인 Run의 Duration은 다음과 같이 표시한다.

```text
⏳ In progress
```

- 시간 숫자는 표시하지 않는다.
- 진행 중임을 나타내는 모래시계 아이콘과 텍스트를 표시한다.
- 모래시계 아이콘에는 천천히 뒤집히는 가벼운 애니메이션을 적용한다.
- 사용자가 운영체제에서 동작 감소를 설정한 경우 `prefers-reduced-motion`에 따라 애니메이션을 중단한다.
- Run의 최신 상태는 사용자가 페이지를 새로고침하거나 화면을 다시 조회했을 때 반영한다.
- 이 화면만을 위해 polling, Server-Sent Events 또는 WebSocket을 추가하지 않는다.

## 6. 행 확장 상세 영역

목록의 Run 행을 클릭하면 별도 팝업 대신 해당 행 아래에 간단한 상세 영역을 펼친다.

### 항상 표시할 정보

- **Intent**: 목록에서 잘린 전체 작업 의도를 표시한다.
- **Summary**: Run 종료 시 기록한 실행 결과 요약이다. 실행 중인 Run은 아직 결과가 없음을 표시한다.
- **Started**: Run 시작 시각이다.
- **Ended**: 종료된 Run의 종료 시각이다. 실행 중이면 표시하지 않거나 `—`로 표시한다.
- **Duration**: 종료된 Run에서만 `ended_at - started_at`으로 계산한 고정 시간을 표시한다.
- **Work Item**: 연결된 WorkItem의 제목과 ID를 표시하고, 가능한 경우 해당 WorkItem 상세 화면으로 이동할 수 있게 한다.

### 조건부로 표시할 정보

- **Termination reason**: `failed`, `interrupted`, `cancelled` 상태일 때만 표시한다. 정상적으로 끝나지 않은 이유를 담는 값이다.
- **Artifacts**: 해당 Run이 남긴 파일, 테스트, lint, 빌드, 커밋 등의 결과 기록이다.
  - 전체 Artifact 개수
  - 종류
  - 검증 상태
  - 짧은 요약
  - 참조 경로인 `uri`가 존재하면 열기 또는 복사 동작

Artifact가 많으면 처음 몇 개만 보여주고, 향후 별도의 Artifacts 화면이 구현되면 전체 목록으로 이동하는 링크를 제공한다.

### 상세 영역 레이아웃

펼쳐진 상세 영역은 목록 행 전체 너비를 사용하되, 새로운 전체 화면처럼 보이지 않도록 목록보다 한 단계 옅거나 진한 배경으로 구분한다. 정보는 다음 순서로 배치한다.

```text
┌─ Intent ───────────────────────────────────────────────────────────┐
│ 사용자가 이 Run에서 요청한 작업의 전체 내용                       │
└───────────────────────────────────────────────────────────────────┘

┌─ Result ───────────────────────────────────────────────────────────┐
│ Run 종료 시 기록한 Summary                                        │
│                                                                   │
│ 실패·중단·취소된 경우                                             │
│ Termination reason: 정상적으로 끝나지 않은 이유                   │
└───────────────────────────────────────────────────────────────────┘

Started       2026-09-22 14:10:12
Ended         2026-09-22 14:13:54
Duration      3m 42s
Work Item     WI-94c421 · 연결된 WorkItem 제목

Artifacts · 3
✓ Test run    전체 테스트 164개 통과
✓ File        Runs 화면 파일 수정
✕ Lint run    JavaScript lint 실패
```

레이아웃 세부 규칙은 다음과 같다.

- 첫 번째 영역에는 `Intent` 전체 내용을 표시한다.
- 두 번째 영역에는 `Summary`를 `Result`라는 사용자 친화적인 제목으로 표시한다.
- `Termination reason`은 Result 영역 안에서 비정상 종료 Run에만 경고 색상으로 표시한다.
- 시각 정보는 Started, Ended, Duration 순서로 같은 그룹에 정렬한다.
- 연결된 WorkItem은 ID와 제목을 함께 표시하고 클릭 가능한 링크로 제공한다.
- Artifact는 별도의 하단 목록으로 표시하며 상태 아이콘, 종류, 짧은 요약만 보여준다.
- Artifact의 `uri`는 파일 경로나 명령 식별자일 수 있으므로 기본 동작은 복사로 한다. `http://` 또는 `https://` URL일 때만 새 창으로 열 수 있게 한다.
- Artifact가 없으면 빈 목록을 만들지 않고 `이 Run에 기록된 Artifact가 없습니다.`라는 짧은 안내만 표시한다.
- 실행 중인 Run은 Summary, Ended 및 고정 Duration이 아직 없으므로 Result에는 `아직 실행 중입니다.`를 표시하고, Duration에는 목록과 동일한 모래시계 애니메이션과 `In progress`를 표시한다.
- 상세 영역에는 별도의 수정, 재시작, 중단 또는 삭제 버튼을 두지 않는다.
- 목록 행의 chevron도 동일한 행 클릭과 키보드 `Enter`·`Space` 동작을 시각적으로 안내하는 표시일 뿐, 별도 액션 메뉴를 열지 않는다.

## 7. 기본 화면에서 다루지 않을 정보

다음 정보는 현재 사용자 가치가 낮거나 데이터가 충분하지 않으므로 첫 구현의 기본 상세 영역에 포함하지 않는다.

- `recall_query`: Run 시작 시 장기 기억을 검색하는 데 사용한 검색어다.
- 전체 Tool event 목록: Run 중 실행된 도구 호출 기록으로, 일반적인 실행 결과 확인에는 지나치게 상세하다.
- Memory Candidate 목록: Run에서 발견한 장기 기억 후보이며 Run 목록의 핵심 정보가 아니다.
- 단계별 Latency 차트: Planning, Execution, Verification 등의 개별 시작·종료 시각을 저장하지 않으므로 정확하게 계산할 수 없다.
- 토큰 사용량, 변경 파일 수, 테스트 통과 개수 등의 구조화된 통계: 현재 Run의 정식 필드로 저장하지 않는다.
- Next Action: `next_action`은 Run이 아니라 현재 WorkItem의 다음 행동을 나타낸다. 이후 Run에서 값이 바뀔 수 있으므로 과거 Run의 결과처럼 표시하지 않는다.
- Trace: OpenTelemetry를 사용할 계획이 없으므로 `trace_ref` 링크나 Trace 상세 UI를 제공하지 않는다.
- Raw JSON: 현재의 단순한 조회 목적에는 필요하지 않다.

Tool event나 `recall_query`가 실제 사용 과정에서 필요하다고 확인되면 나중에 고급 정보 영역으로 추가한다.

## 8. API 계약

현재 웹 콘솔에는 WorkItem별 최근 Run을 조회하는 응답만 있고, Runs 화면에서 전체 실행 이력을 조회하는 전용 API는 없다. Runs 화면 구현과 함께 다음 두 개의 읽기 전용 API를 추가한다.

### Run 목록 조회

```http
GET /api/runs
```

지원할 query parameter는 다음과 같다.

| Parameter | 설명 |
| --- | --- |
| `status` | Run 상태 필터다. 같은 parameter를 반복해 여러 상태를 요청할 수 있다. |
| `query` | Run ID, Intent, WorkItem ID, WorkItem 제목을 대상으로 하는 부분 검색어다. |
| `work_item_id` | 특정 WorkItem에 속한 Run만 조회한다. |
| `started_from` | 이 시각 이후 시작된 Run만 조회하는 ISO 8601 시각이다. |
| `started_to` | 이 시각 이전 시작된 Run만 조회하는 ISO 8601 시각이다. |
| `limit` | 한 번에 반환할 최대 개수다. 기본값은 50, 최댓값은 100으로 제한한다. |
| `offset` | 목록에서 건너뛸 개수다. 기본값은 0이다. |

정렬은 최신 Run이 먼저 오도록 `started_at DESC, id DESC`로 고정한다. 첫 구현에서는 별도의 정렬 parameter를 제공하지 않는다.

응답 예시는 다음과 같다.

```json
{
  "runs": [
    {
      "id": "RUN-f68cca44",
      "work_item_id": "WI-9b4c21",
      "work_item_title": "Agent 메모리 반복 저장 개선",
      "intent": "MemoryGraph 배치 위임 캐시 레이어를 검증한다.",
      "status": "succeeded",
      "started_at": "2026-09-22T14:10:12Z",
      "ended_at": "2026-09-22T14:13:54Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

목록 응답에는 목록에 쓰지 않는 `actor`, `origin`, `trace_ref`, `recall_query`, `summary`, `termination_reason`을 포함하지 않는다. `Duration`은 브라우저가 `started_at`과 `ended_at`으로 계산한다.

### Run 상세 조회

```http
GET /api/runs/{run_id}
```

사용자가 행을 펼칠 때만 호출하는 지연 조회 API다. 모든 Run의 Artifact를 목록 조회 시 한꺼번에 가져오지 않는다.

응답 예시는 다음과 같다.

```json
{
  "run": {
    "id": "RUN-f68cca44",
    "work_item_id": "WI-9b4c21",
    "intent": "MemoryGraph 배치 위임 캐시 레이어를 검증한다.",
    "status": "succeeded",
    "started_at": "2026-09-22T14:10:12Z",
    "ended_at": "2026-09-22T14:13:54Z",
    "summary": "관련 구현과 테스트를 완료했다.",
    "termination_reason": null
  },
  "work_item": {
    "id": "WI-9b4c21",
    "title": "Agent 메모리 반복 저장 개선"
  },
  "artifacts": [
    {
      "id": "ART-1234",
      "kind": "test_run",
      "verification_status": "passed",
      "summary": "전체 테스트가 통과했다.",
      "uri": "command:test:20260922T141200Z",
      "created_at": "2026-09-22T14:12:00Z"
    }
  ]
}
```

상세 응답에서도 `recall_query`와 `trace_ref`는 노출하지 않는다. Run이 없으면 기존 오류 envelope 형식을 유지하면서 HTTP 404와 `not_found` 코드를 반환한다. 현재 공통 404 메시지가 WorkItem만 언급하므로 구현 시 `요청한 대상을 찾을 수 없습니다.`와 같은 공통 문구로 수정한다.

## 9. 조회 및 펼침 동작

- 페이지 진입과 사용자의 새로고침 버튼 클릭 시 Run 목록을 조회한다.
- 자동 polling과 실시간 상태 구독은 하지 않는다.
- 검색어나 필터가 바뀌면 목록 API를 다시 호출한다. 연속 입력 검색은 짧은 debounce를 적용해 불필요한 요청을 줄인다.
- 날짜 필터는 브라우저의 로컬 날짜를 기준으로 입력받되 API 요청 시 UTC ISO 8601 시각으로 변환한다.
- 목록이 `limit`보다 많으면 하단에 이전·다음과 현재 표시 범위를 제공한다.
- Run 행을 펼칠 때 해당 Run의 상세 API를 최초 한 번 호출한다.
- 같은 화면에서 다시 펼칠 때는 이미 받은 상세 응답을 메모리에 보관해 재사용할 수 있다.
- 한 번에 하나의 Run만 펼친다. 다른 행을 펼치면 기존 상세 영역은 닫는다.
- 필터 변경이나 목록 새로고침 시 펼쳐진 행을 닫고 상세 캐시는 비운다.
- WorkItem 링크를 선택하면 Work Items 화면으로 이동하고 해당 WorkItem을 선택한다.

## 10. 기존 화면과의 일관성

현재 WorkItem 상세의 Recent Runs 영역은 실행 중 Run에 대해 브라우저에서 1초마다 경과 시간을 증가시키는 타이머를 사용한다. Runs 화면에서 타이머를 제거하더라도 이 코드를 그대로 두면 동일한 Run이 화면마다 다르게 표현된다.

따라서 Runs 화면 구현 시 다음 변경을 함께 수행한다.

- 전역 1초 타이머와 실행 중 경과 시간 갱신 코드를 제거한다.
- WorkItem 상세의 실행 중 Run도 숫자 타이머 대신 모래시계 애니메이션과 `In progress`를 표시한다.
- 종료된 Run의 Duration 계산과 표기 함수는 두 화면에서 공유한다.

## 11. 구현 경계

- 상태 저장소에 전체 Run을 WorkItem 제목과 함께 조회하는 전용 함수를 추가한다. API 라우터에서 SQLite를 직접 조회하지 않는다.
- Run 상세 조회 함수는 Run, 최소 WorkItem 정보, 해당 Run의 Artifact를 하나의 응답용 구조로 조합한다.
- Runs API는 기존 WorkItem API 파일에 섞기보다 별도의 Runs 라우터 모듈로 분리한다.
- 웹 콘솔은 현재 단일 페이지 구조를 유지한다. Runs 메뉴를 선택하면 Work Items 화면 대신 Runs 화면을 렌더링하고, WorkItem 링크를 선택하면 Work Items 화면으로 전환한 뒤 기존 상세 조회 흐름을 사용한다.
- 새 API의 목록 필터, 정렬, 페이지 범위, 상세 404, Artifact 연결을 테스트한다.
- 브라우저 UI는 행 펼침, 한 행만 열기, 실행 중 애니메이션, 종료 Duration, 비정상 종료 사유, 빈 상태를 테스트한다.

## 12. 데이터 해석 주의사항

- WorkItem 제목과 상태는 Run 생성 당시의 스냅샷이 아니라 현재 WorkItem의 값이다.
- 실행 중 Run에는 `ended_at`, `summary`, `termination_reason`이 없다.
- 성공한 Run에는 `termination_reason`이 없다.
- `termination_reason`은 실패·중단·취소된 Run에만 존재한다.
- Duration은 서버에 저장된 값이 아니라 시작·종료 시각으로 화면 또는 API에서 계산한 값이다.

## 13. 최종 UX 원칙

- Runs 화면은 정밀한 디버깅 도구가 아니라 가벼운 실행 이력표로 유지한다.
- 목록만 보더라도 최근 Run의 상태와 목적을 빠르게 파악할 수 있어야 한다.
- 부가 정보는 행 확장으로만 제공하며 별도의 팝업이나 상세 패널을 만들지 않는다.
- 저장되지 않은 정보를 추론해서 보여주지 않는다.
- 실시간성이 꼭 필요하지 않은 화면이므로 polling이나 실시간 연결을 추가하지 않는다.
