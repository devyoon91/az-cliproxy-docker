# Agent Zero + CLIProxy Docker Setup

Agent Zero AI 에이전트를 Docker Compose 로 구동하는 환경입니다. LLM 연동은 두 트랙을
지원합니다 — **Track A (Direct API)** 는 LiteLLM 으로 Anthropic/OpenAI 등 표준
API 에 직접 연결하는 경로, **Track B (CLIProxy)** 는 공식 CLI 의 OAuth 토큰을
재활용해 구독 한도를 활용하는 경로입니다.

> **⚠️ 중요 (2026-05 기준)**: Anthropic 이 2026-02-20 약관 개정으로 Claude
> Free/Pro/Max OAuth 토큰의 third-party 도구 사용을 명시적으로 금지했고,
> 2026-04-04 자로 서버측 차단이 완전 enforcement 되었습니다. 추가로 2026-06-15
> 부터 구독 플랜의 programmatic 사용분이 별도 credit pool (Pro $20 / Max5× $100
> / Max20× $200) 로 분리되어 full API 가격으로 과금됩니다. 따라서 **Track B 는
> Claude 모델 대상으로는 더 이상 사용 불가**이며 (약관 위반 + 서버 차단), 이
> 저장소는 **Track A (Direct API) 를 기본 권장 경로** 로 운영합니다. CLIProxy 의
> 다른 벤더 (OpenAI Codex, Google Gemini, Qwen) 경로는 아직 동작하지만 동일한
> 정책 흐름을 따라갈 가능성이 높으니 참고용으로만 두세요.

## Spec

| Component | Version | Image / Detail |
|-----------|---------|----------------|
| Agent Zero | v1.20 | `agent0ai/agent-zero:v1.20` |
| CLIProxy | v6.9.18 | `eceasy/cli-proxy-api:v6.9.18` |
| Telegram Bridge | custom | `python:3.12-slim` 기반 |
| LiteLLM | 1.79.3 | Agent Zero 내장 |
| Python | 3.12 | Agent Zero / Telegram Bridge |
| Node.js | 22.x | Agent Zero 내장 |
| Docker Compose | 3.8 | - |

## Architecture

```
┌──────────────┐     ┌──────────────────────┐
│  Telegram    │     │   Agent Zero (UI)    │
│  (Android)   │     │   localhost:50001    │
└──────┬───────┘     └─┬──────────────┬─────┘
       │ 양방향          │              │
       ▼                ▼              ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Bridge Bot  │  │   Track A    │  │   Track B    │
│   :8443      │  │  Direct API  │  │   CLIProxy   │
└──────────────┘  │  (LiteLLM)   │  │    :8317     │
                  └──────┬───────┘  └──────┬───────┘
                         │ API 키          │ CLI OAuth
                         │ (HTTPS)         │ (Claude 차단됨)
                         ▼                 ▼
                 ┌──────────────────────────────┐
                 │  LLM Provider                │
                 │  (Anthropic / OpenAI / ...)  │
                 └──────────────────────────────┘
```

| 구성 | 설명 |
|------|------|
| **Agent Zero** | AI 에이전트 프레임워크 (LiteLLM 기반, 20+ LLM 지원) |
| **Track A — Direct API ★ 권장** | LiteLLM 이 표준 LLM API 에 직접 연결 (API 키 인증). 정책 영향 없음 |
| **Track B — CLIProxy (Claude 차단됨)** | 공식 CLI 의 OAuth 토큰을 OpenAI 호환 API 로 노출. Anthropic 은 2026-04-04 자로 차단 + 약관 위반, OpenAI/Google/Qwen 경로만 잔존 |
| **Telegram Bridge** | 폰에서 Agent Zero 양방향 제어 (알림 + 지시 + 사용량 추적) |

### LLM 연결 방식 — 두 트랙

| | Track A — Direct API ★ 권장 | Track B — CLIProxy |
|---|---|---|
| **인증** | API 키 (Anthropic Console / OpenAI 등) | 공식 CLI OAuth 토큰 (Codex / Gemini / Qwen — Claude 는 차단됨) |
| **셋업** | API 키 1개로 즉시 시작 | CLIProxy 컨테이너 + CLI 로그인 단계 필요 |
| **비용 모델** | 종량제 (요청당 과금) | 구독 한도 내 사용 (Pro / Max 등) — Anthropic 한정 06-15 부터 별도 credit pool 분리 |
| **상태 (2026-05)** | 표준 경로, 정책 영향 없음 | **Anthropic 차단 완료** (2026-04-04). 타 벤더는 동작하지만 동일 흐름 가능성 |
| **추천 용도** | 운영, 비용 가시성, Claude 사용 | Codex/Gemini/Qwen 구독 한도 활용이 필요한 실험 환경 |

두 트랙은 상호 배타적이지 않으므로 `settings.json` 에서 chat-model 과 util-model
의 base URL 을 다르게 지정해 모델별로 섞을 수 있지만, Claude 호출은 반드시
Track A 로 보내야 합니다.

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
# 1. 공통 설정
cp .env.example .env
cp agent-zero/settings.example.json agent-zero/settings.json
# .env / settings.json 에 토큰·모델명 등 입력

# 2-A. Track A (Direct API) — API 키만 있으면 됨
# .env 또는 agent-zero/settings.json 에 ANTHROPIC_API_KEY / OPENAI_API_KEY 등 추가

# 2-B. Track B (CLIProxy) — CLI OAuth 사용 시
cp cliproxy/config.example.yaml cliproxy/config.yaml
# config.yaml 에 사용할 CLI 종류(Codex/Claude Code 등) 명시

# 3. 컨테이너 시작 (두 트랙 모두 같은 명령)
docker compose up -d --build

# 4. (Track B 만) Provider OAuth 로그인
docker exec -it cliproxy /CLIProxyAPI/CLIProxyAPI -codex-login
curl http://localhost:8317/v1/models   # API 동작 확인

# 5. Agent Zero 접속
# http://localhost:50001
```

자세한 설치 과정은 [GUIDE.md](GUIDE.md) 참조.

## 주요 기능

| 기능 | 설명 | 문서 |
|------|------|------|
| **LLM 연동** | Track A (Direct API, LiteLLM) 또는 Track B (CLIProxy, CLI OAuth), 동시 사용 가능, 20+ 프로바이더 지원 | [GUIDE.md](GUIDE.md) |
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
