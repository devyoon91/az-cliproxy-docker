# Agent Zero + CLIProxy Docker Setup

> Agent Zero v1.8 | CLIProxy v6.9+

Agent Zero AI 에이전트를 CLIProxy 기반으로 다양한 LLM과 연동하여 구동하는 Docker Compose 환경입니다.
플러그인, Skills, MCP 서버, Telegram 원격 제어, Git/GitHub CLI 자동화를 포함합니다.

## Architecture

```
┌──────────────┐     ┌─────────────────────┐     ┌──────────────────────┐
│  Telegram    │     │   Agent Zero (UI)   │────▶│  CLIProxy (API)      │
│  (Android)   │     │   localhost:50001   │     │  localhost:8317       │
└──────┬───────┘     └──────────▲──────────┘     └──────────┬───────────┘
       │                        │                           │ OAuth
       ▼                        │                           ▼
┌──────────────┐                │                  ┌─────────────────┐
│  Telegram    │────────────────┘                  │  LLM Provider   │
│  Bridge Bot  │  (양방향 메시지 전달)                │  (OpenAI, etc)  │
│  :8443       │                                   └─────────────────┘
└──────────────┘
```

- **Agent Zero**: AI 에이전트 프레임워크 (LiteLLM 기반, 20+ LLM 지원)
- **CLIProxy**: 다양한 LLM CLI를 OpenAI 호환 API로 노출하는 프록시
- **Telegram Bridge**: 폰에서 Agent Zero 양방향 제어 (알림 + 지시 + 사용량 추적)

## Quick Start

```bash
# 1. 환경변수 설정
cp .env.example .env
# .env 파일에 GITHUB_TOKEN 등 입력

# 2. 컨테이너 시작
docker compose up -d

# 3. LLM Provider OAuth 로그인 (예: OpenAI Codex)
docker exec -it cliproxy /CLIProxyAPI/CLIProxyAPI -codex-login

# 4. API 확인
curl http://localhost:8317/v1/models

# 5. Agent Zero 접속
# http://localhost:50001
```

## Ports

| Service | Port | Description |
|---------|------|-------------|
| Agent Zero | 50001 | Web UI |
| CLIProxy | 8317 | OpenAI-compatible API |
| CLIProxy | 8085 | Management UI |
| CLIProxy | 54545 | OAuth callback |
| Telegram Bridge | 8443 | 알림 Webhook (내부용) |

## Project Structure

```
.
├── docker-compose.yml          # Docker Compose 정의
├── .env                        # 환경변수 (토큰 포함, git 제외)
├── .env.example                # 환경변수 템플릿
├── cliproxy/
│   ├── config.yaml             # CLIProxy 설정
│   ├── auth/                   # OAuth 토큰 저장 (자동생성)
│   └── logs/                   # 로그
├── telegram-bridge/
│   ├── Dockerfile              # Telegram Bot 이미지 빌드
│   └── bot.py                  # 양방향 브릿지 봇
└── agent-zero/
    ├── git-init.sh             # Git 인증 자동 설정 스크립트
    ├── settings.json           # Agent Zero 설정 (영속화)
    ├── prompts/                # 시스템 프롬프트 (커스텀 가능)
    ├── work_dir/               # 에이전트 작업 디렉토리 (clone, 코드 생성)
    ├── memory/                 # 대화 히스토리
    └── logs/                   # 로그
```

## Agent Zero Settings (UI)

| Setting | Value |
|---------|-------|
| Chat model provider | `Other OpenAI compatible` |
| Chat model name | 사용할 모델명 (예: `gpt-4.1`, `o3`) |
| Chat model API base URL | `http://cliproxy:8317/v1` |
| API Key | `sk-placeholder` |

## Telegram Bot (Android Remote Control)

폰에서 Agent Zero를 원격 제어할 수 있습니다. 포트포워딩/VPN 불필요.

```
폰 → Telegram 클라우드 → Bridge Bot(Docker) → Agent Zero
폰 ← Telegram 클라우드 ← Bridge Bot(Docker) ← Agent Zero
```

1. `.env`에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 설정
2. `docker compose up -d --build telegram-bridge`
3. Telegram에서 봇에게 메시지 전송 → Agent Zero가 응답

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 시작 |
| `/status` | Agent Zero 상태 확인 |
| `/new` | 새 대화 시작 |
| 일반 메시지 | Agent Zero에 지시 전달 |

자세한 내용은 [GUIDE.md](GUIDE.md#14-telegram-bot-원격-제어) 참조.

## Git + GitHub CLI Automation

Agent Zero 컨테이너에서 Git clone/commit/push + PR 생성/이슈 관리가 자동으로 됩니다.

1. `.env`에 `GIT_USER_NAME`, `GIT_USER_EMAIL`, `GITHUB_TOKEN` 설정
2. 컨테이너 시작 시 `git-init.sh`가 Git 인증 + GitHub CLI 설치/인증 완료
3. Agent Zero에게 "브랜치 만들어서 작업하고 PR 올려줘" 지시

자세한 내용은 [GUIDE.md](GUIDE.md#11-git--github-cli-자동화) 참조.

## Customizing Prompts

`agent-zero/prompts/` 디렉토리의 `.md` 파일을 수정하여 에이전트 동작을 커스텀할 수 있습니다.

| File | Description |
|------|-------------|
| `agent.system.main.role.md` | 에이전트 역할 정의 |
| `agent.system.behaviour.md` | 행동 규칙 |
| `agent.system.main.solving.md` | 문제 해결 전략 |
| `agent.system.main.coding.md` | 코딩 가이드라인 |

수정 후 반영:
```bash
docker compose up -d agent-zero --force-recreate
```

## v1.8 주요 기능

| 기능 | 설명 |
|------|------|
| **플러그인 시스템** | 모듈식 확장 (Plugin Hub에서 설치, 핫 리로드) |
| **Skills** | SKILL.md 표준 재사용 워크플로우 (Claude Code, Cursor 호환) |
| **Projects** | 프로젝트 컨텍스트 관리 (Git 기반) |
| **Secrets** | `§§secret(name)` 별칭으로 민감 정보 안전 관리 |
| **A2A 통신** | FastA2A 호환 원격 에이전트 간 통신 |
| **notify_user** | 작업 중 사용자 알림 (태스크 종료 없이) |
| **_infection_check** | 프롬프트 인젝션 감지 플러그인 (기본 꺼짐, 토큰 비용) |
| **Memory Hardening** | FAISS SHA-256 검증, 인젝션 필터 강화 |

## Available Models

CLIProxy 또는 직접 API를 통해 사용 가능한 모델 (연결된 프로바이더에 따라 다름):

```bash
# 사용 가능한 모델 확인
curl http://localhost:8317/v1/models
```

Agent Zero는 LiteLLM 기반으로 OpenAI, Anthropic, Google 등 20+ 프로바이더를 지원합니다.

## Documentation

| 문서 | 설명 |
|------|------|
| [GUIDE.md](GUIDE.md) | 전체 구축 가이드 (15단계, 트러블슈팅) |
| [docs/telegram-bot.md](docs/telegram-bot.md) | Telegram Bot 기능 가이드 (명령어, 멀티채팅, 모니터링) |
| [docs/usage.md](docs/usage.md) | Agent Zero 사용 가이드 (한글) |
| [docs/architecture.md](docs/architecture.md) | Agent Zero 아키텍처 (한글) |
| [docs/extensibility.md](docs/extensibility.md) | Agent Zero 확장 가이드 (한글) |
| [docs/scheduler.md](docs/scheduler.md) | Agent Zero 스케줄러 가이드 (한글) |
| [docs/mcp-guide.md](docs/mcp-guide.md) | MCP 서버 연동 가이드 (설정, 활용법, 추가 서버) |

## Preview

![20260403](https://github.com/user-attachments/assets/5dbd7dff-7e80-4d03-8734-c7cafc810087)


## License

MIT
