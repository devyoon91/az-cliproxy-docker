# Agent Zero 아키텍처 (v1.8)

> 원본: [agent0ai/agent-zero](https://github.com/agent0ai/agent-zero) (MIT License)
> 버전: v1.8 (2026-04-08)

---

## 시스템 구조

```
사용자 / Agent 0 (최상위)
    ├── 서브 에이전트 1 (위임된 작업)
    │   └── 서브 에이전트 1-1
    ├── 서브 에이전트 2
    └── 원격 에이전트 (A2A)  ← v1.8 신규
```

각 에이전트는 도구/플러그인을 사용하고, 프롬프트/메모리/지식베이스/스킬 등 공유 자산에 접근합니다.

---

## 런타임 구조

1. **호스트 시스템**: Docker + 브라우저만 필요
2. **런타임 컨테이너**: Agent Zero 전체 프레임워크
   - Web UI + API
   - 코드 실행 환경
   - 플러그인 시스템 (v1.8)
   - 스킬 시스템 (v1.8)

---

## 디렉토리 구조

| 디렉토리 | 설명 |
|----------|------|
| `/plugins` | **내장 플러그인** (v1.8) |
| `/usr/plugins` | **사용자 플러그인** (v1.8) |
| `/docker` | Docker 관련 파일 |
| `/docs` | 문서 |
| `/instruments` | 커스텀 스크립트/도구 |
| `/knowledge` | 지식베이스 저장소 |
| `/logs` | HTML 채팅 로그 |
| `/memory` | 영구 에이전트 메모리 (FAISS) |
| `/prompts` | 시스템/도구 프롬프트 |
| `/python/api` | API 엔드포인트 |
| `/python/extensions` | 모듈식 확장 |
| `/python/helpers` | 유틸리티 함수 |
| `/python/tools` | 도구 구현체 |
| `/agents` | 에이전트 프로필 |
| `/tmp` | 임시 런타임 데이터 (settings.json 등) |
| `/webui` | 웹 인터페이스 |
| `/work_dir` | 작업 디렉토리 |

---

## 핵심 컴포넌트

### 도구 (Tools)

#### 기본 도구

| 도구 | 설명 |
|------|------|
| `code_execution_tool` | Python/Node.js/Bash 코드 실행 |
| `search_engine` | SearXNG 기반 웹 검색 |
| `document_query` | 문서 읽기/분석 |
| `browser_agent` | Playwright 브라우저 자동화 (vision) |
| `call_subordinate` | 서브 에이전트 생성/위임 |
| `memory_save` / `memory_load` | FAISS 장기 기억 (SHA-256 검증) |
| `memory_delete` / `memory_forget` | 기억 삭제 |
| `text_editor` | 파일 편집 + 린팅 |
| `scheduler` | 작업 예약/반복 (cron) |
| `behaviour_adjustment` | 런타임 행동 조정 |
| `vision_load` | 이미지 분석 |
| `response` | 사용자에게 응답 |
| `input` | 사용자에게 질문 |

#### v1.8 신규 도구

| 도구 | 설명 |
|------|------|
| `skills_tool:list` / `skills_tool:load` | 스킬 탐색 및 로드 |
| `notify_user` | 작업 중 사용자 알림 (태스크 종료 없이) |
| `a2a_chat` | 원격 FastA2A 에이전트 통신 |
| `wait` | 지정 시간 대기 |

### 플러그인 시스템 (v1.8)

```
plugin-name/
├── plugin.yaml          # 매니페스트 (이름, 버전, 설정)
├── api/                 # Flask API 엔드포인트
├── extensions/          # Python 확장 훅
├── prompts/             # LLM 프롬프트 조각
└── webui/               # 프론트엔드 컴포넌트
```

- **설치 위치**: `/plugins` (내장), `/usr/plugins` (사용자)
- **핫 리로드**: 파일 변경 감지 → 자동 재로드
- **스코프 설정**: 글로벌 / 프로젝트별 / 에이전트 프로필별

### 지식베이스 (Knowledge)

- `/knowledge/custom/main`에 파일 저장
- 지원 형식: .txt, .pdf, .csv, .html, .json, .md
- UI의 Import Knowledge 버튼으로 추가
- 임베딩 모델로 벡터화하여 유사도 검색

### 메모리 시스템 (Memory)

- **FAISS 벡터 DB**: 임베딩 기반 유사도 검색
- **SHA-256 검증**: v1.8에서 인덱스 무결성 검증 추가
- **자동 저장**: 유용한 정보를 자동으로 기억
- **솔루션 기억**: 문제 해결 방법을 저장하여 재활용
- **통합(Consolidation)**: 유사한 기억을 병합하여 정리
- **인젝션 필터**: v1.8에서 필터 강화 (allowlisting, 길이 제한)

### Secrets 시스템 (v1.8)

- `§§secret(name)` 별칭으로 민감 정보 안전 참조
- 실제 값은 런타임에 자동 주입
- 프롬프트/로그에 노출 방지

### 에이전트 프로필 (Agent Profiles)

```
/agents/
  ├── agent0/         ← 기본 대화 에이전트
  ├── developer/      ← 개발 전용
  └── _example/       ← 예시 프로필
```

각 프로필에 포함 가능:
- 커스텀 프롬프트, 도구, 확장, 플러그인 설정

### 에이전트 계층 구조

```
Agent 0 (사용자와 대화)
  ├── Agent 1 (백엔드 개발 위임)
  │   └── Agent 1-1 (DB 스키마 작성)
  ├── Agent 2 (프론트엔드 개발 위임)
  └── Remote Agent (A2A 통신)  ← v1.8
```

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
| `/history_get` | POST | 대화 히스토리 + 토큰 수 |
