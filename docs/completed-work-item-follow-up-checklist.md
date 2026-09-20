# 완료 WorkItem 후속 Run 문제 해결 체크리스트

> **문서 상태: 중단된 체크리스트**
> `done` WI를 재개하는 구현은 진행하지 않는다. 체크된 항목도 구현 기준이 아니며, 현재 결정은 [`user-controlled-work-item-completion.md`](./user-controlled-work-item-completion.md)를 따른다. 이 문서는 대안을 검토하며 발견한 문제를 보존하기 위한 기록이다.

> 대상 WorkItem: `WI-a6437714c18a`
> 결정 문서: [`completed-work-item-follow-up-runs.md`](./completed-work-item-follow-up-runs.md)

## 사용 방법

이 문서는 완료된 WorkItem을 후속 수정에 재사용하기 전에 해결해야 할 핵심 문제 13개만 추적한다.

각 문제에는 해결 여부를 나타내는 TODO가 정확히 하나 있다. 문제의 방향을 합의하고 `해결 방안`까지 기록하면 TODO를 완료 처리한다. 실제 구현과 테스트에서 발견된 세부 작업은 해당 문제의 `해결해야 할 것` 아래 설명으로 관리하며 별도 TODO를 늘리지 않는다.

#### 1. 일반 Run과 후속 Run의 시작 인터페이스

- [x] 기존 `start_work()`를 확장하는 방식으로 시작 인터페이스를 확정했다.

**문제점**

완료 WI 전용 MCP 도구를 새로 만들면 기존 `start_work()`에 연결된 PostToolUse의 Runtime Binding 생성, 장기 기억 Recall, 시작 안내와 실패 보상 처리를 별도로 복제해야 한다. 연결 하나라도 빠지면 Run은 생성됐지만 PreToolUse가 변경을 허용하지 않는 상태가 생길 수 있다.

**해결해야 할 것**

- 기존 일반 호출의 동작과 호환성을 유지해야 한다.
- 일반 시작과 완료 WI 후속 시작을 명시적으로 구분해야 한다.
- 사용자가 전달한 mode만 믿지 않고 현재 WI 상태를 DB 트랜잭션 안에서 검증해야 한다.
- 생성된 Run이 일반 실행인지 후속 실행인지 이후 완료·복구 단계에서 식별할 수 있어야 한다.

**해결 방안**

- 외부 MCP 도구는 기존 `start_work()` 하나를 유지한다.
- `start_work()`에 `mode: normal | follow_up = normal`을 추가한다.
- `mode`를 생략한 기존 호출은 `normal`로 처리한다.
- `normal`은 `ready` WI만, `follow_up`은 `done` WI만 시작할 수 있게 DB에서 다시 검증한다.
- fzf 결과의 `selection_mode`와 `start_work()`의 `mode`를 같은 값 체계로 연결한다.
- Run에는 `origin: normal | follow_up`을 저장한다.
- 완료 WI를 실제로 전환하고 Run을 생성하는 내부 상태 저장 함수는 `start_follow_up_run()`으로 분리할 수 있다.
- 외부 도구 이름이 유지되므로 기존 PostToolUse의 Runtime Binding과 Recall 흐름을 그대로 재사용한다.

#### 2. 기존 DB에서도 동작하는 상태 전이와 migration

- [x] `done → in_progress` 전이와 Run origin을 신규·기존 DB에 안전하게 적용하는 방식을 확정했다.

**문제점**

현재 스키마는 `done`에서 나가는 상태 전이를 모두 거부한다. 또한 상태 전이 Trigger가 `CREATE TRIGGER IF NOT EXISTS`로 선언되어 있어 schema 파일만 수정하면 신규 DB에는 반영돼도 이미 존재하는 `.harness/state.db`의 Trigger는 갱신되지 않을 수 있다.

**해결해야 할 것**

- 신규 DB에서 `done → in_progress`만 새로 허용하고 기존 금지 전이는 유지해야 한다.
- `runs.origin` 필드를 추가하고 기존 Run을 `normal`로 migration해야 한다.
- 기존 상태 전이 Trigger를 명시적으로 교체해야 한다.
- 후속 시작 시 `next_action` 설정과 `closed_at=NULL`이 WorkItem CHECK 제약을 동시에 만족해야 한다.
- `started_at`은 최초 시작 시각을 유지하고, 재완료 시 `closed_at`은 최신 완료 시각으로 갱신해야 한다.
- 신규 in-memory DB와 기존 파일 DB 업그레이드를 각각 검증해야 한다.

**해결 방안**

- 상태 전이 Trigger에 `done → in_progress`만 추가하고, `done`에서 다른 상태로 가는 기존 금지 규칙은 유지한다.
- 기존 DB에서도 변경된 규칙이 적용되도록 schema migration 트랜잭션 안에서 기존 상태 전이 Trigger를 `DROP TRIGGER IF EXISTS`로 제거한 뒤 새 정의로 다시 생성한다.
- `runs` 테이블에 Run의 시작 유형을 나타내는 `origin TEXT NOT NULL DEFAULT 'normal'` 필드를 추가하고, 값은 `normal | follow_up`만 허용하는 CHECK 제약을 둔다.
- 기존 DB의 Run은 migration 시 기본값에 의해 모두 `origin=normal`로 간주한다. 과거 Run이 후속 실행이었는지 추정하거나 소급 분류하지 않는다.
- 후속 Run 시작 시 하나의 상태 변경에서 WI를 `in_progress`로 바꾸고, 비어 있지 않은 `next_action`을 설정하며, 이전 완료 시각인 `closed_at`은 `NULL`로 초기화하고 `updated_at`을 갱신한다.
- WI의 `started_at`은 최초 작업 시작 시각이므로 후속 Run을 시작할 때 변경하지 않는다. 후속 Run을 완료하면 `closed_at`만 최신 완료 시각으로 다시 기록한다.
- migration과 schema 초기화는 반복 실행해도 같은 최종 스키마가 되도록 구성한다.
- 테스트는 신규 in-memory DB 생성, 기존 `origin` 없는 파일 DB 업그레이드, 허용된 `done → in_progress` 전이, 그 밖의 금지 전이 유지, 기존 Run의 `origin=normal` 보존을 각각 검증한다.

#### 3. WI 재활성화와 Run 생성의 원자성

- [x] 완료 WI 전환과 후속 Run 생성을 하나의 원자적 트랜잭션으로 처리하는 방식을 확정했다.

**문제점**

`done → ready`와 `ready → in_progress + Run 생성`을 외부의 두 호출로 나누면 호출 사이의 실패로 WI만 열린 채 남거나 다른 세션이 끼어들 수 있다.

**해결해야 할 것**

- WI가 여전히 `done`인지 확인해야 한다.
- 같은 WI에 `running` Run이 없는지 확인해야 한다.
- 완료 상태인 기존 WI의 `next_action`은 비어 있어야 하며, 후속 Run 시작 요청으로 새로 받은 `next_action`, `intent`, `recall_query`는 비어 있지 않아야 한다.
- WI 상태 변경, 후속 Run 생성과 State Event 기록이 모두 성공하거나 모두 rollback되어야 한다.
- 동시 요청 두 개 중 하나만 성공해야 한다.
- 기존 AC, Evidence, 이전 Run과 Artifact는 변경하지 않아야 한다.

**해결 방안**

- 외부에서 `done → ready` 전환과 일반 Run 시작을 연속 호출하지 않는다. 내부의 `start_follow_up_run()`이 완료 WI 재활성화와 후속 Run 생성을 하나의 DB 트랜잭션에서 직접 처리한다.
- 트랜잭션 안에서 대상 WI가 여전히 `done`이고 `next_action`이 비어 있는지, 같은 WI에 `running` 상태의 Run이 없는지 다시 확인한다.
- 기존 WI에 저장돼 있던 값이 아니라, 후속 시작 요청으로 새로 전달된 `next_action`, `intent`, `recall_query`가 비어 있지 않은지 검증한다.
- 검증을 통과하면 WI를 `in_progress`로 바꾸고 새 `next_action`을 저장하며 `closed_at=NULL`로 초기화한 뒤, `origin=follow_up`인 Run과 관련 State Event를 생성한다.
- WI 상태 변경, Run 생성과 State Event 기록 중 하나라도 실패하면 트랜잭션 전체를 rollback하여 WI를 원래 `done` 상태로 보존한다.
- 같은 완료 WI에 후속 시작 요청이 동시에 들어오면 트랜잭션 안의 현재 상태와 활성 Run 조건으로 경쟁을 판정하여 하나만 성공하게 한다. 실패한 요청은 기존 Run을 인수하거나 덮어쓰지 않는다.
- 후속 시작 과정에서는 기존 AC, Evidence, 과거 Run과 Artifact를 수정하거나 복제하지 않는다.
- 후속 Run을 정상 완료할 때 WI의 `next_action`은 다시 비우고 `closed_at`을 최신 완료 시각으로 기록한다.

#### 4. 오래된 선택 스냅샷과 동시 선택

- [x] 오래된 선택 스냅샷을 신뢰하지 않고 최신 DB 상태로 충돌을 거부하는 방식을 확정했다.

**문제점**

done WI 목록은 Terminal 실행 전에 읽고 선택 요청은 최대 300초 동안 유지된다. 사용자가 선택하는 사이 다른 세션이 같은 WI를 시작하거나 상태를 바꿀 수 있으므로 request JSON의 상태는 최신 상태가 아닐 수 있다.

**해결해야 할 것**

- request에 포함됐다는 사실만으로 후속 시작을 허용하지 않아야 한다.
- 후속 시작 트랜잭션에서 현재 상태와 활성 Run을 다시 확인해야 한다.
- 이미 다른 세션이 시작한 WI를 덮어쓰거나 자동 takeover하지 않아야 한다.
- 충돌 시 사용자가 같은 `w/` 요청을 다시 시도할 수 있는 이해 가능한 오류를 제공해야 한다.

**해결 방안**

- request JSON은 fzf에 제시했던 후보와 사용자의 선택 경로를 검증하는 용도로만 사용한다. request에 기록된 WI 상태를 현재 상태로 간주하지 않는다.
- `selection_mode=follow_up`인 결과의 WI ID가 request JSON의 `completed_work_items`에 실제로 포함됐는지 기계적으로 확인한다.
- `$work-start`는 기존처럼 `get_work_context()`를 호출하고, 반환된 최신 WI 상태가 `done`인지와 활성 Run이 없는지를 1차 확인한다. 이미 다른 세션이 시작했다면 `start_work()`를 호출하지 않고 중단한다.
- 1차 확인과 실제 시작 사이에도 상태가 바뀔 수 있으므로, 정확성의 최종 책임은 `start_follow_up_run()`의 DB 트랜잭션에 둔다. 트랜잭션에서 WI가 여전히 `done`인지와 `running` Run이 없는지를 다시 확인한다.
- request JSON 확인, preflight 확인과 시작 트랜잭션의 검사는 상태·ID·mode를 비교하는 기계적인 조건으로 처리하며, Codex의 의미 추론으로 시작 가능 여부를 판단하지 않는다.
- 다른 요청이 먼저 WI를 시작했거나 상태를 변경한 경우 `ConflictError`로 거부하고, 기존 Run을 자동 인수하거나 덮어쓰지 않는다.
- 충돌 오류는 “선택 이후 WorkItem 상태가 변경되었습니다. 같은 `w/` 요청을 다시 실행해 주세요.”처럼 원인과 재시도 방법을 함께 제공한다.
- 오래된 request 스냅샷, 두 세션의 동시 선택, preflight 직후 상태 변경을 각각 테스트하여 하나의 후속 Run만 생성되는지 검증한다.

#### 5. fzf 화면 전환 키와 반환 형식

- [x] `Tab`으로 두 화면을 전환하고 구조화된 fzf 결과로 처리하는 방식을 확정했다.

**문제점**

현재 `run_fzf()`는 선택 문자열 또는 `None`만 반환한다. 화면 전환 키를 `--expect`로 받으면 누른 키와 선택 행이 여러 줄로 반환되므로 기존 문자열 계약으로는 일반 선택, 화면 이동과 취소를 안전하게 구분할 수 없다.

**해결해야 할 것**

- 기본 화면에서 `Tab`으로 완료 WI 화면을 열어야 한다.
- 완료 화면에서도 같은 `Tab`으로 기본 WI 화면에 돌아가야 한다.
- picker가 두 fzf 호출을 관리하고 `reload()` 기반의 복잡한 화면 변형은 사용하지 않아야 한다.
- 반환값은 `select | switch_view | cancel` action과 선택 행을 구분하는 구조화된 객체여야 한다.
- 각 화면의 label, prompt, footer와 preview가 현재 화면을 명확히 알려야 한다.
- 실제 fzf 종료 코드 `0`, `1`, `2`, `130`을 올바르게 구분하고 Esc의 `130`을 정상 취소로 처리해야 한다.

**해결 방안**

- macOS와 Windows에서 동일하게 전달되고 셸의 EOF 의미가 없는 `Tab`을 화면 전환 키로 사용한다. `Ctrl-D`, `Ctrl-B`, `Shift-Tab`, Alt/Option 조합과 Function 키는 사용하지 않는다.
- 기본 화면과 완료 WI 화면 모두 `--expect=tab`을 사용한다. `Tab`을 누르면 현재 선택 행과 관계없이 화면 전환으로 처리한다.
- 화면은 두 개뿐이므로 `Tab`을 토글로 정의한다. 기본 화면에서는 완료 WI 화면으로, 완료 WI 화면에서는 기본 화면으로 전환한다.
- picker는 한 fzf 인스턴스의 `reload()`로 내용을 바꾸지 않고, 현재 view를 기억하면서 화면별 fzf를 새로 호출한다.
- `run_fzf()`는 문자열 또는 `None` 대신 `action: select | switch_view | cancel`과 선택 행을 담는 구조화된 결과를 반환한다.
- fzf의 다중 선택 옵션인 `--multi`는 사용하지 않고, `Tab`의 기본 다중 선택 동작 대신 `--expect=tab`의 화면 전환 의미를 우선한다.
- Enter는 현재 화면의 WI 선택, `Tab`은 화면 전환, Esc는 전체 선택 취소로 고정한다. 완료 화면에서 선택한 경우에만 최종 결과의 `selection_mode`를 `follow_up`으로 기록한다.
- fzf 종료 코드 `0`은 출력 파싱, `1`과 `130`은 정상 취소, `2`와 그 밖의 비정상 종료 코드는 선택기 오류로 처리한다.
- 기본 화면 footer는 `Enter: 선택 │ Tab: 완료 WI 보기 │ Esc: 취소`, 완료 화면 footer는 `Enter: 후속 작업 │ Tab: 일반 WI 보기 │ Esc: 취소`처럼 현재 화면과 키 동작을 명시한다.

#### 6. 비어 있는 기본·완료 목록

- [x] WI가 없는 목록에서 sentinel을 표시하고 선택 없음으로 종료하는 방식을 확정했다.

**문제점**

ready/Draft 목록이 비어 있으면 사용자가 완료 WI 화면으로 들어가지 못할 수 있다. 반대로 완료 WI가 없을 때 두 번째 fzf가 즉시 종료되거나 선택 불가능한 상태가 될 수 있다.

**해결해야 할 것**

- 기본 목록이 비어 있어도 `Tab`을 받을 수 있어야 한다.
- 완료 목록이 비어 있으면 “완료된 WI가 없습니다”라는 안내를 보여줘야 한다.
- 빈 목록 안내는 실제 WI로 선택될 수 없는 sentinel이어야 한다.
- 완료 화면에서 `Tab`으로 기본 화면에 돌아가거나 Esc로 빠져나올 수 있어야 한다.
- 기본↔완료 화면을 반복 이동해도 request 만료와 최종 결과가 정확해야 한다.

**해결 방안**

- 일반 또는 완료 WI 목록이 0개이면 fzf를 완전히 비워 두지 않고, “선택 가능한 일반 WorkItem이 없습니다” 또는 “완료된 WorkItem이 없습니다”라는 sentinel 안내 행 하나를 표시한다.
- sentinel은 실제 WI가 없음을 표현하는 내부 안내 행이다. 일반 WI ID와 충돌하지 않는 예약 ID를 사용하고 화면에서는 안내 문구만 보이게 한다.
- sentinel에서 Enter를 누르면 같은 화면을 다시 실행하지 않는다. 실제 WI가 선택되지 않았음을 나타내는 `status=no_selection`, `reason=empty_view`와 현재 `selection_mode`를 결과에 기록하고 Terminal을 종료한다.
- UserPromptSubmit Hook은 `no_selection` 결과를 Run 시작이 불가능한 선택 결과로 처리하고, Codex에는 현재 목록에 선택 가능한 WI가 없었다는 최소 정보만 전달한다.
- `no_selection`은 사용자가 Esc를 누른 `cancelled`와 구분한다. 두 경우 모두 WorkItem 선택, `$work-start` 실행과 Run 생성을 진행하지 않는다.
- sentinel 화면에서도 `Tab`은 일반·완료 WI 화면 전환, Esc는 전체 선택 취소로 동작한다.
- 실제 WI가 있는 화면에서는 기존대로 Enter가 해당 WI를 선택하며 sentinel 관련 분기를 거치지 않는다.
- sentinel의 예약 ID가 result의 `work_item_id`로 기록되거나 실제 WI 선택으로 검증을 통과하지 못하도록 picker와 result 검증 양쪽에서 차단한다.
- 일반 목록만 비어 있는 경우, 완료 목록만 비어 있는 경우, 양쪽 모두 비어 있는 경우, sentinel Enter·Tab·Esc를 각각 테스트한다.

#### 7. request/result 프로토콜과 선택 경로 검증

- [x] 일반·완료 목록과 선택 경로를 분리한 v2 선택 파일 계약을 확정했다.

**문제점**

선택 결과에 WI ID만 있으면 사용자가 일반 화면에서 선택했는지 완료 화면에서 선택했는지 알 수 없다. mode와 목록의 소속을 교차 검증하지 않으면 잘못 작성되거나 변조된 result가 후속 시작을 위장할 수 있다.

**해결해야 할 것**

- 선택 파일 `SCHEMA_VERSION`을 2로 올려 계약 변경을 명시해야 한다.
- request JSON에 `work_items`와 `completed_work_items`를 별도 배열로 저장해야 한다.
- result JSON에 `selection_mode: normal | follow_up`을 저장해야 한다.
- `normal` ID는 `work_items`, `follow_up` ID는 `completed_work_items`에 포함됐는지 각각 검증해야 한다.
- 전체 done 목록을 모델 Context Packet에 넣지 않고 선택된 WI와 mode만 Codex에 전달해야 한다.
- 만료, 알 수 없는 ID, mode 불일치와 변조 결과를 거부해야 한다.

**해결 방안**

- 선택 파일의 `SCHEMA_VERSION`을 2로 올리고 picker와 UserPromptSubmit Hook은 v2 request/result만 허용한다. 남아 있는 v1 임시 파일은 버전 오류로 거부하고 기존 만료 파일 정리 절차로 제거한다.
- request JSON에는 일반 화면의 `ready`·Draft WI를 담는 `work_items`와 완료 화면의 `done` WI를 담는 `completed_work_items`를 별도 배열로 저장한다.
- `status=selected`인 result JSON에는 `work_item_id`와 사용자가 선택한 화면을 나타내는 `selection_mode: normal | follow_up`을 필수로 기록한다.
- `validate_selection_result()`는 `selection_mode=normal`이면 선택 ID가 `work_items`에, `selection_mode=follow_up`이면 `completed_work_items`에 포함됐는지 교차 검증한다.
- `selection_mode`는 선택 경로를 나타낼 뿐 현재 DB 상태를 보증하지 않는다. 최신 WI 상태와 활성 Run은 `$work-start`의 preflight와 실제 Run 시작 트랜잭션에서 별도로 확인한다.
- sentinel에서 Enter를 누른 `status=no_selection` 결과에는 `work_item_id`를 허용하지 않고, `reason=empty_view`와 현재 `selection_mode`를 필수로 기록한다. 해당 mode의 request 배열이 실제로 비어 있을 때만 유효하다.
- Esc로 종료한 `status=cancelled` 결과에는 `work_item_id`를 허용하지 않는다. 특정 WI 선택이 아니므로 `selection_mode`도 기록하지 않는다.
- 모든 result는 request와 `schema_version`, `request_id`, `session_id`, `turn_id`가 일치하고 만료 시각 전에 작성됐는지 검증한다. 허용되지 않은 status, 알 수 없는 ID, mode와 목록의 불일치, 상태별 금지 필드는 오류로 거부한다.
- UserPromptSubmit Hook이 Codex에 전달하는 Context Packet에는 선택된 WI 하나의 ID·제목·Draft 여부와 `selection_mode`만 포함한다. 전체 `completed_work_items` 목록은 request JSON과 Terminal picker 안에서만 사용한다.
- 일반 선택, 후속 선택, 선택 없음, 취소, 만료, v1 파일, 알 수 없는 ID, mode 불일치와 변조된 상태별 필드를 각각 테스트한다.

#### 8. 기존 lifecycle 경로 재사용 검증

- [x] 1번에서 결정한 기존 lifecycle 경로를 후속 Run에도 그대로 적용하기로 확정했다.

**문제점**

1번에서 후속 전용 MCP 도구를 만들지 않고 기존 `start_work(mode="follow_up")`를 사용하기로 결정했으므로, PostToolUse·Runtime Binding·PreToolUse 연동 방식도 이미 정해졌다. 이 항목은 새로운 lifecycle을 설계하는 문제가 아니라 구현 중 기존 경로의 일부가 누락되지 않았는지 검증하는 항목이다.

**해결해야 할 것**

- 후속 Run도 기존 `mcp__harness_state__start_work` PostToolUse 경로를 사용해야 한다.
- session·turn·WI·Run이 일치하는 Runtime Binding을 생성해야 한다.
- 후속 Run에도 Recall과 시작 안내가 정상 동작해야 한다.
- Binding 생성 전에는 변경을 차단하고 생성 후에만 허용해야 한다.
- Run 종료 후 Binding을 삭제해야 한다.
- `selection_mode=follow_up` 값만으로 PreToolUse를 우회할 수 없어야 한다.

**해결 방안**

- 1번 결정에 따라 후속 Run도 기존 MCP 도구인 `start_work(mode="follow_up")`를 호출한다. 후속 작업 전용 MCP 도구와 전용 Hook은 만들지 않는다.
- 기존 `start_work()`의 PostToolUse 경로가 후속 Run 응답의 `work_item_id`와 `run_id`를 사용해 Runtime Binding을 생성하고 기존 Recall 절차를 실행한다.
- Runtime Binding은 현재 Codex session·turn과 DB의 WI·Run을 연결하는 작업 허가 정보다. Binding 저장에 성공한 뒤에만 기존 PreToolUse가 프로젝트 변경을 허용한다.
- 기존 PreToolUse는 수정하지 않고 Runtime Binding과 SQLite의 `running` Run이 일치하는지만 검사한다. `selection_mode`나 Run의 `origin`은 변경 권한으로 사용하지 않는다.
- 후속 Run이 종료되면 기존 `finish_work()` PostToolUse 경로가 Runtime Binding을 삭제한다.
- Binding 생성 실패 시 프로젝트 변경을 차단하고 기존 복구 경로를 호출한다. 후속 WI를 원래 `done`으로 복구하는 상세 규칙은 10번에서 결정한다.
- 일반 Run과 후속 Run 각각에 대해 Binding 생성 성공 후 변경 허용, Binding이 없거나 불일치할 때 변경 차단, Run 종료 후 Binding 삭제를 회귀 테스트한다.

#### 9. 기존 AC만으로 후속 Run이 즉시 완료되는 문제

- [x] 현재 후속 Run의 검증 가능한 결과가 있어야 `done` 처리하는 방식을 확정했다.

**문제점**

완료 WI의 기존 AC와 Evidence는 이미 유효하므로 후속 Run 생성 직후에도 기존 완료 검사는 통과할 수 있다. 아무 수정이나 검증 없이 Run을 바로 종료하면 최초 문제였던 조기 완료가 구조적으로 반복된다.

**해결해야 할 것**

- `origin=follow_up` Run의 완료에는 현재 Run이 만든 유효한 Artifact가 하나 이상 필요해야 한다.
- 이전 Run의 Artifact로 현재 후속 Run의 최소 결과 조건을 충족할 수 없어야 한다.
- 후속 Artifact를 기존 AC의 Evidence로 다시 연결하도록 강제하지 않아야 한다.
- 기존 AC의 `passed | waived` 상태와 기존 Evidence 검증은 그대로 유지해야 한다.
- 변경 불필요로 결론 난 경우에도 그 판단을 확인할 수 있는 검토 Artifact 또는 명시적 종료 경로가 필요하다.

**해결 방안**

- 기존 Artifact 흐름을 유지한다. Codex가 작업 중 만든 파일·테스트·lint·빌드·커밋·스크린샷·보고서 등의 실제 결과를 판단하고, DB에 누락됐다면 `record_artifact()`로 현재 `run_id`에 등록한다. 시스템이 파일이나 도구 출력을 자동 스캔해 Artifact를 생성하지는 않는다.
- Artifact는 Run에서 나온 검증 가능한 결과 기록이다. Artifact가 특정 Acceptance Criterion을 증명할 때만 `verify_criterion()`을 호출해 Evidence로 연결하고 해당 AC를 `passed` 또는 `failed`로 판정한다. Evidence는 Artifact와 AC 사이의 증명 관계다.
- 완료 WI의 기존 AC, 기존 Evidence와 과거 Run의 Artifact는 후속 Run을 시작할 때 유지한다. 후속 Run의 Artifact를 기존 AC에 다시 연결하도록 일괄 강제하지 않는다.
- `$work-finish`는 종료 전에 `get_postflight_status(work_item_id)`를 호출한다. 이 함수는 현재 `running` Run의 ID를 찾고 `get_run_artifacts(run_id)`로 그 Run에 속한 Artifact만 반환하므로 과거 Run의 Artifact와 구분할 수 있다.
- `$work-finish` 지침에 후속 Run의 실제 결과가 Artifact 목록에서 빠졌다면 `record_artifact()`로 등록하도록 명시한다. 등록 후 `get_postflight_status()`를 다시 호출해 현재 상태를 종료 판단 기준으로 사용한다.
- `origin=follow_up`인 Run을 `completed`로 종료하려면 현재 Run에 `verification_status=passed`인 Artifact가 하나 이상 있어야 한다. 과거 Run의 Artifact는 이 최소 결과 조건에 포함하지 않는다.
- 위 조건은 스킬의 판단에만 맡기지 않고 `finish_run()` 또는 그 직전의 상태 저장 완료 검증에서 `run_id`와 `origin`을 기준으로 기계적으로 검사한다. 조건을 만족하지 않으면 `ConflictError`를 반환하고 Run과 WI 상태를 변경하지 않는다.
- `get_postflight_status()`에도 후속 Run Artifact 누락을 완료 불가 사유로 노출해 Codex가 `finish_work()` 호출 전에 원인을 알 수 있게 한다. 최종 DB 검사는 preflight 이후의 누락이나 잘못된 직접 호출까지 차단한다.
- 실제 수정이 불필요하다고 판단한 경우에도 단순한 설명만으로 완료하지 않는다. 파일 확인, 테스트, 브라우저 확인 스크린샷 또는 검토 보고서처럼 다시 확인할 수 있는 URI를 가진 `passed` Artifact를 등록한다. 기존 `file`, `test_run`, `screenshot`, `report`, `other` 종류를 사용하며 별도 `review` 종류는 추가하지 않는다.
- 현재 후속 Run에 `passed` Artifact가 있는 경우와 없는 경우, 과거 Run에만 Artifact가 있는 경우, 현재 Artifact가 `pending` 또는 `failed`인 경우, 변경 불필요를 검증 가능한 Artifact로 기록한 경우를 각각 테스트한다.

#### 10. Runtime Binding 생성 실패의 보상 복구

- [x] Run에 저장한 이전 완료 시각을 사용해 Binding 실패를 원래 상태로 복구하는 방식을 확정했다.

**문제점**

후속 Run이 DB에 생성된 뒤 Runtime Binding 파일 생성이 실패할 수 있다. 현재 일반 Run 보상 함수는 WI를 `ready`로 돌리지만, 실제 수정이 시작되기 전의 후속 WI는 원래 `done`이었다.

**해결해야 할 것**

- 방금 생성한 정확한 Run만 compare-and-set 방식으로 `interrupted` 처리해야 한다.
- `origin=follow_up`이면 WI를 이전 `done` 상태로 복구해야 한다.
- 후속 시작 전의 `closed_at`을 State Event payload 또는 동등한 위치에 저장해 복원해야 한다.
- 보상 처리까지 실패하면 프로젝트 변경을 중단하고 PreToolUse가 계속 차단해야 한다.

**해결 방안**

- `runs` 테이블에 후속 시작 전 WI의 완료 시각을 보관하는 nullable 필드 `previous_work_item_closed_at`을 추가한다. 일반 Run은 `NULL`, `origin=follow_up` Run은 시작 직전 WI의 `closed_at` 값을 저장한다.
- `previous_work_item_closed_at`은 Binding 실패 보상 복구에 사용하는 Run 생성 시점의 스냅샷이다. 후속 Run 생성 후에는 수정하지 않고 Run 이력과 함께 보존한다.
- `start_follow_up_run()`은 단일 트랜잭션 안에서 WI가 `done`이고 `closed_at`이 존재하는지 확인한 뒤, 새 Run의 `previous_work_item_closed_at`에 그 값을 저장하고 WI의 `closed_at`을 `NULL`로 바꾼다.
- 기존 `recover_unbound_run()`을 확장해 방금 생성한 Run의 `origin`을 확인한다. `origin=normal`이면 기존대로 WI를 `ready`로, `origin=follow_up`이면 WI를 `done`으로 복구한다.
- `origin=follow_up` 복구 시 WI의 `closed_at`은 현재 시각으로 새로 기록하지 않고 해당 Run의 `previous_work_item_closed_at` 값으로 정확히 복원한다. WI의 `next_action`과 `block_reason`은 완료 상태에 맞게 비운다.
- 복구 대상은 `expected_run_id`와 현재 WI의 유일한 `running` Run이 일치하고, Run과 WI가 각각 `running`·`in_progress`일 때만 허용한다. 다른 세션이 만든 새 Run을 자동 중단하거나 덮어쓰지 않는다.
- Run의 `origin`과 `previous_work_item_closed_at` 조합이 잘못된 경우에는 추측으로 복구하지 않고 오류를 반환한다. 후속 Run인데 이전 완료 시각이 없으면 데이터 불일치로 취급한다.
- Run을 `interrupted`로 종료하고 WI를 원래 상태로 복구하며 관련 State Event를 기록하는 작업은 하나의 DB 트랜잭션에서 모두 성공하거나 모두 rollback한다.
- 보상 복구까지 실패하면 PostToolUse는 작업 시작 실패를 반환하고 Runtime Binding을 만들지 않는다. 기존 PreToolUse는 Binding이 없으므로 프로젝트 변경을 계속 차단한다.
- 일반 Run의 `ready` 복구, 후속 Run의 `done` 및 기존 `closed_at` 복원, 잘못된 `expected_run_id`, 바뀐 활성 Run, 누락된 이전 완료 시각과 복구 트랜잭션 실패를 각각 테스트한다.

#### 11. 작업 중단·실패·후속 수정 취소의 의미

- [ ] 후속 Run 중단과 WI 전체 취소를 구분하는 종료·복구 규칙을 확정한다.

**문제점**

Binding 성공 후에는 실제 파일 수정이 남았을 수 있으므로 후속 Run이 중단됐다고 WI를 무조건 이전 `done`으로 돌릴 수 없다. 또한 현재 `finish_work(outcome=cancelled)`는 Run뿐 아니라 WI 전체를 `cancelled`로 만들어, 후속 수정 포기가 과거의 정상 완료까지 취소할 수 있다.

**해결해야 할 것**

- 작업 중 stale·interrupted 후속 Run은 WI를 `ready`로 돌려 이어갈 수 있게 해야 한다.
- 후속 수정 의도를 `next_action`에 유지해야 한다.
- 다른 세션의 Run은 사용자 확인 없이 복구하거나 takeover하지 않아야 한다.
- “후속 수정만 포기”와 “WI 자체 취소”를 별도 의미로 처리해야 한다.
- 실제 변경이 남았을 수 있는 경우 자동으로 `done`을 복원하지 않아야 한다.
- 모든 경로에 Run 종료 시각, summary, termination_reason과 State Event를 남겨야 한다.

#### 12. Feature 상태와 진행률의 일시적 불일치

- [ ] 완료 WI 재활성화가 Feature와 프로젝트 진행률에 미치는 영향을 허용 가능한 방식으로 처리한다.

**문제점**

`completed` Feature 아래의 WI를 `in_progress`로 재활성화하면 Feature 상태는 완료인데 계산된 완료율은 100% 미만일 수 있다. 프로젝트의 `done_count`도 작업 중 일시적으로 감소한다.

**해결해야 할 것**

- 후속 WI 시작 때문에 Feature를 자동으로 `active`로 바꾸지 않아야 한다.
- Feature의 수동 lifecycle 상태와 계산된 진행률이 다를 수 있음을 UI가 안전하게 표현해야 한다.
- 시작 시 `done_count - 1`, `in_progress_count + 1`이 되고 재완료 시 복원되는지 검증해야 한다.
- 후속 Run이 WI 총개수나 완료 집계에 중복 항목을 만들지 않아야 한다.

#### 13. 전체 회귀 검증과 운영 문서 동기화

- [ ] 정상·실패 경로를 종단 간 검증하고 관련 코드 지침과 문서를 일치시킨다.

**문제점**

개별 함수 테스트만 통과해도 Hook→picker→MCP→Binding→PreToolUse→finish 전체 흐름에서 누락이 생길 수 있다. 기존 일반 `w/` 선택과 `start_work(mode=normal)`이 깨질 위험도 있으며, 구현 후 설계 문서와 실제 코드가 달라질 수 있다.

**해결해야 할 것**

- 신규 DB와 기존 DB migration을 모두 테스트해야 한다.
- 상태 저장, MCP, request/result, fzf 키, 빈 목록, 경쟁 상태, Binding 보상, 중단·취소와 완료 조건을 검증해야 한다.
- 실제 picker 키 흐름과 전체 lifecycle을 종단 간 확인해야 한다.
- 기존 일반 `w/`와 `mode=normal`에 회귀가 없어야 한다.
- `CONTEXT.md`, MCP·DB 함수표, fzf 선택 문서, 단일 요청 파이프라인, Harness 개요와 관련 skill을 구현 결과에 맞게 갱신해야 한다.
- 문서 인덱스의 카드와 문서 수를 실제 파일과 일치시켜야 한다.

## 전체 완료 조건

13개 문제의 TODO가 모두 완료되고 현재 WorkItem의 Acceptance Criteria가 유효한 Evidence로 검증되기 전에는 WI를 `done`으로 종료하지 않는다.

## 용어

- **WI / WorkItem**: 사용자가 달성하려는 작업 목적과 완료 조건을 담는 작업 개체다.
- **Run**: 한 WI를 위해 Codex가 수행하는 한 번의 실행 기록이다.
- **후속 Run**: 완료 WI에 사용자 피드백을 반영하기 위해 새로 시작하는 Run이다.
- `**mode**`: `start_work()`가 일반 시작인지 완료 WI 후속 시작인지 구분하는 입력값이다.
- `**origin**`: Run이 일반 실행인지 후속 실행인지 DB에 영속적으로 기록하는 필드다.
- **AC / Acceptance Criterion**: WI의 목표 달성 여부를 판단하는 개별 완료 조건이다.
- **Evidence**: AC 충족을 유효한 Artifact와 연결해 보여주는 검증 근거다.
- **Artifact**: 파일, 테스트, 커밋처럼 Run의 결과를 나중에 확인할 수 있는 기록이다.
- **Runtime Binding**: 현재 session·turn을 정확한 WI·Run에 연결하는 임시 실행 포인터다.
- **State Event**: 상태 변경의 이전 값, 이후 값과 이유를 시간순으로 남기는 감사 기록이다.
- **migration**: 기존 데이터를 보존하면서 DB 스키마와 제약을 새 구조로 옮기는 절차다.
- **선택 스냅샷**: Terminal 실행 전에 request JSON에 고정한 WI 후보 목록이다.
- **sentinel**: 실제 WI가 아니라 빈 목록 안내를 표현하는 선택 불가능한 특수 행이다.
