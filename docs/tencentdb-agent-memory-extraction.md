# TencentDB Agent Memory의 자동 기억 추출 방식

조사 기준은 공식 저장소의 커밋 [`fe3230f`](https://github.com/TencentCloud/TencentDB-Agent-Memory/tree/fe3230f176f1bf5832fee79d12494bbc2d19a8aa)이다. README의 설명만 옮기지 않고 실제 실행 경로를 따라 확인했다.

## 먼저 결론

TencentDB Agent Memory가 중요한 정보를 알아내는 핵심 주체는 **규칙이나 임베딩이 아니라 별도로 호출되는 LLM**이다.

- 규칙은 빈 메시지나 명백한 잡음을 먼저 제거하고, 언제 추출을 시작할지 결정한다.
- LLM은 대화 내용을 읽고 무엇이 장기 기억인지, 기억 종류와 중요도는 무엇인지 판단한다.
- 임베딩과 키워드 검색은 새 기억과 비슷한 기존 기억을 좁혀 주지만, 저장 여부를 판단하지는 않는다.
- 두 번째 LLM 호출이 새 기억을 `store`, `skip`, `update`, `merge` 중 하나로 판정한다.
- 저장 계층은 LLM의 판정 결과를 JSONL과 검색용 저장소에 반영한다.

즉, 아주 쉽게 줄이면 다음과 같다.

```text
작업 종료
  → 원본 대화 저장
  → 일정량이 쌓이거나 대화가 멈추면 추출 LLM 호출
  → 중요한 사실·결정·방법·작업·산출물을 구조화
  → 검색으로 비슷한 기존 기억 후보를 찾음
  → 중복 판정 LLM 호출
  → 새로 저장하거나 기존 기억을 갱신·병합
```

다만 이 구조는 “중요도 판정을 안전하게 보장하는 완성된 알고리즘”은 아니다. 중요도 기준 대부분이 프롬프트에만 있고, 코드에는 최소 중요도 점수를 다시 검사하는 강제 장치가 없다.

## 핵심 용어

- `L0 Conversation`: 가공하기 전의 원본 대화 기록이다.
- `L1 Atom`: LLM이 대화에서 뽑은 독립적인 기억 한 건이다.
- `L2 Scenario`: 여러 L1 기억을 프로젝트나 상황별 문서 블록으로 묶은 것이다.
- `L3 Persona/Core`: 반복되는 성향과 장기 패턴을 더 높은 수준으로 요약한 것이다.
- `priority`: LLM이 매긴 기억 중요도 숫자다. 일반적으로 값이 높을수록 중요하다.
- `source_message_ids`: 기억의 근거가 된 원본 메시지 ID 목록이다.
- `scene_name`: 기억이 속한 대화·작업 상황의 이름이다.
- `DedupDecision`: 새 기억을 저장·무시·갱신·병합 중 어떻게 처리할지 적은 객체다.
- `Embedding`: 문장의 의미를 숫자 배열로 바꾼 값이다. 의미가 비슷한 기억을 찾는 데 사용한다.
- `FTS5/BM25`: 단어가 겹치는 정도와 희소성을 이용해 비슷한 문서를 찾는 키워드 검색 방식이다.

## 실제 실행 흐름

### 1. 성공한 작업이 끝나면 원본 대화를 저장한다

OpenClaw 연동에서는 `agent_end` hook, 즉 에이전트 작업 종료 이벤트에 자동 캡처가 연결된다. 실패한 작업, 세션 키가 없는 작업, 메시지가 없는 작업은 건너뛴다. 성공한 경우 `performCapture()`를 거쳐 Gateway의 `addConversation()` API로 이번 턴의 메시지를 전송한다. [`agent_end` 등록 코드](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/openclaw-plugin/index.ts#L217-L285), [`performCapture()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/openclaw-plugin/src/hooks/capture.ts#L62-L184)

`performCapture()`는 이번 턴에 새로 추가된 사용자·어시스턴트 메시지만 고르고, 이전에 주입했던 기억 문맥을 제거하며, 어시스턴트 답변의 코드 블록도 제거한다. 그 후 `addConversation()`을 호출한다. 이 단계는 중요한 기억을 고르는 단계가 아니라 **추후 판정할 원본을 L0에 쌓는 단계**다. [`performCapture()` 정제 및 전송](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/openclaw-plugin/src/hooks/capture.ts#L146-L184)

Gateway의 `handleConversationAdd()`는 메시지를 `L0Record` 객체로 만들어 저장하고, 사용자 메시지가 있으면 비동기 기억 파이프라인에 알린다. `L0Record`는 원본 메시지 한 건과 세션·사용자·에이전트 식별자를 담는 저장 객체다. L0 저장이 이미 성공한 뒤 파이프라인 알림이 실패하면 경고만 남기므로, 다음 실행에서 밀린 데이터를 다시 처리할 수 있다. [`handleConversationAdd()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/gateway/v2-router.ts#L647-L739)

### 2. 매번 LLM을 호출하지 않고 묶어서 처리한다

`MemoryPipelineManager.notifyConversation()`은 대화 횟수와 마지막 활동 시간을 갱신한다. 설정한 대화 횟수에 도달하면 바로 L1 추출을 예약하고, 아직 부족하면 사용자가 대화를 멈춘 뒤 실행되는 idle timer를 다시 건다. 새 세션은 기본적으로 `1 → 2 → 4 → ...`처럼 임계치를 키우는 warm-up을 사용한다. 첫 대화는 빨리 처리하고, 장기 세션에서는 호출 횟수를 줄이려는 방식이다. [`MemoryPipelineManager` 설계](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/utils/pipeline-manager.ts#L1-L73), [`notifyConversation()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/utils/pipeline-manager.ts#L390-L441)

`createL1Runner()`는 아직 처리하지 않은 L0 메시지를 커서 이후부터 읽고, 오래된 것부터 최대 10건 정도씩 `extractL1Memories()`에 전달한다. `cursor`는 “여기까지 처리했다”를 나타내는 위치 표시 값이다. [`createL1Runner()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/utils/pipeline-factory.ts#L348-L389), [L0 읽기와 묶음 처리](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/utils/pipeline-factory.ts#L389-L569)

### 3. 코드 규칙은 명백한 잡음만 제거한다

`shouldExtractL1()`은 빈 문자열, 프레임워크 내부 메시지, 슬래시 명령, 짧은 기호만으로 된 메시지 등을 제거한다. 이름은 “엄격한 품질 게이트”지만, 현재 커밋에서는 길이 제한과 prompt injection 검사 코드가 주석 처리되어 있다. 따라서 이 함수가 의미적인 중요도를 판별한다고 보면 안 된다. [`shouldExtractL1()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/utils/sanitize.ts#L124-L155)

### 4. 첫 번째 LLM이 “무엇이 중요한 기억인가”를 판단한다

`extractL1Memories()`는 필터를 통과한 최근 메시지와 소량의 이전 배경 메시지를 `callLlmExtraction()`에 전달한다. 이 함수는 도구 사용을 끈 별도 LLM 실행을 만들고, `l1-extraction`이라는 작업으로 시스템 프롬프트와 사용자 프롬프트를 보낸다. 즉, 메인 에이전트가 작업하면서 기억을 직접 고르는 방식이 아니라, 저장된 대화를 **기억 추출 전용 LLM이 나중에 재검토**하는 구조다. [`extractL1Memories()`의 추출 호출](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-extractor.ts#L130-L188), [`callLlmExtraction()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-extractor.ts#L385-L447)

프롬프트 모드는 두 가지다.

| 모드 | 기억 종류 | 쉬운 의미 |
| --- | --- | --- |
| `chat` | `persona`, `episodic`, `instruction` | 사용자 성향, 실제 사건, 앞으로 계속 지킬 지침 |
| `code` | `work_fact`, `work_task`, `work_method`, `work_artifact` | 프로젝트 사실·결정, 다음 작업, 재사용 방법, 문서·PR 같은 산출물 |

`MemoryType`은 기억의 종류를 제한하는 값 목록이다. 코드에 정의된 전체 종류는 위 일곱 가지다. [`MemoryType`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-writer.ts#L30-L38)

일반 대화용 프롬프트는 다음 조건을 LLM에게 준다.

- 현재 대화 밖에서도 독립적으로 이해할 수 있어야 한다.
- 잡담·일회성 요청·순수 감정·AI 자신의 출력은 버린다.
- 강하게 연결되거나 인과관계가 있는 메시지는 한 기억으로 합친다.
- 유형별로 `priority`를 매기고 낮은 점수는 추출하지 않는다.

예를 들어 `episodic`은 중요한 사건이면 80–100점, 일반 사건이면 60–70점으로 하고 60점 미만은 버리라고 지시한다. `instruction`은 일회성 요구를 버리고 장기 행동 규칙만 남기라고 한다. [일반 대화 추출 프롬프트](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/prompts/l1-extraction.ts#L15-L101)

작업용 프롬프트는 Harness와 더 직접적으로 관련 있다. 다음을 강조한다.

- 이후 협업, 작업 재개, 경험 재사용, 같은 실수 방지에 도움 되는 정보만 저장한다.
- 한 사람의 제안과 팀의 확정 결정을 구분한다.
- AI 제안은 사람이 채택했거나 실제 도구 실행·실험·산출물로 확인된 경우에만 사실로 취급한다.
- 배경 메시지는 이해에만 쓰고, 기억 근거는 새 메시지에서만 가져온다.
- 프로젝트 사실·결정·위험은 `work_fact`, 다음 행동은 `work_task`, 재사용 절차와 원칙은 `work_method`, 문서·PR·보고서는 `work_artifact`로 나눈다.

[작업 기억의 공통 판정 규칙](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/prompts/l1-extraction.ts#L105-L180), [작업 기억 종류와 점수 기준](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/prompts/l1-extraction.ts#L184-L319)

LLM은 다음 형태의 JSON을 출력한다.

```json
[
  {
    "scene_name": "팀이 인증 모듈의 토큰 갱신 방식을 결정함",
    "message_ids": ["msg-1", "msg-2"],
    "memories": [
      {
        "content": "인증 모듈은 refresh token 방식으로 갱신한다.",
        "type": "work_fact",
        "priority": 92,
        "source_message_ids": ["msg-2"],
        "metadata": { "status": "decided" }
      }
    ]
  }
]
```

- `content`: 대화 밖에서도 이해되는 기억 본문이다.
- `type`: 기억 종류다.
- `priority`: LLM이 정한 중요도다.
- `source_message_ids`: 근거 메시지 목록이다.
- `metadata`: 작업 상태, 담당자, 마감일 같은 선택 정보를 담는 객체다.

[작업 모드 출력 스키마](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/prompts/l1-extraction.ts#L334-L372)

### 5. 출력은 일부만 검증한다

`parseExtractionResult()`는 JSON 배열을 찾고, 잘못된 제어 문자를 정리하며, 일부 비정상 `priority` 값을 50으로 복구한다. 내용이 비어 있지 않은지와 `type`이 허용된 값인지도 검사한다. 형식이 완전히 깨지면 빈 결과로 끝난다. [`parseExtractionResult()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-extractor.ts#L450-L533), [`normalizeType()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-extractor.ts#L657-L674)

그러나 다음은 코드가 강제하지 않는다.

- `priority`가 유형별 최소 기준 이상인지
- `priority`가 -1 또는 0–100 범위인지
- `source_message_ids`가 실제 새 메시지 ID인지
- 내용이 정말 원본 메시지에서 근거를 찾을 수 있는지
- 확정된 결정과 단순 제안을 제대로 구분했는지

실제 코드에서는 LLM이 반환한 숫자를 그대로 받고, 유형을 정규화한 뒤 최대 개수만 앞에서 잘라낸다. **낮은 `priority`를 버리는 코드상 필터는 없다.** 따라서 “중요한 정보만 저장”은 거의 전적으로 프롬프트 준수 능력에 달려 있다. [`priority` 수용과 최대 개수 제한](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-extractor.ts#L190-L250)

### 6. 임베딩은 중요도 판단이 아니라 중복 후보 찾기에 사용한다

새 기억이 만들어지면 `batchDedup()`이 기존 기억과 비교한다.

1. 임베딩이 있으면 의미가 비슷한 기존 L1 기억을 top-K로 찾는다.
2. 임베딩이 없고 FTS5가 있으면 키워드 검색으로 후보를 찾는다.
3. 둘 다 없거나 후보가 없으면 새 기억을 전부 `store`로 처리한다.
4. 후보가 있을 때만 두 번째 LLM을 호출한다.

[`batchDedup()`의 2단계 구조](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-dedup.ts#L1-L12), [후보 검색 분기](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-dedup.ts#L82-L130), [벡터·FTS 후보 검색](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-dedup.ts#L202-L307)

두 번째 LLM은 다음 행동 중 하나를 선택한다.

- `store`: 기존에 없는 새 정보이므로 추가한다.
- `skip`: 기존 기억이 더 낫거나 새 정보가 없으므로 버린다.
- `update`: 새 내용이 더 정확하거나 최신이므로 기존 기억을 교체한다.
- `merge`: 서로 보완되는 내용을 하나로 합친다.

이 단계에서도 판단 주체는 LLM이다. 임베딩은 비교할 후보를 찾는 역할만 한다. [`runLlmJudgment()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-dedup.ts#L133-L196), [중복 판정 프롬프트](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/prompts/l1-dedup.ts#L13-L78)

### 7. 실패 시에는 중복보다 데이터 보존을 우선한다

중복 판정 LLM 호출이나 JSON 파싱이 실패하면 모든 새 기억을 `store`한다. 이를 fail-open이라고 부를 수 있다. `fail-open`은 안전 판정에 실패했을 때 동작을 막지 않고 통과시키는 방식이다. 기억 누락은 줄지만, 중복이나 잘못된 기억이 늘어날 수 있다. [`runLlmJudgment()` 실패 처리](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-dedup.ts#L184-L195), [`parseBatchResult()` 실패 처리](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-dedup.ts#L315-L407)

추출 LLM 자체가 실패하면 반대로 이번 추출 결과는 0건으로 끝난다. 즉, **추출 실패는 저장하지 않고, 중복 판정 실패는 모두 저장한다.** [`extractL1Memories()` 실패 처리](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-extractor.ts#L170-L188)

### 8. 저장·갱신·병합은 JSONL과 검색 저장소에 반영한다

`writeMemory()`는 `DedupDecision`을 실행한다.

- `skip`이면 아무것도 쓰지 않는다.
- `store`면 새 `MemoryRecord`를 만든다.
- `update`나 `merge`면 검색 저장소에서 `target_ids`에 해당하는 옛 기억을 제거하고 새 기억을 쓴다.
- JSONL은 덧붙이기 전용이라 옛 기록이 즉시 없어지지 않고, 별도 정리 작업이 나중에 치운다.
- 새 기억은 JSONL과 벡터/FTS 저장소에 이중 기록된다.

`MemoryRecord`는 실제 저장되는 기억 객체이고, 본문·종류·중요도·근거 메시지·세션·버전 등을 담는다. [`MemoryRecord` 스키마](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-writer.ts#L46-L98), [`writeMemory()` 행동 처리](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-writer.ts#L145-L242), [삭제와 이중 기록](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-writer.ts#L244-L353)

## Skill 자동 추출은 더 강한 별도 구조다

이 저장소는 일반 기억과 별개로, 대화와 도구 호출에서 재사용 가능한 Agent Skill을 추출한다.

`SkillConversationAddHandler`는 메시지를 버퍼에 쌓다가 기본적으로 도구 호출 10회 또는 누적 40KB에 도달하면 대화 묶음을 archive하고 Skill 검토 작업을 큐에 넣는다. `archive`는 나중에 검토할 대화 묶음을 고정해서 저장하는 동작이다. [`HandlerThresholds`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/skill/conversation-add/add-handler.ts#L37-L99), [archive 판정](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/skill/conversation-add/add-handler.ts#L193-L265)

`SkillExtractor.extract()`는 archive된 대화를 머리 8천 자와 꼬리 3만2천 자 중심으로 자르고, 별도의 Skill Review Agent를 실행한다. 이 Agent는 단순 JSON을 반환하는 대신 `skill_list`, `skill_view`, `skill_create`, `skill_update`, `skill_patch`, `skill_files_write` 도구를 사용할 수 있다. 따라서 **LLM이 판단하고, 실제 저장은 구조화된 도구가 수행한다.** [`SkillExtractor.extract()`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/skill/skill-extractor.ts#L90-L184)

Skill 프롬프트는 일반 기억 프롬프트보다 강한 게이트를 둔다.

1. 먼저 후보를 `Skill`, `Memory`, `Wiki`, `Code-Graph`, `Temporary Context` 중 하나로 분류한다.
2. 재사용 가능한 실행 능력인 `Skill`만 통과시킨다.
3. 원자적 능력 30점, 작업 경계 25점, 재사용성 20점, 실행 가능성 25점으로 채점한다.
4. 총점 72점 이상이고 모든 항목이 12점 이상이어야 한다.
5. `skill_list`와 `skill_view`로 기존 Skill을 먼저 확인한 뒤 새로 만들거나 수정한다.
6. 저장할 것이 없으면 정확히 `Nothing to save.`라고 끝낸다.

[분류 게이트](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/skill/prompts/skill-review-prompt.ts#L93-L120), [72점 수용 게이트](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/skill/prompts/skill-review-prompt.ts#L121-L149), [조회 후 저장하는 도구 순서](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/skill/prompts/skill-review-prompt.ts#L200-L230)

중요한 차이는 점수 계산도 여전히 코드가 아니라 LLM이 스스로 한다는 것이다. 72점 검사가 코드에 따로 구현된 것은 아니다. 그래도 일반 기억 추출보다 분류 기준, 수용 기준, 기존 항목 조회, 저장 도구, “저장하지 않음” 출력 계약이 훨씬 명시적이다.

## 관계 그래프도 자동으로 만드는가?

Chat Memory의 L1 추출 파이프라인은 `MemoryRecord`를 저장하며, 그 스키마에는 `source_message_ids`, `scene_name`, `timestamps` 같은 추적 필드는 있지만 그래프 관계의 시작 노드·끝 노드·관계 종류 필드는 없다. 중복 단계도 기존 기억을 교체하거나 병합할 뿐 `SOLVES`, `CAUSES` 같은 관계 edge를 만들지 않는다. [`MemoryRecord`와 `DedupDecision`](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/fe3230f176f1bf5832fee79d12494bbc2d19a8aa/MemoryCore/src/core/record/l1-writer.ts#L46-L137)

따라서 소스 구조를 기준으로 한 결론은 다음과 같다.

> 이 프로젝트는 기억을 scene으로 묶고, 비슷한 기억을 검색해 병합하지만, MemoryGraph처럼 기억 사이의 의미 관계를 자동 추출하는 그래프 파이프라인은 아니다.

저장소의 별도 `CodeGraph` 기능은 코드 저장소의 심볼·호출 관계를 다루는 자산이고, 대화에서 추출한 L1 기억 관계 그래프와는 다른 기능이다.

## Harness v2에 가져올 부분

### 그대로 가져오기 좋은 원리

1. **원본과 장기 기억을 분리한다.**
   - Run trace는 원본 근거로 유지하고, MemoryGraph에는 검토를 통과한 작은 기억만 넣는다.

2. **메인 작업과 기억 검토를 분리한다.**
   - Codex 작업 중 억지로 계속 분류하지 않고, Run 종료 시 구조화된 후보를 별도 LLM 검토에 보낸다.

3. **두 단계로 판단한다.**
   - 1차: 저장할 가치가 있는 후보인지 판단한다.
   - 2차: 기존 기억과 비교해 새 저장·무시·갱신·병합을 결정한다.

4. **임베딩은 후보 축소에만 사용한다.**
   - 의미 판단을 임베딩 점수 하나에 맡기지 않고, 관련 가능성이 높은 기존 기억 몇 개만 LLM에 보여준다.

5. **근거를 반드시 보존한다.**
   - `source_message_ids` 대신 우리 구조에서는 `run_id`, `trace_ref`, `artifact_ref`를 저장한다.
   - `run_id`는 어떤 실제 작업 실행에서 나온 기억인지 가리키는 ID다.
   - `trace_ref`는 그 판단의 상세 실행 로그 위치다.
   - `artifact_ref`는 테스트 결과·커밋·설계 문서 같은 증거 위치다.

6. **Skill Review의 강한 패턴을 일반 기억에도 적용한다.**
   - 후보를 먼저 `Decision`, `Problem`, `Solution`, `Pattern`, `Temporary` 등으로 분류한다.
   - 저장 기준을 명시적으로 채점한다.
   - 기존 MemoryGraph를 먼저 검색한다.
   - LLM은 판단만 하고, 실제 쓰기·연결은 구조화된 도구가 수행한다.
   - 저장할 것이 없다는 결과를 정상 결과로 인정한다.

### Harness용으로 단순화한 추천 흐름

```text
1. Run 종료 시 기계적으로 근거 묶음 생성
   - 사용자 요청
   - 변경 요약
   - 테스트 결과
   - 결정/문제/해결 후보

2. Memory Reviewer LLM 호출
   - 후보를 장기 기억 / 프로젝트 상태 / 일회성 정보로 분류
   - 장기 기억 후보만 수용 기준으로 채점

3. 기존 MemoryGraph 검색
   - 타입·태그 필터
   - 임베딩 또는 키워드 top-K

4. Reviewer가 행동 결정
   - store / skip / update / merge
   - 필요한 관계도 명시

5. 결정적 도구가 검증 후 MemoryGraph 반영
   - 허용된 노드·관계인지 검사
   - 근거 ID가 실제 Run에 속하는지 검사
   - 성공한 저장 ID를 상태 저장소에 기록
```

`Memory Reviewer`는 작업 결과에서 장기 기억 후보를 평가하는 별도 LLM 역할이다. `결정적 도구`는 의미 판단을 하지 않고, 입력 스키마 검증과 DB 쓰기만 수행하는 함수 또는 CLI wrapper다.

## 그대로 사용하면 위험한 부분

1. **중요도 최소값이 코드에 없다.**
   - LLM이 낮은 점수를 출력해도 저장될 수 있다.
   - 우리 도구는 유형별 최소 점수와 허용 범위를 코드로 다시 검사해야 한다.

2. **근거 ID 검증이 약하다.**
   - LLM이 낸 `source_message_ids`가 실제 입력 메시지인지 확인하지 않는다.
   - 우리 도구는 `run_id`, `trace_ref`, `artifact_ref`가 현재 Run에 실제 존재하는지 검사해야 한다.

3. **중복 판정 실패 시 전부 저장한다.**
   - 중복과 오류가 장기 기억에 누적될 수 있다.
   - 우리 구조는 실패 시 후보를 `pending_review`로 남기고 실제 저장은 보류하는 편이 안전하다.
   - `pending_review`는 자동 판정에 실패해 아직 저장 여부가 결정되지 않은 상태다.

4. **prompt injection 방어가 꺼져 있다.**
   - 현재 `shouldExtractL1()`의 해당 검사가 주석 처리되어 있다.
   - trace를 데이터로 인용하고, Reviewer의 역할·도구 권한을 강하게 격리해야 한다.

5. **LLM 출력 검증이 부분적이다.**
   - 점수 범위, 관계 대상, 근거 존재 여부, 병합 대상이 실제 검색 후보였는지를 도구가 검증해야 한다.

6. **이중 기록의 부분 실패 가능성이 있다.**
   - JSONL 쓰기와 벡터 저장 중 하나가 실패해도 다른 쪽은 계속 진행한다.
   - 우리 상태 저장소는 저장 트랜잭션 결과와 실제 MemoryGraph ID를 확인한 뒤 완료로 표시해야 한다.

7. **어시스턴트 코드 블록 제거는 개발 작업에 손실이 될 수 있다.**
   - 일반 대화에서는 잡음을 줄이지만, 코드 에이전트에서는 중요한 패치나 명령 결과를 잃을 수 있다.
   - 전체 코드를 기억 후보에 넣기보다 commit·artifact·trace 참조를 보존하는 편이 낫다.

## 최종 판단

TencentDB Agent Memory가 주는 가장 중요한 답은 “중요한 정보는 규칙만으로 자동 판별한다”가 아니다.

> 원본을 빠짐없이 저장하고, 값싼 규칙으로 호출 시점과 명백한 잡음만 관리하며, 별도 LLM에게 명시적인 분류·점수·출력 계약을 주고, 임베딩으로 기존 후보를 좁힌 다음, 구조화된 저장 도구로 반영한다.

우리 Harness에서는 이 구조를 그대로 복제하기보다, 일반 Memory 추출보다 더 엄격한 **Skill Review 방식**을 장기 기억 검토에 적용하는 것이 좋다. 특히 저장 전 코드 검증, 근거 참조 검증, 실패 시 보류, MemoryGraph 관계 출력 스키마를 추가해야 한다.
