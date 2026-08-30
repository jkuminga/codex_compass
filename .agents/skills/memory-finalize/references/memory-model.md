# MemoryGraph 노드와 관계 선택

## 노드 타입

- `task`: 완료 상태가 아니라, 여러 세션에서 재사용할 작업 방법이나 과제 정의
- `code_pattern`: 반복 적용할 코드 구조와 구현 패턴
- `problem`: 설계·구조·운영에서 해결해야 하는 일반 문제
- `solution`: 문제를 해결하는 재사용 가능한 접근
- `project`: 프로젝트 전체에 유효한 구조·제약·결정
- `technology`: 도구·라이브러리·플랫폼 사용 지식
- `error`: 실제로 관찰한 구체적인 오류와 실패 증상
- `fix`: 특정 오류에 실제 적용한 수정
- `command`: 다시 실행할 명령과 사용 조건
- `file_context`: 특정 파일·모듈의 책임과 해석에 필요한 문맥
- `workflow`: 반복되는 작업 순서와 운영 절차
- `conversation`: 사용자의 장기 선호처럼 대화 자체가 출처인 정보
- `general`: 위 타입으로 정확히 표현할 수 없는 독립적인 지식. 마지막 수단으로 사용

### 가까운 타입 구분

- `problem`은 일반적인 문제 구조, `error`는 관찰된 구체적 실패다.
- `solution`은 재사용 가능한 해결 원리, `fix`는 특정 대상에 적용한 수정이다.
- 현재 진행 상태는 `task`나 `project`로 저장하지 않고 State DB에 둔다.

## 관계 생성 규칙

관계는 “A가 B와 관련 있다”가 아니라 “A가 B를 어떻게 바꾸는가”를 표현한다.

1. 후보 노드와 inspect 결과의 기존 Memory 사이를 한 문장으로 설명할 수 있어야 한다.
2. 더 구체적인 관계가 있으면 `RELATED_TO`를 사용하지 않는다.
3. 명시적인 원인·해결·의존 관계는 `strength=0.9`, 명확한 문맥·확장 관계는 `0.7`을 기본값으로 한다. 그보다 약하면 생략한다.
4. 코드·테스트·문서 논증·사용자 결정 등 적합한 근거가 직접 뒷받침하면 `confidence=0.9`, 문맥상 명확하지만 간접적이면 `0.7`을 사용한다.
5. 후보당 최대 3개만 만든다.
6. `outgoing`은 “새 노드가 TYPE 기존 노드”, `incoming`은 “기존 노드가 TYPE 새 노드”로 읽는다.

## 허용 관계

### 원인과 결과

- `CAUSES`: 원인이 된다
- `TRIGGERS`: 동작이나 사건을 촉발한다
- `LEADS_TO`: 결과로 이어진다
- `PREVENTS`: 문제를 예방한다
- `BREAKS`: 기존 동작을 깨뜨린다

### 문제와 해결

- `SOLVES`: 문제를 해결한다
- `ADDRESSES`: 문제의 일부를 다룬다
- `ALTERNATIVE_TO`: 대안이다
- `IMPROVES`: 기존 방법을 개선한다
- `REPLACES`: 기존 방법을 대체한다

### 적용 문맥

- `OCCURS_IN`: 해당 환경에서 발생한다
- `APPLIES_TO`: 대상에 적용된다
- `WORKS_WITH`: 함께 사용할 수 있다
- `REQUIRES`: 사용하기 위해 필요하다
- `USED_IN`: 해당 위치에서 사용된다

### 지식 확장

- `BUILDS_ON`: 기존 지식을 기반으로 확장한다
- `CONTRADICTS`: 기존 기억과 모순된다
- `CONFIRMS`: 기존 기억을 확인한다
- `GENERALIZES`: 더 일반적인 원리다
- `SPECIALIZES`: 더 구체적인 사례다

### 유사성

- `SIMILAR_TO`: 의미가 비슷하다
- `VARIANT_OF`: 변형이다
- `RELATED_TO`: 구체적인 관계는 없지만 지속적으로 함께 찾아야 한다
- `ANALOGY_TO`: 구조적으로 비유할 수 있다
- `OPPOSITE_OF`: 반대되는 개념이다

### 작업 흐름

- `FOLLOWS`: 다음 단계로 이어진다
- `DEPENDS_ON`: 다른 항목에 의존한다
- `ENABLES`: 다른 작업을 가능하게 한다
- `BLOCKS`: 다른 작업을 막는다
- `PARALLEL_TO`: 병렬로 수행할 수 있다

### 품질과 선호

- `EFFECTIVE_FOR`: 특정 상황에서 효과적이다
- `INEFFECTIVE_FOR`: 특정 상황에서는 효과가 없다
- `PREFERRED_OVER`: 다른 방법보다 선호된다
- `DEPRECATED_BY`: 새로운 방법 때문에 폐기됐다
- `VALIDATED_BY`: 테스트·문서 논증·사용자 결정 등의 근거로 검증됐다

