# 사용자 명시적 WorkItem 완료 결정

## 문서 상태

- 상태: 구현 기준
- 대체하는 설계: [`completed-work-item-follow-up-runs.md`](./completed-work-item-follow-up-runs.md)
- 폐기한 접근: `done` WI 재개, 후속 Run mode, 완료 WI 전용 fzf 화면

## 결정 배경

Codex가 한 Run의 구현과 검증을 끝낸 직후 WorkItem까지 `done`으로 닫으면, 사용자가 결과를 직접 확인한 뒤 수정 사항을 요청할 때 같은 WI를 다시 선택할 수 없다. 이를 해결하기 위해 `done` WI를 재개하는 구조를 검토했지만 상태 역전, 완료 시각 복구, 후속 Run 구분과 실패 보상처럼 많은 추가 규칙이 필요했다.

WorkItem의 최종 종료 권한을 사용자에게 두면 기존의 단순한 실행 흐름을 유지하면서 같은 WI를 반복해서 사용할 수 있다.

## 핵심 결정

Codex는 Run의 결과만 판단하고 WorkItem을 스스로 `done` 처리하지 않는다.

성공한 Run이 끝나면 WI는 `next_action`을 가진 `ready`로 돌아간다. 추가 작업이 남았다면 Codex가 구체적인 다음 작업을 입력하고, 현재 AC 기준으로 완료 가능하다면 상태 저장 함수가 고정된 완료 권장 문구를 입력한다. 사용자가 웹 콘솔에서 직접 완료하거나 Codex에게 해당 WI의 완료를 명시적으로 요청한 경우에만 `ready → done` 전이를 수행한다.

```text
WI 생성
  → ready

작업 요청
  → ready → in_progress
  → Run 실행
  → Run succeeded
  → in_progress → ready
  → next_action 갱신

추가 수정 요청
  → 같은 ready WI 선택
  → 새 Run 실행
  → 다시 ready

사용자의 명시적 완료 선언
  → ready → done
```

별도의 `awaiting_review` 상태는 추가하지 않는다. `ready`는 실행 가능한 WI이면서 사용자 확인 또는 추가 요청을 기다리는 열린 WI라는 현재 역할을 계속 맡는다.

## Run 완료와 WI 완료의 구분

- **Run 성공**: 이번 실행에서 요청받은 작업과 검증을 마쳤다는 뜻이다. Run은 `succeeded`, WI는 `ready`가 된다.
- **WI 완료**: 사용자가 더 이상 이 WI에서 작업할 필요가 없다고 선언한 상태다. WI는 `done`이 되고 `closed_at`이 기록된다.
- **`Run.intent`**: 사용자가 방금 요청한 이번 Run의 작업 내용이다. `start_work()`를 호출할 때 기록한다.
- **`next_action`**: 현재 Run이 끝난 뒤 WI에서 예상되는 다음 작업 또는 사용자에게 권장하는 다음 행동이다. `finish_work()`를 호출할 때 갱신한다. 완료 가능 여부와 관계없이 WI는 계속 선택하고 실행할 수 있다.

현재 AC 기준으로 완료 가능한 경우에는 함수가 고정 템플릿을 저장한다.

```text
status: ready
next_action: [완료 확인 권장] 현재 AC 기준으로 완료 가능합니다. 결과를 확인해 WI를 완료하거나, 추가 작업을 계속 진행하세요.
```

사용자가 수정 사항을 전달하면 그 요청은 새 Run의 `intent`에 기록한다. 일반적인 수정 요청을 Run 시작 전에 WI의 `next_action`으로 복사하지 않는다.

```text
start_work(
  work_item_id="WI-123",
  intent="사용자 피드백에 따라 Run 카드 간격과 버튼 위치를 수정한다"
)
```

`next_action`은 해당 Run을 종료할 때 그 이후에 수행할 작업으로 새로 저장한다. 작업 후에도 현재 AC 기준으로 완료 가능하다면 고정 완료 권장 템플릿을 다시 저장하고, 실제 후속 작업이 남았다면 Codex가 그 작업을 구체적으로 입력한다.

```text
사용자의 새 요청
  → Run 시작 시 Run.intent에 기록
  → Run 실행
  → Run 종료 시 이후의 WI.next_action을 저장
```

WI의 기존 계획 자체가 낡았거나 잘못된 경우에는 작업 시작 전에 `revise_work_item()`으로 `next_action`을 수정할 수 있다. 이는 예외적인 계획 정리이며 사용자 요청을 매번 `next_action`에 복사하는 절차가 아니다.

## WorkItem 완료 권한

WI를 `done`으로 바꾸는 경로는 두 개만 허용한다.

1. 사용자가 웹 콘솔에서 해당 WI의 완료를 직접 선택한다.
2. 사용자가 `w/` 요청에서 선택한 WI의 완료 처리를 명시하고 Codex가 사용자 완료 전용 MCP 도구를 호출한다.

사용자가 WI ID를 외우거나 웹 콘솔에서 복사해 입력하도록 요구하지 않는다. `w/ 이 WI 완료 처리해줘`처럼 요청하고 fzf에서 대상 WI를 선택하는 방식을 기본 흐름으로 사용한다. UPS Hook이 선택 결과를 검증한 뒤 `<work-selection-context>` 패킷의 `work_item_id`로 전달하므로 `$work-start`는 임시 `result.json`을 직접 읽지 않는다.

“좋아”, “확인했어”처럼 완료 의도가 불명확한 표현만으로 WI를 닫지 않는다. “이 WI를 완료 처리해줘”, “선택한 작업을 done으로 닫아줘”처럼 선택한 WI 자체를 지금 닫으라는 의도가 명확해야 한다. “WI 완료 처리 기능을 구현해줘”, “완료됐는지 확인해줘”처럼 구현이나 확인을 요청한 문장은 완료 선언으로 취급하지 않는다.

사용자 완료 전용 상태 저장 함수는 다음 조건을 기계적으로 검사한다.

- WI가 `ready`인지 확인한다.
- 같은 WI에 `running` Run이 없는지 확인한다.
- Acceptance Criterion이 하나 이상 존재하는지 확인한다.
- 모든 AC가 `passed` 또는 `waived`인지 확인한다.
- `passed` AC마다 유효한 Evidence가 있는지 확인한다.
- 검증을 통과하면 `next_action`을 비우고 `closed_at`을 기록한 뒤 `ready → done` State Event를 남긴다.

## 상태 저장소 변경

### 상태 전이

새 상태를 추가하지 않고 다음 전이만 확장한다.

```text
ready → in_progress   Run 시작
in_progress → ready   Run 성공·진전·실패·중단 후 계속 가능
ready → done          사용자의 명시적 완료
```

`done → ready`와 `done → in_progress`는 허용하지 않는다. 닫힌 WI는 다시 열지 않는다.

기존 스키마에는 `in_progress → done`이 허용되고 `ready → done`이 금지되어 있으므로 상태 전이 Trigger를 다음과 같이 교체한다.

```text
제거: in_progress → done
추가: ready → done
```

`.harness/schema.sql`의 Trigger는 `CREATE TRIGGER IF NOT EXISTS`로 선언되어 있어 정의만 수정하면 기존 DB에는 반영되지 않는다. `initialize_database()`가 기존 `work_items_validate_status_transition` Trigger를 삭제하고 새 정의로 재생성하는 마이그레이션을 실행해야 한다. 새 DB와 기존 DB가 동일한 전이 규칙을 갖는지 모두 검증한다.

### Run 종료

Codex가 작업을 성공적으로 마치면 항상 기존 `progressed` outcome을 사용한다. `progressed`는 Run을 `succeeded`로 종료하고 WI를 `ready`로 돌리는 기존 경로다.

추가 작업이 남은 경우에는 Codex가 `next_action`을 직접 입력한다.

```text
finish_work(
  outcome="progressed",
  completion_recommended=false,
  next_action="모바일 레이아웃을 구현한다"
)
```

현재 AC 기준으로 완료 가능한 경우에는 Codex가 `completion_recommended=true`를 전달하고 `next_action`은 입력하지 않는다.

```text
finish_work(
  outcome="progressed",
  completion_recommended=true
)
```

`finish_work()`는 완료 검증을 통과한 경우 다음 고정 템플릿을 `next_action`으로 사용한다.

```text
[완료 확인 권장] 현재 AC 기준으로 완료 가능합니다. 결과를 확인해 WI를 완료하거나, 추가 작업을 계속 진행하세요.
```

입력 규칙은 다음과 같다.

- `completion_recommended=false`: Codex가 구체적인 `next_action`을 반드시 입력한다.
- `completion_recommended=true`: `next_action`을 입력하지 않는다. 함수가 완료 검증 후 고정 템플릿을 저장한다.
- `completion_recommended=true`와 `next_action`이 함께 전달되면 입력 오류로 거부한다.

`completion_recommended=true`의 완료 검증과 템플릿 저장은 상태 저장소의 동일한 트랜잭션에서 처리한다. MCP 계층이 AC·Evidence를 먼저 조회한 뒤 별도 호출로 저장하지 않는다. 상태 저장소가 다음 작업을 원자적으로 수행한다.

```text
실행 중인 Run과 WI 재조회
  → AC·Evidence 완료 조건 검증
  → 고정 next_action 선택
  → Run: running → succeeded
  → WI: in_progress → ready
```

이렇게 하여 완료 조건을 확인한 시점과 Run을 종료하는 시점 사이에 WI 검증 상태가 달라지는 경쟁 조건을 막는다.

결과는 다음과 같다.

```text
Run: running → succeeded
WI: in_progress → ready
```

`finish_work()`의 outcome 목록에서 `completed`를 제거한다. 따라서 Run 종료와 WI `done`을 한 번에 수행하는 `finish_work(outcome="completed")` 호출은 더 이상 지원하지 않는다.

기존 `complete_work_item()`은 다음 작업을 한 번에 수행하는 전용 함수다.

```text
활성 Run 조회
  → AC·Evidence 완료 검증
  → Run: running → succeeded
  → WI: in_progress → done
```

새 정책에서는 Run 종료 과정이 WI를 `done`으로 만들지 않으므로 이 함수의 기존 역할을 제거한다. `complete_work_item()`은 삭제하고, AC·Evidence 검증 로직은 아래의 사용자 완료 함수 `close_work_item()`에서 재사용한다.

`complete_work_item()` 외에 남아 있는 자동 `done` 우회 경로도 함께 제거한다.

- `finish_run()`의 허용 조합에서 `("succeeded", "done")`을 제거한다.
- DB 상태 전이에서 `in_progress → done`을 제거한다.
- 일반 상태 변경 함수 `change_work_item_status(status="done")`는 거부한다.
- `ready → done` 전이는 사용자 완료 전용 `close_work_item()`만 수행한다.

따라서 Run 종료 함수, 일반 상태 변경 함수와 Codex의 `finish_work()` 중 어느 경로로도 WI를 `done` 처리할 수 없다.

### 사용자 완료 함수

상태 저장소에는 Run을 요구하지 않는 전용 함수를 둔다. 설계상 이름은 다음과 같다.

```text
close_work_item(work_item_id, actor, reason)
```

이 함수가 사용자 완료의 유일한 상태 저장 Interface가 된다. 웹 콘솔과 MCP는 동일한 함수를 호출하여 완료 규칙을 중복 구현하지 않는다.

## MCP와 에이전트 동작 변경

### `finish_work()`

- Codex의 정상 성공 종료는 모두 `progressed`를 사용한다.
- 추가 작업이 남았다면 Codex가 구체적인 `next_action`을 입력한다.
- 현재 AC 기준으로 완료 가능하다면 `completion_recommended=true`를 입력하고 함수가 고정 템플릿을 저장한다.
- `completed` outcome은 허용 목록과 처리 분기에서 제거한다.
- `retry_needed`, `blocked`, `interrupted`, `cancelled`의 기존 의미는 유지한다.
- `cancelled`는 WI 자체를 폐기하는 의미이므로 사용자의 명시적인 취소가 있을 때만 사용한다.

### 사용자 완료 MCP

Run 없이 호출할 수 있는 전용 도구를 추가한다.

```text
close_work_item(work_item_id, reason)
```

Codex는 사용자가 대상 WI의 완료를 명시한 경우에만 이 도구를 호출한다. 도구는 상태 저장소의 `close_work_item()`에 위임하며 현재 상태, 활성 Run과 AC·Evidence를 다시 검증한다.

### `$work-finish`

- Codex가 WI 전체 완료 여부를 판단해 `done`으로 바꾸는 절차를 제거한다.
- 성공한 Run은 `progressed`로 종료하고 WI를 `ready`로 돌린다.
- 종료 전 Artifact, Evidence와 Memory Candidate를 확인하는 기존 Postflight 절차는 유지한다.
- 최종 답변에서는 “이번 Run을 완료했다”와 “WI가 done이다”를 구분한다.

### `$work-start`

- 기존 `ready` WI 선택과 `start_work()` 흐름을 유지한다.
- 사용자 수정 요청은 같은 `ready` WI에서 새 Run을 시작한다.
- `selection_status="selected"`이고 사용자 요청이 선택한 WI 자체의 명시적 완료 요청이면 일반 작업 시작보다 먼저 완료 분기로 진입한다.
- 완료 분기에서는 패킷의 정확한 `work_item_id`를 사용해 `close_work_item()`을 호출한다.
- 완료 분기에서는 Draft 구체화, 후보 비교·검색, WI 생성·수정, `intent`·`recall_query` 생성과 `start_work()` 호출을 모두 생략한다.
- 완료 성공 또는 거부 사유를 보고한 뒤 해당 Turn의 작업을 종료한다.
- `done`과 `cancelled` WI는 계속 새 Run 대상으로 사용하지 않는다.

권장 흐름은 다음과 같다.

```text
사용자: w/ 이 WI 완료 처리해줘
  → fzf에서 대상 WI 선택
  → UPS Hook이 선택 결과 검증
  → work-selection-context에 선택된 work_item_id 전달
  → (Codex) $work-start가 명시적 완료 요청으로 분류
  → (Codex) close_work_item(work_item_id, reason) 호출
  → Run을 생성하지 않고 ready → done
```

자연어가 명시적 완료 요청인지에 대한 판단은 스킬 지침으로 일관되게 유도하되 완전한 기계적 강제 대상으로 삼지 않는다. 이는 기존 `$work-start`의 요청 분류와 WI 선택 절차가 모델 지침과 상태 저장 함수의 검증을 함께 사용하는 것과 같은 수준의 보장이다. 실제 상태 변경의 안전성은 `close_work_item()`이 `ready` 상태, 활성 Run 부재와 AC·Evidence 완료 조건을 다시 검사하여 보완한다.

### AGENTS 지침

활성 에이전트 지침에 다음 원칙을 둔다.

- Codex는 자신의 완료 판단만으로 WI를 `done` 처리하지 않는다.
- 성공한 프로젝트 Run은 구체적인 `next_action`을 남기고 WI를 `ready`로 돌린다.
- WI의 `done` 전이는 웹 콘솔 또는 사용자의 명시적인 완료 요청으로만 수행한다.
- 사용자 완료 요청은 새 Run 없이 처리한다.
- 최종 응답에서 Run 성공과 WI 최종 완료를 명확히 구분한다.

현재 `AGENTS.md`의 lifecycle 관련 지침 대부분은 HTML 주석 안에 있으므로, 실제 적용할 규칙은 활성 영역에 작성해야 한다.

## Hook과 선택기 변경

### UserPromptSubmit와 fzf

기존 단일 선택 화면을 그대로 사용한다. 성공한 Run 뒤 WI가 `ready`로 돌아오므로 같은 WI가 다음 `w/` 요청에서 자동으로 다시 나타난다.

현재 선택 요청은 `id`, `title`, `goal`, `kind`, `priority`, `is_draft`만 전달하므로 완료 권장 템플릿을 fzf에서 볼 수 없다. 선택 요청의 WorkItem 항목에 `next_action`을 추가하고 요청 파일 검증과 fzf 포맷도 함께 확장한다. fzf 미리보기에는 다음 행동을 별도 영역으로 표시한다.

```text
◆ 다음 행동
  [완료 확인 권장] 현재 AC 기준으로 완료 가능합니다. 결과를 확인해 WI를 완료하거나, 추가 작업을 계속 진행하세요.
```

별도의 완료 상태나 DB 필드는 추가하지 않는다. 고정 템플릿이 `next_action`에 저장되므로 fzf는 일반 다음 작업과 동일한 필드를 그대로 표시한다. 웹 콘솔은 기존 `Next Action` 영역에서 같은 문구를 표시하며, 별도 배지는 필수 구현 범위에 포함하지 않는다.

다음 기능은 구현하지 않는다.

- 완료 WI 전용 목록
- `Tab` 화면 전환
- `completed_work_items` request 배열
- `selection_mode=follow_up`
- sentinel 기반 완료 목록 처리

### PreToolUse

파일·코드·테스트 변경은 기존처럼 활성 Run과 Runtime Binding이 있어야 한다. 사용자 완료 전용 `close_work_item()`만 Run 없이 호출할 수 있는 상태 변경 도구로 허용한다.

`close_work_item()` 허용은 코드 변경 권한을 부여하지 않는다. 함수가 성공하면 WI 상태만 `ready → done`으로 바뀐다.

### PostToolUse와 Stop

- `finish_work(progressed)` 성공 후 기존 PostToolUse가 종료된 Run의 Runtime Binding을 삭제한다.
- Stop Hook은 기존처럼 활성 Run이 남았을 때 `$work-finish` 실행을 요구한다.
- `close_work_item()`은 활성 Run과 Binding이 없는 상태에서 실행하므로 Binding 생성이나 삭제가 필요하지 않다.
- stale Run 복구와 Binding 생성 실패 보상은 기존 `in_progress → ready` 동작을 유지한다.

## 웹 콘솔 변경

`ready` WI의 허용 상태 변경에 `done`을 추가하고 명확한 완료 버튼 또는 상태 메뉴를 제공한다.

웹 요청은 상태를 직접 UPDATE하지 않고 상태 저장소의 `close_work_item()`을 호출한다. AC·Evidence가 미완료이거나 활성 Run이 있으면 이유를 표시하고 완료를 거부한다.

권장 사용자 흐름은 다음과 같다.

```text
Ready WI 상세 화면
  → 완료 처리
  → 확인 대화상자
  → close_work_item()
  → Done
```

## 필요 없는 기존 설계

이 결정으로 다음 항목은 구현하지 않는다.

- `done → in_progress` 상태 전이
- `start_work(mode="follow_up")`
- `start_follow_up_run()`
- `runs.origin`
- `runs.previous_work_item_closed_at`
- 완료 WI 전용 fzf 화면과 단축키
- 후속 Run 전용 Artifact 완료 조건
- 후속 Run Binding 실패 시 `done` 복구
- 완료 WI 재활성화에 따른 Feature 진행률 보정

## 테스트 범위

- 성공한 Run이 `succeeded`로 끝나고 WI가 새 `next_action`과 함께 `ready`가 되는지 검증한다.
- `completion_recommended=true`이면 완료 검증 후 고정 `next_action`이 저장되는지 검증한다.
- 완료 조건 검증과 고정 `next_action` 저장이 하나의 트랜잭션에서 수행되고 실패 시 Run과 WI 변경이 모두 롤백되는지 검증한다.
- `completion_recommended=false`이면 Codex가 제공한 구체적인 `next_action`이 저장되는지 검증한다.
- `completion_recommended=true`와 `next_action`을 함께 전달하면 거부하는지 검증한다.
- Codex의 `finish_work()`로 WI를 `done` 처리할 수 없는지 검증한다.
- `finish_work()`가 `completed` outcome을 허용하지 않는지 검증한다.
- `finish_run(succeeded, done)`과 `change_work_item_status(status="done")` 우회 호출을 거부하는지 검증한다.
- `ready → done`은 사용자 완료 함수로만 가능한지 검증한다.
- 활성 Run이 있는 WI의 완료 요청을 거부하는지 검증한다.
- AC 또는 Evidence가 부족하면 완료를 거부하는지 검증한다.
- 완료 성공 시 `next_action=NULL`, `closed_at` 설정과 State Event가 함께 기록되는지 검증한다.
- 웹 콘솔과 MCP가 동일한 상태 저장 함수를 사용하는지 검증한다.
- 사용자 완료 MCP가 활성 Run 없이 호출 가능하지만 파일 변경 권한을 만들지 않는지 검증한다.
- `w/` 완료 요청에서 fzf로 선택한 패킷의 `work_item_id`가 `close_work_item()`에 전달되는지 검증한다.
- 명시적 완료 요청에서는 Draft 구체화, WI 검색·생성, `intent`·`recall_query` 생성과 `start_work()`가 수행되지 않는지 검증한다.
- 구현·확인 요청이 완료 선언으로 잘못 분류되지 않는지 스킬 시나리오를 검증한다.
- 성공 후 `ready`로 돌아온 WI가 기존 fzf 목록에 다시 표시되는지 검증한다.
- 선택 요청에 `next_action`이 포함되고 fzf 미리보기에서 완료 권장 템플릿을 확인할 수 있는지 검증한다.
- 기존 DB 초기화 시 상태 전이 Trigger가 교체되어 `ready → done`만 허용되고 `in_progress → done`은 거부되는지 검증한다.
- 같은 WI에서 여러 Run을 반복한 뒤 사용자가 한 번의 완료 요청으로 닫을 수 있는지 검증한다.
- 기존 실패·차단·중단·stale 복구 흐름이 계속 `ready` 또는 `blocked`로 동작하는지 회귀 검증한다.

## 구현 대상

- `.harness/schema.sql`: `ready → done` 추가와 `in_progress → done` 제거
- `src/harness/state_store.py`: 기존 Trigger 교체 마이그레이션, `complete_work_item()`과 자동 `done` 우회 제거, 완료 권장 템플릿의 원자적 검증·저장, `close_work_item()` 추가
- `src/harness/mcp_server.py`: `completed` outcome 제거, `completion_recommended` 입력과 사용자 완료 도구 추가
- `src/harness/hook_policy.py`: 사용자 완료 도구의 Run 없는 호출 허용
- `src/harness/work_item_picker.py`와 UserPromptSubmit Hook: 선택 요청에 `next_action`을 포함하고 fzf 미리보기에 표시
- `.agents/skills/work-finish/SKILL.md`: 성공 Run을 `progressed + ready`로 종료
- `.agents/skills/work-start/SKILL.md`: 명시적 완료 요청은 Run 없이 처리
- `AGENTS.md`: 사용자만 WI를 완료한다는 활성 지침 추가
- `src/harness/control_center/work_items_api.py`: ready WI 완료 endpoint 또는 상태 변경 추가
- `src/harness/control_center/static/app.js`: ready WI 완료 UI 추가
- 관련 상태 저장·MCP·Hook·웹 콘솔·fzf 테스트
- 상태 저장소, 단일 요청 파이프라인과 MCP 함수표 문서

## 용어

- **WorkItem / WI**: 사용자가 달성하려는 목표, 완료 조건과 다음 행동을 보관하는 작업 개체다.
- **Run**: 한 WI에서 Codex가 수행한 한 번의 실행 기록이다.
- **`Run.intent`**: 사용자가 요청한 해당 Run의 구체적인 작업 내용이다.
- **`ready`**: 활성 Run은 없지만 다음 작업을 시작할 수 있는 열린 WI 상태다.
- **`in_progress`**: 활성 Run이 실행 중인 WI 상태다.
- **`done`**: 사용자가 명시적으로 닫은 WI 상태다.
- **`next_action`**: Run 종료 후 WI에서 예상되는 다음 작업 또는 사용자에게 권장하는 다음 행동이다.
- **AC / Acceptance Criterion**: WI의 목표가 충족됐는지 판단하는 완료 조건이다.
- **Evidence**: Artifact가 특정 AC를 충족한다는 연결 기록이다.
- **Runtime Binding**: 현재 Codex session·turn을 활성 WI·Run에 연결하는 임시 작업 권한 정보다.
