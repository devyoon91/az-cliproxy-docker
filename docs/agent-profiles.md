# 에이전트 프로필 가이드

Agent Zero는 프로필별로 프롬프트, 도구, 확장을 분리하여 전문화된 에이전트를 사용할 수 있습니다.

---

## 기본 제공 프로필

### developer (기본값)

| 항목 | 내용 |
|------|------|
| 역할 | Master Developer — 풀스택 소프트웨어 개발 |
| 특징 | 요구사항 분석 → 설계 → 구현 → 테스트 자율 수행 |
| 프롬프트 | 코딩 가이드라인, 단순성 우선, 수술적 변경, 작업 이력 관리 |
| 사용 시 | "프로젝트 만들어줘", "API 구현해줘", "버그 수정해줘" |

### reviewer (커스텀)

| 항목 | 내용 |
|------|------|
| 역할 | Code Reviewer — 코드 리뷰 및 품질 보증 |
| 특징 | 보안/성능/품질/아키텍처/테스트 5가지 관점 체계적 리뷰 |
| 프롬프트 | 리뷰 체크리스트, APPROVE/REQUEST_CHANGES 판정 |
| 사용 시 | "이 PR 리뷰해줘", "이 코드 보안 점검해줘" |

#### 리뷰 체크리스트

| 카테고리 | 점검 항목 |
|----------|-----------|
| **보안** | 하드코딩된 시크릿, SQL 인젝션, XSS, CSRF, 입력 검증, 인증/인가 |
| **성능** | N+1 쿼리, 불필요한 반복, 인덱스 누락, 페이징 미사용, 캐싱 부재 |
| **코드 품질** | 단일 책임 원칙, 50줄 초과 메서드, 빈 catch 블록, 매직 넘버, 네이밍 |
| **아키텍처** | 레이어 분리, DTO/Entity 분리, DI 사용, 디자인 패턴 적절성 |
| **테스트** | 신규 코드 테스트 존재, 엣지 케이스, 테스트 독립성, 목킹 적절성 |
| **Git** | 커밋 메시지, PR 범위 집중도, 관련 없는 변경 포함 여부 |

### devops (커스텀)

| 항목 | 내용 |
|------|------|
| 역할 | DevOps Engineer — 인프라, CI/CD, 배포 자동화 |
| 특징 | Dockerfile, Docker Compose, K8s, CI/CD, 모니터링, IaC |
| 프롬프트 | 인프라 표준, 롤백 계획 필수, 시크릿 관리, 모니터링 기준 |
| 사용 시 | "Docker 설정 만들어줘", "CI/CD 파이프라인 구성해줘", "모니터링 대시보드 만들어줘" |

### 기본 제공 (Agent Zero 내장)

| 프로필 | 역할 |
|--------|------|
| `agent0` | 범용 대화 에이전트 |
| `default` | 기본값 |
| `hacker` | 보안/해킹 전문 |
| `researcher` | 조사/연구 전문 |

---

## 프로필 사용 방법

### Settings에서 기본 프로필 변경

Settings → `agent_profile` 에서 선택:
- `developer` — 개발 작업 시 (기본 권장)
- `reviewer` — 코드 리뷰 시
- `devops` — 인프라 작업 시

### 서브 에이전트에 프로필 지정

메인 에이전트가 `developer`로 코드를 짜고, 서브 에이전트를 `reviewer`로 만들어 셀프 리뷰하는 워크플로우:

> "이 코드를 작성한 후, reviewer 프로필의 서브 에이전트를 만들어서 코드 리뷰를 받아줘. 리뷰 결과에 따라 수정하고 push해줘."

### 조합 예시

#### 개발 + 리뷰 + 배포 자동화
```
1. developer: 기능 구현 + 테스트 작성
2. reviewer: 코드 리뷰 (서브 에이전트)
3. developer: 리뷰 반영 수정
4. devops: CI/CD 파이프라인 + Docker 설정 (서브 에이전트)
5. developer: 최종 push + PR
```

#### PR 자동 리뷰
> "이 저장소의 최신 PR을 reviewer 프로필로 리뷰하고, 결과를 Telegram으로 알려줘."

---

## 프로필 디렉토리 구조

```
agent-zero/agents/
  ├── developer/
  │   ├── agent.yaml                              ← 프로필 메타데이터
  │   └── prompts/
  │       ├── agent.system.main.specifics.md       ← 역할 + 프로세스 정의
  │       └── agent.system.main.communication.md   ← 커뮤니케이션 규칙
  ├── reviewer/
  │   ├── agent.yaml
  │   └── prompts/
  │       └── agent.system.main.specifics.md       ← 리뷰 체크리스트 + 출력 포맷
  └── devops/
      ├── agent.yaml
      └── prompts/
          └── agent.system.main.specifics.md       ← 인프라 표준 + 출력 포맷
```

### 커스텀 프로필 추가

1. `agent-zero/agents/` 아래에 새 디렉토리 생성
2. `agent.yaml` 작성 (title, description, context)
3. `prompts/agent.system.main.specifics.md` 작성 (역할 정의)
4. `docker compose up -d agent-zero --force-recreate`
5. Settings에서 새 프로필 선택 가능

---

## 참고

- [Agent Zero 확장 가이드 — 에이전트 프로필](extensibility.md#6-agent-profiles-에이전트-프로필)
- [Agent Zero 아키텍처 — 에이전트 계층 구조](architecture.md#에이전트-계층-구조)
