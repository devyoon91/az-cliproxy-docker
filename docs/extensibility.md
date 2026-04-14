# Agent Zero 확장 가이드

> 원본: [agent0ai/agent-zero](https://github.com/agent0ai/agent-zero) (MIT License)

---

## 확장 가능한 컴포넌트

플러그인 시스템이 추가되어 확장 방식이 크게 개선되었습니다.

| 확장 방식 | 설명 | 복잡도 |
|-----------|------|--------|
| **플러그인** | 완전한 패키지 (백엔드 + API + UI) | 높음 |
| **Extensions** | 라이프사이클 훅 (Python) | 중간 |
| **Tools** | 에이전트가 호출하는 도구 | 중간 |
| **Instruments** | 실행 가능한 스크립트 | 낮음 |
| **Skills** | SKILL.md 표준 재사용 가능 워크플로우 | 낮음 |
| **Agent Profiles** | 프로필별 프롬프트/도구 분리 | 낮음 |
| **MCP 서버** | 외부 도구 연동 | 중간 |

---

## 1. 플러그인 시스템

### 구조

```
plugin-name/
├── plugin.yaml          # 매니페스트 (이름, 버전, 설정)
├── api/                 # Flask API 엔드포인트 (자동 등록)
├── extensions/          # Python 확장 훅
├── prompts/             # LLM 프롬프트 조각
└── webui/               # Alpine.js 프론트엔드 컴포넌트
```

### 설치 위치

- `/plugins/` — 내장 플러그인
- `/usr/plugins/` — 사용자 플러그인

### 핫 리로드

파일 변경 시 watchdog이 자동 감지:
1. `after_plugin_change` 이벤트 발생
2. 캐시 클리어
3. Python 모듈 새로고침

재시작 없이 개발 가능!

### 활성화/비활성화

- UI에서 토글
- 또는 `.toggle-1` (활성), `.toggle-0` (비활성) 파일로 제어

### 스코프 설정

플러그인 설정은 3단계로 적용:
1. **글로벌** — 모든 에이전트
2. **프로젝트별** — 특정 프로젝트에서만 오버라이드
3. **에이전트 프로필별** — 특정 프로필에서만 오버라이드

### 내장 플러그인 목록

| 플러그인 | 기능 |
|----------|------|
| `_model_config` | LLM 프로바이더/모델 관리, 프리셋 |
| `_memory` | FAISS 벡터 메모리 (SHA-256 검증) |
| `_code_execution` | Python/Shell 코드 실행 |
| `_browser_agent` | Playwright 브라우저 자동화 |
| `_chat_compaction` | 대화 요약으로 컨텍스트 관리 |
| `_text_editor` | 파일 편집 + 린팅 |
| `_skills` | 스킬 브라우징/로드 |
| `_chat_branching` | 대화 분기 (대안 경로) |
| `_error_retry` | LLM 호출 자동 재시도 (backoff) |
| `_infection_check` | 프롬프트 인젝션 감지 (기본 꺼짐) |
| `_plugin_installer` | 플러그인 허브 설치/관리 |
| `_plugin_validator` | 플러그인 보안 검증 |
| `_telegram_integration` | Telegram 연동 |
| `_email_integration` | 이메일 연동 |
| `_whatsapp_integration` | WhatsApp 연동 |
| `_discovery` | 플러그인 탐색 |
| `_onboarding` | 초기 설정 가이드 |

### 플러그인 허브에서 설치

1. UI → Plugins → Plugin Hub
2. 커뮤니티 플러그인 검색
3. Install 클릭

또는 Git URL / ZIP 파일로 직접 설치 가능

---

## 2. Extensions (확장)

에이전트 라이프사이클의 특정 시점에 훅을 걸어 동작을 수정합니다.

### 사용 가능한 훅 포인트

| 훅 | 시점 |
|----|------|
| `agent_init` | 에이전트 초기화 시 |
| `before_main_llm_call` | LLM 호출 직전 |
| `message_loop_start` | 메시지 처리 루프 시작 |
| `message_loop_prompts_before` | 프롬프트 처리 전 |
| `message_loop_prompts_after` | 프롬프트 처리 후 |
| `message_loop_end` | 메시지 처리 루프 종료 |
| `monologue_start` / `monologue_end` | 에이전트 독백 시작/종료 |
| `reasoning_stream` | 추론 스트림 수신 시 |
| `response_stream` | 응답 스트림 수신 시 |
| `system_prompt` | 시스템 프롬프트 처리 시 |

### 확장 생성

```python
# 파일: /agents/{profile}/extensions/agent_init/my_extension.py
from helpers.extension import Extension

class MyExtension(Extension):
    async def execute(self, **kwargs):
        self.agent.agent_name = "CustomAgent" + str(self.agent.number)
```

### 위치
- 기본: `/python/extensions/{hook_point}/`
- 프로필별: `/agents/{profile}/extensions/{hook_point}/`
- 프로필별 파일이 동일 이름의 기본 파일을 덮어씀

---

## 3. Tools (도구)

```python
# 파일: /agents/{profile}/tools/my_tool.py
from helpers.tool import Tool

class MyTool(Tool):
    async def execute(self, **kwargs):
        result = "작업 완료"
        return result
```

- 기본: `/python/tools/`
- 프로필별: `/agents/{profile}/tools/`

---

## 4. Instruments (인스트루먼트)

```
/instruments/
  └── default/
      └── yt_download/     ← YouTube 다운로드 인스트루먼트
```

- 프롬프트에 설명이 포함되어 에이전트가 자동 인식
- `code_execution_tool`로 실행

---

## 5. Skills

SKILL.md 표준 기반 재사용 가능한 워크플로우. Claude Code, Cursor 등과 호환.

### 사용
```
"사용 가능한 스킬 목록 보여줘"     → skills_tool:list
"playwright 스킬 로드해줘"        → skills_tool:load
```

### 장점
- 처음부터 활성화되어 토큰 절약
- 외부 도구와 호환

---

## 6. Agent Profiles (에이전트 프로필)

```
/agents/
  ├── agent0/              ← 기본 대화 에이전트
  │   ├── prompts/
  │   ├── tools/
  │   ├── extensions/
  │   └── plugins/       : 프로필별 플러그인 설정
  ├── developer/           ← 개발 전용 에이전트
  └── _example/
```

Settings에서 `agent_profile`로 기본 프로필 선택.

---

## 7. MCP 서버 연동

외부 MCP 서버의 도구를 Agent Zero에서 사용합니다.

### 지원 방식
- **Stdio**: 로컬 실행 파일
- **SSE**: 네트워크 서버
- **Streaming HTTP**: HTTP 기반 스트리밍

### 설정
Settings → MCP Servers에서 JSON 입력 → 재시작

자세한 내용: [MCP 가이드](mcp-guide.md)

---

## 8. 프롬프트 커스터마이징

### 파일 구조

```
/prompts/
  ├── agent.system.main.md              ← 진입점
  ├── agent.system.main.role.md         ← 역할 정의
  ├── agent.system.main.solving.md      ← 문제 해결 전략
  ├── agent.system.main.coding.md       ← 코딩 규칙 (커스텀)
  ├── agent.system.main.tips.md         ← 운영 규칙
  ├── agent.system.skills.md            ← 스킬 시스템
  ├── agent.system.projects.main.md     ← 프로젝트 시스템
  ├── agent.system.secrets.md           ← Secrets 관리
  ├── agent.system.tool.*.md            ← 도구별 사용법
  └── fw.*.md                           ← 프레임워크 메시지
```

### include 문법
```markdown
{{ include "./agent.system.main.role.md" }}
```

### 변수 치환
```markdown
{{rules}}      ← Settings에서 설정한 행동 규칙 주입
{{tools}}      ← 사용 가능한 도구 목록 자동 주입
{{secrets}}    ← Secret 별칭 목록
{{vars}}       ← 일반 변수 목록
```
