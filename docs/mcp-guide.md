# MCP 서버 연동 가이드

이 프로젝트에 기본 설정된 MCP(Model Context Protocol) 서버 3종의 기능 설명과 Agent Zero에서 활용하는 방법을 안내합니다.

---

## 목차

1. [MCP란?](#mcp란)
2. [기본 설정된 MCP 서버](#기본-설정된-mcp-서버)
3. [Sequential Thinking — 단계별 추론](#1-sequential-thinking--단계별-추론)
4. [Git — 저장소 분석](#2-git--저장소-분석)
5. [Fetch — 웹 콘텐츠 가져오기](#3-fetch--웹-콘텐츠-가져오기)
6. [Agent Zero에서 활용하는 법](#agent-zero에서-활용하는-법)
7. [추가 MCP 서버 연동](#추가-mcp-서버-연동)
8. [트러블슈팅](#트러블슈팅)

---

## MCP란?

MCP(Model Context Protocol)는 AI 에이전트가 외부 도구/서비스와 표준화된 방식으로 소통하는 프로토콜입니다.

```
Agent Zero ←→ MCP 프로토콜 ←→ MCP 서버 (도구 제공)
```

- Agent Zero가 **클라이언트**, MCP 서버가 **도구 제공자**
- MCP 서버를 연결하면 Agent Zero의 **사용 가능한 도구가 자동으로 확장**됨
- 에이전트는 별도 학습 없이 새 도구를 즉시 사용 가능

---

## 기본 설정된 MCP 서버

| 서버 | 패키지 | API 키 | 설명 |
|------|--------|--------|------|
| Sequential Thinking | `@modelcontextprotocol/server-sequential-thinking` | 불필요 | 복잡한 문제 단계별 추론 |
| Git | `@modelcontextprotocol/server-git` | 불필요 | Git 저장소 읽기/분석 |
| Fetch | `@tokenizin/mcp-npx-fetch` | 불필요 | 웹 콘텐츠 가져오기 |

> 3개 모두 **로컬 실행**, **API 키 불필요**, **npx로 자동 설치**됩니다.

---

## 1. Sequential Thinking — 단계별 추론

### 개요

복잡한 문제를 여러 단계로 나누어 체계적으로 사고하는 도구입니다. Agent Zero의 기본 추론 능력을 보강합니다.

### 제공 도구

| 도구명 | 설명 |
|--------|------|
| `sequentialthinking` | 문제를 단계별로 분해하여 추론. 각 단계의 사고 과정을 명시적으로 기록 |

### 이런 상황에 유용합니다

- 복잡한 아키텍처 설계
- 버그 원인 분석 (여러 가능성을 순차적으로 검토)
- 멀티스텝 마이그레이션 계획
- 트레이드오프 비교 분석

### Agent Zero에게 이렇게 지시하세요

> "이 프로젝트를 모노레포로 전환하는 방법을 단계별로 분석해줘. sequential thinking을 활용해서 각 단계의 장단점을 검토해줘."

> "이 버그의 원인을 체계적으로 추론해줘. 가능한 원인을 하나씩 검토하고 가장 가능성 높은 것을 찾아줘."

> "React vs Vue vs Svelte 중 이 프로젝트에 맞는 프레임워크를 sequential thinking으로 비교 분석해줘."

---

## 2. Git — 저장소 분석

### 개요

Git 저장소를 읽고, 검색하고, 분석하는 도구입니다. 코드 실행(`git` 명령) 없이 MCP를 통해 직접 저장소에 접근합니다.

### 제공 도구

| 도구명 | 설명 |
|--------|------|
| `git_status` | 저장소 상태 확인 (변경 파일, 스테이징 상태) |
| `git_log` | 커밋 히스토리 조회 |
| `git_diff` | 변경 사항 비교 |
| `git_show` | 특정 커밋 상세 조회 |
| `git_branch_list` | 브랜치 목록 |
| `git_search_code` | 코드 내 텍스트 검색 |
| `git_file_read` | 특정 파일 내용 읽기 |
| `git_file_list` | 디렉토리 내 파일 목록 |

### 이런 상황에 유용합니다

- 프로젝트 코드 분석/리뷰
- 최근 변경 이력 파악
- 특정 함수/변수가 사용된 곳 검색
- 브랜치 간 차이 비교

### Agent Zero에게 이렇게 지시하세요

> "/a0/work_dir/my-project 저장소에서 최근 10개 커밋 확인하고 주요 변경사항 요약해줘."

> "이 프로젝트에서 'DATABASE_URL'이 사용된 모든 파일을 찾아줘."

> "main 브랜치와 feature/login 브랜치의 차이점을 분석해줘."

> "최근 일주일간 변경된 파일 목록과 각 변경 요약을 만들어줘."

---

## 3. Fetch — 웹 콘텐츠 가져오기

### 개요

URL에서 웹 콘텐츠를 가져와 LLM이 처리하기 좋은 형태로 변환합니다. HTML을 마크다운으로 변환하여 깨끗한 텍스트를 제공합니다.

### 제공 도구

| 도구명 | 설명 |
|--------|------|
| `fetch` | URL에서 콘텐츠를 가져와 마크다운으로 변환 |

### 이런 상황에 유용합니다

- API 문서 참조
- 라이브러리 공식 문서 확인
- 웹페이지 내용 분석
- GitHub README/이슈 내용 가져오기

### Agent Zero에게 이렇게 지시하세요

> "https://fastapi.tiangolo.com/tutorial/ 문서를 읽고 기본 구조를 요약해줘."

> "이 GitHub 이슈 내용을 확인해줘: https://github.com/user/repo/issues/42"

> "https://docs.docker.com/compose/ 에서 볼륨 마운트 관련 내용을 찾아줘."

> "이 API 문서를 참고해서 클라이언트 코드를 작성해줘: https://api.example.com/docs"

---

## Agent Zero에서 활용하는 법

### 기본 원칙

MCP 도구는 **Agent Zero가 자동으로 인식**합니다. 특별한 문법 없이 자연어로 지시하면 됩니다.

```
❌ "mcp의 git_log 도구를 사용해서..."
✅ "이 저장소의 최근 커밋 히스토리를 확인해줘"
```

Agent Zero가 상황에 맞는 도구를 자동 선택합니다.

### 조합 활용 예시

#### 코드 리뷰 자동화
> "my-project 저장소에서 최근 PR의 변경사항을 분석하고, 
> 코드 품질 이슈가 있으면 정리해줘.
> 관련 공식 문서가 있으면 참조해서 개선안도 제시해줘."

→ Agent Zero가 **Git**(변경사항 확인) + **Fetch**(문서 참조) + **Sequential Thinking**(분석) 조합 사용

#### 기술 조사 + 구현
> "FastAPI로 인증 시스템을 만들려고 해.
> 공식 문서를 참고해서 JWT 인증 구현 계획을 세우고,
> 코드를 작성해줘."

→ **Fetch**(문서 확인) + **Sequential Thinking**(설계) + code_execution_tool(구현)

#### 프로젝트 현황 분석
> "이 저장소의 전체 구조를 파악하고,
> 최근 1주일 변경 이력을 기반으로 
> 현재 진행 상황을 정리해줘."

→ **Git**(구조 + 이력) + **Sequential Thinking**(분석)

---

## 추가 MCP 서버 연동

### 설정 방법

Agent Zero UI → **Settings** → **MCP Servers** 에서 JSON을 편집합니다.

```json
{
  "mcpServers": {
    "서버이름": {
      "command": "npx",
      "args": ["--yes", "--package", "패키지명", "실행명"],
      "env": {
        "API_KEY": "필요시 여기에"
      }
    }
  }
}
```

설정 후 Agent Zero를 **재시작**하면 자동으로 패키지가 설치되고 도구가 등록됩니다.

### 추가 가능한 유용한 서버

| 서버 | 패키지 | API 키 | 용도 |
|------|--------|--------|------|
| Brave Search | `@anthropic/server-brave-search` | 필요 (무료) | 웹 검색 (SearXNG 대안) |
| Playwright | `@anthropic/server-playwright` | 불필요 | 브라우저 자동화 강화 |
| GitHub | `@modelcontextprotocol/server-github` | PAT 필요 | GitHub API (이슈, PR, 코드 검색) |
| Filesystem | `@modelcontextprotocol/server-filesystem` | 불필요 | 파일 접근 제어 |
| Memory | `@modelcontextprotocol/server-memory` | 불필요 | 지식 그래프 메모리 |
| PostgreSQL | `@modelcontextprotocol/server-postgres` | DB URL | 데이터베이스 직접 쿼리 |
| Notion | `@notionhq/mcp-server-notion` | 필요 | Notion 워크스페이스 연동 |

### 예시: Brave Search 추가

```json
{
  "mcpServers": {
    "brave-search": {
      "command": "npx",
      "args": ["--yes", "--package", "@anthropic/server-brave-search", "mcp-server-brave-search"],
      "env": {
        "BRAVE_API_KEY": "YOUR_BRAVE_API_KEY"
      }
    }
  }
}
```

---

## 트러블슈팅

### MCP 서버가 연결되지 않음

**원인**: 첫 기동 시 npx 패키지 설치에 시간 소요

**해결**: 
- `mcp_client_init_timeout`을 30초 이상으로 설정 (기본값 수정 완료)
- Agent Zero 로그 확인: `docker logs agent-zero --tail 20`
- 재시작 후 재시도: `docker compose restart agent-zero`

### MCP 도구가 에이전트에게 보이지 않음

**원인**: Settings 저장 후 재시작 필요

**해결**:
```bash
docker compose up -d agent-zero --force-recreate
```

### npx 패키지 설치 실패

**원인**: 컨테이너 내 네트워크 또는 npm 문제

**해결**:
```bash
# 컨테이너에서 직접 테스트
docker exec agent-zero npx --yes @modelcontextprotocol/server-sequential-thinking --help
```

---

## 참고 문서

- [MCP 공식 사양](https://modelcontextprotocol.io/)
- [공식 MCP 서버 목록](https://github.com/modelcontextprotocol/servers)
- [Awesome MCP Servers (1200+)](https://mcp-awesome.com/)
- [Agent Zero MCP 설정 (내부)](../docs/extensibility.md#5-mcp-서버-연동)
- [Agent Zero 스케줄러와 MCP 조합](../docs/scheduler.md)
