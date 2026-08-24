# Harness v2 핵심 용어

이 문서는 Harness v2의 설계와 구현에서 같은 단어를 같은 뜻으로 사용하기 위한 짧은 용어집이다. 세부 규칙은 아래 정본 문서를 따른다.

- 상태 구조와 제약: `docs/state-store.html`
- 단일 요청의 실행 흐름: `docs/single-request-pipeline.html`
- 작업 단위 분해: `docs/implementation-units.md`
- MCP 도구와 DB 함수: `docs/mcp-state-store-functions.html`
- 장기 기억 후보: `docs/memory-candidate-schema.html`

## 작업 구조

**Project**  
현재 저장소 전체를 뜻한다. 모노 프로젝트이므로 별도 `projects` 테이블이나 Project 객체는 두지 않는다.

**Feature**  
여러 WorkItem을 묶어 프로젝트의 큰 기능·목표·작업 영역을 보여주는 선택적 분류다. 실제 진행률은 소속 WorkItem에서 계산한다.

**WorkItem**  
독립적인 목표와 완료 조건을 가진 실제 프로젝트 작업 한 건이다. 설계·조사·의사결정·구현·검증을 모두 포함할 수 있으며, Feature가 없어도 만들 수 있다.

**WorkItem kind**  
작업의 성격이다. `implementation`, `bug`, `research`, `decision`, `refactor`, `migration`, `verification`, `maintenance` 중 하나다.

**WorkItem status**  
작업의 현재 단계다. `backlog → ready → in_progress → blocked | done | cancelled` 흐름을 사용하며, `blocked`는 해결 후 `ready`로 돌아간다.

**Priority**  
실행 우선순위다. 높은 순서대로 `urgent`, `high`, `normal`, `low`를 사용한다.

**next_action**  
다음 Run이 가장 먼저 수행할 구체적인 행동이다. `ready`, `in_progress`, `blocked` WorkItem에는 필수이고, `done`, `cancelled`에서는 비운다.

**block_reason**  
WorkItem이 `blocked`인 이유다. 막힌 상태에서만 기록하고, 다시 진행할 수 있으면 지운다.

## 실행과 종료

**Run**  
하나의 WorkItem을 진전시키기 위한 한 번의 Codex 작업 실행이다. 하나의 WorkItem에는 여러 Run이 쌓일 수 있지만 동시에 `running`인 Run은 하나만 허용한다.

**intent**<br>
이번 Run에서 하려는 일을 사람이 이해할 수 있는 짧은 자연어 문장으로 기록한 값이다. `start_work()`를 호출할 때 Codex가 만든다.

**recall_query**<br>
이번 Run과 관련된 장기 기억을 찾기 위한 공백 구분 핵심어 문자열이다. `start_work()`가 `intent`와 별도로 받고, PostToolUse Hook은 단어를 추측하지 않고 그대로 Recall Wrapper에 전달한다.

**Run status**  
Run 자체의 실행 결과다. `running`, `succeeded`, `failed`, `interrupted`, `cancelled` 중 하나다.

**Outcome**  
`finish_work()`에 전달하는 종료 의도다. DB 필드가 아니라 Run과 WorkItem 상태를 함께 결정하는 MCP 입력값이다.

- `completed`: Run은 `succeeded`, WorkItem은 `done`
- `progressed`: 이번 Run은 성공했지만 전체 작업이 남아 WorkItem은 새 `next_action`과 함께 `ready`
- `retry_needed`: Run은 `failed`, WorkItem은 다시 시도할 수 있도록 `ready`
- `blocked`: Run은 `interrupted`, WorkItem은 `blocked`
- `interrupted`: Run은 `interrupted`, WorkItem은 이어갈 수 있도록 `ready`
- `cancelled`: Run과 WorkItem 모두 취소

**summary**  
Run에서 실제로 한 일과 얻은 결과를 짧게 설명한 종료 기록이다.

**termination_reason**  
Run이 성공하지 못하고 `failed`, `interrupted`, `cancelled`로 끝난 이유다. 성공한 Run에는 기록하지 않는다.

## 완료와 검증

**Acceptance Criterion**  
WorkItem을 `done`으로 판정하기 위해 충족해야 하는 결과 중심의 완료 조건 한 건이다. 특정 테스트 명령보다 확인할 결과를 기술한다.

**Criterion status**  
완료 조건의 판정이다. `pending`, `passed`, `failed`, `waived` 중 하나다. 모든 조건은 기본적으로 필수이며, 더 이상 적용할 수 없는 조건만 이유와 함께 `waived`로 면제한다.

**Artifact**  
Run이 남긴 파일, 커밋, 테스트·린트·빌드 결과처럼 위치를 참조하고 검증할 수 있는 산출물이다. 원문 전체 대신 `uri`, 검증 상태와 짧은 요약을 상태 저장소에 기록한다.

**Artifact verification status**  
Artifact의 검증 결과다. `not_applicable`, `pending`, `passed`, `failed` 중 하나다. `pending`은 나중에 `passed` 또는 `failed`로 한 번만 확정할 수 있다.

**Evidence**  
어떤 Artifact가 어떤 Acceptance Criterion을 뒷받침하는지 나타내는 연결이다. 독립적인 파일이 아니라 `criterion_evidence` 테이블의 관계 행이다.

## 상태와 실행 기록

**State Store**  
Feature, WorkItem, Run, 검증 결과와 기억 후보의 현재 상태를 보관하는 로컬 SQLite 저장소다. 실제 데이터의 정본은 `.harness/state.db`, 재현 가능한 구조의 정본은 `.harness/schema.sql`이다.

**State Event**  
상태가 언제, 누구에 의해, 왜 바뀌었는지 순서대로 남기는 수정 불가능한 감사 기록이다. 현재 상태나 상세 실행 로그를 대신하지 않는다.

**Trace**  
Run 중 발생한 모델·도구 호출, 오류와 실행 시간을 담는 상세 관측 기록이다. 상태 저장소에는 Trace 본문 대신 Run의 `trace_ref`만 저장한다.

**Runtime Binding**  
현재 Codex 세션을 실행 중인 `work_item_id`와 `run_id`에 연결하는 짧게 살아 있는 로컬 JSON 포인터다. `.harness/runtime/bindings/<session-id>.json`에 저장하며, 정본이 아니므로 Hook은 SQLite 상태를 다시 확인해야 한다. `runtime_binding.py`가 원자적 저장·조회·삭제와 Run 소유 세션 검색을 제공한다.

**expected_run_id**
사용자가 중단 처리하기로 확인한 Run의 ID다. `recover_abandoned_work()`는 이 ID가 여전히 해당 WorkItem의 실행 중 Run일 때만 `interrupted`로 종료하여, 확인 도중 다른 세션의 상태가 바뀐 경우 잘못 덮어쓰지 않는다.

## 장기 기억

**Memory Candidate**  
MemoryGraph에 장기 기억으로 저장할 가치가 있을 수 있는 검토 전 초안이다. 작업 중 발견 즉시 현재 Run에 한 건씩 저장한다.

**MemoryGraph**  
검토를 통과한 결정, 문제, 해결책과 재사용 패턴을 관계와 함께 보관하는 장기 기억 저장소다. 프로젝트 진행 상태나 원본 Trace를 저장하는 곳이 아니다.

**Promotion**  
Memory Candidate를 검토하여 MemoryGraph에 새 노드로 저장하거나 기존 기억에 병합하고, 후보 상태를 `promoted`로 바꾸는 과정이다.

**Rejection**  
중복되거나 장기 가치가 부족한 Memory Candidate를 `rejected`로 마감하는 과정이다.

## 실행 주체와 인터페이스

**MCP**  
Codex에 상태 저장소의 기능을 도구로 노출하는 인터페이스다. Codex는 MCP 도구를 호출하고, MCP 서버는 `state_store.py`의 DB 함수를 실행한다.

**DB function**  
`state_store.py`에 모아 둔 내부 함수다. SQLite 연결, 상태 규칙과 SQL 실행을 책임지며 MCP, Hook, CLI와 테스트가 재사용한다.

**Hook**  
정해진 생명주기 시점에 모델 추론 없이 실행되는 자동 처리다. 상태 조회, Run 일치 검사, Trace와 명확한 Artifact 기록, 종료 누락 검사처럼 기계적인 일을 담당한다.

**PreToolUse**  
각 도구 실행 직전에 Runtime Binding과 실제 Run 상태, 실행 정책을 확인하고 Trace 입력을 기록하는 Hook이다.

**PostToolUse**  
각 도구 실행 직후 결과를 Trace에 기록하고, `start_work`·`finish_work`에 맞춰 Runtime Binding을 관리하며, 기계적으로 식별 가능한 검증 결과를 Artifact로 등록하는 Hook이다.

**additionalContext**<br>
Hook이 Codex의 다음 추론에 추가하는 짧은 문자열이다. `start_work` 전용 PostToolUse는 Recall 결과에서 기억의 제목·요약·매칭 키워드만 추려 JSON Context Packet으로 넣는다.

**Stop Guard**  
Codex가 종료하려 할 때 실행 중인 Run, 미해결 검증과 기억 후보가 남았는지 검사하는 Hook이다. 결과를 대신 판단하거나 Run을 자동 종료하지 않고, 누락이 있으면 같은 Codex에 한 번만 정리를 요청한다.

**Skill**  
LLM의 판단이 필요한 반복 절차를 묶은 지침이다. 이 프로젝트에서는 Memory Candidate의 가치·중복·관계를 판단하는 Finalize 절차 등에 사용한다.

## 핵심 구분

- WorkItem은 프로젝트 작업의 목표와 현재 상태이고, Run은 그 목표를 진전시키려는 한 번의 실행이다.
- Artifact는 검증 가능한 결과이고, Evidence는 그 결과와 완료 조건 사이의 연결이다.
- State Event는 상태 변경 이력이고, Trace는 상세 실행 이력이다.
- State Store는 현재 프로젝트 진행의 정본이고, MemoryGraph는 미래에 재사용할 지식의 정본이다.
- Runtime Binding은 현재 Codex 세션이 어느 Run을 수행하는지 알려주는 임시 포인터일 뿐 정본이 아니다.
- Hook은 기계적 처리를 맡고, Codex는 의미와 종료 결과를 판단하며, Skill은 반복되는 고급 판단 절차를 제공한다.
