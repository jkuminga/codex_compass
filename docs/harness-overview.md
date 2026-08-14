# Harness v2 개요와 다음 설계 과제

## 현재 구조

```text
사용자 요청
  ↓
메인 에이전트
  ├─ 현재 작업 상태 조회
  ├─ 관련 장기 기억 회상
  ├─ 필요 시 스킬/서브에이전트 호출
  ├─ 코드·Git·테스트 수행
  ├─ 검증
  └─ 상태·기억·실행 기록 갱신
       ↓
[상태 저장소] [MemoryGraph] [Git/CI] [Trace]
```

## 구성 요소 역할


| 구성          | 역할                | 저장하는 것                          |
| ----------- | ----------------- | ------------------------------- |
| `AGENTS.md` | 항상 지킬 최소 규칙       | 승인 원칙, 테스트 기준, 기억 사용 규칙         |
| 상태 저장소      | 프로젝트의 현재 지도       | 작업 상태, 다음 행동, 블로커, 완료 조건, 검증 근거 |
| MemoryGraph | 장기 경험 지도          | 결정, 원인, 해결책, 패턴, 기억 간 관계        |
| Git/CI      | 실제 변경과 검증의 증거     | 코드, 커밋, PR, 빌드·테스트 결과           |
| Trace       | 실행을 사후 분석하는 상세 기록 | 도구 호출, 오류, 시간, 토큰, 실행 흐름        |
| 스킬          | 반복 작업 방법의 재사용 단위  | 조사, 구현, 리뷰, 검증, 기억 관리 절차        |


## 결정된 원칙

- 프로젝트 진행 상황은 여러 문서가 아니라 **상태 저장소 한 곳**에서 관리한다.
- 문서는 규칙과 설명만 담당한다. 진행 현황·기능 인덱스 문서는 필요할 때 상태 저장소에서 자동 생성한다.
- `Feature`는 큰 목표·기능·장기 작업 영역이며, 실제 상태 관리와 실행은 작고 검증 가능한 `WorkItem` 단위로 한다.
- `WorkItem`은 현재 목표, 완료 조건, 상태, 블로커, `next_action`을 가진다.
- `WorkItem`은 구현뿐 아니라 설계, 조사, 의사결정과 검증 작업도 포함한다.
- `Run`은 한 WorkItem을 진전시키려는 한 번의 Codex 작업 세션이다.
- 한 Run이 정상적으로 진전됐지만 WorkItem 전체가 남았다면 `progressed`로 종료한다. Run은 `succeeded`, WorkItem은 새 `next_action`이 있는 `ready`로 이어간다.
- 답변으로 끝나는 일회성 질문에는 WorkItem과 Run을 만들지 않는다.
- 프로젝트 작업은 관련 기존 WorkItem 재사용을 우선하며, 독립적인 새 목표일 때만 WorkItem을 만든다.
- 하나의 Run에는 커밋, 테스트 결과, PR 등 여러 `Artifact`가 연결될 수 있다.
- `Artifact`는 에이전트의 완료 주장이 아니라 실제로 검증 가능한 산출물이다.
- MemoryGraph에는 모든 대화나 로그가 아니라 미래에 재사용할 결정·문제·해결·패턴만 저장한다.
- 작업 시작에는 관련 기억을 회상하고, 작업 종료에는 기억 후보를 선별해 저장·연결한다.
- Trace는 기본적으로 기계 로그로 저장하고, 모델에는 필요한 짧은 요약만 전달한다.
- 모델에게 세세한 절차를 지시하기보다 최소 규칙, 권한 경계, 검증 기준을 제공한다.
- 서브에이전트는 고정 조직으로 상시 운영하지 않고, 조사·구현·리뷰·검증처럼 필요할 때만 전문 역할로 호출한다.

## Codex 작업 lifecycle과 강제 방식

Codex가 메인 코드 에이전트가 된다. 목표 구조에서는 필수 절차를 `AGENTS.md`의 긴 지시가 아니라 Codex Hook과 로컬 스크립트로 처리한다. 현재는 SQLite 상태 저장소와 MCP 도구까지 구현되었고, Hook·Trace·Runtime Binding·MemoryGraph Finalize Skill은 다음 구현 범위다.

```text
UserPromptSubmit
  → 상태 DB 조회, 관련 기억 회상, 짧은 Context Packet 주입

Codex + MCP
  → 일회성 답변과 프로젝트 작업 구분
  → 기존 WorkItem 재사용 또는 독립적인 새 WorkItem 생성
  → 설계·조사·의사결정·구현·검증 프로젝트 작업이면 Run 시작

PreToolUse
  → 위험 명령·범위 위반을 실행 전에 차단 또는 승인 확인

PostToolUse
  → trace 기록, 식별 가능한 테스트·빌드·커밋 Artifact 등록

Codex + MCP / Skill
  → Evidence 연결, 기억 후보 검토, Run과 WorkItem 종료

Stop
  → 종료를 대신하지 않고 누락만 검사, 실패 시 한 번만 Codex continuation 요청

SessionEnd
  → trace flush·임시 파일 정리 등 보조 처리
```

Hook은 `state_store.py`를 직접 사용하고, Codex는 MCP를 사용한다. Hook이 Run을 자동 생성·종료하지 않으며, 논리 판단이 필요한 상태 정리나 MemoryGraph 저장은 메인 Codex가 수행한다. `Stop` 검사를 통과하지 못한 경우에만 같은 Codex를 조건부로 한 번 더 실행한다.

요청 분류, Hook·MCP·Skill의 역할과 event별 호출은 [단일 Codex 요청 파이프라인](./single-request-pipeline.html)을 정본으로 참고한다.

## 권장 v1 범위

처음에는 구조 검증을 우선하고, 팀 기능이나 대시보드는 나중에 확장한다.

```text
- 로컬 단일 사용자
- 단일 Git 저장소
- SQLite 기반 상태 저장소
- MemoryGraph CLI 또는 MCP 연동
- 최소 work CLI
- JSONL 기반 trace
- 자동 상태 문서 생성은 후순위
```

## 상태 저장소 사용 Interface

### 1. 상태 저장소 인터페이스

`work`는 상태 저장소의 WorkItem을 다루는 CLI 또는 MCP 도구다.

```text
work get HW-12       # 작업 상세 조회
work start HW-12     # 작업 시작
work block HW-12     # 블로커 기록
work evidence HW-12  # 커밋/테스트 결과 연결
work verify HW-12    # 완료 조건 검증
work next            # 다음 우선 작업 제안
```

### 2. 상태 변경 권한

권장 기준은 아래와 같다.


| 상태 변경 | 권한 |
| --- | --- |
| `backlog` → `ready` | 목표·완료 조건·다음 행동이 준비되면 에이전트가 변경 가능 |
| `ready` → `in_progress` | `start_work`가 Run 생성과 함께 변경 |
| `in_progress` → `ready` 또는 `blocked` | Run 종료 이유와 다음 행동을 남기며 `finish_work`가 변경 |
| `in_progress` → `done` | 모든 완료 조건과 유효한 Evidence를 확인한 `finish_work`만 변경 |
| 미완료 상태 → `cancelled` | 취소 이유를 남기고 진행 중인 Run이 있다면 먼저 함께 종료 |
| 프로젝트 목표·범위·우선순위 변경 | 사용자의 의도를 기준으로 결정 |


### 3. MemoryGraph 기억 형식

초기에는 MemoryGraph 자체를 수정하기보다, 하네스가 저장할 기억의 최소 형식을 정한다.

```text
저장 대상:
- 결정
- 문제
- 해결책
- 재사용 패턴
- 중요한 제약

기억마다 포함할 정보:
- 짧은 제목
- 한 가지 명확한 내용
- 태그
- 근거(Git/테스트/문서 참조)
- 관련 WorkItem/Artifact 참조
```

### 4. 검증 기준

`done`의 의미를 프로젝트 초기에 정한다. 작업 성격에 따라 단위 테스트, 타입 검사, 린트, 빌드, E2E 검증, 코드 리뷰, 사용자 승인을 완료 조건으로 선택할 수 있다.

고정 원칙은 다음과 같다.

> 증거 없이 완료 처리하지 않는다.

### 5. 첫 스킬 목록

```text
project-orientation  # 프로젝트 구조와 현재 상태 파악
memory-management    # MemoryGraph 회상·후보 선별·저장·연결
implementation        # 일반 코드 구현 흐름
verification          # 테스트·빌드·E2E 검증
code-review            # 변경 사항과 회귀 위험 검토
```

## 시작 전에 알아둘 주제


| 주제                  | 필요한 이유                                  |
| ------------------- | --------------------------------------- |
| SQLite              | 로컬 우선 상태 저장소의 구현 기반                     |
| Git / PR / CI       | 코드 변경과 검증 근거를 Artifact로 연결              |
| MCP                 | MemoryGraph와 향후 도구를 에이전트에 연결하는 표준 인터페이스 |
| 상태 머신               | 상태 전이 규칙을 설계하는 개념                       |
| OpenTelemetry/Trace | 실행 기록을 모델 입력과 분리해 관측하는 개념               |
| 오픈소스 라이선스           | 외부 도구·코드 포함 시 고지 의무 확인                  |
| 에이전트 스킬             | 반복 작업 방법을 짧고 재사용 가능하게 만드는 방식            |


우선순위는 SQLite, 상태 머신, MCP, Git/CI 순이다.

## 다음 설계 순서

```text
1. v1 범위 확정
2. [완료] WorkItem / Run / Artifact의 SQLite 스키마 설계
3. [완료] 상태 전이 규칙 설계
4. [완료] 실제 `.harness/schema.sql` 작성
5. [완료] 상태 저장소 함수와 MCP 도구 구현
6. Codex hook과 로컬 lifecycle 스크립트 설계
7. MemoryGraph 사용 스킬 설계
8. 작은 실제 프로젝트로 하네스 검증
9. 사용하며 trace·평가·서브에이전트 확장
```
