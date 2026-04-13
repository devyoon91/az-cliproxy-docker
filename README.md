# Agent Zero + CLIProxy Docker Setup

Agent Zero AI 에이전트를 CLIProxy 기반으로 다양한 LLM과 연동하여 구동하는 Docker Compose 환경입니다.

## Spec

| Component | Version | Image / Detail |
|-----------|---------|----------------|
| Agent Zero | v1.8 | `agent0ai/agent-zero:v1.8` |
| CLIProxy | v6.9.18 | `eceasy/cli-proxy-api:v6.9.18` |
| Telegram Bridge | custom | `python:3.12-slim` 기반 |
| LiteLLM | 1.79.3 | Agent Zero 내장 |
| Python | 3.12 | Agent Zero / Telegram Bridge |
| Node.js | 22.x | Agent Zero 내장 |
| Docker Compose | 3.8 | - |

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

| 구성 | 설명 |
|------|------|
| **Agent Zero** | AI 에이전트 프레임워크 (LiteLLM 기반, 20+ LLM 지원) |
| **CLIProxy** | 다양한 LLM CLI를 OpenAI 호환 API로 노출하는 프록시 |
| **Telegram Bridge** | 폰에서 Agent Zero 양방향 제어 (알림 + 지시 + 사용량 추적) |

## Quick Start

```bash
# 1. 설정 파일 생성
cp .env.example .env
cp cliproxy/config.example.yaml cliproxy/config.yaml
cp agent-zero/settings.example.json agent-zero/settings.json
# 각 파일에 토큰/모델명 입력

# 2. 컨테이너 시작
docker compose up -d --build

# 3. LLM Provider OAuth 로그인
docker exec -it cliproxy /CLIProxyAPI/CLIProxyAPI -codex-login

# 4. API 확인
curl http://localhost:8317/v1/models

# 5. Agent Zero 접속
# http://localhost:50001
```

자세한 설치 과정은 [GUIDE.md](GUIDE.md) 참조.

## 주요 기능

| 기능 | 설명 | 문서 |
|------|------|------|
| **LLM 연동** | CLIProxy 또는 API 직접 연결, 20+ 프로바이더 지원 | [GUIDE.md](GUIDE.md) |
| **Telegram 원격 제어** | 폰에서 양방향 지시, 실시간 모니터링, 멀티채팅 | [telegram-bot.md](docs/telegram-bot.md) |
| **Git + GitHub CLI** | clone/commit/push + PR 생성/이슈 관리 자동화 | [GUIDE.md](GUIDE.md#11-git--github-cli-자동화) |
| **에이전트 프로필** | 서브 에이전트로 전문가 팀 구성 ([az-agent-config](https://github.com/devyoon91/az-agent-config) fork 활용) | [agent-profiles.md](docs/agent-profiles.md) |
| **MCP 서버** | Sequential Thinking, Git, Fetch 등 도구 확장 | [mcp-guide.md](docs/mcp-guide.md) |
| **스케줄러** | cron 기반 반복 실행, 예약 실행, 수동 실행 | [scheduler.md](docs/scheduler.md) |
| **플러그인** | 18개 내장 플러그인, Plugin Hub, 핫 리로드 | [usage.md](docs/usage.md) |
| **토큰 사용량 추적** | /usage 명령, 일일 리포트, 비용 모니터링 | [optimization.md](docs/optimization.md) |
| **백업/복원** | 로컬 스크립트(full/config/light) + Telegram 원격 백업 | [backup.md](docs/backup.md) |
| **프롬프트 커스텀** | 코딩 규칙, 한국어, 작업 이력 관리 | [GUIDE.md](GUIDE.md#12-프롬프트-커스터마이징) |
| **개인화 분리** | 하네스 킷과 개인 설정을 별도 저장소로 관리 ([az-agent-config](https://github.com/devyoon91/az-agent-config)) | [GUIDE.md](GUIDE.md#16-팁-개인화-저장소-분리) |

## Project Structure

```
.
├── docker-compose.yml
├── .env.example                    # 환경변수 템플릿
├── scripts/
│   ├── backup.sh                   # 백업 (light/config/full)
│   └── restore.sh                  # 복원
├── cliproxy/
│   ├── config.example.yaml         # CLIProxy 설정 템플릿
│   └── auth/                       # OAuth 토큰 (자동생성)
├── telegram-bridge/
│   ├── Dockerfile
│   └── bot.py                      # 양방향 브릿지 봇
├── agent-zero/
│   ├── settings.example.json       # Agent Zero 설정 템플릿
│   ├── git-init.sh                 # Git + gh CLI 자동 설정
│   ├── prompts/                    # 시스템 프롬프트
│   ├── agents/                     # 에이전트 프로필
│   │   ├── developer/              # 풀스택 개발
│   │   ├── reviewer/               # 코드 리뷰
│   │   └── devops/                 # 인프라/배포
│   ├── work_dir/                   # 작업 디렉토리
│   └── memory/                     # 에이전트 메모리
└── docs/                           # 한글 문서
```

## Ports

| Service | Port | Description |
|---------|------|-------------|
| Agent Zero | 50001 | Web UI |
| CLIProxy | 8317 | OpenAI-compatible API |
| CLIProxy | 8085 | Management UI |
| CLIProxy | 54545 | OAuth callback |
| Telegram Bridge | 8443 | 알림/추적 Webhook |

## Documentation

| 문서 | 설명 |
|------|------|
| [GUIDE.md](GUIDE.md) | 전체 구축 가이드 (17단계, 트러블슈팅) |
| [docs/telegram-bot.md](docs/telegram-bot.md) | Telegram Bot 기능 가이드 |
| [docs/agent-profiles.md](docs/agent-profiles.md) | 에이전트 프로필 가이드 |
| [docs/mcp-guide.md](docs/mcp-guide.md) | MCP 서버 연동 가이드 |
| [docs/scheduler.md](docs/scheduler.md) | 스케줄러 가이드 |
| [docs/backup.md](docs/backup.md) | 백업 및 복원 가이드 |
| [docs/optimization.md](docs/optimization.md) | 비용 최적화 가이드 |
| [docs/usage.md](docs/usage.md) | Agent Zero 사용 가이드 (한글) |
| [docs/architecture.md](docs/architecture.md) | Agent Zero 아키텍처 (한글) |
| [docs/extensibility.md](docs/extensibility.md) | Agent Zero 확장 가이드 (한글) |

## Preview

![20260403](https://github.com/user-attachments/assets/5dbd7dff-7e80-4d03-8734-c7cafc810087)

## License

MIT
