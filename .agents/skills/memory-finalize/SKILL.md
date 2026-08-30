---
name: memory-finalize
description: 활성 Run의 pending Memory Candidate를 기존 MemoryGraph 기억과 비교해 저장 가치·노드 타입·create/merge/reject·관계를 판단하고 finalize_memory_candidate로 마감한다. work-finish가 장기 기억 후보를 처리할 때 사용한다.
---

# Memory finalize

pending 후보 하나를 장기 기억으로 마감한다. 판단은 이 스킬이 수행하고, State DB·MemoryGraph 쓰기와 재시도 안전성은 `finalize_memory_candidate()`에 맡긴다.

## 후보 처리

1. `inspect_memory_candidate(candidate_id)`를 호출한다.
2. 응답의 `candidate.storage_plan`이 이미 있으면 새 판단을 만들지 않는다. `finalize_memory_candidate(candidate_id)`만 한 번 호출하고 종료한다.
3. 최초 판단이면 [scoring.md](references/scoring.md)를 전부 읽고 강제 제외와 10점 평가를 적용한다.
4. 6점 이하거나 강제 제외 대상이면 `reject` Plan을 만든다.
5. 7점 이상이면 `matches`와 의미를 비교한다.
   - 내용과 근거가 완전히 같음: `reject`
   - 같은 주제의 기존 기억에 검증된 새 정보를 보강함: `merge`
   - 독립적으로 다시 찾을 가치가 있는 새 지식: `create`
6. 저장할 노드 타입을 결정한다. 관계를 만들거나 타입이 애매하면 [memory-model.md](references/memory-model.md)를 전부 읽는다.
7. 아래 계약으로 Storage Plan을 만들고 `finalize_memory_candidate(candidate_id, storage_plan)`을 한 번 호출한다.

후보의 `proposed_type`은 힌트일 뿐이다. 최종 `memory.type`은 후보가 나중에 어떤 목적으로 검색될지를 기준으로 다시 고른다.

## Storage Plan

```json
{
  "decision": "create | merge | reject",
  "target_memory_id": "merge 대상 Memory ID 또는 null",
  "memory": {
    "type": "MemoryGraph MemoryType",
    "title": "200자 이하의 검색 가능한 제목",
    "content": "다른 세션에서도 독립적으로 이해되는 내용",
    "summary": "500자 이하 핵심 요약",
    "tags": ["검색 태그"],
    "importance": 0.8,
    "confidence": 0.9
  },
  "relationships": [{
    "direction": "outgoing | incoming",
    "target_memory_id": "inspect 결과에 포함된 기존 Memory ID",
    "type": "MemoryGraph RelationshipType",
    "strength": 0.9,
    "confidence": 0.9,
    "context": "두 기억의 관계를 설명하는 한 문장"
  }],
  "reason": "score=8/10; create; 판단 근거"
}
```

- `reject`는 `memory`, `target_memory_id`, 관계를 비운다.
- `merge`는 `matches`에 실제로 존재하는 `target_memory_id`가 필요하다.
- `importance`는 저장 가치 점수 ÷ 10을 기본값으로 한다.
- `confidence`는 근거 명확성 2점이면 `0.9`, 1점이면 `0.7`을 기본값으로 한다.
- 관계는 명확한 것만 최대 3개 만든다. 약한 연상은 생략한다.
- `reason`에는 총점, 강제 제외 여부, create·merge·reject 이유를 짧게 남긴다.

## 완료 조건

- `committed`: 후보 처리가 끝났다.
- `partial` 또는 도구 실패: 후보와 저장된 Plan을 그대로 남기고 같은 Turn에서 계획을 바꾸거나 반복 호출하지 않는다.
- `validation_error`: Plan을 실제 계약과 대조해 한 번만 고쳐 호출한다. 같은 오류가 반복되면 남은 오류를 보고한다.
- 후보 여러 개를 처리한 뒤 `get_postflight_status()`를 다시 읽어 pending 수를 확인한다.

