---
name: work-start
description: 사용자 요청을 기존 WorkItem에 연결하거나 새 WorkItem과 Run을 시작할 때 UPS 상태 패킷을 처리하여 WorkItem 선택·검색·생성, get_work_context, start_work까지 수행한다.
---

# Work start

`<work-selection-context>`와 현재 사용자 요청을 함께 사용한다. 이 절차는 메인 Codex가 직접 수행한다.

## 1. 입구 확인

상태 패킷이 없으면 요청의 성격만 분류한다. 단순 질의는 답할 수 있지만 프로젝트 작업은 변경하지 않고 다음과 같이 알린다.

> UPS 상태 패킷을 받지 못해 WorkItem과 Run을 안전하게 결정할 수 없습니다. 프로젝트 파일은 변경하지 않았습니다. Hook 상태를 확인한 후 다시 요청해 주세요.

스킬에서 UPS의 DB 조회나 stale Run 복구를 재현하지 않는다. 패킷의 `db_ok`가 `false`인 경우도 같은 방식으로 프로젝트 작업을 중단하고 `warnings`를 알린다.

## 2. 요청 분류

다음은 답변만 제공하는 일회성 요청이다.

- 프로젝트의 파일, 설계, 결정, 상태 또는 다음 행동을 바꾸지 않는 개념 설명과 짧은 질의응답

다음은 WorkItem과 Run이 필요한 프로젝트 작업이다.

- 설계·아이디어 구체화, 프로젝트 조사·결정
- 문서·코드 생성 또는 수정
- 테스트·검증·디버깅
- 프로젝트 결과, 진행 상태 또는 다음 행동을 바꾸는 일

## 3. 명시적 완료 분기

`selection_status`가 `selected`이고 사용자 요청이 선택한 WI 자체를 지금 완료하라는 명시적 요청이면 패킷의 `work_item_id`로 `close_work_item(work_item_id, reason)`을 호출하고 종료한다.

- 완료 요청: “이 WI 완료 처리해줘”, “선택한 작업을 done으로 닫아줘”
- 일반 작업: “WI 완료 기능을 구현해줘”, “완료됐는지 확인해줘”, “결과가 좋아”

이 분기에서는 Draft 구체화, 후보 비교·검색, WI 생성·수정, `intent`·`recall_query` 생성과 `start_work()`를 수행하지 않는다. 완료 함수가 현재 상태, 활성 Run과 AC·Evidence를 검증하며, 실패하면 이유를 보고하고 다른 흐름으로 우회하지 않는다.

## 4. 실행 중 충돌 확인

프로젝트 작업이면 `active_work_items`를 먼저 비교한다. 같은 목표의 WorkItem이 `other_session` 또는 `unknown` 소유로 실행 중이면 새 WorkItem과 Run을 만들지 않는다. 다음 문장으로 사용자 확인을 받는다.

> 이 WorkItem은 다른 세션에서 실행 중입니다. 해당 세션이 이미 종료됐다면 기존 Run을 `interrupted`로 정리하고 WorkItem을 `ready`로 되돌릴까요?

사용자가 세션 종료를 명시적으로 확인한 경우에만 패킷의 정확한 ID로 `recover_abandoned_work(work_item_id, expected_run_id, "confirmed")`를 호출한다. 복구 함수는 새 Run을 만들지 않으므로 이후 이 절차를 다시 이어간다.

`current_session` 소유의 같은 Turn Run은 Hook 재시도로 이미 시작된 작업이다. 같은 WorkItem에 `start_work()`를 다시 호출하지 않고 기존 Run을 사용한다.

## 5. WorkItem 결정

`selection_status`가 `selected`이면 사용자가 `w/` 선택기에서 고른 `work_item_id`를 이번 작업의 대상으로 사용한다. 후보 비교·검색·새 WorkItem 생성은 하지 않는다.

선택한 WorkItem의 `is_draft`가 `true`이면 먼저 `get_work_context(work_item_id)`를 호출한다. 제목·종류·목표·선택 메모(`description`)를 읽고, 이번 작업을 실제로 시작할 수 있도록 다음을 판단한다.

- `priority`
- `feature_id`: 명확히 속하는 Feature가 있을 때만 설정
- `next_action`
- 결과 중심 Acceptance Criteria 1개 이상

그 결과를 `refine_draft_work_item()`에 한 번 전달한다. 이 도구는 Draft를 `ready`로 바꾸고 Acceptance Criteria를 함께 저장한다. 성공 후에는 `get_work_context(work_item_id)`를 다시 호출하고 6단계로 간다. 구체화 또는 저장에 실패하면 Run과 프로젝트 변경을 시작하지 않는다.

`selection_status`가 없을 때만 아래의 자동 결정 절차를 사용한다.

1. `ready_candidates` 최대 3개를 비교한다. “이 요청의 완료가 기존 WorkItem의 `goal` 달성에 필요한가?”가 명확히 참인 항목만 재사용한다. 제목보다 `goal`과 `next_action`을 우선한다.
2. 맞는 후보가 없으면 요청에서 구별력 있는 핵심어 2~5개를 만들고 `search_work_items(terms, statuses=["backlog", "ready", "blocked"], limit=5)`를 한 번만 호출한다.
3. 검색 결과가 여러 개라 목표를 구분할 수 없으면 사용자에게 짧게 확인한다.
4. 같은 목표가 없을 때만 새 WorkItem을 만든다.

검색 결과 상태별 처리:

- `ready`: 선택한다.
- `backlog`: `next_action`이 실행 가능하면 `change_work_item_state(..., status="ready")`로 준비한다.
- `blocked`: 현재 요청이 차단 원인을 실제로 해결할 때만 `ready`로 바꾼다.
- `in_progress`: 중복 실행하지 않는다.
- `done`, `cancelled`: 새 Run 대상으로 재사용하지 않는다.

새 WorkItem에는 짧은 `title`, 결과 중심의 `goal`, 작업 `kind`, 바로 수행할 `next_action`, 4단계 `priority`, 구체적인 명령에 종속되지 않은 Acceptance Criteria를 넣는다. 명확히 속하는 기존 Feature가 있을 때만 연결한다. 생성 후 `ready`로 바꾼다.

## 6. Run 시작

1. 선택한 항목에 `get_work_context(work_item_id)`를 호출한다.
2. `goal`, `next_action`, Acceptance Criteria, 최근 Run, 활성 상태를 확인하고 낡은 계획만 필요한 범위에서 정리한다.
3. 이번 실행을 설명하는 짧은 자연어 문장 `intent`를 만든다.
4. 장기 기억 검색용 핵심어 2~5개를 공백으로 구분한 `recall_query`를 만든다.
5. `start_work(work_item_id, intent, recall_query)`를 호출한다.

WorkItem 선택을 위한 최소 조회는 허용된다. 실제 프로젝트 변경은 `start_work()` 성공과 PostToolUse의 Runtime Binding 생성 결과를 받은 뒤 시작한다. 실패하거나 Hook이 중단 경고를 주면 프로젝트를 변경하지 않는다.
