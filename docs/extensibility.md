# Agent Zero 확장 가이드

> 원본: [agent0ai/agent-zero/docs/extensibility.md](https://github.com/agent0ai/agent-zero) (MIT License)

---

## 확장 가능한 컴포넌트

Agent Zero는 모듈식 구조로 개별 컴포넌트를 교체/확장할 수 있습니다.

---

## 1. Extensions (확장)

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
| `monologue_start` | 에이전트 독백 시작 |
| `monologue_end` | 에이전트 독백 종료 |
| `reasoning_stream` | 추론 스트림 수신 시 |
| `response_stream` | 응답 스트림 수신 시 |
| `system_prompt` | 시스템 프롬프트 처리 시 |

### 확장 생성 방법

```python
# 파일: /agents/{profile}/extensions/agent_init/my_extension.py
from python.helpers.extension import Extension

class MyExtension(Extension):
    async def execute(self, **kwargs):
        self.agent.agent_name = "CustomAgent" + str(self.agent.number)
```

### 위치
- 기본: `/python/extensions/{hook_point}/`
- 프로필별: `/agents/{profile}/extensions/{hook_point}/`
- 프로필별 파일이 같은 이름의 기본 파일을 덮어씀

---

## 2. Tools (도구)

에이전트가 LLM 응답을 통해 호출하는 기능 모듈입니다.

### 커스텀 도구 생성

```python
# 파일: /agents/{profile}/tools/my_tool.py
from python.helpers.tool import Tool

class MyTool(Tool):
    async def execute(self, **kwargs):
        # 도구 로직 구현
        result = "작업 완료"
        return result
```

### 위치
- 기본: `/python/tools/`
- 프로필별: `/agents/{profile}/tools/`
- 프로필별 도구가 기본 도구를 덮어씀

---

## 3. Instruments (인스트루먼트)

런타임에 실행 가능한 스크립트/프로그램입니다.

### 구조
```
/instruments/
  └── default/
      └── yt_download/     ← YouTube 다운로드 인스트루먼트
```

- 프롬프트에 설명이 포함되어 에이전트가 자동으로 인식
- `code_execution_tool`로 실행

---

## 4. Agent Profiles (에이전트 프로필)

에이전트별로 프롬프트, 도구, 확장을 분리하여 전문화합니다.

### 구조
```
/agents/
  ├── agent0/              ← 기본 대화 에이전트
  │   ├── prompts/         ← 커스텀 프롬프트
  │   ├── tools/           ← 커스텀 도구
  │   └── extensions/      ← 커스텀 확장
  ├── developer/           ← 개발 전용 에이전트
  └── _example/            ← 예시 프로필
```

Settings에서 `agent_profile`로 기본 프로필 선택 가능.

---

## 5. MCP 서버 연동

외부 MCP(Model Context Protocol) 서버의 도구를 Agent Zero에서 사용할 수 있습니다.

### 지원 방식
- **Stdio**: 로컬 실행 파일 (stdin/stdout 통신)
- **SSE**: 네트워크 서버 (Server-Sent Events)
- **Streaming HTTP**: HTTP 기반 스트리밍

### 설정 방법
1. Settings → MCP Servers 섹션
2. 서버 정보 입력 (이름, 커맨드, 인자)
3. Save → Restart

### 예시: sequential-thinking 서버
```json
{
  "name": "sequential-thinking",
  "command": "npx",
  "args": ["--yes", "--package", "@modelcontextprotocol/server-sequential-thinking", "mcp-server-sequential-thinking"]
}
```

---

## 6. 프롬프트 커스터마이징

### 파일 구조
```
/prompts/
  ├── agent.system.main.md          ← 진입점
  ├── agent.system.main.role.md     ← 역할 정의
  ├── agent.system.main.solving.md  ← 문제 해결 전략
  ├── agent.system.main.coding.md   ← 코딩 규칙 (우리가 추가)
  ├── agent.system.main.tips.md     ← 운영 규칙
  ├── agent.system.tool.*.md        ← 도구별 사용법
  └── fw.*.md                       ← 프레임워크 메시지
```

### include 문법
```markdown
{{ include "./agent.system.main.role.md" }}
```

### 변수 치환
```markdown
{{rules}}      ← Settings에서 설정한 행동 규칙 주입
{{tools}}      ← 사용 가능한 도구 목록 자동 주입
```
