# Agent Zero + CLIProxy Docker Setup

Agent Zero AI 에이전트를 Claude Code(CLIProxy) 기반으로 구동하는 Docker Compose 환경입니다.

## Architecture

```
┌─────────────────────┐     ┌──────────────────────┐
│   Agent Zero (UI)   │────▶│  CLIProxy (API)      │
│   localhost:50001   │     │  localhost:8317       │
│                     │     │                      │
│  - LiteLLM          │     │  Claude Code CLI     │
│  - OpenAI compat    │     │  → OpenAI compat API │
└─────────────────────┘     └──────────┬───────────┘
                                       │ OAuth
                                       ▼
                              ┌─────────────────┐
                              │  Claude AI      │
                              │  (Pro/Max 구독)  │
                              └─────────────────┘
```

- **Agent Zero**: AI 에이전트 프레임워크 (LiteLLM 기반, 20+ LLM 지원)
- **CLIProxy**: Claude Code CLI를 OpenAI 호환 API로 노출하는 프록시

## Quick Start

```bash
# 1. 환경변수 설정
cp .env.example .env
# .env 파일에 GITHUB_TOKEN 등 입력

# 2. 컨테이너 시작
docker compose up -d

# 3. Claude OAuth 로그인
docker exec -it cliproxy /CLIProxyAPI/CLIProxyAPI -claude-login

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
└── agent-zero/
    ├── git-init.sh             # Git 인증 자동 설정 스크립트
    ├── prompts/                # 시스템 프롬프트 (커스텀 가능)
    ├── work_dir/               # 에이전트 작업 디렉토리 (clone, 코드 생성)
    ├── memory/                 # 대화 히스토리
    └── logs/                   # 로그
```

## Agent Zero Settings (UI)

| Setting | Value |
|---------|-------|
| Chat model provider | `Other OpenAI compatible` |
| Chat model name | `claude-sonnet-4-6` |
| Chat model API base URL | `http://cliproxy:8317/v1` |
| API Key | `sk-placeholder` |

## Git Push Automation

Agent Zero 컨테이너에서 Git clone/commit/push가 자동으로 됩니다.

1. `.env`에 `GIT_USER_NAME`, `GIT_USER_EMAIL`, `GITHUB_TOKEN` 설정
2. 컨테이너 시작 시 `git-init.sh`가 자동 실행되어 Git 인증 완료
3. Agent Zero에게 "저장소 클론 받아서 작업하고 push해줘" 지시

자세한 내용은 [GUIDE.md](GUIDE.md#11-git-push-자동화) 참조.

## Customizing Prompts

`agent-zero/prompts/` 디렉토리의 `.md` 파일을 수정하여 에이전트 동작을 커스텀할 수 있습니다.

| File | Description |
|------|-------------|
| `agent.system.main.role.md` | 에이전트 역할 정의 |
| `agent.system.behaviour.md` | 행동 규칙 |
| `agent.system.main.solving.md` | 문제 해결 전략 |
| `agent.system.main.communication.md` | 커뮤니케이션 스타일 |

수정 후 반영:
```bash
docker compose up -d agent-zero --force-recreate
```

## Available Models

CLIProxy를 통해 사용 가능한 모델 목록:

| Model | Description |
|-------|-------------|
| `claude-opus-4-6` | 최신 Opus |
| `claude-sonnet-4-6` | 최신 Sonnet |
| `claude-sonnet-4-5-20250929` | Sonnet 4.5 |
| `claude-opus-4-5-20251101` | Opus 4.5 |
| `claude-opus-4-1-20250805` | Opus 4.1 |
| `claude-3-7-sonnet-20250219` | Sonnet 3.7 |
| `claude-3-5-haiku-20241022` | Haiku 3.5 |

## License

MIT
