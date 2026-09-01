# 🚨 URGENT: MemoryGraph 운영 보강

`finalize_memory_candidate()`의 핵심 저장 로직은 구현됐다. 실제 운영 전에 아래 네 가지를 마무리한다.

## 1. 후보 생성 경로 통일 · 완료

`AGENTS.md`의 로컬 JSON 지침을 제거하고 State DB의 `pending` 후보로 바로 이어지는 경로로 통일했다.

- 활성 Run에서 장기 기억 후보를 발견하면 `create_memory_candidate()`를 즉시 호출한다.
- `work-finish`는 같은 State DB에서 후보를 읽어 최종 검토로 넘긴다.
- `long-term/*.json` 후보 경로와 fallback은 사용하지 않는다.

## 2. 후보 상태 변경 우회 차단 · 완료

후보 상태를 직접 바꾸던 `promote_memory_candidate()`와 `reject_memory_candidate()`를 Codex 공개 MCP에서 제거했다.

- `promote_candidate()`와 `reject_candidate()`는 State DB 내부 함수로 유지한다.
- MCP 공개 도구와 `.codex/config.toml`의 허용 목록에서는 제외했다.
- 후보의 `promoted`·`rejected` 변경은 `finalize_memory_candidate()` 내부 처리만 수행한다.

## 3. merge 내용 보존 계약 강화

`merge`의 `memory`는 후보 내용만 담는 값이 아니라 기존 기억과 새 정보를 합친 완성본이어야 한다.

- 기존 기억의 유효한 내용을 보존한다.
- 후보에서 확인된 새 정보만 보강한다.
- 기존 내용을 축약하거나 제거하면 `reason`에 이유를 기록한다.
- 기존 내용이 사라지지 않는 병합 테스트를 추가한다.

## 4. 실제 SQLite 종단 간 검증

가짜 Client·Runner 테스트를 넘어 실제 저장 경로를 한 번에 검증한다.

```text
State DB 후보
→ Python MCP Adapter
→ harness-memory CLI
→ MemoryGraph SQLite
→ 노드·관계 재조회
→ State DB 후보 promoted 확인
```

- 임시 State DB와 임시 MemoryGraph DB를 사용한다.
- create, merge, reject, 관계 생성, partial 재시도를 포함한다.
- 테스트가 실제 프로젝트 DB를 변경하지 않는지 확인한다.

## 완료 기준

- [x] 후보 생성 지침과 실제 State DB 입력 경로가 하나로 통일됨
- [x] 직접 promote·reject MCP 우회 경로가 제거됨
- [ ] merge가 기존 기억을 보존한다는 계약과 테스트가 추가됨
- [ ] 실제 SQLite 기반 종단 간 테스트가 통과함
