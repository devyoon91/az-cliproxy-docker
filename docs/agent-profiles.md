# 에이전트 프로필 가이드

Agent Zero는 프로필별로 프롬프트, 도구, 확장을 분리하여 전문화된 에이전트를 사용할 수 있습니다.

---

## 구조

```
Agent 0 (마스터, Developer)
    │  "로그인 만들고 리뷰 받아서 배포해줘"
    │
    ├── call_subordinate(profile="developer")  → 개발자: 코드 작성
    ├── call_subordinate(profile="reviewer")   → 리뷰어: 코드 리뷰
    └── call_subordinate(profile="devops")     → 인프라: 배포 설정
```

- **마스터 에이전트 (Agent 0)**: Settings에서 기본 프로필 설정 (권장: `developer`)
- **서브 에이전트**: `call_subordinate`의 `profile` 파라미터로 전문가 호출
- 프로필이 많을수록 **AI 팀이 커지는 구조**

---

## Agent Zero 내장 프로필

| 프로필 | 역할 |
|--------|------|
| `developer` | 풀스택 개발 (마스터 기본값) |
| `researcher` | 조사/연구 |
| `hacker` | 보안 전문 |
| `agent0` | 범용 대화 |
| `default` | 기본값 |

---

## 커스텀 프로필 (개인화 저장소)

커스텀 프로필은 하네스 킷이 아닌 **별도 개인화 저장소**에서 관리합니다.

### 공개 레퍼런스

범용 프로필 (reviewer, devops)은 공개 레퍼런스로 제공됩니다:
- **[az-agent-config-template](https://github.com/devyoon91/az-agent-config-template)** — fork하여 회사/팀에 맞게 커스텀

### 사용 방법

1. `az-agent-config-template` 저장소를 fork 또는 clone
2. 회사/팀 도메인에 맞는 프로필 추가
3. docker-compose에 볼륨 마운트

```yaml
# docker-compose.yml의 agent-zero 서비스에 추가
volumes:
  - ../az-agent-config-template/agents/reviewer:/a0/usr/agents/reviewer:ro
  - ../az-agent-config-template/agents/devops:/a0/usr/agents/devops:ro
  # 추가 프로필...
```

> **⚠️ 경로 주의**: 반드시 `/a0/usr/agents/`에 마운트하세요. `/a0/agents/`는 내장 프로필 디렉토리라 마운트하면 기존 프로필이 사라집니다.
> - ✅ `/a0/usr/agents/reviewer` — 사용자 프로필 (내장과 자동 merge)
> - ❌ `/a0/agents/reviewer` — 내장 프로필 덮어쓰기 위험

### 제공 프로필

| 프로필 | 역할 | 핵심 |
|--------|------|------|
| `reviewer` | 코드 리뷰 전문가 | 보안/성능/품질/아키텍처 4관점 체크리스트 |
| `devops` | 인프라/배포 전문가 | Docker/K8s/CI/CD/모니터링 표준 |

### 회사 특화 프로필 추가 예시

fork한 저장소에 도메인 전문가를 추가합니다:

```
az-agent-config-template/agents/
  ├── reviewer/       ← 범용 (fork 기본 제공)
  ├── devops/         ← 범용 (fork 기본 제공)
  ├── dba/            ← DB 설계/최적화 전문
  ├── qa/             ← 테스트 전문
  ├── backend/        ← 회사 백엔드 스택 특화
  ├── frontend/       ← 회사 프론트 스택 특화
  └── tech-writer/    ← 기술 문서 작성 전문
```

---

## 프로필 생성 방법

### 1. agent.yaml 작성

```yaml
title: DBA
description: Agent specialized in database design, optimization, and migration.
context: Use this agent for database schema design, query optimization,
  migration planning, and performance tuning.
```

### 2. specifics.md 작성

`prompts/agent.system.main.specifics.md`에 전문성을 정의합니다:

```markdown
## Your Role
You are Agent Zero 'DBA' - database expert specialized in...

### Core Capabilities
- Schema design and normalization
- Query optimization and indexing
- Migration planning
...

### Operational Directives
- Always communicate and respond in Korean (한국어)
```

### 3. docker-compose 마운트 추가

```yaml
- ../az-agent-config-template/agents/dba:/a0/usr/agents/dba:ro
```

### 4. 재시작

```bash
docker compose up -d agent-zero --force-recreate
```

---

## 사용 예시

### 서브 에이전트에 프로필 지정

> "이 코드를 작성한 후, reviewer 프로필의 서브 에이전트를 만들어서 코드 리뷰를 받아줘."

### 멀티 프로필 워크플로우

> "이 프로젝트에 로그인 기능 추가하고, 리뷰하고, 배포 설정까지 해줘"

Agent 0가 자동으로:
1. `developer` 서브 에이전트 → 기능 구현
2. `reviewer` 서브 에이전트 → 코드 리뷰
3. `developer` → 리뷰 반영 수정
4. `devops` 서브 에이전트 → 배포 설정
5. 최종 결과를 사용자에게 보고

### PR 자동 리뷰

> "이 저장소의 최신 PR을 reviewer 프로필로 리뷰하고, 결과를 Telegram으로 알려줘."

---

## 참고

- [az-agent-config (공개 레퍼런스)](https://github.com/devyoon91/az-agent-config-template)
- [개인화 저장소 분리 가이드 (GUIDE.md #16)](../GUIDE.md#16-팁-개인화-저장소-분리)
- [Agent Zero 확장 가이드 — 에이전트 프로필](extensibility.md#6-agent-profiles-에이전트-프로필)
