# 완료 WorkItem의 후속 수정 Run 재사용 결정

> **문서 상태: 폐기된 대안**
> 이 문서의 `done` WI 재개 설계는 구현하지 않는다. 현재 구현 기준은 [`user-controlled-work-item-completion.md`](./user-controlled-work-item-completion.md)이며, Codex의 성공한 Run은 WI를 `ready`로 돌리고 사용자만 WI를 `done`으로 닫는다. 아래 내용은 결정 과정의 기록으로만 보존한다.

## 문서 목적

시각적·경험적 결과를 사용자가 직접 확인한 뒤 같은 작업에 추가 수정을 요청하는 경우, 완료된 WorkItem을 재사용하는 흐름을 정의한다.

최초 구현이 정상적으로 완료되었더라도 UI 배치, 문구, 사용감처럼 사용자의 직접 확인이 필요한 결과에는 후속 피드백이 생길 수 있다. 현재는 `done` WorkItem이 `w/` 선택기에 나타나지 않아, 같은 목적의 수정마다 새 WorkItem을 생성해야 한다. 이 문서는 그 반복 비용을 없애기 위한 결정사항을 기록한다.

## 결정 요약

완료된 WorkItem을 기본 선택 목록에 섞지 않고, `w/` 선택 흐름에서 `Ctrl-D`를 누르면 **완료한 WI에서 후속 작업** 화면으로 이동하게 한다.

사용자가 이 진입점에서 `done` WorkItem을 직접 선택하면 다음 동작을 수행한다.

1. 전용 함수가 선택한 WorkItem을 `done`에서 `in_progress`로 전환한다.
2. 같은 DB 트랜잭션에서 해당 WorkItem에 새로운 활성 Run을 하나 생성한다.
3. 기존 Acceptance Criteria와 Evidence는 변경하지 않는다.
4. Codex가 사용자 피드백에 따른 수정과 검증을 수행한다.
5. 새 Run을 종료하고 같은 WorkItem을 다시 `done`으로 닫는다.

이 과정에서 Codex가 요청 내용을 분석해 과거 WorkItem을 추론하거나, PreToolUse 정책을 우회하여 Run 없이 코드를 변경하지 않는다. 어느 완료 WorkItem을 재사용할지는 사용자가 명시적으로 선택한다.

## 문제 흐름과 변경 후 흐름

### 현재 흐름

```text
w/ 최초 요청
  → WI 선택
  → Run 생성
  → 모든 AC 충족
  → WI done
  → 사용자 확인 후 수정 필요
  → w/ 수정 요청
  → done WI가 목록에 없음
  → 동일 목적의 WI 새로 생성
  → 수정 Run 완료 후 WI done
  → 추가 수정마다 위 과정 반복
```

### 변경 후 흐름

```text
w/ 최초 요청
  → WI 선택
  → 최초 Run 완료
  → WI done
  → 사용자 확인 후 수정 필요
  → w/ 수정 요청
  → Ctrl-D로 "완료한 WI에서 후속 작업" 화면 이동
  → 기존 done WI 선택
  → 같은 WI에 새 Run 생성
  → 수정 및 검증
  → 새 Run 종료
  → 같은 WI를 다시 done
```

추가 수정이 생겨도 같은 완료 WorkItem을 다시 선택하여 후속 Run만 추가한다.

## 선택 화면 원칙

기본 fzf 화면은 현재 작업 선택에 집중할 수 있도록 유지한다. 완료 WorkItem은 기본 목록에 항상 노출하지 않고 단축키로 별도 화면에 진입한다.

```text
기본 WorkItem 화면
  Enter   선택한 WI 확정
  Ctrl-D  완료한 WI에서 후속 작업 화면으로 이동
  Esc     선택 취소

완료 WorkItem 화면
  Enter   선택한 완료 WI 확정
  Ctrl-B  기본 WorkItem 화면으로 복귀
  Esc     선택 취소
```

fzf의 `--expect=ctrl-d` 옵션으로 기본 화면의 종료 키를 구분한다. 선택 프로그램은 `ctrl-d`를 받으면 완료 WorkItem 목록으로 두 번째 fzf를 실행한다. 한 fzf 안에서 `reload()`로 목록과 헤더를 동적으로 바꾸는 방식은 상태와 검증 로직을 복잡하게 만들기 때문에 사용하지 않는다.

완료 WorkItem 화면은 다음 원칙을 따른다.

- 현재 프로젝트에 속한 `done` WorkItem만 보여준다.
- 최근 완료된 항목부터 정렬한다.
- 터미널 레이아웃이 과도하게 길어지지 않도록 최초 조회 또는 표시 개수를 제한한다.
- 제목과 식별자를 이용해 원하는 WorkItem을 검색할 수 있게 한다.
- 구체적인 기본 조회 개수와 오래된 항목 탐색 방식은 구현 설계에서 결정한다.

## 선택 데이터 적재

UserPromptSubmit Hook은 Terminal.app을 실행하기 전에 SQLite에서 기본 선택 목록과 최근 완료 목록을 모두 읽는다. 두 목록은 한 시점의 선택 스냅샷으로 request JSON에 저장한다.

```json
{
  "work_items": ["ready WI와 Draft WI"],
  "completed_work_items": ["최근 done WI"]
}
```

선택 프로그램은 request JSON만 읽으며 DB에 직접 접근하지 않는다. 기본 화면에는 `work_items`를 전달하고, 사용자가 `Ctrl-D`를 누르면 이미 전달받은 `completed_work_items`로 두 번째 fzf 화면을 즉시 연다.

이 구조는 다음 성질을 보장한다.

- 화면 전환 시 추가 DB 조회가 필요 없다.
- 선택 프로그램과 상태 저장소의 결합이 생기지 않는다.
- 사용자가 보는 후보가 request 생성 시점의 스냅샷으로 고정된다.
- 최종 결과를 request에 포함된 WorkItem인지 기계적으로 검증할 수 있다.

선택 결과에는 일반 선택인지 완료 WI 후속 선택인지 구분할 수 있는 값을 포함한다. UserPromptSubmit Hook과 이후 작업 시작 절차는 이 값을 이용해 일반 `start_work()`와 완료 WI 전용 시작을 구분한다.

## 상태 전이와 후속 Run 시작

현재 WorkItem 상태 이름은 `active`가 아니라 `in_progress`다. 기존 스키마는 `done`에서 나가는 상태 전이를 허용하지 않고, 일반 `start_run()`도 `ready` WorkItem만 시작할 수 있으므로 완료 WI 재사용에는 명시적인 확장이 필요하다.

완료 WI 후속 작업은 다음 전이를 사용한다.

```text
done
  → start_follow_up_run()
  → in_progress + 새 running Run
  → finish_work(completed)
  → done
```

`start_follow_up_run()`은 완료 WorkItem에 후속 Run을 시작하는 상태 저장 함수다. 함수 이름은 구현 시 프로젝트 명명 규칙에 맞출 수 있지만, 다음 동작은 하나의 DB 트랜잭션 안에서 원자적으로 수행해야 한다.

1. 선택한 WorkItem이 실제로 `done`인지 확인한다.
2. 같은 WorkItem에 `running` Run이 없는지 확인한다.
3. 후속 요청을 비어 있지 않은 `next_action`과 Run의 `intent`로 저장한다.
4. `closed_at`을 비우고 WorkItem을 `in_progress`로 변경한다.
5. 새로운 `running` Run을 정확히 하나 생성한다.
6. Run 생성과 WorkItem 상태 전이를 State Event로 기록한다.

스키마의 허용 전이에는 `done → in_progress`를 추가한다. `done → ready → in_progress`를 외부의 두 호출로 나누지 않는다. 그래야 WI 재활성화와 Run 생성 사이의 부분 실패 및 다른 세션의 동시 진입을 막을 수 있다.

완료 WI를 다시 닫으면 `closed_at`은 가장 최근 완료 시각으로 갱신한다. 최초 완료 시각과 이전 완료 이력은 기존 Run의 `ended_at`과 State Event에서 조회한다. 재활성화된 동안에는 프로젝트 진행률에서 해당 WI가 `done`이 아닌 `in_progress`로 집계되는 것을 정상 동작으로 본다.

## 상태 및 데이터 보존 규칙

완료 WorkItem의 재사용은 기존 작업을 무효화하는 동작이 아니다. 사용자가 이미 완료한 작업의 맥락에서 후속 피드백을 처리하는 동작이다.

따라서 후속 Run을 시작할 때 다음 정보를 그대로 보존한다.

- 기존 Acceptance Criteria의 내용
- 각 Acceptance Criterion의 검증 상태
- 기존 Evidence 연결
- 이전 Run과 Artifact 기록

기존 Acceptance Criteria를 `pending`으로 되돌리거나, 후속 수정 내용을 새로운 Criterion으로 자동 추가하지 않는다. 후속 Run은 수정 파일, 테스트 결과, 커밋 같은 새로운 Artifact만 자신의 실행 결과로 남긴다.

후속 Run을 다시 `done`으로 종료할 때 기존 Acceptance Criteria와 Evidence가 이미 유효하므로 현재 완료 검증을 그대로 재사용한다. 후속 Run의 Artifact를 기존 Criterion에 다시 Evidence로 연결하도록 강제하지 않는다.

## PreToolUse 정책

PreToolUse는 도구 실행 전에 활성 Run 존재 여부 등 실행 조건을 기계적으로 검사하는 Hook이다.

이번 결정에서는 PreToolUse의 Run 필수 정책을 완화하지 않는다. 완료 WorkItem 선택 단계에서 새 Run을 먼저 생성하므로, 이후 코드 변경·테스트·커밋은 기존 정책 안에서 수행할 수 있다.

PreToolUse는 다음 내용을 판단하지 않는다.

- 현재 수정 요청이 어느 과거 WorkItem과 관련되는지
- 수정 요청이 기존 WorkItem의 범위 안인지
- 여러 완료 WorkItem 중 어느 항목이 가장 적절한지

이 판단을 기계화하거나 Codex 추론에 맡기는 대신 사용자의 명시적 선택으로 해결한다.

## 완료 의미

최초 Run의 `done`은 잘못된 조기 종료로 간주하지 않는다. 당시 Acceptance Criteria를 만족했다면 정상적인 완료다.

후속 Run은 최초 구현의 실패를 복구하는 것이 아니라, 사용자 확인 후 발견된 시각적·경험적 수정사항을 같은 작업 맥락에 추가하는 실행이다. 후속 수정과 필요한 검증이 끝나면 Run과 WorkItem을 다시 정상적으로 종료한다.

## 범위 밖 결정

다음 표시 범위만 구현 단계에서 실제 데이터 규모와 터미널 레이아웃을 확인해 정한다.

- 완료 WorkItem 최초 조회 개수
- 오래된 완료 WorkItem을 추가로 불러오는 방식

## 용어

- **WI / WorkItem**: 사용자가 달성하려는 하나의 작업 목적과 완료 조건을 담는 작업 개체다.
- **Run**: 한 WorkItem을 위해 Codex가 수행하는 한 번의 실행 기록이다.
- **후속 Run**: 완료 WorkItem에 사용자 피드백을 반영하기 위해 새로 추가하는 Run이다.
- **AC / Acceptance Criterion**: WorkItem의 목표 달성 여부를 판단하는 개별 완료 조건이다.
- **Evidence**: 특정 Acceptance Criterion이 충족되었음을 Artifact와 연결해 보여주는 검증 근거다.
- **Artifact**: 수정 파일, 테스트 결과, 커밋처럼 Run의 결과를 나중에 확인할 수 있는 기록이다.
- **fzf**: 터미널에서 WorkItem 후보를 검색하고 직접 선택하는 인터페이스다.
- **UserPromptSubmit Hook**: 사용자 프롬프트가 Codex에 전달되기 전에 WI 후보 조회와 선택 요청을 준비하는 Hook이다.
- **request JSON**: Hook이 선택 프로그램에 WI 후보와 요청 식별 정보를 전달하는 임시 파일이다.
- **`start_follow_up_run()`**: 완료 WI를 `in_progress`로 바꾸고 후속 Run을 원자적으로 생성하는 전용 상태 저장 함수의 설계상 이름이다.
- **State Event**: WorkItem과 Run의 상태 변화를 시간순으로 남기는 감사 기록이다.
- **PreToolUse**: 파일 변경이나 명령 실행 전에 활성 Run 등 필수 실행 조건을 검사하는 Hook이다.
- **`done`**: WorkItem의 현재 목표와 완료 조건이 충족되어 닫힌 상태다.
- **`in_progress`**: WorkItem에 활성 Run이 연결되어 현재 작업 중임을 나타내는 상태다.
- **`closed_at`**: WorkItem이 가장 최근에 `done` 또는 `cancelled`로 닫힌 시각이다.

## 관련 작업

- WorkItem: `WI-a6437714c18a` — 완료 WI에서 후속 수정 Run 시작 지원
