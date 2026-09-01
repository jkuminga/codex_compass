# fzf로 WorkItem 선택하기: UPS와 외부 터미널 방식

> 구현 대상 문서. `/work` 접두사가 붙은 요청은 UPS(UserPromptSubmit Hook)가 외부 터미널의 `fzf` 선택창으로 WorkItem(WI)을 고르게 한다. Codex는 사용자가 고른 WI를 그대로 사용하며 WI 매칭을 추론하지 않는다.

## 결정 요약

| 항목 | 결정 |
| --- | --- |
| 사용자 진입점 | `/work <요청>` 접두사 |
| WI 선택자 | 사용자 + `fzf` |
| WI 목록 | UPS가 상태 DB에서 `ready` WI 전체 조회 |
| 기본 터미널 | macOS `Terminal.app` |
| Codex의 WI 매칭·검색·자동 생성 | 사용하지 않음 |
| MCP Elicitation | 사용하지 않음 |
| 선택 취소·오류·시간 초과 | 작업 시작 금지 |
| UPS 대기 시간 | 300초를 기본값으로 구현 |
| `/work` 없는 요청 | DB 조회·터미널 실행 없이 즉시 통과 |

**WorkItem(WI)** 은 이후 세션에도 이어서 할 수 있는 작업 카드다. **UPS** 는 사용자가 프롬프트를 보낸 직후, 모델보다 먼저 실행되는 로컬 Hook이다.

## 전체 흐름

```text
사용자: /work 로그인 기능 구현을 이어서 해줘
  → UPS: /work 접두사 확인
  → UPS: ready WI 전체 조회
  → UPS: 선택 요청 JSON 파일을 원자적으로 생성
  → UPS: Terminal.app 새 탭에서 선택 프로그램 실행
  → 선택 프로그램: WI 목록을 fzf 화면에 표시
  → 사용자: 검색·방향키·Enter로 선택, 또는 Esc로 취소
  → 선택 프로그램: 선택 결과 JSON 파일을 원자적으로 생성
  → UPS: 결과 파일을 읽어 검증하고 Codex 추가 문맥으로 반환
  → Codex: 선택된 WI만 get_work_context()로 읽고 이후 작업
```

**원자적 생성**은 임시 파일을 완성한 뒤 최종 파일명으로 바꾸는 방식이다. 다른 프로세스가 반쯤 작성된 JSON을 읽는 일을 막는다.

Codex가 받는 추가 문맥은 한 WI만 담는다.

```json
{
  "selection_status": "selected",
  "work_item_id": "WI-12",
  "title": "로그인 기능 구현",
  "request": "로그인 기능 구현을 이어서 해줘"
}
```

`get_work_context()`는 선택된 WI의 목표와 다음 일을 읽는 조회 함수다. Codex는 WI 후보 목록을 다시 읽거나 비교하지 않는다.

## 구성별 역할

| 구성 | 역할 |
| --- | --- |
| UPS | 접두사 확인, WI 조회, 요청 파일 작성, 터미널 실행, 결과 대기·검증·정리 |
| 선택 프로그램 `harness-work-picker` | 요청 파일을 읽고 fzf를 실행해 선택·취소 결과 파일을 작성 |
| `fzf` | 터미널에서 한 줄 목록을 검색하고 Enter로 고르는 명령줄 프로그램 |
| Terminal.app | 선택 프로그램과 fzf 화면을 사용자가 볼 수 있게 실행하는 장소 |
| Codex | 결과로 받은 WI 하나만 사용해 이후 작업을 수행 |

선택 프로그램은 UPS와 분리된 작은 로컬 Python CLI다. 쉘로도 만들 수 있지만 JSON 입출력, 취소, 오류 처리를 안전하게 다루기 위해 Python으로 구현한다. `fzf`는 JSON을 처리하거나 새 터미널을 열지 않는다.

## 선택 파일 규칙

선택 파일은 Git에 넣지 않는 임시 실행 데이터다.

```text
.harness/
  selections/
    selection-<request_id>.request.json
    selection-<request_id>.result.json
```

`request_id`는 매 `/work` 요청마다 새로 만드는 UUID4 기반의 랜덤 식별자다. 예시는 `selection-7f3a1c9e-....request.json`이다.

파일명에는 사용자 프롬프트, WI 제목, 세션 ID, Turn ID를 넣지 않는다. 파일명을 짧고 안전하게 유지하며, 세션·Turn 값은 JSON 내부에서 검증에만 사용한다.

### 요청 파일

```json
{
  "schema_version": 1,
  "request_id": "7f3a1c9e-...",
  "session_id": "현재 Codex 세션 식별자",
  "turn_id": "현재 Turn 식별자",
  "created_at": "2026-09-01T12:00:00Z",
  "expires_at": "2026-09-01T12:05:00Z",
  "work_items": [
    {
      "id": "WI-12",
      "title": "로그인 기능 구현",
      "goal": "refresh token을 적용한다",
      "priority": "high"
    }
  ]
}
```

### 결과 파일

```json
{
  "schema_version": 1,
  "request_id": "7f3a1c9e-...",
  "session_id": "현재 Codex 세션 식별자",
  "turn_id": "현재 Turn 식별자",
  "created_at": "2026-09-01T12:01:10Z",
  "status": "selected",
  "work_item_id": "WI-12"
}
```

취소는 `status: "cancelled"`만 쓰고 `work_item_id`는 넣지 않는다.

UPS는 결과를 받았다고 바로 믿지 않는다. 아래를 모두 확인한 결과만 수용한다.

- `request_id`, `session_id`, `turn_id`가 요청 파일과 같다.
- `status`가 `selected` 또는 `cancelled`다.
- `selected`라면 `work_item_id`가 원래 요청 파일의 WI 목록에 실제로 있다.
- 결과가 `expires_at` 이전에 작성됐다.

## 파일 수명과 정리

```text
정상 선택·취소
  UPS가 결과 검증
  → request.json과 result.json을 finally 블록에서 삭제

터미널 실행 실패
  → request.json을 즉시 삭제

시간 초과
  → UPS가 request.json을 삭제하고 작업을 시작하지 않음
  → 선택 프로그램은 expires_at을 시작 전·결과 쓰기 전 확인해 늦은 결과를 쓰지 않음

비정상 종료
  → 다음 /work UPS 시작 시 만료된 선택 파일을 정리
```

`finally 블록`은 성공·취소·오류 여부와 관계없이 마지막에 실행되는 정리 구간이다.

시간 초과 직전에 결과 파일이 만들어지는 경쟁 상황으로 남는 파일이 있을 수 있다. 다음 `/work` 시작 시 `expires_at`을 넘긴 파일을 청소하는 규칙이 최후의 안전망이다. 청소는 진행 중인 선택을 지우지 않도록 만료 후 충분한 여유를 둔 파일만 대상으로 한다.

## 터미널 실행 규칙

UPS의 표준 입력·출력은 Codex Hook JSON 통신용이다. 따라서 UPS 안에서 `fzf`를 직접 실행하면 Hook 통신이 깨진다.

```text
UPS 프로세스                     별도 Terminal 창
───────────                      ───────────────
Hook JSON 입출력 유지             fzf 화면 표시
결과 JSON 파일을 대기       ←     사용자 선택
Codex에 선택 결과 반환            결과 JSON 파일 작성
```

터미널 선택 우선순위는 아래와 같다.

1. 사용자가 하네스 설정으로 지정한 실행기
   - 예: `orca`, `Terminal.app`, `iTerm`.
2. 설정이 없고 macOS인 경우 `Terminal.app`
   - macOS 기본 포함 앱이다.
   - 2026-09-01에 비대화형 프로세스에서 AppleScript로 새 Terminal 탭을 열고 명령을 실행하는 것을 확인했다.
3. 실행기를 열 수 없으면 선택 실패를 반환하고 Codex 작업을 시작하지 않는다.

`$TERMINAL` 환경 변수는 사용하지 않는다. Desktop 앱이 실행한 UPS에는 일반 셸 환경 변수가 없을 수 있다.

macOS 기본 실행기는 AppleScript로 Terminal.app의 새 탭에 아래 명령을 실행하게 한다.

```text
uv run python -m src.harness.work_item_picker --request <절대-요청-파일-경로>
```

Linux와 Windows에는 모든 환경에서 공통인 GUI 터미널 실행 명령이 없다. 추후 지원 시 운영체제별 실행기를 분리하고, 지원하지 않는 환경은 명확하게 실패 처리한다.

## 최소 구현 의사 코드

UPS는 `/work`일 때만 이 흐름을 수행한다.

```python
def handle_user_prompt(event: dict[str, object]) -> dict[str, object]:
    prompt = read_prompt(event)

    if not prompt.startswith("/work"):
        return {"selection_status": "not_requested"}

    request = strip_work_prefix(prompt)
    work_items = state_store.list_ready_work_items()
    request_id = create_uuid4_request_id()

    request_file = write_selection_request(
        request_id=request_id,
        session_id=event["session_id"],
        turn_id=event["turn_id"],
        work_items=work_items,
        timeout_seconds=300,
    )

    try:
        launch_terminal_picker(request_file)
        result = wait_for_selection_result(
            request_id=request_id,
            timeout_seconds=300,
        )
        return validate_selection_result(request_file, result)
    finally:
        cleanup_selection_files(request_id)
```

선택 프로그램은 외부 Terminal 탭에서만 실행된다.

```python
def main(request_file: Path) -> None:
    request = read_and_validate_request(request_file)

    if utc_now() >= request["expires_at"]:
        return

    lines = [
        f"{wi['id']}\t{wi['priority']}\t{wi['title']}\t{wi['goal']}"
        for wi in request["work_items"]
    ]
    selected_line = run_fzf(lines)

    if utc_now() >= request["expires_at"]:
        return

    if selected_line is None:
        write_result(request, status="cancelled")
        return

    work_item_id = selected_line.split("\t", 1)[0]
    write_result(request, status="selected", work_item_id=work_item_id)
```

`strip_work_prefix()`는 `/work`만 제거하고 나머지 사용자 요청 문장을 Codex에 전달하는 함수다. `wait_for_selection_result()`는 결과 파일이 생길 때까지 제한된 시간 동안만 확인하는 함수다.

## `/work` 없는 요청

```text
사용자 일반 프롬프트
  → UPS: 접두사 없음 확인
  → not_requested 반환
  → Codex: 평소처럼 처리
```

이 경우 UPS는 아래를 하지 않는다.

- 상태 DB의 WI 목록·진행량 조회
- fzf 또는 외부 터미널 실행
- 선택 요청·결과 파일 생성
- WI 매칭·검색·자동 생성
- Run 생성

## 구현 체크리스트

- [x] MCP Elicitation 대신 외부 터미널 + fzf 방식을 채택한다.
- [x] `/work` 접두사로 선택 필요 여부를 기계적으로 결정한다.
- [x] macOS Terminal.app을 비대화형 프로세스에서 열 수 있음을 확인했다.
- [x] 선택 파일 위치, 이름, 검증, 정상·비정상 정리 규칙을 정의했다.
- [ ] `.harness/`와 선택 파일을 Git에서 제외한다.
- [ ] UPS Hook 입력에서 사용자 프롬프트를 읽고 `/work`를 판별한다.
- [ ] `ready` WI 전체 조회 함수를 확인하거나 추가한다.
- [ ] `harness-work-picker` Python CLI와 fzf 실행을 구현하고, fzf 설치 여부를 확인한다.
- [ ] macOS Terminal.app 실행기와 사용자 설정형 실행기 선택을 구현한다.
- [ ] 선택 프로그램 실행에 쓸 Python·uv 경로를 설정으로 정한다. GUI 앱의 PATH에 의존하지 않는다.
- [ ] UPS의 Hook timeout을 300초로 바꾸고, 선택 대기를 구현한다.
- [ ] 선택·취소·시간 초과·터미널 실행 실패·비정상 종료를 테스트한다.
- [ ] 선택 결과를 Codex `additionalContext`로 전달하고, 이전 WI 자동 매칭 패킷·스킬·지침을 제거하거나 축소한다.
- [ ] 기존 MCP Elicitation PoC 도구·설정 제거 여부를 결정한다.
