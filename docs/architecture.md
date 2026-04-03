# Agent Zero 아키텍처

> 원본: [agent0ai/agent-zero/docs/architecture.md](https://github.com/agent0ai/agent-zero) (MIT License)

---

## 시스템 구조

```
사용자 / Agent 0 (최상위)
    ├── 서브 에이전트 1 (위임된 작업)
    │   └── 서브 에이전트 1-1
    └── 서브 에이전트 2
```

각 에이전트는 도구를 사용하고, 프롬프트/메모리/지식베이스 등 공유 자산에 접근할 수 있습니다.

---

## 런타임 구조

1. **호스트 시스템**: Docker + 브라우저만 필요
2. **런타임 컨테이너**: Agent Zero 전체 프레임워크 포함
   - Web UI + API
   - 코드 실행 환경
   - 모든 핵심 기능

---

## 디렉토리 구조

| 디렉토리 | 설명 |
|----------|------|
| `/docker` | Docker 관련 파일 |
| `/docs` | 문서 |
| `/instruments` | 커스텀 스크립트/도구 |
| `/knowledge` | 지식베이스 저장소 |
| `/logs` | HTML 채팅 로그 |
| `/memory` | 영구 에이전트 메모리 |
| `/prompts` | 시스템/도구 프롬프트 |
| `/python/api` | API 엔드포인트 |
| `/python/extensions` | 모듈식 확장 |
| `/python/helpers` | 유틸리티 함수 |
| `/python/tools` | 도구 구현체 |
| `/tmp` | 임시 런타임 데이터 |
| `/webui` | 웹 인터페이스 |
| `/work_dir` | 작업 디렉토리 |

---

## 핵심 컴포넌트

### 도구 (Tools)

에이전트가 사용하는 실행 가능한 기능:

| 도구 | 설명 |
|------|------|
| `code_execution_tool` | Python/Node.js/Bash 코드 실행 |
| `search_engine` | SearXNG 기반 웹 검색 |
| `document_query` | 문서 읽기/분석 |
| `browser_agent` | 브라우저 자동화 |
| `call_subordinate` | 서브 에이전트 생성/위임 |
| `memory_save` | 장기 기억 저장 |
| `memory_load` | 장기 기억 검색 |
| `memory_delete` / `memory_forget` | 기억 삭제 |
| `response` | 사용자에게 응답 |
| `input` | 사용자에게 질문 |
| `scheduler` | 작업 예약/반복 |
| `behaviour_adjustment` | 런타임 행동 조정 |
| `vision_load` | 이미지 분석 |

### 지식베이스 (Knowledge)

- `/knowledge/custom/main`에 파일 저장
- 지원 형식: .txt, .pdf, .csv, .html, .json, .md
- UI의 Import Knowledge 버튼으로 추가
- 임베딩 모델로 벡터화하여 유사도 검색

### 메모리 시스템 (Memory)

- **자동 저장**: 에이전트가 유용한 정보를 자동으로 기억
- **유사도 검색**: 임베딩 벡터 기반으로 관련 기억 검색
- **솔루션 기억**: 문제 해결 방법을 저장하여 재활용
- **통합(Consolidation)**: 유사한 기억을 병합하여 정리

### 에이전트 프로필 (Agent Profiles)

`/agents/` 디렉토리에 프로필별 설정 분리:

```
/agents/
  ├── agent0/         ← 기본 에이전트 (대화용)
  ├── developer/      ← 개발 전용
  └── _example/       ← 예시 프로필
```

각 프로필에 포함 가능:
- 커스텀 프롬프트
- 커스텀 도구
- 커스텀 확장

### 에이전트 계층 구조

```
Agent 0 (사용자와 대화)
  ├── Agent 1 (백엔드 개발 위임)
  │   └── Agent 1-1 (DB 스키마 작성)
  └── Agent 2 (프론트엔드 개발 위임)
```

- 상위 에이전트가 하위 에이전트에게 작업 위임
- 하위 에이전트는 결과를 상위에게 보고
- 각 에이전트는 독립적 컨텍스트 보유

---

## API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/message_async` | POST | 메시지 전송 (비동기) |
| `/poll` | POST | 대화 로그 폴링 |
| `/csrf_token` | GET | CSRF 토큰 획득 |
| `/health` | GET | 상태 확인 |
| `/settings_get` | POST | 설정 조회 |
| `/settings_set` | POST | 설정 변경 |
| `/chat_load` | POST | 채팅 불러오기 |
| `/chat_reset` | POST | 채팅 초기화 |
| `/chat_export` | POST | 채팅 내보내기 |
| `/upload` | POST | 파일 업로드 |
| `/restart` | POST | 프레임워크 재시작 |
