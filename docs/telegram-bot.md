# Telegram Bot 기능 가이드

Agent Zero를 Android/iOS 폰에서 원격 제어하는 커스텀 Telegram Bot 브릿지입니다.

> **중요**: Agent Zero v1.8에 `_telegram_integration` 내장 플러그인이 있지만, 기본 알림만 지원합니다. 이 커스텀 봇은 웹 채팅 모니터링, 멀티채팅, 토큰 추적 등 훨씬 풍부한 기능을 제공합니다. **내장 플러그인은 끄고 이 커스텀 봇을 사용하세요.**

---

## 동작 원리

```
폰 (Telegram 앱)
    ↕ Telegram 클라우드 서버 (중계)
Bridge Bot (Docker 컨테이너, polling)
    ↕ Agent Zero HTTP API
Agent Zero (Docker 컨테이너)
```

- 폰과 PC가 직접 통신하지 않음 — Telegram 클라우드가 중계
- 포트포워딩/VPN 불필요, 인터넷만 되면 동작
- `TELEGRAM_CHAT_ID`로 본인만 봇 사용 가능 (다른 사용자 차단)

---

## 명령어 목록

### 대화

| 명령 | 설명 |
|------|------|
| `/new` | 새 대화 시작 (컨텍스트 초기화) |
| `/chats` | Agent Zero의 활성 채팅 목록 조회 |
| `/switch [번호]` | 특정 채팅으로 전환 (예: `/switch 2`) |
| `/logs` | 현재 채팅의 전체 로그를 파일(JSON + TXT)로 전송 |
| `/docs` | 프로젝트 문서 목록 조회 |
| `/docs [번호]` | 특정 문서 열람 (짧으면 텍스트, 길면 파일 전송) |
| `/docs all` | 전체 문서 파일 다운로드 |
| 일반 메시지 | Agent Zero에 지시 전달 → 응답은 모니터를 통해 자동 수신 |

### 모니터링

| 명령 | 설명 |
|------|------|
| `/monitor_on` | 웹 채팅 모니터링 켜기 (현재 시점부터, 이전 히스토리 전송 안함) |
| `/monitor_off` | 웹 채팅 모니터링 끄기 |
| `/follow_on` | 자동 추적 켜기 — 웹에서 채팅 전환 시 모니터가 따라감 (기본: 켜짐) |
| `/follow_off` | 자동 추적 끄기 — 현재 채팅만 고정 추적 |

### 상태/비용

| 명령 | 설명 |
|------|------|
| `/status` | Agent Zero 연결 상태, 모니터링/추적 상태, 현재 채팅 ID |
| `/usage` | 오늘 토큰 사용량, 예상 비용, 최근 7일 기록 |
| `/start` | 봇 시작 안내 |
| `/help` | 전체 명령어 목록 |

---

## 모니터링 알림 형식

웹 UI 또는 Telegram에서 Agent Zero와 대화할 때, 모든 활동이 Telegram으로 실시간 전달됩니다.

| 아이콘 | 로그 타입 | 설명 |
|--------|-----------|------|
| 👤 | user | 사용자가 입력한 메시지 |
| 🤖 | response/ai/agent | Agent Zero의 응답 |
| ⚙️ | code_exe | 코드 실행 내용 (미리보기 500자) |
| 🔧 | tool | 도구 호출 (검색, 메모리 등) |
| ℹ️ | info | 정보성 메시지 |
| ❌ | error | 오류 발생 |
| ⚠️ | warning | 경고 |
| 🔄 | (자동) | 채팅 전환 감지 (자동 추적 시) |

- 임시 메시지(thinking 등)는 전달하지 않음
- 응답이 2000자 초과 시 자동 생략
- Telegram 메시지 길이 제한(4096자)에 맞춰 자동 분할

---

## 멀티채팅 기능

### 채팅 목록 조회

Telegram에서 `/chats` 입력 시:

```
📋 채팅 목록:

→ 1. 프로젝트 개발
     ID: 1188c4fe-581...
  2. 일반 대화
     ID: a3f2b8c1-2e4...

현재 추적 중: 1188c4fe-581...

채팅 전환: /switch [번호]
```

`→` 표시가 현재 모니터링 중인 채팅입니다.

### 채팅 전환

```
/switch 2
```

→ 2번 채팅으로 전환. 이후 Telegram 메시지는 해당 채팅에 전달되고, 모니터도 해당 채팅을 추적합니다.

### 자동 추적 (Auto Follow)

`/follow_on` 상태에서는 웹 UI에서 다른 채팅을 열면 모니터가 자동으로 따라갑니다.

```
🔄 채팅 전환 감지: 1188c4fe... → a3f2b8c1...
```

`/follow_off`하면 현재 채팅만 고정 추적하여 웹에서 다른 채팅을 열어도 Telegram 알림은 고정된 채팅의 것만 옵니다.

---

## 알림 Webhook

외부에서 HTTP POST로 Telegram 알림을 보낼 수 있습니다.

```bash
curl -X POST http://telegram-bridge:8443/notify \
     -H 'Content-Type: application/json' \
     -d '{"text": "작업 완료!"}'
```

Agent Zero에게 지시할 때 활용:
> "작업 완료되면 curl로 http://telegram-bridge:8443/notify 에 결과를 알려줘"

---

## 토큰 사용량 추적

### 동작 원리

Agent Zero가 LLM을 호출할 때마다 LiteLLM Extension이 **모델명 + 입력/출력 토큰 수**를 자동으로 캡처하여 Telegram Bridge `/track` webhook으로 전송합니다.

```
Agent Zero → LLM 호출
    ↓
LiteLLM CustomLogger (Extension)
    ↓ model, prompt_tokens, completion_tokens
Telegram Bridge /track webhook
    ↓ 모델별 집계 + 비용 계산
/usage 명령으로 조회
```

### 추적되는 정보

| 항목 | 설명 |
|------|------|
| `model` | 호출된 모델명 (예: `claude-sonnet-4-6`) |
| `input_tokens` | 입력 토큰 수 (프롬프트 + 컨텍스트) |
| `output_tokens` | 출력 토큰 수 (응답) |
| `cost` | LiteLLM 가격표 기반 예상 비용 (USD) |

모든 LLM 호출이 추적됩니다: 메인 모델(chat), 유틸리티 모델(요약/메모리), 브라우저 모델 등.

### /usage 명령

전체 합산 + 모델별 내역 + 최근 7일 기록을 조회합니다:
```
📊 토큰 사용량 (2026-04-14)

총 요청: 15건
총 입력: 12,500 토큰
총 출력: 3,200 토큰
총 비용: $0.0506

🤖 모델별 내역:
  claude-sonnet-4-6
    9건 | in:10,200 out:2,800 | $0.0472
  claude-haiku-4-5-20251001
    6건 | in:2,300 out:400 | $0.0034

📈 최근 기록:
  2026-04-13: 9건, $0.0078

💰 총 누적: $0.0584
```

### /status 명령 (관련 정보)

현재 사용 중인 모델과 프로필도 확인 가능합니다:
```
Agent Zero: 정상 동작 중

📋 프로필: developer
🤖 메인 모델: claude-sonnet-4-6
⚡ 유틸 모델: claude-haiku-4-5-20251001

모니터링: 켜짐
자동 추적: 켜짐
현재 채팅: 1188c4fe...
```

### 사용량 추적 Webhook

Agent Zero Extension이 자동 전송하지만, 외부에서 수동으로 기록할 수도 있습니다:
```bash
curl -X POST http://telegram-bridge:8443/track \
     -H 'Content-Type: application/json' \
     -d '{"model": "gpt-4.1", "input_tokens": 1500, "output_tokens": 500}'
```

### 사용량 조회 API
```bash
curl http://telegram-bridge:8443/usage
```
→ JSON으로 `today` (모델별 내역 포함) + `history` (최근 7일) 반환

### 일일 리포트

매일 자정에 하루 사용량 요약이 Telegram으로 자동 전송됩니다.

### 비용 계산 방식

- LiteLLM의 [model_prices_and_context_window.json](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)에서 **봇 시작 시 최신 가격표를 자동 다운로드**
- 2,600+ 모델의 공식 가격 반영
- 다운로드 실패 시 기본값 ($2/$8 per 1M tokens) fallback
- 구독형(CLIProxy) 사용 시 실제 과금이 아닌 **참고용 추정치**

---

## 기술 세부사항

### Agent Zero API 연동
- 메시지 전송: `POST /message_async` (비동기, 즉시 반환)
- 응답 수집: `POST /poll` (3초 간격 폴링)
- 채팅 목록: `/poll` 응답의 `contexts` 필드
- CSRF 보호: `GET /csrf_token` → `X-CSRF-Token` 헤더

### 세션 관리
- aiohttp `CookieJar`로 쿠키 유지
- CSRF 403 에러 시 자동 재발급 및 재시도
- `/new` 시 세션 완전 리셋

### 히스토리 스킵 (flooding 방지)
채팅 전환, `/new`, `/switch`, `/monitor_on`, 자동 추적 시 이전 대화 로그가 일괄 전송되는 것을 방지합니다.
- `sync_log_version()`으로 현재 시점의 log_version만 조용히 획득
- 해당 시점 이후의 새 메시지만 Telegram에 전달
- `monitor_log_version = 0` 직접 리셋은 초기 변수 선언 외에 사용하지 않음

### 환경변수

| 변수 | 설명 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API 토큰 |
| `TELEGRAM_CHAT_ID` | 허가된 Telegram 사용자 chat ID |
| `AZ_API_URL` | Agent Zero API 주소 (기본: `http://agent-zero:80`) |
