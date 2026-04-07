# Telegram Bot 기능 가이드

Agent Zero를 Android/iOS 폰에서 원격 제어하는 Telegram Bot 브릿지입니다.

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
| 일반 메시지 | Agent Zero에 지시 전달 → 응답은 모니터를 통해 자동 수신 |

### 모니터링

| 명령 | 설명 |
|------|------|
| `/monitor_on` | 웹 채팅 모니터링 켜기 (기본: 켜짐) |
| `/monitor_off` | 웹 채팅 모니터링 끄기 |
| `/follow_on` | 자동 추적 켜기 — 웹에서 채팅 전환 시 모니터가 따라감 (기본: 켜짐) |
| `/follow_off` | 자동 추적 끄기 — 현재 채팅만 고정 추적 |

### 상태

| 명령 | 설명 |
|------|------|
| `/status` | Agent Zero 연결 상태, CSRF 토큰, 모니터링/추적 상태, 현재 채팅 ID 확인 |
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

### 환경변수

| 변수 | 설명 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API 토큰 |
| `TELEGRAM_CHAT_ID` | 허가된 Telegram 사용자 chat ID |
| `AZ_API_URL` | Agent Zero API 주소 (기본: `http://agent-zero:80`) |
