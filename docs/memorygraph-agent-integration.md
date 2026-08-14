# MemoryGraph의 에이전트 연동 방식

## 결론

2026년 8월 9일 현재 공식 `memory-graph/memory-graph`에는 Codex나 Claude가 설치해서 불러오는
정식 Agent Skill이 없다.

- 저장소에 `SKILL.md`나 `skills/` 디렉터리가 없다.
- 공식 README에도 skills.sh 또는 ClawHub를 통한 Skill 설치 안내가 없다.
- 공식 제공 방식은 **MemoryGraph CLI 설치 + `AGENTS.md`나 `CLAUDE.md`에 사용 규칙 추가**다.
- 저장소에는 Claude Code Hook 예제가 있지만 정식 Skill이 아니며, 현재 v0.13 설치 방식과도 맞지 않는 오래된 예제다.

이 문서는 공식 저장소의 `main` 브랜치 커밋
[`4f834c0`](https://github.com/memory-graph/memory-graph/tree/4f834c01765dc52b66c621fa42928fb0b52208cb)을 기준으로 확인했다.

## 무엇이 있고 무엇이 없는가

| 구성 | 공식 제공 여부 | 쉬운 설명 |
| --- | --- | --- |
| MemoryGraph CLI | 있음 | 에이전트가 터미널 명령으로 기억을 조회·저장하는 실제 프로그램 |
| `AGENTS.md`/`CLAUDE.md` 지침 템플릿 | 있음 | 언제 어떤 CLI 명령을 실행할지 모델에게 알려 주는 고정 규칙 |
| Claude Code Hook 예제 | 일부 있음 | Claude Code Web 시작 시 설치를 시도하던 자동 실행 예제 |
| `SKILL.md` 기반 Agent Skill | 없음 | 필요한 작업에서 선택적으로 불러오는 정식 Skill 패키지는 제공되지 않음 |
| skills.sh/ClawHub 공식 패키지 | 확인되지 않음 | 공식 저장소·문서·공식 GitHub 조직에 설치 안내나 연결된 패키지가 없음 |
| Codex 전용 통합 | 없음 | Codex용 Skill, Hook, 설치 스크립트는 따로 제공되지 않음 |

공식 README는 에이전트가 셸 명령을 실행할 수 있으므로 MemoryGraph CLI를 직접 사용하게 하고,
그 사용법을 `AGENTS.md`, `CLAUDE.md` 등의 지침 파일에 붙여 넣으라고 안내한다.
([공식 설치·지침 안내](https://github.com/memory-graph/memory-graph/blob/4f834c01765dc52b66c621fa42928fb0b52208cb/README.md#L25-L55))

## 지침, Hook, Skill의 차이

| 종류 | 쉬운 설명 | MemoryGraph 공식 제공 상태 |
| --- | --- | --- |
| 지침 | 에이전트가 항상 읽는 짧은 운영 규칙 | README에 `AGENTS.md`/`CLAUDE.md`용 템플릿 제공 |
| Hook | 특정 시점에 스크립트를 자동 실행하는 장치 | Claude Code Web용 예제만 존재 |
| Skill | 이름·설명·실행 절차를 `SKILL.md`에 담아 필요할 때 선택적으로 불러오는 묶음 | 제공하지 않음 |

공식 저장소의 루트 `AGENTS.md`와 `CLAUDE.md`는 MemoryGraph를 사용하는 소비자용 Skill도 아니다.
둘 다 MemoryGraph 자체를 개발할 때 필요한 빌드·테스트·코드 구조를 설명하는 저장소 개발 지침이다.
([공식 `AGENTS.md`](https://github.com/memory-graph/memory-graph/blob/4f834c01765dc52b66c621fa42928fb0b52208cb/AGENTS.md),
[공식 `CLAUDE.md`](https://github.com/memory-graph/memory-graph/blob/4f834c01765dc52b66c621fa42928fb0b52208cb/CLAUDE.md))

## Claude Hook 예제를 그대로 쓰면 안 되는 이유

저장소의 `examples/claude-code-hooks` 문서는 Python 패키지를 설치하고 MCP 서버로 등록하는
Claude Code Web용 `SessionStart` 예제를 설명한다.
([Hook 예제 설명](https://github.com/memory-graph/memory-graph/blob/4f834c01765dc52b66c621fa42928fb0b52208cb/examples/claude-code-hooks/README.md#L1-L25))

하지만 현재 v0.13 공식 README는 TypeScript/Bun CLI를 설치해 에이전트가 셸에서 직접 실행하도록 안내한다.
([현재 설치 방식](https://github.com/memory-graph/memory-graph/blob/4f834c01765dc52b66c621fa42928fb0b52208cb/README.md#L25-L41))
또한 현재 커밋의 Hook 예제 디렉터리에는 설명 문서와 복사 도우미만 있고, 도우미가 복사하려는
`.claude/settings.json`과 Hook 스크립트는 Git 트리에 존재하지 않는다.
([현재 Hook 예제 디렉터리](https://github.com/memory-graph/memory-graph/tree/4f834c01765dc52b66c621fa42928fb0b52208cb/examples/claude-code-hooks),
[복사 도우미가 기대하는 파일](https://github.com/memory-graph/memory-graph/blob/4f834c01765dc52b66c621fa42928fb0b52208cb/examples/claude-code-hooks/copy-to-project.sh#L13-L23))

따라서 이 예제는 현재 하네스에 복사하지 않고 참고 자료로만 봐야 한다.

## Codex에서 사용할 수 있는가?

정식 Codex Skill이 제공되는 것은 아니지만, Codex는 셸 명령과 `AGENTS.md`를 사용할 수 있으므로
공식 CLI 방식은 적용할 수 있다. 이는 MemoryGraph가 Codex 전용 통합을 보장한다는 뜻이 아니라,
공식 README의 “셸 명령을 실행하는 코딩 에이전트” 방식을 Codex에도 적용할 수 있다는 판단이다.

우리 하네스에서는 다음처럼 역할을 나누는 것이 적절하다.

```text
MemoryGraph CLI
→ 기억을 실제 조회·저장하는 도구

AGENTS.md
→ 반드시 지켜야 할 아주 짧은 원칙

우리의 memory-management Skill
→ 후보 선정, 중복 검사, 관계 선택 같은 자세한 추론 절차

우리의 Codex Hook
→ 작업 전 recall과 종료 전 검토 누락 여부를 기계적으로 확인
```

즉 MemoryGraph가 제공하는 Skill을 설치하는 구조가 아니다. MemoryGraph CLI는 그대로 사용하고,
우리 하네스의 운영 방식에 맞는 Codex용 Skill과 Hook은 우리가 별도로 설계해야 한다.

## 동명 패키지 주의

skills.sh나 ClawHub에서 이름에 `memory graph`가 들어간 항목을 발견하더라도 공식 저장소나
공식 GitHub 조직에서 연결하거나 설치를 안내하지 않았다면 이 프로젝트의 공식 Skill로 간주하면 안 된다.
설치 전에는 게시자, 연결된 저장소, 코드 내용과 라이선스를 별도로 확인해야 한다.
