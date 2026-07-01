# Agent Zero Docker Setup

Agent Zero AI 에이전트를 Docker Compose 로 구동하는 환경입니다. LLM 연동은
LiteLLM 을 통해 Anthropic/OpenAI 등 표준 API 에 공식 API 키로 직접 연결합니다.
Claude 를 쓸 때는 프롬프트 캐싱이 자동 적용돼 반복 호출 입력 비용이 크게
줄어듭니다.

## Spec

| Component | Version | Image / Detail |
|-----------|---------|----------------|
| Agent Zero | v2.1 | `agent0ai/agent-zero:v2.1` |
| Telegram Bridge | custom | `python:3.12-slim` 기반 |
| LiteLLM | 1.88.1 | Agent Zero 내장 |
| Python | 3.12 | Agent Zero / Telegram Bridge |
| Node.js | 22.x | Agent Zero 내장 |
| Docker Compose | 3.8 | - |

## Architecture

```
┌──────────────┐     ┌──────────────────────┐
│  Telegram    │     │   Agent Zero (UI)    │
│  (Android)   │     │   localhost:50001    │
└──────┬───────┘     └──────────┬───────────┘
       │ 양방향                    │
       ▼                         ▼
┌──────────────┐        ┌──────────────┐
│  Bridge Bot  │        │  Direct API  │
│   :8443      │        │  (LiteLLM)   │
└──────────────┘        └──────┬───────┘
                               │ API 키 (HTTPS)
                               ▼
                 ┌──────────────────────────────┐
                 │  LLM Provider                │
                 │  (Anthropic / OpenAI / ...)  │
                 └──────────────────────────────┘
```

| 구성 | 설명 |
|------|------|
| **Agent Zero** | AI 에이전트 프레임워크 (LiteLLM 기반, 20+ LLM 지원) |
| **LLM 연동 (Direct API)** | LiteLLM 이 표준 LLM API 에 공식 키로 직접 연결. Claude 사용 시 프롬프트 캐싱 자동 적용 |
| **Telegram Bridge** | 폰에서 Agent Zero 양방향 제어 (알림 + 지시 + 사용량 추적) |

### 로컬 커스텀 마운트 (`docker-compose.override.yml`)

개인화 영역(예: sibling [az-agent-config](https://github.com/devyoon91/az-agent-config-template)
의 스킬/에이전트, 사내 전용 프롬프트 등)을 컨테이너에 임시로 마운트할 때는
[`docker-compose.override.yml`](https://docs.docker.com/compose/multiple-compose-files/) 을
사용합니다. Docker Compose 표준 기능으로 main `docker-compose.yml` 과 자동 머지됩니다
— 별도 플래그 불필요.

```yaml
# docker-compose.override.yml — .gitignore 됨, 추적 안 함
services:
  agent-zero:
    volumes:
      - ../az-agent-config/skills:/a0/usr/skills:ro
```

**정책** ([issue #53](https://github.com/devyoon91/az-cliproxy-docker/issues/53)):

- main `docker-compose.yml` 에 sibling 디렉토리(`../...`) 마운트 **추가 금지** — 다른
  환경/사용자에서 해당 디렉토리가 없으면 `:ro` mount source missing 으로 부팅 실패.
- 개인화·테스트 마운트는 `docker-compose.override.yml` 에만, 항상 gitignored.
- 테스트 종료 시 파일 삭제 후 `docker compose up -d --force-recreate <service>` 로 롤백.
- 정책은 [`scripts/preflight.sh`](scripts/preflight.sh) 가 자동 enforce — CI 에서
  PR 시점에 sibling mount 가 발견되면 fail. 로컬에서도 `./scripts/preflight.sh` 로
  체크 가능.

## Quick Start

```bash
# 1. 설정
cp .env.example .env
cp agent-zero/settings.example.json agent-zero/settings.json
# .env / settings.json 에 API 키·모델명 입력
# (ANTHROPIC_API_KEY 권장 — settings.json 의 provider 는 anthropic 기본값)

# 2. 컨테이너 시작
docker compose up -d --build

# 3. Agent Zero 접속
# http://localhost:50001
```

자세한 설치 과정은 [GUIDE.md](GUIDE.md) 참조.

## 주요 기능

| 기능 | 설명 | 문서 |
|------|------|------|
| **LLM 연동** | LiteLLM 기반 Direct API 연결, 20+ 프로바이더 지원 (Anthropic 권장 — 프롬프트 캐싱 자동) | [GUIDE.md](GUIDE.md) |
| **Telegram 원격 제어** | 폰에서 양방향 지시, 실시간 모니터링, 멀티채팅 | [telegram-bot.md](docs/telegram-bot.md) |
| **Git + GitHub CLI** | clone/commit/push + PR 생성/이슈 관리 자동화 | [GUIDE.md](GUIDE.md#11-git--github-cli-자동화) |
| **에이전트 프로필** | 서브 에이전트로 전문가 팀 구성 ([az-agent-config-template](https://github.com/devyoon91/az-agent-config-template) fork 활용) | [agent-profiles.md](docs/agent-profiles.md) |
| **MCP 서버** | Sequential Thinking, Git, Fetch 등 도구 확장 | [mcp-guide.md](docs/mcp-guide.md) |
| **스케줄러** | cron 기반 반복 실행, 예약 실행, 수동 실행 | [scheduler.md](docs/scheduler.md) |
| **플러그인** | 18개 내장 플러그인, Plugin Hub, 핫 리로드 | [usage.md](docs/usage.md) |
| **토큰 사용량 추적** | `/usage` (실시간) + `/today` `/week` `/tasks` (영구), `by:model` / `by:profile` breakdown | [optimization.md](docs/optimization.md) |
| **예산 알림** | `/budget day|week N` 한도 설정, 80/100/150% 단계별 자동 Telegram 알림 | [telegram-bot.md](docs/telegram-bot.md) |
| **가격 추적** | `/pricing` LiteLLM 가격 일일 스냅샷 + drift 감지 (매일 00:30 KST) | [telegram-bot.md](docs/telegram-bot.md) |
| **웹 대시보드** | Chart.js 차트 (일별/모델별/scatter), 자동 갱신, `DASHBOARD_TOKEN` 인증 | [README#web-dashboard](#web-dashboard-선택) |
| **백업/복원** | 로컬 스크립트(full/config/light) + Telegram 원격 백업 | [backup.md](docs/backup.md) |
| **품질 평가 (eval)** | 골든셋 10개 + LLM-as-judge 채점 + `/eval` 명령 + `/dashboard/eval` 추이 + CI 회귀 게이트 | [eval.md](docs/eval.md) |
| **채팅 PDF 추출** | 사이드바 드롭다운 버튼 한 번으로 활성 채팅을 PDF 로 다운로드 (한국어 폰트 포함) | [chat_pdf_export](agent-zero/usr-plugins/chat_pdf_export/README.md) |
| **프롬프트 커스텀** | 코딩 규칙, 한국어, 작업 이력 관리 | [GUIDE.md](GUIDE.md#12-프롬프트-커스터마이징) |
| **개인화 분리** | 하네스 킷과 개인 설정을 별도 저장소로 관리 ([az-agent-config-template](https://github.com/devyoon91/az-agent-config-template)) | [GUIDE.md](GUIDE.md#16-팁-개인화-저장소-분리) |

## Project Structure

```
.
├── docker-compose.yml
├── .env.example                    # 환경변수 템플릿
├── scripts/
│   ├── backup.sh                   # 백업 (light/config/full)
│   └── restore.sh                  # 복원
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
| Telegram Bridge | 8443 | 알림/추적 Webhook · /dashboard (선택, 텔레그램 토큰 없이도 단독 사용 가능) |

### Web Dashboard (선택)

`telegram-bridge` 의 8443 포트에서 비용/사용량 차트 페이지를 제공합니다.

**활성화**: `.env` 에 `DASHBOARD_TOKEN=...` 추가 후 컨테이너 재기동.
빈 값/미설정 시 `/dashboard` 와 `/api/stats` 는 404 로 차단됩니다.

**접속**:
```
http://localhost:8443/dashboard?token=<TOKEN>
curl "http://localhost:8443/api/stats?range=30d&token=<TOKEN>"
curl -H "X-Dashboard-Token: <TOKEN>" http://localhost:8443/api/stats
```

도커 호스트 외부에서 접근하려면 `docker-compose.yml` 의 `ports:` 매핑 또는
SSH 포트포워딩(`ssh -L 8443:localhost:8443 <host>`)을 사용하세요.

뷰: 일별 비용 (30일), 모델별 비용 (7일 도넛), 태스크 소요 vs 비용
scatter (프로파일별 색상), 윈도우 합계.

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
| [docs/eval.md](docs/eval.md) | 골든셋 기반 품질 평가 가이드 (작성 / 실행 / CI / 트러블슈팅) |
| [docs/usage.md](docs/usage.md) | Agent Zero 사용 가이드 (한글) |
| [docs/architecture.md](docs/architecture.md) | Agent Zero 아키텍처 (한글) |
| [docs/extensibility.md](docs/extensibility.md) | Agent Zero 확장 가이드 (한글) |
| [docs/plugins.md](docs/plugins.md) | usr-plugins 작성 가이드 (Alpine store, 슬롯, 흔한 실수) |

## Preview

**AgentZero & Telegram**

![20260403](https://github.com/user-attachments/assets/5dbd7dff-7e80-4d03-8734-c7cafc810087)

**AZ Cost Dashboard**

<img width="944" height="958" alt="image" src="https://github.com/user-attachments/assets/031552cf-fc38-458e-b925-93c9b08b796f" />


## License

MIT
