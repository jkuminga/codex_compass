---
name: work-finish
description: 활성 Run이 있는 프로젝트 작업의 최종 답변 전에 Postflight를 검토하고 Artifact·Evidence·기억 후보를 확인하여 올바른 outcome으로 finish_work를 호출한다.
---

# Work finish

현재 Turn에서 시작하거나 이어받은 활성 Run을 최종 답변 전에 정리한다. 완료 의미는 Codex가 판단하고 Hook에는 위임하지 않는다.

## 1. Postflight 읽기

`get_postflight_status(work_item_id)`를 호출해 다음을 확인한다.

- `running_run`: 이번에 종료할 정확한 Run
- `verification`: Acceptance Criteria의 해결 여부와 `can_complete`
- `artifacts`: 이번 Run이 남긴 검증 가능한 결과
- `pending_memory_candidates`: 아직 장기 기억으로 검토되지 않은 후보

활성 Run이 없으면 `finish_work()`를 호출하지 않는다. 예상한 Run과 반환된 `running_run.id`가 다르면 종료하지 않고 충돌을 보고한다.

## 2. 검증 결과 정리

이번 Run에서 실제로 끝난 테스트·lint·빌드·커밋·파일 결과가 Artifact로 빠졌다면 `record_artifact()`로 기록한다. 진행 로그, 도움말, dry-run, 불완전 실행은 기록하지 않는다.

- `pending` Artifact의 실제 결과를 알게 됐다면 `resolve_artifact()`로 `passed` 또는 `failed`를 확정한다.
- Artifact가 Acceptance Criterion을 증명하면 `verify_criterion()`으로 Evidence를 연결하고 판정한다.
- 실패한 Artifact를 성공 Evidence로 사용하거나, 완료를 만들기 위해 Criterion을 사후 왜곡하지 않는다.

정리 후 `get_postflight_status()`를 다시 호출한다. 이 재조회 결과가 종료 판단의 기준이다.

## 3. 기억 후보 확인

각 `pending_memory_candidates`를 다음 순서로 처리한다.

1. `inspect_memory_candidate(candidate_id)`로 후보와 관련된 기존 기억을 읽는다.
2. 후보의 `storage_plan`이 이미 있으면 새 판단을 만들지 않고 `finalize_memory_candidate(candidate_id)`만 호출한다.
3. 저장된 Plan이 없으면 후보와 기존 기억을 비교해 하나의 Storage Plan을 만든다.
   - `create`: 새 기억 노드를 만든다.
   - `merge`: `target_memory_id`로 지정한 기존 기억을 보강한다.
   - `reject`: 장기적으로 저장할 가치가 낮아 후보를 폐기한다.
   - `relationships`: 최종 노드와 기존 노드 사이에 실제로 필요한 관계만 넣는다.

Storage Plan은 다음 키만 사용한다. `merge`에는 `target_memory_id`가 필수이고, `reject`에는 `memory`와 관계를 넣지 않는다. `importance`, `confidence`, `strength`는 0~1 숫자다.

```json
{
  "decision": "create | merge | reject",
  "target_memory_id": "merge 대상 Memory ID 또는 null",
  "memory": {
    "type": "MemoryGraph MemoryType",
    "title": "짧은 제목",
    "content": "독립적으로 이해되는 내용",
    "summary": "500자 이하 요약",
    "tags": ["검색 태그"],
    "importance": 0.8,
    "confidence": 0.9
  },
  "relationships": [{
    "direction": "outgoing | incoming",
    "target_memory_id": "기존 Memory ID",
    "type": "MemoryGraph RelationshipType",
    "strength": 0.8,
    "confidence": 0.9,
    "context": "관계 설명"
  }],
  "reason": "판단 이유"
}
```
4. `finalize_memory_candidate(candidate_id, storage_plan)`을 한 번 호출한다. 이 도구가 전체 저장 성공 뒤 후보를 `promoted`로 바꾸며, `reject`도 내부에서 처리한다. `promote_memory_candidate()`나 `reject_memory_candidate()`를 별도로 호출하지 않는다.
5. `partial` 또는 도구 실패라면 후보는 `pending`으로 남는다. 같은 Turn에서 계획을 바꾸거나 무한 반복하지 않고, 저장된 Plan을 다음 재시도에서 재사용한다.

후보 처리 후 `get_postflight_status()`를 다시 호출한다. pending 후보가 남았다면 그 수와 오류를 최종 보고에 포함하되, 후보가 있다는 이유만으로 Run 종료 자체를 막지는 않는다.

## 4. Outcome 선택

다음 중 실제 상태와 일치하는 하나만 선택한다.

| outcome | 의미 | 필수 입력 |
| --- | --- | --- |
| `completed` | WorkItem 전체 목표가 끝났고 재조회한 `can_complete`가 `true` | `summary`, `reason` |
| `progressed` | 이번 Run은 성공했지만 WorkItem에 후속 작업이 남음 | `summary`, `reason`, 구체적인 `next_action` |
| `retry_needed` | 구현이나 검증이 실패하여 다음 Run에서 다시 수행해야 함 | `summary`, `reason`, `termination_reason`, 구체적인 `next_action` |
| `blocked` | 외부 결정·권한·환경 때문에 진행할 수 없음 | `summary`, `reason`, `termination_reason`, `block_reason`, 해제 후의 `next_action` |
| `interrupted` | 작업이 중간에 멈췄지만 다시 시작할 수 있음 | `summary`, `reason`, `termination_reason`, 구체적인 `next_action` |
| `cancelled` | WorkItem 자체를 더 수행하지 않기로 확정함 | `summary`, `reason`, `termination_reason` |

`completed`는 일부 테스트 성공이나 이번 Run의 성공만으로 선택하지 않는다. `can_complete`가 `false`라면 남은 일이 정상적인 후속 작업인지, 실패인지, 차단인지에 따라 다른 outcome을 선택한다.

## 5. Run 종료

선택한 값으로 `finish_work(run_id, outcome, ...)`를 한 번 호출한다.

- 성공 응답에서 Run과 WorkItem의 종료 상태를 확인한다.
- 실패하면 같은 호출을 추측으로 반복하거나 완료했다고 보고하지 않는다. 현재 Postflight 상태와 실패 원인을 사용자에게 알린다.
- 성공 직후의 전용 PostToolUse가 현재 세션 Runtime Binding을 삭제한다. 스킬이 Binding 파일을 직접 지우지 않는다.

최종 답변에는 수행 결과, 검증 결과, 남은 작업 또는 경고를 간단히 전달한다.
