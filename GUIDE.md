# Agent Zero + CLIProxy 구축 가이드

> Agent Zero v1.8 | CLIProxy v6.9+

이 문서는 Agent Zero를 CLIProxy와 연동하여 Docker 환경에서 구동하는 전체 과정을 단계별로 정리한 가이드입니다.

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
11. [Git + GitHub CLI 자동화](#11-git--github-cli-자동화)
12. [프롬프트 커스터마이징](#12-프롬프트-커스터마이징)
13. [설정 영속화](#13-설정-영속화)
14. [Telegram Bot 원격 제어](#14-telegram-bot-원격-제어)
15. [팁: 호스트 파일시스템 접근](#15-팁-호스트-파일시스템-접근)
16. [팁: 개인화 저장소 분리](#16-팁-개인화-저장소-분리)
17. [트러블슈팅](#17-트러블슈팅)

---

## 1. 개요

### 구성 요소

- **Agent Zero** (`agent0ai/agent-zero`): 자율 AI 에이전트 프레임워크. LiteLLM을 통해 다양한 LLM 프로바이더를 지원하며, OpenAI 호환 API 엔드포인트에 연결할 수 있습니다.
- **CLIProxy** (`eceasy/cli-proxy-api`): 다양한 AI CLI 도구의 OAuth 인증을 활용하여 OpenAI 호환 REST API로 노출하는 프록시.

### 동작 흐름

```
사용자 → Agent Zero UI (50001)
           → LiteLLM (OpenAI 호환 요청)
              → CLIProxy (8317, OpenAI API 포맷)
                 → LLM CLI (OAuth)
                    → LLM Provider
```

---

## 2. 사전 준비

- Docker Desktop 설치 및 실행
- Claude Pro 또는 Max 구독 계정
- 브라우저 (OAuth 인증용)
- GitHub Personal Access Token (PAT) — Git push 자동화 시 필요

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
├── .env                  # 실제 토큰 포함 (git에서 제외됨)
├── .env.example          # 토큰 없는 템플릿 (git에 포함)
├── cliproxy/
│   ├── config.yaml
│   ├── auth/
│   └── logs/
└── agent-zero/
    ├── git-init.sh       # 컨테이너 시작 시 Git 인증 자동 설정
    ├── prompts/          # (Step 12에서 추가)
    ├── work_dir/         # Agent Zero 작업 디렉토리 (clone, 코드 생성 등)
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
    image: eceasy/cli-proxy-api:v6.9.18
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
    image: agent0ai/agent-zero:v1.8
    container_name: agent-zero
    ports:
      - "50001:80"
    env_file:
      - .env
    environment:
      - GIT_USER_NAME=${GIT_USER_NAME}
      - GIT_USER_EMAIL=${GIT_USER_EMAIL}
      - GITHUB_TOKEN=${GITHUB_TOKEN}
    volumes:
      - ./agent-zero/work_dir:/a0/work_dir
      - ./agent-zero/memory:/a0/memory
      - ./agent-zero/logs:/a0/logs
      - ./agent-zero/prompts:/a0/prompts
      - ./agent-zero/git-init.sh:/a0/git-init.sh:ro
    entrypoint: ["/bin/sh", "-c", "sh /a0/git-init.sh && exec /exe/initialize.sh"]
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

# Git Configuration (Agent Zero 컨테이너 내 git push 자동화)
GIT_USER_NAME=your-github-username
GIT_USER_EMAIL=your-email@example.com
GITHUB_TOKEN=ghp_your_personal_access_token
```

> **보안 주의**: `.env` 파일에는 GitHub PAT 토큰이 포함되므로 `.gitignore`에 의해 저장소에서 제외됩니다. `.env.example`을 참고하여 `.env` 파일을 생성하세요.

### 환경변수 설명

| 변수 | 설명 |
|------|------|
| `OPENAI_API_KEY` | LiteLLM이 OpenAI 프로바이더 사용 시 필수로 요구. CLIProxy는 실제 검증하지 않으므로 아무 값 입력 |
| `CHAT_MODEL_DEFAULT` | 메인 채팅 모델명 |
| `CHAT_MODEL_BASE_URL` | CLIProxy API 엔드포인트 (Docker 내부 주소) |
| `UTILITY_MODEL_DEFAULT` | 유틸리티(요약, 압축 등) 모델명 |
| `GIT_USER_NAME` | GitHub 사용자명 |
| `GIT_USER_EMAIL` | Git 커밋용 이메일 |
| `GITHUB_TOKEN` | GitHub Personal Access Token (repo 권한 필요) |

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
| `claude-haiku-4-5-20251001` | Haiku 4.5 |

---

## 10. Agent Zero UI 모델 설정

`http://localhost:50001` 접속 후 모델을 설정합니다.

### v1.8 이상: Plugins → _model_config

v1.8부터 모델 설정은 **`_model_config` 플러그인**에서 관리합니다.

UI → **Plugins** → **_model_config** 에서:

| 항목 | 값 |
|------|------|
| **Chat Model Provider** | `Other OpenAI compatible` |
| **Chat Model Name** | 사용할 모델명 (예: `gpt-4.1`, `o3`) |
| **Chat Model API Base** | `http://cliproxy:8317/v1` |
| **Chat Model API Key** | `sk-placeholder` |
| **Utility Model Provider** | `Other OpenAI compatible` |
| **Utility Model Name** | 경량 모델 (예: `gpt-4.1-mini`) |
| **Utility Model API Base** | `http://cliproxy:8317/v1` |
| **Utility Model API Key** | `sk-placeholder` |
| **Embedding** | `huggingface` / `sentence-transformers/all-MiniLM-L6-v2` (기본 유지) |

설정 파일 위치: `/a0/usr/plugins/_model_config/config.json`

### 주의사항

- Provider를 `OpenAI`가 아닌 **`Other OpenAI compatible`** 선택
- API base URL은 `localhost`가 아닌 **`cliproxy`** (Docker 내부 DNS)
- **API Key 필수** — CLIProxy가 검증하지 않아도 LiteLLM이 요구하므로 `sk-placeholder` 입력

---

## 11. Git + GitHub CLI 자동화

Agent Zero 컨테이너 안에서 Git clone/commit/push는 물론, GitHub CLI(`gh`)를 통해 PR 생성, 이슈 관리까지 자동으로 수행할 수 있습니다.

### 동작 원리

1. 컨테이너 시작 시 `git-init.sh` 스크립트가 실행됨
2. `.env`의 `GIT_USER_NAME`, `GIT_USER_EMAIL`, `GITHUB_TOKEN`으로 Git 인증 설정
3. GitHub CLI(`gh`) 자동 설치 및 PAT 토큰으로 인증
4. Agent Zero가 `work_dir/` 안에서 clone → 작업 → commit → push → PR 생성 수행

### git-init.sh

`agent-zero/git-init.sh` 파일이 컨테이너 시작 시 자동 실행됩니다:

```bash
#!/bin/sh
# Git 설정
git config --global user.name "${GIT_USER_NAME}"
git config --global user.email "${GIT_USER_EMAIL}"
git config --global credential.helper store
echo "https://${GIT_USER_NAME}:${GITHUB_TOKEN}@github.com" > /root/.git-credentials

# GitHub CLI 설치 (첫 기동 시 1~2분 소요)
if ! command -v gh > /dev/null 2>&1; then
    # gh 설치...
fi

# gh 인증
echo "${GITHUB_TOKEN}" | gh auth login --with-token
```

### GitHub PAT 생성 방법

1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. "Generate new token (classic)" 클릭
3. 권한 선택: `repo` (Full control of private repositories)
4. 토큰 생성 후 `.env`의 `GITHUB_TOKEN`에 입력

### 사용 예시

**기본: clone → 작업 → push**
> "https://github.com/username/my-project 를 clone 받아서 README.md를 수정하고 commit 후 push 해줘"

**브랜치 + PR 생성**
> "feature/login 브랜치 만들어서 로그인 기능 구현하고 PR 올려줘"

**이슈 기반 작업**
> "이슈 #3 확인해서 수정하고 PR 만들어줘"

Agent Zero가 컨테이너 내부에서:
```bash
cd /a0/work_dir
git clone https://github.com/username/my-project
cd my-project
git checkout -b feature/login
# 작업 수행...
git add . && git commit -m "Add login feature"
git push -u origin feature/login
gh pr create --title "Add login feature" --body "로그인 기능 추가"
```

### 보안 주의사항

- `.env` 파일에 PAT 토큰이 포함되므로 **절대 저장소에 올리지 마세요**
- `.gitignore`에 `.env`가 등록되어 있어 자동으로 제외됨
- `.env.example`을 참고하여 `.env` 파일을 생성하세요
- 컨테이너 내부의 `/root/.git-credentials`에 토큰이 저장됨 (컨테이너 재시작 시 재생성)
- `gh auth login`은 컨테이너 시작 시 자동 수행 (재시작마다 재인증)

---

## 12. 프롬프트 커스터마이징

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

## 13. 설정 영속화

Agent Zero UI에서 변경한 설정은 컨테이너 내부 `/a0/tmp/settings.json`에 저장됩니다. 컨테이너 재시작 시 설정이 초기화되는 것을 방지하기 위해 호스트에 마운트합니다.

### 설정 추출 (최초 1회)

UI에서 설정 변경 후 Save → 컨테이너에서 추출:
```bash
docker exec agent-zero cat /a0/tmp/settings.json > ./agent-zero/settings.json
```

### docker-compose 볼륨 마운트

```yaml
volumes:
  - ./agent-zero/settings.json:/a0/tmp/settings.json
```

이후 UI에서 설정을 변경하면 호스트의 `settings.json`에도 자동 반영됩니다.

---

## 14. Telegram Bot 원격 제어

Android 폰에서 Telegram을 통해 Agent Zero를 원격으로 제어할 수 있습니다. 포트포워딩이나 VPN 없이 동작합니다.

> **주의**: Agent Zero v1.8에 `_telegram_integration` 내장 플러그인이 있지만, 기본 알림만 지원합니다. 이 프로젝트의 커스텀 Telegram Bridge는 웹 채팅 실시간 모니터링, 멀티채팅, 토큰 사용량 추적, 문서 열람 등 더 많은 기능을 제공합니다. **내장 `_telegram_integration` 플러그인은 끄고, 커스텀 Telegram Bridge를 사용하세요.**

### 동작 원리

```
폰 → Telegram 클라우드 서버 → Bridge Bot (Docker, polling) → Agent Zero
폰 ← Telegram 클라우드 서버 ← Bridge Bot (Docker)          ← Agent Zero
```

- Bridge Bot이 Telegram 서버에 주기적으로 polling하여 새 메시지를 가져옴
- 폰과 PC가 직접 통신하지 않음 — Telegram 클라우드가 중계
- 인터넷만 연결되어 있으면 어디서든 제어 가능

### Telegram Bot 생성

1. Telegram 앱 설치 (Android Play Store)
2. 검색창에 `@BotFather` 검색 → 대화 시작
3. `/newbot` 입력 → 봇 이름, username 설정
4. **Bot Token** 수령 (형식: `123456789:ABCdefGHI...`)
5. 생성된 봇에게 아무 메시지 전송 (채팅방 활성화)
6. 브라우저에서 `https://api.telegram.org/bot{TOKEN}/getUpdates` 열기
7. 응답에서 `chat.id` 값 확인 → **Chat ID**

### 환경변수 설정

`.env`에 추가:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### 실행

```bash
docker compose up -d --build telegram-bridge
```

### 사용법

| Telegram 명령 | 설명 |
|---------------|------|
| `/start` | 봇 시작 안내 |
| `/status` | Agent Zero 상태 확인 (연결, CSRF 토큰, 컨텍스트) |
| `/new` | 새 대화 시작 (컨텍스트 초기화) |
| `/help` | 도움말 |
| 일반 메시지 | Agent Zero에 지시 전달 → 응답 수신 |

### 알림 Webhook

Agent Zero 작업 중 알림을 Telegram으로 보내려면:
```bash
curl -X POST http://telegram-bridge:8443/notify \
     -H 'Content-Type: application/json' \
     -d '{"text": "작업 완료!"}'
```

Agent Zero에게 지시할 때 활용:
> "작업 완료되면 curl로 http://telegram-bridge:8443/notify 에 결과를 알려줘"

### 기술 세부사항

- Agent Zero API: `/message_async`로 메시지 전송, `/poll`로 응답 수집
- CSRF 보호: `/csrf_token`에서 토큰 획득 후 `X-CSRF-Token` 헤더에 포함
- 세션 유지: aiohttp CookieJar로 쿠키 유지, 403 시 자동 재발급
- 보안: `TELEGRAM_CHAT_ID`로 본인만 봇 사용 가능 (다른 사용자 차단)

---

## 15. 팁: 호스트 파일시스템 접근

기본적으로 Agent Zero는 Docker 컨테이너 안에서 격리되어 `work_dir/`만 접근 가능합니다. 호스트 PC의 프로젝트 폴더에 직접 접근하고 싶다면 `docker-compose.yml`에 볼륨을 추가하세요.

```yaml
# agent-zero 서비스의 volumes에 추가
volumes:
  - C:/Users/myname/projects:/a0/work_dir/host-projects   # Windows
  - /home/myname/projects:/a0/work_dir/host-projects       # Linux/Mac
```

Agent Zero에서 `/a0/work_dir/host-projects/`로 접근하면 호스트의 실제 프로젝트 파일을 읽고 수정할 수 있습니다.

> **주의**: 호스트 파일시스템을 마운트하면 Agent Zero가 해당 경로의 파일을 삭제/수정할 수 있습니다. 중요한 디렉토리는 읽기 전용(`:ro`)으로 마운트하는 것을 권장합니다.
> ```yaml
> - C:/Users/myname/documents:/a0/work_dir/docs:ro   # 읽기 전용
> ```

---

## 16. 팁: 개인화 저장소 분리

이 프로젝트는 **하네스 킷(공용 환경)**으로 유지하고, 회사/팀/개인 고유 내용은 **별도 저장소**에서 관리하는 것을 권장합니다.

### 구조

```
az-cliproxy-docker/              ← 하네스 킷 (이 저장소)
  ├── docker-compose.yml
  ├── agent-zero/prompts/         ← 기본 프롬프트
  └── agent-zero/agents/          ← 기본 프로필 (developer, reviewer, devops)

my-agent-config/                 ← 개인화 저장소 (별도 관리)
  ├── knowledge/                  ← 코딩 표준, API 규칙, 아키텍처 문서
  ├── agents/                     ← 프로필 오버라이드/추가
  ├── templates/                  ← 프로젝트 보일러플레이트
  └── instruments/                ← 커스텀 인스트루먼트
```

### 개인화 저장소 생성

```bash
mkdir my-agent-config
cd my-agent-config
git init

mkdir knowledge agents templates instruments

# 예시: 코딩 표준 문서 추가
echo "# 회사 코딩 표준" > knowledge/coding-standards.md

# 예시: 프로젝트 템플릿
mkdir templates/springboot-api
```

### docker-compose 볼륨 연결

`docker-compose.yml`의 agent-zero 서비스에 개인화 저장소를 마운트합니다:

```yaml
# agent-zero 서비스의 volumes에 추가
volumes:
  # 지식베이스 (에이전트가 자동으로 참조)
  - ../my-agent-config/knowledge:/a0/knowledge/custom/team

  # 커스텀 인스트루먼트
  - ../my-agent-config/instruments:/a0/instruments/custom

  # 프로젝트 템플릿 (work_dir에서 접근)
  - ../my-agent-config/templates:/a0/work_dir/templates:ro
```

> **경로 주의**: `../my-agent-config`는 docker-compose.yml 기준 상대 경로입니다. 개인화 저장소를 같은 상위 디렉토리에 clone하세요.

### 지식베이스 활용 예시

`my-agent-config/knowledge/` 에 넣으면 Agent Zero가 자동으로 검색합니다:

```
knowledge/
  ├── coding-standards.md     ← "API 만들어줘" → 이 표준에 맞게 생성
  ├── api-conventions.md      ← REST URL/응답 형식 규칙
  ├── architecture.md         ← 레이어 구조, 패턴 규칙
  └── git-workflow.md         ← 브랜치 전략, 커밋 컨벤션
```

### 프로젝트 템플릿 활용 예시

```
templates/
  ├── springboot-api/         ← "Spring Boot API 프로젝트 만들어줘"
  ├── nextjs-app/             ← "Next.js 앱 만들어줘"
  └── python-fastapi/         ← "FastAPI 프로젝트 만들어줘"
```

Agent Zero에게:
> "/a0/work_dir/templates/springboot-api 를 복사해서 새 프로젝트 만들어줘"

### 장점

- 하네스 킷과 개인화 내용이 **완전 분리** — 킷 업데이트 시 충돌 없음
- 개인화 저장소를 **팀끼리 공유** 가능 (private repo)
- 여러 환경(개인 PC, 서버)에서 **동일한 개인화** 적용
- 하네스 킷은 공개 가능, 개인화 내용은 비공개 유지

---

## 17. 트러블슈팅

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

### Telegram Bot `CSRF token missing or invalid` (403)

**원인**: Agent Zero의 CSRF 보호 — 모든 POST 요청에 토큰 필요

**해결**: Bridge Bot이 자동으로 `/csrf_token`에서 토큰을 획득하고 헤더에 포함합니다. 403이 지속되면:
```bash
docker compose restart telegram-bridge
```

### Telegram Bot `getUpdates` 결과가 비어있음

**원인**: 봇에게 메시지를 아직 보내지 않음

**해결**: Telegram 앱에서 생성한 봇을 검색 → 아무 메시지 전송 → getUpdates 재호출

### Telegram Bot `Cannot connect to host agent-zero:80`

**원인**: Agent Zero 컨테이너가 아직 시작되지 않았거나, telegram-bridge가 먼저 기동됨

**해결**:
```bash
# 1. Agent Zero 상태 확인
docker ps
docker logs agent-zero --tail 5

# 2. Agent Zero가 정상이면 telegram-bridge만 재시작
docker compose restart telegram-bridge

# 3. Agent Zero가 안 떠있으면 전체 시작
docker compose up -d
```

### Agent Zero 설정이 재시작 시 초기화됨

**원인**: `tmp/settings.json`이 볼륨 마운트되지 않음

**해결**: [13. 설정 영속화](#13-설정-영속화) 참조

---

## 참고 링크

- [Agent Zero GitHub](https://github.com/agent0ai/agent-zero)
- [CLIProxyAPI GitHub](https://github.com/router-for-me/CLIProxyAPI)
- [CLIProxy Documentation](https://help.router-for.me/)
- [Agent Zero Docker Hub](https://hub.docker.com/r/agent0ai/agent-zero)
- [CLIProxy Docker Hub](https://hub.docker.com/r/eceasy/cli-proxy-api)
