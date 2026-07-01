# Agent Zero 구축 가이드

> Agent Zero v2.1 | Telegram Bridge (custom)

이 문서는 [README.md](README.md) 를 읽었다는 가정 하에, 신규 사용자가 부팅까지
실수 없이 도달하기 위한 **walkthrough** 입니다. README 가 "이 저장소가 무엇을
하는가" 를 답한다면, GUIDE 는 "정확히 무엇을 입력하고, 무엇을 확인해야 하는가"
를 답합니다.

**중요한 차이점**: 임베디드 `docker-compose.yml` 예제는 두지 않습니다 —
저장소의 실제 [`docker-compose.yml`](docker-compose.yml) 이 권위 있는 source
이고, 임베디드 예제는 시간이 지나며 drift 가 누적되기 때문입니다. 환경변수도
마찬가지로 [`.env.example`](.env.example) 이 권위 있는 source 이고, GUIDE 는
"어떤 항목을 채워야 하는가" 만 안내합니다.

---

## 목차

1. [LLM 프로바이더 선택](#1-llm-프로바이더-선택)
2. [사전 준비](#2-사전-준비)
3. [저장소 clone + 환경 설정](#3-저장소-clone--환경-설정)
4. [컨테이너 부팅](#4-컨테이너-부팅)
5. [API 동작 확인](#5-api-동작-확인)
6. [Agent Zero UI 모델 설정](#6-agent-zero-ui-모델-설정)
7. [Telegram Bot 연동](#7-telegram-bot-연동)
8. [(선택) Web Dashboard 활성화](#8-선택-web-dashboard-활성화)
9. [(선택) 개인화 저장소 분리](#9-선택-개인화-저장소-분리)
10. [핵심 기능 cross-link](#10-핵심-기능-cross-link)
11. [트러블슈팅](#11-트러블슈팅)

---

## 1. LLM 프로바이더 선택

이 환경은 LiteLLM 을 통해 표준 LLM API 에 공식 키로 직접 연결합니다. 부팅 전에
어느 프로바이더를 쓸지 정하면 `.env` 와 `settings.json` 의 값이 결정됩니다.

| 프로바이더 | 인증 | 비고 |
|---|---|---|
| **Anthropic ★ 권장** | `ANTHROPIC_API_KEY` | 프롬프트 캐싱 자동 적용 — 반복 호출 입력 비용 대폭 절감 |
| **OpenAI** | `OPENAI_API_KEY` | 표준 종량제 |
| **기타 OpenAI 호환** | 엔드포인트별 키 | `settings.json` provider `"other"` + base URL 지정 |

`settings.json` 의 `chat_model_provider` 와 `.env` 에 넣은 키가 일치해야 합니다.
chat-model 과 util-model 을 서로 다른 프로바이더로 섞어 쓸 수도 있습니다.

---

## 2. 사전 준비

| 항목 | 비고 |
|------|------|
| Docker Desktop (또는 Docker Engine + Compose v2) | 실행 가능 상태 |
| API 키 (Anthropic / OpenAI 등) | `.env` 에 입력 |
| GitHub Personal Access Token (PAT) — `repo` scope | Agent Zero 컨테이너에서 `git push`, `gh pr create` 자동화에 사용 |
| Telegram Bot Token + Chat ID | [§7](#7-telegram-bot-연동) 에서 발급. **양방향 제어가 핵심 기능이라 권장** (미설정 시 [§8](#8-선택-web-dashboard-활성화) 대시보드만 단독 사용 가능) |

---

## 3. 저장소 clone + 환경 설정

### 3-1. clone

```bash
git clone https://github.com/devyoon91/az-cliproxy-docker
cd az-cliproxy-docker
```

### 3-2. 환경변수 — `.env`

`.env.example` 을 복사한 뒤, 4개 섹션 중 **본인 프로바이더에 맞는 항목만** 채웁니다.

```bash
cp .env.example .env
```

`.env.example` 에 모든 옵션이 주석으로 분류되어 있으니 그대로 따라가면 됩니다:

| 섹션 | 항목 | 필수성 |
|------|------|--------|
| **1) LLM 프로바이더** | Option A (Anthropic) / B (OpenAI) 중 택 1 | 필수 |
| **2) Git** | `GIT_USER_NAME`, `GIT_USER_EMAIL`, `GITHUB_TOKEN` | 필수 |
| **3) Telegram** | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 선택 — 미설정 시 봇 polling 비활성, 대시보드/webhook 단독 동작 ([§7](#7-telegram-bot-연동) 발급) |
| **4) Web Dashboard** | `DASHBOARD_TOKEN` | 선택 ([§8](#8-선택-web-dashboard-활성화)) |

> `.env` 는 `.gitignore` 에 등록되어 있어 저장소에 올라가지 않습니다.
> `.env.example` 의 주석에 모델명·base URL·비용표 등이 포함되어 있으니
> 옵션 선택 시 같이 읽으세요.

### 3-3. Agent Zero 설정 — `agent-zero/settings.json`

```bash
cp agent-zero/settings.example.json agent-zero/settings.json
```

`chat_model_provider` 값이 `.env` 에서 선택한 프로바이더와 **반드시 일치** 해야 합니다:

| `.env` 옵션 | `settings.json` → `chat_model_provider` |
|-------------|------------------------------------------|
| Option A (Anthropic) | `"anthropic"` |
| Option B (OpenAI) | `"openai"` |

---

## 4. 컨테이너 부팅

```bash
docker compose up -d --build
```

서비스 2개가 같은 `az-net` 브릿지 네트워크에 뜹니다 — 자세한 mount/port 구성은
[`docker-compose.yml`](docker-compose.yml) 참조.

| 서비스 | 포트 | 역할 |
|--------|------|------|
| `agent-zero` | 50001 | 웹 UI (메인 대시보드) |
| `telegram-bridge` | 8443 | Telegram Bot + 알림 webhook + (선택) /dashboard |

부팅 확인:

```bash
docker ps                       # 2개 컨테이너 모두 Up 상태
docker logs agent-zero --tail 20
docker logs telegram-bridge --tail 20
```

---

## 5. API 동작 확인

`.env` 의 API 키만 정확하면 별도 확인 없이 동작합니다. Agent Zero UI
(`http://localhost:50001`) 에서 첫 대화를 시도해 응답이 오면 OK.

---

## 6. Agent Zero UI 모델 설정

`http://localhost:50001` 접속.

모델 설정은 **Plugins → `_model_config` 플러그인** 에서 관리합니다 (UI 좌측
사이드바 → Plugins). `settings.json` 의 값과 일치시키세요:

| 항목 | Anthropic | OpenAI |
|------|-----------|--------|
| **Chat Model Provider** | `Anthropic` | `OpenAI` |
| **Chat Model API Base** | (비워둠) | (비워둠) |
| **Chat Model API Key** | `${ANTHROPIC_API_KEY}` | `${OPENAI_API_KEY}` |
| **Utility Model** | 동일 패턴 | 동일 패턴 |

설정 파일 위치 (호스트 마운트됨): `agent-zero/usr-plugins/_model_config/config.json`.
`docker compose up --force-recreate` 로 재생성해도 보존됩니다.

---

## 7. Telegram Bot 연동

폰에서 Agent Zero 를 양방향 제어하는 커스텀 브릿지입니다. 포트포워딩/VPN 없이
동작 — Telegram 클라우드가 중계합니다.

> **선택사항**: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 가 비어 있으면
> 봇 polling 만 비활성화되고 `telegram-bridge` 컨테이너의 aiohttp 서버
> (`/dashboard`, `/api/stats`, `/track` 등) 는 그대로 기동됩니다. 보조 PC 등에서
> 비용 대시보드만 단독 사용하고 싶다면 이 섹션을 건너뛰고 [§8](#8-선택-web-dashboard-활성화)
> 만 진행해도 됩니다.

> **참고**: Agent Zero 에 `_telegram_integration` 내장 플러그인이 있지만,
> 기본 알림 + `/project`, `/config` 정도만 지원합니다. 이 저장소의 커스텀 브릿지가
> **웹 채팅 실시간 모니터링, 멀티채팅, 토큰/비용 추적, 예산 알림, 가격 drift 감지,
> 문서 열람, 백업** 등 풍부한 기능을 제공하니 **내장 플러그인은 끄고 커스텀
> 브릿지를 사용하세요.**

### 7-1. 봇 생성 + 토큰 발급

1. Telegram 앱에서 `@BotFather` 검색 → 대화 시작
2. `/newbot` → 봇 이름 + username 설정
3. **Bot Token** 수령 (형식: `123456789:ABCdef...`)
4. 생성된 봇에게 아무 메시지 1건 전송 (채팅방 활성화)
5. 브라우저에서 `https://api.telegram.org/bot{TOKEN}/getUpdates` 열기
6. 응답에서 `chat.id` 값을 메모 → **Chat ID**

### 7-2. `.env` 에 입력

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### 7-3. 재기동

```bash
docker compose up -d --force-recreate telegram-bridge
```

### 7-4. 핵심 명령

자세한 명령 reference 와 동작 원리는 [docs/telegram-bot.md](docs/telegram-bot.md)
에. 자주 쓰는 것:

| 그룹 | 명령 |
|------|------|
| 대화 | 일반 메시지 → AZ 지시, `/new`, `/chats`, `/switch [N]` |
| 모니터 | `/monitor_on`/`off`, `/track_chat_on`/`off`, `/verbose_on`/`off` |
| 비용 | `/usage`, `/today`, `/week`, `/tasks [N]`, `/budget [day\|week] [USD]`, `/pricing` |
| 파일 | `/logs`, `/docs`, `/backup` |
| 상태 | `/status`, `/help` |

### 7-5. 알림 webhook (외부 → Telegram)

Agent Zero 가 작업 완료 시 알림을 Telegram 으로 보내도록:

```bash
curl -X POST http://telegram-bridge:8443/notify \
     -H 'Content-Type: application/json' \
     -d '{"text": "작업 완료!"}'
```

Agent Zero 에게 지시할 때:
> "작업 완료되면 curl 로 http://telegram-bridge:8443/notify 에 결과 알려줘"

---

## 8. (선택) Web Dashboard 활성화

`telegram-bridge:8443` 에 비용/사용량 차트 페이지가 함께 떠 있습니다 (Chart.js
기반). 텔레그램 텍스트 명령 (`/today`, `/week`, `/usage`) 외에 시각화가 필요할
때 사용하세요.

### 활성화

```bash
echo "DASHBOARD_TOKEN=$(openssl rand -hex 16)" >> .env
docker compose up -d --force-recreate telegram-bridge
```

`DASHBOARD_TOKEN` 이 빈 값/미설정이면 `/dashboard` 와 `/api/stats` 둘 다
**404** 로 차단됩니다.

### 접속

```bash
# 브라우저
http://localhost:8443/dashboard?token=<TOKEN>

# JSON API (헤더 인증 권장 — 액세스 로그에 토큰 안 남음)
curl -H "X-Dashboard-Token: <TOKEN>" http://localhost:8443/api/stats
```

차트: 일별 비용 (30일 bar) · 모델별 비용 (7일 doughnut) · 태스크 소요시간 vs
비용 (scatter, 프로파일별 색상) · 윈도우 합계.

### 원격 접속

8443 은 호스트 로컬에만 바인딩됩니다. 원격에서 보려면 SSH 포트포워딩을
사용하세요:

```bash
ssh -L 8443:localhost:8443 <host>
```

---

## 9. (선택) 개인화 저장소 분리

**이 저장소 = 하네스 킷 (공용)**, 회사/팀/개인 고유 내용은 **별도 저장소**에서
관리하는 것을 권장합니다. 이유: 하네스 킷 업데이트 시 충돌 없고, 개인화 내용을
private 으로 유지 가능.

### 패턴

```
az-cliproxy-docker/                ← 하네스 킷 (이 저장소)
my-agent-config/                   ← 개인화 (별도 저장소)
  ├── agents/                      ← 프로필 (서브 에이전트)
  ├── skills/                      ← 스킬 (SKILL.md 기반 지침서)
  ├── knowledge/                   ← 코딩 표준, 아키텍처 문서
  ├── templates/                   ← 프로젝트 보일러플레이트
  └── instruments/                 ← 커스텀 인스트루먼트
```

템플릿: [az-agent-config-template](https://github.com/devyoon91/az-agent-config-template).

### 마운트는 `docker-compose.override.yml` 로

main `docker-compose.yml` 에 sibling 디렉토리 (`../my-agent-config/...`)
마운트를 **추가하지 마세요**. 다른 환경/사용자에서 디렉토리가 없으면 부팅 실패
([issue #53](https://github.com/devyoon91/az-cliproxy-docker/issues/53)).

대신 [`docker-compose.override.yml`](https://docs.docker.com/compose/multiple-compose-files/)
을 사용 — Docker Compose 가 `docker-compose.yml` 과 자동 머지하고, 이 파일은
gitignored 입니다:

```yaml
# docker-compose.override.yml — 추적 안 함
services:
  agent-zero:
    volumes:
      # ── 스킬 (✅ 통째 마운트 안전) ──
      - ../my-agent-config/skills:/a0/usr/skills:ro
      # ── 프로필 (⚠️ 반드시 개별 마운트) ──
      - ../my-agent-config/agents/my-reviewer:/a0/usr/agents/my-reviewer:ro
      # ── 지식베이스 (⚠️ 서브 디렉토리로) ──
      - ../my-agent-config/knowledge:/a0/knowledge/custom/team:ro
```

> **마운트 주의**: `agents/` 는 통째로 마운트하면 내장 프로필 (developer,
> researcher 등) 이 덮어씌워집니다. **개별 마운트** 필수. `skills/` 는
> `usr/skills/` 가 내장과 분리되어 있어 통째 마운트 안전.

`./scripts/preflight.sh` 가 sibling 마운트 정책을 자동 검사합니다 — CI 에서도
PR 시점에 발견되면 fail.

### Profile vs Skill

| | Profile (`agents/`) | Skill (`skills/`) |
|---|---|---|
| 정체 | 독립된 전문가 에이전트 | 지침서/매뉴얼 (SKILL.md) |
| 실행 | 별도 에이전트 인스턴스 생성 | 현재 에이전트에 지침 추가 |
| 활성화 | `call_subordinate` 명시 호출 | `trigger_patterns` 키워드 자동 매칭 |
| 적합한 경우 | 깊은 도메인 지식, 별도 인격 필요 | 절차/체크리스트, 가벼운 지침 |

자세한 가이드: [docs/agent-profiles.md](docs/agent-profiles.md),
[docs/extensibility.md](docs/extensibility.md).

---

## 10. 핵심 기능 cross-link

GUIDE 본문은 부팅까지의 walkthrough 에 집중하기 때문에, 운영 단계의 깊은 주제는
별도 문서로 분리되어 있습니다.

| 주제 | 문서 |
|------|------|
| **Telegram Bot 명령 reference + 동작 원리** | [docs/telegram-bot.md](docs/telegram-bot.md) |
| **task_report 시스템** — 태스크 단위 비용/시간/모델 영속 기록 (`/today`, `/week`, `/tasks` 의 데이터 source) | [docs/usage.md](docs/usage.md) · [docs/optimization.md](docs/optimization.md) |
| **에이전트 프로필 + 서브 에이전트 호출** | [docs/agent-profiles.md](docs/agent-profiles.md) |
| **MCP 서버** (Sequential Thinking, Git, Fetch 등) | [docs/mcp-guide.md](docs/mcp-guide.md) |
| **스케줄러** (cron 기반, 예약 실행) | [docs/scheduler.md](docs/scheduler.md) |
| **백업/복원** (full / config / light, Telegram 원격 백업) | [docs/backup.md](docs/backup.md) |
| **비용 최적화** (프롬프트 캐싱, 모델 분리, 예산) | [docs/optimization.md](docs/optimization.md) |
| **AZ 아키텍처 + 확장점** | [docs/architecture.md](docs/architecture.md) · [docs/extensibility.md](docs/extensibility.md) |
| **번들된 플러그인 — chat_pdf_export** (사이드바 PDF 내보내기, 한국어 폰트 포함) | [agent-zero/usr-plugins/chat_pdf_export/README.md](agent-zero/usr-plugins/chat_pdf_export/README.md) |
| **번들된 플러그인 — dashboard_link** (UI 에서 대시보드 열기) | [agent-zero/usr-plugins/dashboard_link/README.md](agent-zero/usr-plugins/dashboard_link/README.md) |
| **프롬프트 캐싱** (Anthropic 자동 적용 메커니즘) | [docs/prompt-caching.md](docs/prompt-caching.md) |

### 호스트 파일시스템 접근 (선택)

기본적으로 Agent Zero 는 `work_dir/` 만 접근 가능합니다. 호스트 PC 의 프로젝트
폴더에 직접 접근하려면 `docker-compose.override.yml` 에 볼륨을 추가하세요 (main
yml 수정 X — §9 참조):

```yaml
services:
  agent-zero:
    volumes:
      - C:/Users/myname/projects:/a0/work_dir/host-projects   # Windows
      - /home/myname/projects:/a0/work_dir/host-projects      # Linux/Mac
      # 중요한 디렉토리는 :ro 권장
      - C:/Users/myname/documents:/a0/work_dir/docs:ro
```

### 프롬프트 커스터마이징

`docker-compose.yml` 이 `./agent-zero/prompts:/a0/prompts` 를 마운트하므로
호스트의 prompts 디렉토리를 수정하면 컨테이너 재기동 시 반영됩니다.

```bash
# 첫 추출 (선택)
docker cp agent-zero:/a0/prompts ./agent-zero/prompts

# 수정 후 반영
docker compose up -d agent-zero --force-recreate
```

핵심 파일:

| File | Description |
|------|-------------|
| `agent.system.main.role.md` | **에이전트 역할 정의 (가장 핵심)** |
| `agent.system.behaviour.md` | 행동 규칙 |
| `agent.system.main.solving.md` | 문제 해결 전략 |
| `agent.system.main.communication.md` | 커뮤니케이션 스타일 |

---

## 11. 트러블슈팅

### Telegram Bot `CSRF token missing or invalid` (403)

Agent Zero 의 CSRF 보호 — 모든 POST 에 토큰 필요. Bridge 가 자동으로 토큰을
획득하지만, 지속되면:

```bash
docker compose restart telegram-bridge
```

### Telegram Bot `getUpdates` 결과가 비어있음

봇에게 아직 메시지를 보내지 않은 상태 → Telegram 앱에서 봇 검색 → 아무 메시지
1건 전송 → 다시 호출.

### Telegram Bot `Cannot connect to host agent-zero:80`

Agent Zero 가 아직 안 떴거나 telegram-bridge 가 먼저 기동된 경우. 둘 다 부팅
확인 후 bridge 만 재시작:

```bash
docker ps                                   # agent-zero 상태 확인
docker compose restart telegram-bridge      # bridge 만 재시작
```

### Agent Zero 설정이 재시작 시 초기화됨

`docker-compose.yml` 이 `./agent-zero/settings.json:/a0/usr/settings.json`
와 `./agent-zero/usr-plugins:/a0/usr/plugins` 를 마운트하므로 정상이면 보존됩니다.
초기화된다면 호스트 측 파일이 비어있거나 마운트 권한 문제 — `docker logs
agent-zero` 확인.

### `force-recreate` 후 모델 설정이 사라짐

`docker-compose.yml` 의 `usr-plugins` 마운트가 `/a0/usr/plugins/_model_config/config.json`
을 보존합니다. 이 마운트가 제거된 상태로 `force-recreate` 하면 default_config.yaml
의 (잘못된) 기본값으로 떨어지니, 마운트를 복구한 뒤 재기동하세요.

---

## 참고 링크

- [Agent Zero GitHub](https://github.com/agent0ai/agent-zero)
- [az-agent-config-template](https://github.com/devyoon91/az-agent-config-template) — 개인화 저장소 템플릿
