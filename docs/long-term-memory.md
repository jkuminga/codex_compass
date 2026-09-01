# 장기 기억 (MemoryGraph) 운영 전략

## 목적

MemoryGraph는 대화 전문이나 프로젝트 진행률을 저장하는 곳이 아니다. 다음 작업에서도 다시 도움이 될 **결정, 문제 원인, 검증된 해결책, 재사용 패턴**을 관계와 함께 보관하는 장기 기억 저장소다.

```text
상태 저장소
→ 지금 무엇을 하고 있는가

MemoryGraph
→ 왜 그렇게 하며, 과거에 무엇을 배웠는가
```

## 설치 위치와 데이터 위치 분리

MemoryGraph CLI는 전역으로 설치해 어느 프로젝트에서도 실행할 수 있다. 하지만 기억 DB는 프로젝트마다 분리한다.

```text
전역 설치된 memorygraph CLI
  ↓
프로젝트 전용 wrapper
  ↓
Harness_v2/.harness/memorygraph.db
```

기본 전역 DB를 그대로 쓰면 여러 프로젝트의 기억이 섞일 수 있다. 이 하네스는 `MEMORY_BACKEND=sqlite`와 `MEMORY_SQLITE_PATH=<프로젝트 루트>/.harness/memorygraph.db`를 설정한다.

`.harness/memorygraph.db`는 로컬 작업 데이터이며 v1에서는 Git으로 추적하지 않는다. 상태 저장소의 `.harness/state.db`와 같은 SQLite 기술을 사용하지만, MemoryGraph가 자체 스키마와 마이그레이션을 관리하는 별도 DB 파일이다. 여러 기기나 팀 간 공유가 필요해지면 Cloud backend, 공유 DB, export/import 중 하나를 별도로 설계한다.

## Wrapper의 역할

Codex나 Hook이 전역 `memorygraph` CLI를 직접 호출하지 않고, 프로젝트의 `harness-memory` wrapper를 사용한다.

```text
harness-memory recall "SQLite schema"
```

wrapper는 아래를 자동으로 처리한다.

```text
1. 현재 Git 프로젝트 루트 확인
2. MEMORY_BACKEND=sqlite 설정
3. MEMORY_SQLITE_PATH를 .harness/memorygraph.db로 설정
4. 상태 DB와 섞이지 않은 프로젝트 전용 MemoryGraph DB 확인
5. 전역 memorygraph CLI 실행
```

따라서 Codex는 어느 DB를 쓰는지 기억할 필요 없이 `harness-memory`만 사용한다.

## 역할 분리

| 구성 | 역할 |
| --- | --- |
| `AGENTS.md` | 기억을 언제 사용해야 하는지 알리는 짧은 원칙 |
| `memory-management` 스킬 | 기억 후보를 평가하고 중복·관계를 판단하는 상세 방법 |
| Codex Hook | 작업 시작 recall과 종료 시 pending 후보 누락 검사 |
| `harness-memory` wrapper | 프로젝트별 DB 설정으로 MemoryGraph CLI 실행 |
| MemoryGraph | 선별된 장기 지식과 관계 보관 |

`AGENTS.md`에는 최소 규칙만 둔다.

```md
- 작업 시작 시 관련 장기 기억을 조회한다.
- 중요한 결정, 문제 원인, 검증된 해결책, 재사용 패턴만 기억 후보로 삼는다.
- 단순 수정, 진행 상태, 원본 로그는 MemoryGraph에 저장하지 않는다.
```

## 작업 시작: Hook이 자동으로 기억 회상

`start_work()`가 성공한 직후의 전용 `PostToolUse` hook이 장기 기억 조회를 실행한다. 이때는 작업할 WorkItem과 새 Run이 이미 확정되어 있으므로, `UserPromptSubmit`처럼 아직 작업 대상이 정해지지 않은 시점보다 정확한 문맥을 사용할 수 있다.

```text
start_work 성공
  ↓
Runtime Binding 저장
  ↓
확정된 작업 문맥으로 장기 기억 조회
  ↓
상위 3~5개 기억만 짧은 Context Packet으로 Codex에 전달
```

내부 조회는 `harness-memory` wrapper가 MemoryGraph의 `recall`을 사용한다. Codex가 공백으로 구분한 짧은 키워드 문자열을 넘기면 wrapper가 키워드별로 `recall`을 실행하고, 같은 기억을 ID로 병합한 뒤 여러 키워드 검색에 반복해서 등장한 기억을 우선한다. 상위 3~5개만 Codex에 전달하며, 자세한 흐름은 [단일 Codex 요청 파이프라인의 장기 기억 Recall](./single-request-pipeline.html#memory-recall)을 따른다. 조회가 실패해도 이미 시작된 Run은 취소하지 않고 경고만 남긴 뒤 작업을 계속한다.

예를 들어 결제 웹훅 작업이라면 `payment`, `webhook`, `order`, `idempotency` 같은 키워드로 검색한다.

전체 기억 DB나 원본 trace는 모델에 전달하지 않는다. 검색 결과 중 필요한 짧은 정보만 전달해 토큰 사용을 제한한다.

## 작업 중: Hook은 사실을 수집하고 Codex는 후보를 판단

`PostToolUse` hook은 다음과 같은 사실을 trace나 상태 저장소에 기록한다.

- 테스트와 빌드의 성공·실패
- 커밋 SHA, PR URL 같은 Artifact 후보
- 수정 파일 목록과 명령 실패 정보
- WorkItem 상태 변경 이벤트

이 Hook은 “이 변경이 장기 기억으로 가치가 있는가?”를 추론하지 않는다. 모든 도구 결과를 MemoryGraph에 바로 저장하지도 않는다.

## 목표 v1: 메인 Codex가 MCP로 후보 기록

별도 Reviewer LLM을 운영하지 않는다. 메인 Codex가 작업 중 현재 문맥에서 장기적으로 유효한 정보를 발견하면 실행 중인 Run에 `create_memory_candidate` MCP 도구로 후보를 즉시 기록한다.

```text
Codex 작업
  ↓
결정·문제와 해결·재사용 지식 발견
  ↓
create_memory_candidate(run_id, ...)
  ↓
관련 검색·사용 내역은 Run의 trace_ref가 가리키는 Trace에서 확인
```

후보는 SQLite의 `memory_candidates`가 정본이다. Hook은 후보 가치를 판단하지 않으며, Stop 시 pending 후보가 남았는지만 기계적으로 확인한다. 누락된 경우에도 별도 Reviewer를 호출하지 않고 같은 메인 Codex를 한 번 이어서 실행한다.

현재 저장소에는 후보 DB와 MCP 도구가 구현되어 있지만, `AGENTS.md`의 후보 기록 지침은 아직 `long-term/*.json` 실험 방식을 사용한다. 파이프라인 Hook을 구현할 때 이 지침을 `create_memory_candidate` 호출로 교체하면 SQLite가 실제 정본이 된다.

## 다음 구현 목표: MemoryGraph Finalize Skill

후보 DB와 MemoryGraph 저장 도구는 구현되었다. 종료 직전 Finalize Skill이 pending 후보를 검토해 저장·병합·제외와 관계를 판단하고 `finalize_memory_candidate()`를 호출한다. 후보의 최종 상태는 이 도구가 MemoryGraph 처리 결과를 확인한 뒤 내부에서 마감한다.

후보 DB는 [`memory_candidates` 스키마](./memory-candidate-schema.html)를 따른다. 후보는 발견 즉시 한 건씩 저장하고, 관계는 종료 전 검토에서 기존 MemoryGraph 기억과 함께 판단한다. 전체 호출 시점은 [단일 Codex 요청 파이프라인](./single-request-pipeline.html)을 따른다.

## 무엇을 저장하는가

다음은 저장 후보가 된다.

- 중요한 설계 또는 기술 선택과 이유
- 버그의 근본 원인과 검증된 해결책
- 여러 작업에서 재사용할 코드·운영 패턴
- 기존 방식을 대체하거나 무효화한 결정
- 반복해서 알아야 할 외부 제약 또는 위험

다음은 저장하지 않는다.

- 단순 파일 수정
- 일회성 스타일 변경
- 현재 진행률과 체크리스트
- 도구 출력 전문과 대화 전문
- 테스트되지 않은 추측

## 기존 기억과의 관계

새 기억을 저장하기 전에는 관련 기억을 검색한다. 같은 사실이면 중복 저장하지 않고, 확실한 관계가 있을 때만 연결한다.

| 상황 | 관계 또는 처리 |
| --- | --- |
| 같은 사실 | 새 기억을 만들지 않음 |
| 기존 기억의 근거가 추가됨 | `CONFIRMS` |
| 해결책이 문제를 해결함 | `SOLVES` |
| 새 방식이 기존 방식을 대체함 | `REPLACES` |
| 새 사실이 기존 기억을 무효화함 | `CONTRADICTS` |
| 원인과 결과 | `CAUSES` |

관계를 많이 만드는 것이 목표가 아니다. 근거가 확실한 관계만 연결한다.

## 토큰 절감 원칙

```text
- Hook은 DB/파일/CLI만 사용하고 기본적으로 모델 호출을 하지 않는다.
- 작업 시작에는 관련 기억 소수만 짧게 주입한다.
- 작업 중 모든 로그를 MemoryGraph에 저장하지 않는다.
- 기억 검토는 의미 있는 신호가 있을 때만 한다.
- 추가 Codex continuation은 검토가 누락된 경우에만 한 번 사용한다.
```

## 핵심 원칙

> Hook은 기억을 자동으로 찾고, 기억 후보를 놓치지 않게 돕는다. 실제 기억 저장과 관계 판단은 필요한 경우에만 Codex가 수행한다.
