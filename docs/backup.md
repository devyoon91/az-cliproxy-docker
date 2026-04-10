# 백업 및 복원 가이드

개인화된 설정, 인증 정보, 메모리, 작업물을 백업하고 복원하는 방법입니다.

---

## 백업 대상 목록

| 카테고리 | 파일/디렉토리 | 설명 | 민감도 | 백업 모드 |
|----------|-------------|------|:------:|:---------:|
| **설정** | `.env` | API 토큰, Git PAT, Telegram 토큰 | 🔴 높음 | light |
| **설정** | `cliproxy/config.yaml` | CLIProxy 설정 (비밀번호 해시) | 🔴 높음 | light |
| **설정** | `agent-zero/settings.json` | 모델, 플러그인, MCP 설정 | 🟡 중간 | light |
| **설정** | `docker-compose.yml` | 서비스 구성 | 🟢 낮음 | light |
| **인증** | `cliproxy/auth/*.json` | LLM OAuth 토큰 | 🔴 높음 | light |
| **프롬프트** | `agent-zero/prompts/` | 커스텀 시스템 프롬프트 | 🟢 낮음 | config |
| **프로필** | `agent-zero/agents/` | 에이전트 프로필 (developer, reviewer, devops) | 🟢 낮음 | config |
| **스크립트** | `agent-zero/git-init.sh` | Git/GitHub CLI 초기화 | 🟢 낮음 | config |
| **메모리** | `agent-zero/memory/` | FAISS 벡터 DB (장기 기억) | 🟡 중간 | full |
| **작업물** | `agent-zero/work_dir/` | 프로젝트 파일, clone된 저장소 | 🟡 중간 | full |
| **로그** | `agent-zero/logs/` | 채팅 로그 (HTML) | 🟢 낮음 | full |
| **로그** | `cliproxy/logs/` | CLIProxy API 로그 | 🟢 낮음 | full |

---

## 방법 1: 로컬 스크립트 (전체 백업)

### 백업

```bash
# 경량 백업 (설정 + 인증만)
./scripts/backup.sh light

# 설정 백업 (설정 + 인증 + 프롬프트 + 프로필)
./scripts/backup.sh config

# 전체 백업 (설정 + 인증 + 프롬프트 + 프로필 + 메모리 + 작업물 + 로그)
./scripts/backup.sh full
```

백업 파일은 `backups/` 디렉토리에 저장됩니다:
```
backups/
  ├── backup_light_20260410_143000.tar.gz    (수 KB)
  ├── backup_config_20260410_143000.tar.gz   (수십 KB)
  └── backup_full_20260410_143000.tar.gz     (수 MB ~ 수백 MB)
```

### 복원

```bash
# 백업 목록 확인
./scripts/restore.sh

# 복원 실행
./scripts/restore.sh backups/backup_config_20260410_143000.tar.gz
```

복원 후:
```bash
docker compose down
docker compose up -d --build
```

---

## 방법 2: Telegram 원격 백업 (경량)

폰에서 `/backup` 명령으로 설정을 ZIP 파일로 받을 수 있습니다.

```
/backup
```

→ `az_backup_20260410_143000.zip` 파일이 Telegram으로 전송됨

### 포함 내용

| 파일 | 설명 |
|------|------|
| `agent-zero/settings.json` | Agent Zero 설정 (API에서 실시간 추출) |
| `docs/*.md` | 프로젝트 문서 |
| `usage_data.json` | 토큰 사용량 기록 |
| `backup_meta.json` | 백업 시간, 컨텍스트 등 메타정보 |

> **참고**: `.env`, OAuth 토큰 등 민감 파일은 Telegram으로 전송하지 않습니다. 전체 백업은 로컬 스크립트를 사용하세요.

---

## 백업 모드 비교

| 모드 | 대상 | 크기 | 용도 |
|------|------|------|------|
| **light** | 설정 + 인증 | 수 KB | 다른 PC에서 빠르게 복원 |
| **config** | light + 프롬프트 + 프로필 | 수십 KB | 커스텀 설정 전체 보존 |
| **full** | config + 메모리 + 작업물 + 로그 | 수 MB+ | 완전한 상태 복원 |
| **telegram** | 설정 + 문서 + 사용량 | 수십 KB | 폰에서 원격 백업 |

---

## 권장 백업 주기

| 시점 | 권장 모드 |
|------|-----------|
| 하루 작업 끝날 때 | `config` |
| 중요 프로젝트 완료 시 | `full` |
| 설정 변경 후 | `light` 또는 Telegram `/backup` |
| 다른 PC로 이전 시 | `full` |
| Docker 이미지 업데이트 전 | `full` |

---

## 보안 주의사항

- 백업 파일에 **API 토큰, OAuth 인증, Git PAT**가 포함됩니다
- 백업 파일을 **안전한 위치**에 보관하세요 (암호화된 드라이브, 비공개 저장소)
- `backups/` 디렉토리는 `.gitignore`에 포함되어 저장소에 올라가지 않습니다
- Telegram `/backup`은 민감 파일을 **제외**하므로 상대적으로 안전합니다

---

## 참고

- Agent Zero 내장 백업: Settings → Backup 탭 (지식베이스, 메모리, 채팅 히스토리)
- 이 스크립트는 **하네스 킷 전체**를 백업하는 것으로, Agent Zero 내장 백업과 범위가 다릅니다
