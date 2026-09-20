# 작업 단위 분해 가이드

## 핵심 구분

`Feature`는 여러 작업을 묶는 큰 목표·기능·장기 작업 영역이고, `WorkItem`은 에이전트나 사람이 실제로 끝낼 수 있는 프로젝트 작업이다. WorkItem은 구현뿐 아니라 설계, 조사, 의사결정과 검증도 포함한다.

`Issue`, `Task`, `Ticket`은 사용하는 도구에 따라 이름만 다를 뿐이다. 이 하네스에서는 외부 도구의 Issue와 연결할 수 있는 내부 작업 단위를 `WorkItem`으로 부른다.

```text
Project
  ├─ Feature / Capability / Work Area
  │    └─ WorkItem
  │         └─ Run
  └─ WorkItem                    # Feature가 아직 없으면 직접 생성 가능
       └─ Run
```

| 단위 | 의미 | 예시 |
| --- | --- | --- |
| `Project` | 제품 또는 큰 목표 | `Harness v2` |
| `Feature` / `Capability` | 여러 WorkItem을 묶는 큰 목표·기능·작업 영역 | `상태 관리 파이프라인` |
| `WorkItem` | 독립적인 목표와 완료 조건을 가진 프로젝트 작업 | `단일 요청의 Run 생성 정책 확정` |
| `Run` | WorkItem을 진전시키려는 한 번의 Codex 작업 세션 | `Run 생성 정책 검토 #3` |

하나의 WorkItem은 여러 Run을 가질 수 있다. 성공한 Run은 `finish_work(outcome="progressed")`로 끝나고 WorkItem은 새 `next_action`이 있는 `ready`로 돌아간다. AC와 Evidence가 완료 조건을 충족하면 `completion_recommended=true`로 고정된 완료 권장 문구를 저장한다. WI는 웹 콘솔 또는 사용자의 명시적 완료 요청에서 `close_work_item()`으로만 `done` 처리한다.

## 언제 WorkItem과 Run을 만드는가

판단 기준은 코드 수정 여부가 아니라 **프로젝트를 실제로 진전시키는 작업인가**이다.

| 사용자 요청 | 처리 |
| --- | --- |
| 답변으로 끝나는 일회성 질문·설명 | WorkItem과 Run 없음 |
| 기존 목표를 위한 설계·조사·문서·구현·검증 | 기존 WorkItem에 새 Run 생성 |
| 독립적인 목표와 완료 조건이 필요한 프로젝트 작업 | 새 WorkItem을 만들고 새 Run 생성 |

사용자가 WorkItem ID를 프롬프트에 적는 흐름에 의존하지 않는다. `UserPromptSubmit` Hook은 실행 중인 WorkItem과 `ready` 후보를 짧게 제공하고, Codex가 사용자 요청과 각 목표를 비교한다.

`UserPromptSubmit`은 후보를 만들기 전에 현재 세션 Binding의 이전 `turn_id`에 남은 `running` Run만 `interrupted`로 종료하고 해당 WorkItem을 기존 `next_action`이 있는 `ready`로 복구한다. 그 뒤 `running` Run을 다시 조회하므로, 남은 실행 중 WorkItem은 다른 세션 소유로 취급한다. 서로 다른 WorkItem은 병렬 실행할 수 있지만 같은 WorkItem에는 세션 하나만 접근한다. 다른 세션 소유라면 Codex는 새 Run을 만들지 않고 원래 세션에서 이어가도록 안내한다. 사용자가 원래 세션의 종료를 명시적으로 확인한 경우에만 `recover_abandoned_work(work_item_id, expected_run_id)`로 정확한 Run을 복구하고, 이후 `start_work()`로 현재 세션의 새 Run을 만든다.

UPS의 후보 3개가 사용자 요청과 맞지 않으면 목록을 더 펼치지 않는다. Codex가 요청에서 핵심 용어 2~5개를 만들고 `search_work_items()`를 한 번 호출해 미완료 WorkItem의 제목·목표·다음 행동을 검색한다. 최대 5개 결과에도 적합한 목표가 없을 때만 새 WorkItem을 만든다.

기존 WorkItem 재사용이 기본값이다. 다음 질문이 참이면 같은 WorkItem을 사용한다.

> 기존 WorkItem을 완료하려면 이번 요청도 처리해야 하는가?

다음 중 하나가 명확할 때만 새 WorkItem을 만든다.

- 기존 WorkItem과 독립적으로 완료할 수 있다.
- 별도의 목표와 완료 조건이 필요하다.
- 기존 WorkItem이 끝나도 별도 작업으로 남는다.
- 기존 WorkItem에 포함하면 목표가 지나치게 넓어진다.

Feature가 아직 없는 초기 설계 단계에서는 `feature_id`를 비운 `research` 또는 `decision` WorkItem을 바로 만들 수 있다. 작업 영역이 분명해진 뒤 관련 Feature를 만들고 연결해도 된다.

## Feature만으로 관리하지 않는 이유

Feature 하나에는 보통 설계, 데이터, API, UI, 사용자 흐름, 보안, 테스트가 모두 포함된다.

```text
Feature: 사용자 로그인
  ├─ DB: 사용자 세션 저장 구조
  ├─ API: 로그인/로그아웃 엔드포인트
  ├─ UI: 로그인 화면
  ├─ Flow: 로그인 후 리다이렉트
  ├─ Security: 세션 만료/권한 처리
  └─ Test: 단위/E2E 테스트
```

따라서 Feature는 프로젝트를 이해하고 묶는 상위 분류로 사용하고, 실제 설계·조사·구현 상태와 실행은 더 작은 `WorkItem`으로 관리한다.

## 권장 방식: 세로 분해 (Vertical Slice)

제품 기능을 구현할 때 가장 추천하는 방식은 사용자 흐름의 작은 완성 조각으로 나누는 **세로 분해**다. 설계·조사·의사결정 WorkItem에는 아래의 상황별 분해 기준을 사용한다.

DB, API, UI처럼 기술 층별로 나누지 않는다. 하나의 WorkItem이 필요한 경우 DB·API·UI를 모두 조금씩 바꿔, 사용자가 확인할 수 있는 결과 하나를 만든다.

```text
Feature: 프로필 관리

WorkItem 1: 로그인 사용자가 자신의 프로필을 조회할 수 있다
  - 필요한 DB 조회
  - API
  - 화면 표시
  - 최소 테스트

WorkItem 2: 사용자가 닉네임을 수정하고 저장할 수 있다
  - 입력 검증
  - 저장 API
  - UI 피드백
  - 테스트

WorkItem 3: 사용자가 프로필 이미지를 업로드할 수 있다
  - 파일 검증
  - 스토리지 연결
  - 업로드 UI
  - 테스트
```

이 방식의 장점은 다음과 같다.

- 작업 중간에 멈춰도 실제로 동작하는 작은 기능이 남는다.
- 완료 조건과 테스트를 작성하기 쉽다.
- 에이전트가 프로젝트 전체 맥락을 과하게 읽을 필요가 없다.
- 큰 기능을 초기에 실제 사용자 흐름으로 검증할 수 있다.

## 상황별 분해 방식

세로 분해가 기본이지만, 작업 성격에 따라 다른 방식도 함께 사용한다.

| 방식 | 어떻게 나누는가 | 적합한 상황 |
| --- | --- | --- |
| 사용자 흐름 분해 | 사용자가 하는 행동 한 단계씩 나눔 | 일반 제품 기능 구현 |
| 위험 우선 분해 | 가장 불확실하거나 위험한 부분부터 분리 | 외부 API, 결제, AI, 권한, 성능 |
| 기술 기반 분해 | DB, API, UI, 인프라 같은 공통 기반으로 분리 | 공통 기반 작업, 내부 구조 변경 |
| 결정/조사 분해 | 프로젝트 진행에 필요한 질문이나 결정으로 분리 | 아키텍처·정책이 불확실한 경우 |
| 마이그레이션 분해 | 준비 → 이중 지원 → 전환 → 제거로 나눔 | 기존 시스템을 안전하게 변경할 때 |

실제 Feature는 여러 분해 방식을 조합할 수 있다.

```text
Feature: 외부 결제 연동

WorkItem: 결제사 웹훅 중복 수신 정책 결정        # 결정/조사
WorkItem: 결제 요청 생성 API 구현                # 사용자 흐름
WorkItem: 결제 완료 화면 및 주문 상태 반영       # 사용자 흐름
WorkItem: 웹훅 중복 처리와 재시도 구현           # 위험 우선
WorkItem: 결제 실패·취소 흐름 E2E 검증           # 검증
```

## 좋은 WorkItem의 기준

좋은 `WorkItem`은 아래 질문에 대부분 “예”라고 답할 수 있어야 한다.

- 한 문장으로 목표를 말할 수 있는가?
- 완료 조건을 2~5개 정도로 적을 수 있는가?
- 문서, 결정 기록, 조사 결과, 테스트나 동작 결과 등으로 검증할 수 있는가?
- 다른 작업의 상태를 과하게 바꾸지 않고 수행할 수 있는가?
- 막혔을 때 원인과 다음 행동을 명확히 남길 수 있는가?

```text
좋지 않은 WorkItem
- 사용자 기능 구현
- 인증 시스템 리팩터링
- 프론트엔드 작업

좋은 WorkItem
- 상태 저장소가 설계 작업을 추적할 기준을 확정한다
- MemoryGraph 후보의 저장 시점과 검토 절차를 결정한다
- 로그인 사용자가 자신의 프로필을 조회할 수 있게 한다
- 닉네임 변경 시 2~20자 검증 오류를 화면에 표시한다
- 만료된 세션의 API 요청에 재로그인 응답을 반환한다
```

반대로 설계 질문이나 구현 단계가 너무 작은 경우는 별도 WorkItem 대신 기존 WorkItem의 한 Run 또는 실행 단계로 둔다.

```text
너무 작은 단위
- Feature 용어의 한 문장을 고친다
- 기존 결정의 표현만 다시 설명한다
- user 테이블에 컬럼 하나 추가
- 버튼 색상 변경
- API 파일 생성
```

## 설계부터 구현까지의 흐름

```text
1. 초기 아이디어를 진행할 research 또는 decision WorkItem 생성
2. 각 설계·조사 세션을 Run으로 실행하고 결과와 다음 행동 기록
3. 큰 목표·기능·작업 영역이 보이면 Feature 생성
4. 가까운 설계·구현 목표를 독립적으로 검증 가능한 WorkItem으로 분해
5. WorkItem마다 완료 조건과 다음 행동 기록
6. 작업 중 새 목표가 발견되면 기존 WorkItem 재사용 여부를 먼저 판단
7. 독립적인 목표일 때만 새 WorkItem 추가
```

프로젝트 초기에 모든 작업 단위를 완벽하게 정의할 필요는 없다. 당장 진행할 설계·조사 WorkItem부터 시작하고, 가까운 목표만 구체화한다. 이후 드러나는 세부 사항은 기존 WorkItem의 Run으로 처리하거나 독립적인 목표일 때만 새 WorkItem으로 추가한다.

## 초기 계획과 유동적 분해

초기에는 현재 진행할 목표와 큰 작업 영역만 정의한다. 모든 Feature와 세부 WorkItem을 미리 만들지는 않는다.

```text
초기 아이디어 또는 사용자 요청
  ↓
research / decision WorkItem과 Run
  ↓
필요한 Feature와 가까운 WorkItem만 구체화
  ↓
설계·조사·구현 중 기존 WorkItem 재사용 또는 독립 목표만 추가
```

이 방식은 두 문제를 함께 피한다.

- 모든 세부 작업을 미리 정의하면, 실제 코드와 설계가 달라질 때 계획을 유지하는 비용이 커진다.
- 너무 대략적으로만 정의하면, 에이전트가 현재 작업의 범위를 과하게 넓힐 수 있다.

따라서 원칙은 다음과 같다.

> 전체는 거칠게 계획하고, 다음에 실행할 작업만 구체적으로 정의한다.

작업 중 발견한 새 요구사항, 결정, 의존성, 위험 요소가 기존 목표의 완료에 필요하면 같은 WorkItem에서 계속한다. 독립적으로 완료해야 할 때만 새 WorkItem으로 추가한다. 이미 시작했거나 완료된 WorkItem을 중단할 때는 삭제보다 `cancelled` 상태로 남겨 이력을 보존한다. 아직 시작하지 않은 단순 후보는 삭제할 수 있다.

## Feature와 WorkItem의 저장 위치

설계 문서와 현재 진행 상태는 서로 다른 위치에 저장한다.

| 대상 | 저장 위치 | 이유 |
| --- | --- | --- |
| 설계의 구조·이유·사용자 흐름 | Git으로 관리하는 설계 문서 | 사람이 읽고 리뷰하는 상세 본문이기 때문 |
| 설계·조사·구현 WorkItem의 현재 상태 | 상태 저장소 DB | 목표·진행·다음 행동처럼 자주 바뀌는 구조화된 데이터이기 때문 |
| 기능 인덱스와 진행 현황 | 상태 저장소에서 자동 생성한 뷰 | 직접 수정할 필요가 없기 때문 |
| 코드·PR·테스트 결과 | Git/CI 및 Artifact 참조 | 실제 변경과 검증 근거이기 때문 |

```text
docs/
  architecture.md         # 설계의 이유와 구조
  state-store.html        # 상태 저장소 개념
  implementation-units.md # 작업 단위 분해 원칙

.harness/
  state.db                # Feature / WorkItem / Run / Artifact의 현재 상태
  schema.sql              # 테이블·제약·View 정의
  exports/events.jsonl    # DB의 State Event를 필요할 때 내보낸 파생물
```

로컬 단일 사용자 v1에서는 `state.db`를 Git으로 추적하지 않는 로컬 작업 데이터로 둔다. SQLite DB는 바이너리 파일이므로 Git diff와 머지에 적합하지 않다. 팀 협업이나 여러 기기 동기화가 필요해질 때 공유 DB, GitHub Issue/Project 연동, JSON export/import 중 하나를 추가한다.

## Artifact 보관과 연결

`Artifact`는 Run이 남긴 검증 가능한 산출물이다. 코드 결과뿐 아니라 설계 문서, 조사 보고서와 결정 기록도 Artifact가 될 수 있다. 작업 종료 시 지우는 임시 파일이 아니라, 완료와 검증의 근거다.

상태 저장소에는 Artifact 파일 자체를 넣지 않는다. 대신 어떤 WorkItem과 Run이 만들었는지, 실제 원본이 어디에 있는지, 검증 결과가 무엇인지를 저장한다.

```text
run_id: run-0042
kind: test_run
uri: .harness/artifacts/HW-12/run-0042/test-result.json
verification_status: passed
```

Artifact에는 `work_item_id`를 중복 저장하지 않는다. `run_id`로 Run을 찾고, Run의 `work_item_id`를 따라가면 소속 WorkItem을 알 수 있다.

로컬에 보관해야 하는 검증 자료는 WorkItem과 Run 단위로 나눈다. 하나의 WorkItem은 여러 번 실행될 수 있기 때문이다.

```text
.harness/
  artifacts/
    HW-12/
      run-0042/
        test-result.json
        e2e-login.png
      run-0045/
        review-summary.md
```

모든 Artifact를 로컬에 복사할 필요는 없다.

| Artifact 종류 | 실제 보관 위치 | 상태 저장소에 저장할 참조 |
| --- | --- | --- |
| 커밋 | Git | 커밋 SHA |
| PR | GitHub/GitLab | PR URL |
| CI 테스트 | CI 서비스 | 실행 URL 또는 ID |
| 로컬 테스트 결과 | `.harness/artifacts/` | 파일 경로 |
| 스크린샷/E2E 영상 | `.harness/artifacts/` 또는 외부 스토리지 | 파일 경로 또는 URL |
| 설계·조사 문서 | Git 또는 `docs/` | 파일 경로 또는 커밋 SHA |
| 임시 다운로드·빌드 산출물 | 임시 폴더 | 보통 등록하지 않음 |

상태 저장소에는 별도의 `retention` 필드를 두지 않는다. 보관 여부는 Artifact의 `uri`가 가리키는 Git, 문서 저장소, CI 또는 로컬 Artifact 관리 규칙이 담당한다.

## 원칙

> Feature는 큰 목표와 작업 영역을 이해하기 위한 분류다. 설계·조사·의사결정·구현·검증의 실제 상태와 Codex 작업 세션은 작고 검증 가능한 WorkItem과 Run으로 관리한다.
