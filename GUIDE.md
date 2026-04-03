# Agent Zero + CLIProxy 구축 가이드

이 문서는 Agent Zero를 CLIProxy(Claude Code)와 연동하여 Docker 환경에서 구동하는 전체 과정을 단계별로 정리한 가이드입니다.

---

## 목차

1. [개요](#1-개요)
2. [사전 준비](#2-사전-준비)
3. [프로젝트 구조 생성](#3-프로젝트-구조-생성)
4. [Docker Compose 구성](#4-docker-compose-구성)
5. [CLIProxy 설정](#5-cliproxy-설정)
6. [Agent Zero 환경변수 설정](#6-agent-zero-환경변수-설정)
7. [컨테이너 실행](#7-컨테이너-실행)
8. [Claude OAuth 로그인](#8-claude-oauth-로그인)
9. [API 동작 확인](#9-api-동작-확인)
10. [Agent Zero UI 모델 설정](#10-agent-zero-ui-모델-설정)
11. [프롬프트 커스터마이징](#11-프롬프트-커스터마이징)
12. [트러블슈팅](#12-트러블슈팅)

---

## 1. 개요

### 구성 요소

- **Agent Zero** (`frdel/agent-zero`): 자율 AI 에이전트 프레임워크. LiteLLM을 통해 다양한 LLM 프로바이더를 지원하며, OpenAI 호환 API 엔드포인트에 연결할 수 있습니다.
- **CLIProxy** (`eceasy/cli-proxy-api`): Claude Code CLI의 OAuth 인증을 활용하여 OpenAI 호환 REST API로 노출하는 프록시. Claude Pro/Max 구독을 API처럼 사용할 수 있게 해줍니다.

### 동작 흐름

```
사용자 → Agent Zero UI (50001)
           → LiteLLM (OpenAI 호환 요청)
              → CLIProxy (8317, OpenAI API 포맷)
                 → Claude Code CLI (OAuth)
                    → Claude AI (Anthropic)
```

---

## 2. 사전 준비

- Docker Desktop 설치 및 실행
- Claude Pro 또는 Max 구독 계정
- 브라우저 (OAuth 인증용)

---

## 3. 프로젝트 구조 생성

```bash
mkdir -p agent-zero_cliproxy/cliproxy/auth
mkdir -p agent-zero_cliproxy/cliproxy/logs
mkdir -p agent-zero_cliproxy/agent-zero/work_dir
mkdir -p agent-zero_cliproxy/agent-zero/memory
mkdir -p agent-zero_cliproxy/agent-zero/logs
```

최종 디렉토리 구조:

```
agent-zero_cliproxy/
├── docker-compose.yml
├── .env
├── cliproxy/
│   ├── config.yaml
│   ├── auth/
│   └── logs/
└── agent-zero/
    ├── prompts/          # (Step 11에서 추가)
    ├── work_dir/
    ├── memory/
    └── logs/
```

---

## 4. Docker Compose 구성

`docker-compose.yml` 파일 생성:

```yaml
version: "3.8"

services:
  # ── CLIProxy: Claude Code CLI → OpenAI-compatible API ──
  cliproxy:
    image: eceasy/cli-proxy-api:latest
    container_name: cliproxy
    ports:
      - "8317:8317"   # OpenAI-compatible API
      - "8085:8085"   # Web Management UI
      - "54545:54545" # OAuth callback
    volumes:
      - ./cliproxy/config.yaml:/CLIProxyAPI/config.yaml
      - ./cliproxy/auth:/root/.cli-proxy-api
      - ./cliproxy/logs:/CLIProxyAPI/logs
    restart: unless-stopped
    networks:
      - az-net
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8317/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

  # ── Agent Zero: AI Agent Framework ──
  agent-zero:
    image: frdel/agent-zero:latest
    container_name: agent-zero
    ports:
      - "50001:80"
    env_file:
      - .env
    volumes:
      - ./agent-zero/work_dir:/a0/work_dir
      - ./agent-zero/memory:/a0/memory
      - ./agent-zero/logs:/a0/logs
      - ./agent-zero/prompts:/a0/prompts
    depends_on:
      cliproxy:
        condition: service_started
    restart: unless-stopped
    networks:
      - az-net

networks:
  az-net:
    driver: bridge
```

### 핵심 포인트

- 두 컨테이너는 `az-net` 브릿지 네트워크로 연결
- Agent Zero에서 CLIProxy 접근 시 `http://cliproxy:8317/v1` 사용 (Docker 내부 DNS)
- `localhost`가 아닌 **컨테이너 이름**을 사용해야 함

---

## 5. CLIProxy 설정

`cliproxy/config.yaml` 파일 생성:

```yaml
# CLIProxy Configuration
host: "0.0.0.0"
port: 8317

# 핵심: auth 디렉토리 명시 (이 값이 없으면 mkdir 오류 발생)
auth-dir: "/root/.cli-proxy-api"

# Logging
debug: false
logging-to-file: false

# Request retry
request-retry: 3

# Management API
remote-management:
  allow-remote: false
  secret-key: ""

# Provider 설정 (OAuth 로그인 후 자동 저장됨)
auth: {}
```

> **주의**: `auth-dir` 필드를 반드시 명시해야 합니다. 누락 시 `failed to create auth directory: mkdir: no such file or directory` 오류가 발생합니다.

---

## 6. Agent Zero 환경변수 설정

`.env` 파일 생성:

```env
# LiteLLM이 요구하는 API key (CLIProxy는 검증 안 하지만 필수값)
OPENAI_API_KEY=sk-placeholder

CHAT_MODEL_DEFAULT=claude-sonnet-4-6
CHAT_MODEL_BASE_URL=http://cliproxy:8317/v1
CHAT_API_KEY=sk-placeholder

UTILITY_MODEL_DEFAULT=claude-sonnet-4-6
UTILITY_MODEL_BASE_URL=http://cliproxy:8317/v1
UTILITY_API_KEY=sk-placeholder
```

### 환경변수 설명

| 변수 | 설명 |
|------|------|
| `OPENAI_API_KEY` | LiteLLM이 OpenAI 프로바이더 사용 시 필수로 요구. CLIProxy는 실제 검증하지 않으므로 아무 값 입력 |
| `CHAT_MODEL_DEFAULT` | 메인 채팅 모델명 |
| `CHAT_MODEL_BASE_URL` | CLIProxy API 엔드포인트 (Docker 내부 주소) |
| `UTILITY_MODEL_DEFAULT` | 유틸리티(요약, 압축 등) 모델명 |

---

## 7. 컨테이너 실행

```bash
cd agent-zero_cliproxy

# 전체 시작
docker compose up -d

# 로그 확인
docker logs -f cliproxy
```

CLIProxy 로그에서 오류 없이 시작 메시지가 나오면 성공입니다.

---

## 8. Claude OAuth 로그인

### 실행 명령

Windows CMD:
```cmd
docker exec -it cliproxy /CLIProxyAPI/CLIProxyAPI -claude-login
```

Windows Git Bash (경로 변환 방지):
```bash
MSYS_NO_PATHCONV=1 docker exec -it cliproxy /CLIProxyAPI/CLIProxyAPI -claude-login
```

> **참고**: 컨테이너 내 실행 파일 경로는 `/CLIProxyAPI/CLIProxyAPI` (대소문자 구분). `cliproxyapi` 소문자로는 찾을 수 없습니다.

### OAuth 인증 흐름

1. 터미널에 콜백 URL 입력 요청 → `http://localhost:54545/callback` 입력
2. OAuth 인증 URL이 출력됨 → 브라우저에서 해당 URL 열기
3. Claude 계정으로 로그인 및 권한 승인
4. 브라우저가 `http://localhost:54545/callback?code=...&state=...` 로 리다이렉트
5. 자동으로 콜백이 잡히지 않으면 → 브라우저 주소창의 **전체 URL을 복사**하여 터미널에 붙여넣기
6. 인증 성공 시 토큰이 `./cliproxy/auth/` 에 저장됨

### CLIProxy 사용 가능한 아규먼트

| 아규먼트 | 설명 |
|---------|------|
| `-claude-login` | Claude OAuth 로그인 |
| `-login` | Google 계정 로그인 (Gemini) |
| `-codex-login` | OpenAI Codex OAuth 로그인 |
| `-qwen-login` | Qwen OAuth 로그인 |
| `-no-browser` | OAuth 시 브라우저 자동 열기 안함 |
| `-tui` | 터미널 관리 UI 모드 |
| `-config string` | 설정 파일 경로 지정 |

---

## 9. API 동작 확인

```bash
curl http://localhost:8317/v1/models
```

정상 응답 예시:
```json
{
  "data": [
    {"id": "claude-opus-4-6", "object": "model", "owned_by": "anthropic"},
    {"id": "claude-sonnet-4-6", "object": "model", "owned_by": "anthropic"},
    ...
  ],
  "object": "list"
}
```

### 사용 가능한 모델 목록

| Model ID | Description |
|----------|-------------|
| `claude-opus-4-6` | 최신 Opus |
| `claude-sonnet-4-6` | 최신 Sonnet |
| `claude-sonnet-4-5-20250929` | Sonnet 4.5 |
| `claude-opus-4-5-20251101` | Opus 4.5 |
| `claude-opus-4-1-20250805` | Opus 4.1 |
| `claude-sonnet-4-20250514` | Sonnet 4 |
| `claude-3-7-sonnet-20250219` | Sonnet 3.7 |
| `claude-3-5-haiku-20241022` | Haiku 3.5 |

---

## 10. Agent Zero UI 모델 설정

`http://localhost:50001` 접속 후 Settings에서 다음과 같이 설정:

| Setting | Value |
|---------|-------|
| **Chat model provider** | `Other OpenAI compatible` |
| **Chat model name** | `claude-sonnet-4-6` |
| **Chat model API base URL** | `http://cliproxy:8317/v1` |
| **API Key** | `sk-placeholder` |
| **Context length** | `100000` |
| **Supports Vision** | ON |

### 주의사항

- Provider를 `OpenAI`가 아닌 **`Other OpenAI compatible`** 선택
- API base URL은 `localhost`가 아닌 **`cliproxy`** (Docker 내부 DNS)
- `OPENAI_API_KEY` 환경변수가 `.env`에 없으면 `AuthenticationError` 발생

---

## 11. 프롬프트 커스터마이징

### 기본 프롬프트 추출

```bash
docker cp agent-zero:/a0/prompts ./agent-zero/prompts
```

### 주요 프롬프트 파일

| File | Description |
|------|-------------|
| `agent.system.main.md` | 메인 시스템 프롬프트 (다른 파일을 include) |
| `agent.system.main.role.md` | **에이전트 역할 정의 (가장 핵심)** |
| `agent.system.behaviour.md` | 행동 규칙 |
| `agent.system.behaviour_default.md` | 기본 행동 규칙 |
| `agent.system.main.solving.md` | 문제 해결 전략 |
| `agent.system.main.communication.md` | 커뮤니케이션 스타일 |
| `agent.system.main.tips.md` | 추가 팁/지침 |
| `agent.system.tool.code_exe.md` | 코드 실행 도구 설명 |
| `agent.system.tool.browser.md` | 브라우저 도구 설명 |
| `agent.system.tool.memory.md` | 메모리 도구 설명 |

### 프롬프트 구조

`agent.system.main.md`가 진입점이며 include로 다른 파일을 로드합니다:

```markdown
# Agent Zero System Manual
{{ include "./agent.system.main.role.md" }}
{{ include "./agent.system.main.environment.md" }}
{{ include "./agent.system.main.communication.md" }}
{{ include "./agent.system.main.solving.md" }}
{{ include "./agent.system.main.tips.md" }}
```

### 커스텀 예시

`agent-zero/prompts/agent.system.main.role.md` 수정:

```markdown
## Your role
You are a senior full-stack developer agent.
You write clean, production-ready code.
You always commit to git with meaningful messages.
You write tests for all code you produce.
You communicate in Korean.
```

### 반영

```bash
docker compose up -d agent-zero --force-recreate
```

---

## 12. 트러블슈팅

### CLIProxy `mkdir: no such file or directory`

**원인**: `config.yaml`에 `auth-dir` 필드 누락

**해결**: config.yaml에 추가
```yaml
auth-dir: "/root/.cli-proxy-api"
```

### `executable file not found: cliproxyapi`

**원인**: 실행 파일명이 다름

**해결**: 올바른 경로 사용
```bash
# Windows CMD
docker exec -it cliproxy /CLIProxyAPI/CLIProxyAPI -claude-login

# Git Bash
MSYS_NO_PATHCONV=1 docker exec -it cliproxy /CLIProxyAPI/CLIProxyAPI -claude-login
```

### `callback URL missing code`

**원인**: 콜백 URL에 인증 코드가 포함되지 않음

**해결**: 브라우저에서 승인 후 리다이렉트된 전체 URL (`http://localhost:54545/callback?code=...&state=...`)을 복사하여 터미널에 붙여넣기

### `AuthenticationError: OPENAI_API_KEY`

**원인**: LiteLLM이 OpenAI 프로바이더 사용 시 API key를 필수로 요구

**해결**: `.env`에 추가
```env
OPENAI_API_KEY=sk-placeholder
```

### Agent Zero에서 CLIProxy 연결 안됨

**원인**: `localhost` 사용

**해결**: Docker 내부 DNS인 `http://cliproxy:8317/v1` 사용. Agent Zero 컨테이너에서 `localhost`는 자기 자신을 가리킴

---

## 참고 링크

- [Agent Zero GitHub](https://github.com/agent0ai/agent-zero)
- [CLIProxyAPI GitHub](https://github.com/router-for-me/CLIProxyAPI)
- [CLIProxy Documentation](https://help.router-for.me/)
- [Agent Zero Docker Hub](https://hub.docker.com/r/frdel/agent-zero)
- [CLIProxy Docker Hub](https://hub.docker.com/r/eceasy/cli-proxy-api)
