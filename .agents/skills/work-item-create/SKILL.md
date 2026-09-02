---
name: work-item-create
description: 사용자가 현재 대화의 내용을 바탕으로 WorkItem을 생성·등록·추가하거나 완성 WI·non-Draft WI를 만들어 달라고 명시적으로 요청할 때, 실행 없이 완전한 WorkItem을 ready 상태로 등록한다.
---

# WorkItem 생성

사용자가 현재 대화에서 완성 WI 또는 non-Draft WI를 만들어 달라고 명시한 경우에만 이 스킬을 사용한다. Draft 생성은 웹 콘솔의 별도 흐름이다. 이 절차는 미래 작업을 등록하는 것이며, `w/` 선택이나 Run 시작은 포함하지 않는다.

## 절차

1. 현재 요청과 직전 대화에서 이미 합의된 내용을 읽고 다음 값을 정리한다.
   - `title`: 한 줄 제목
   - `goal`: 작업으로 얻을 결과
   - `kind`: `implementation`, `bug`, `research`, `decision`, `refactor`, `migration`, `verification`, `maintenance` 중 하나
   - `priority`: `urgent`, `high`, `normal`, `low` 중 하나
   - `next_action`: 바로 시작할 수 있는 다음 행동
   - `acceptance_criteria`: 결과를 확인할 수 있는 조건 1개 이상
2. 대화에서 제목이나 목표를 합리적으로 정할 수 없으면 생성 전에 필요한 항목만 한 번 짧게 질문한다. 명확한 Feature가 없으면 `feature_id`는 생략하고, 유용한 배경 메모가 있을 때만 `description`을 넣는다.
3. Acceptance Criteria는 결과 중심으로 작성하며 특정 명령 하나만 강제하지 않는다. `ready`는 실행 준비 상태이지 작업 완료가 아니다.
4. `create_ready_work_item()` MCP 도구를 한 번 호출한다. 이 도구는 `is_draft=false`, `status=ready`, 실행 계획과 Acceptance Criteria를 하나의 DB 트랜잭션으로 저장하는 함수다.
5. 생성 결과의 WorkItem ID로 `get_work_context()`를 한 번 읽어 `ready` 상태와 Acceptance Criteria 저장을 확인한다. 반환된 ID, 제목, 목표, `ready` 상태를 사용자에게 짧게 알린다. Run은 만들지 않는다.

## 완료 확인

생성 결과와 `get_work_context()` 응답에서 다음을 확인한다.

- `is_draft`가 `false`다.
- `status`가 `ready`다.
- `next_action`이 비어 있지 않다.
- Acceptance Criteria가 하나 이상 저장됐다.

도구가 실패하면 다른 생성 도구로 우회하지 말고 오류를 알린다.
