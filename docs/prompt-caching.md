# Anthropic 프롬프트 캐싱 가이드

Anthropic API를 사용할 때 **프롬프트 캐싱(Prompt Caching)**을 활용하면 반복되는 컨텍스트의 입력 비용을 **90% 절감**할 수 있습니다. 이 문서는 캐싱의 원리, Agent Zero 내부 처리 로직, 그리고 커스텀 방법을 다룹니다.

---

## 1. 프롬프트 캐싱이란?

### 핵심 원리: 접두사 일치 (Prefix Matching)

Anthropic의 캐싱은 **앞에서부터 완전히 동일한 부분**을 서버 측 KV Cache에 저장하고, 다음 요청에서 재사용합니다.

```
요청 1: [시스템 프롬프트] [도구 정의] [히스토리] [새 메시지]
         ──────────────── 캐시 생성 ────────────── ──새로──

요청 2: [시스템 프롬프트] [도구 정의] [히스토리] [다른 메시지]
         ──────────────── 캐시 읽기 (90% 할인) ── ──새로──
```

**중요**: 앞에서부터 **단 한 토큰이라도 다르면**, 그 지점 이후 전체가 캐시 미스(miss)됩니다.

### 비용 구조

| 토큰 유형 | 가격 (Sonnet 4 기준) | 설명 |
|-----------|---------------------|------|
| 일반 입력 | $3.00 / 1M | 캐시 없이 처리 |
| 캐시 쓰기 (write) | $3.75 / 1M | 처음 캐시 생성 시 (25% 추가) |
| **캐시 읽기 (read)** | **$0.30 / 1M** | **재사용 시 (90% 할인!)** |
| 출력 | $15.00 / 1M | 캐싱과 무관 |

### 캐시 수명

| 유형 | TTL | 용도 |
|------|-----|------|
| `ephemeral` | 5분 (사용할 때마다 리셋) | Agent Zero 기본값 |

→ 연속 대화 중이면 사실상 **무제한** 유지 (5분 내 다음 호출이 계속 오므로)

### 최소 토큰 수

캐싱이 작동하려면 캐시 블록이 최소 **1,024 토큰** 이상이어야 합니다. Agent Zero의 시스템 프롬프트는 보통 5,000~20,000 토큰이므로 조건을 충분히 만족합니다.

---

## 2. Agent Zero 내부 처리 로직

### 메시지 조립 순서

Agent Zero가 LLM에 보내는 메시지는 다음 순서로 조립됩니다:

```
┌─────────────────────────────────────────────────┐
│ [0] system  ← 시스템 프롬프트 (안정, 대형)       │  ← cache_control ✅
│     ├── _10_main_prompt      (에이전트 역할)      │
│     ├── _11_tools_prompt     (도구 정의)          │
│     ├── _12_mcp_prompt       (MCP 서버 도구)      │
│     ├── _13_secrets_prompt   (API 키)            │
│     ├── _13_skills_prompt    (스킬 목록)          │
│     └── _14_project_prompt   (프로젝트 설정)      │
├─────────────────────────────────────────────────┤
│ [1] user      ← 첫 번째 사용자 메시지             │
│ [2] assistant ← 첫 번째 AI 응답                  │
│ [3] user      ← 두 번째 사용자 메시지             │
│ ...           ← 대화 히스토리 계속                │
│ [N] assistant ← 마지막 AI 응답                   │  ← cache_control ✅
├─────────────────────────────────────────────────┤
│ [N+1] user    ← extras (날짜, 에이전트 정보 등)   │  ← 매번 변함 (캐시 X)
│     ├── _60_current_datetime (현재 시각)          │
│     ├── _63_relevant_skills  (관련 스킬)          │
│     ├── _65_loaded_skills    (로드된 스킬)         │
│     ├── _70_agent_info       (에이전트 번호/프로필) │
│     └── _75_workdir_extras   (작업 디렉토리 구조)  │
└─────────────────────────────────────────────────┘
```

### 캐시 마커 위치 (코드)

파일: **`/a0/models.py`** — `_convert_messages()` 메서드 (374~381행)

```python
if explicit_caching and result:
    # 마커 1: 시스템 프롬프트 전체를 캐시
    if result[0]["role"] == "system":
        result[0]["cache_control"] = {"type": "ephemeral"}

    # 마커 2: 마지막 assistant 메시지까지 캐시
    for i in range(len(result) - 1, -1, -1):
        if result[i]["role"] == "assistant":
            result[i]["cache_control"] = {"type": "ephemeral"}
            break
```

### 캐시가 작동하는 이유

| 블록 | 안정성 | 캐시 효과 |
|------|--------|----------|
| 시스템 프롬프트 (마커 1) | **완전 고정** — 세션 중 변하지 않음 | 매 호출마다 재사용 |
| 대화 히스토리 (마커 2) | **누적** — 새 턴이 추가만 됨 | 접두사 일치로 재사용 |
| extras (마커 없음) | **매번 변함** (시각, 스킬 등) | 캐시 안 함 (의도적) |

**핵심 설계**: 변하는 데이터(extras)가 메시지 배열 **맨 끝**에 위치하므로, 앞쪽의 시스템 프롬프트와 히스토리 캐시를 **깨뜨리지 않습니다**.

### 호출 흐름 요약

```
agent.call_chat_model(explicit_caching=True)      # agent.py:800
  └→ model.unified_call(explicit_caching=True)     # models.py:484
       └→ _convert_messages(explicit_caching=True)  # models.py:321
            └→ cache_control 마커 삽입               # models.py:374-381
       └→ litellm.acompletion(messages=msgs)        # models.py:520
            └→ LiteLLM이 Anthropic API로 변환
                 └→ cache_control을 content block 내부로 이동 (LiteLLM 자동 처리)
```

---

## 3. 프로바이더별 캐싱 지원

| 프로바이더 | settings.json provider | 캐싱 작동 | 비고 |
|-----------|----------------------|----------|------|
| **Anthropic API** | `"anthropic"` | ✅ **자동** | LiteLLM이 `cache_control`을 Anthropic 형식으로 변환 |
| OpenAI API | `"openai"` | ❌ | OpenAI는 자체 automatic caching 사용 (별도 마커 불필요) |
| CLIProxy | `"other"` | ❌ | OpenAI 호환으로 라우팅되어 `cache_control` 무시 |
| Google Vertex | `"google"` | ❌ | Gemini는 context caching API가 다름 |

**결론: Anthropic API를 사용해야만 Agent Zero의 내장 캐싱 로직이 활성화됩니다.**

---

## 4. 설정 방법

### Step 1: `.env` 설정

```env
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
```

### Step 2: `settings.json` 변경

```json
{
    "chat_model_provider": "anthropic",
    "chat_model_name": "claude-sonnet-4-6",
    "chat_model_api_base": "",

    "util_model_provider": "anthropic",
    "util_model_name": "claude-haiku-3.5-sonnet",
    "util_model_api_base": "",

    "browser_model_provider": "anthropic",
    "browser_model_name": "claude-haiku-3.5-sonnet",
    "browser_model_api_base": ""
}
```

### Step 3: CLIProxy 제거 (선택)

`docker-compose.yml`에서 cliproxy 서비스를 주석 처리하거나 제거합니다.

### Step 4: 재시작

```bash
docker compose up -d --build
```

---

## 5. 캐싱 효과 확인

### API 응답에서 확인

Anthropic API 응답의 `usage` 필드에 캐싱 정보가 포함됩니다:

```json
{
  "usage": {
    "input_tokens": 500,
    "cache_creation_input_tokens": 15000,
    "cache_read_input_tokens": 0
  }
}
```

| 필드 | 의미 |
|------|------|
| `input_tokens` | 캐시되지 않은 새 입력 토큰 |
| `cache_creation_input_tokens` | 이번에 캐시에 저장된 토큰 (첫 호출) |
| `cache_read_input_tokens` | 캐시에서 읽은 토큰 (**이 값이 높으면 성공**) |

### 정상 작동 패턴

```
[첫 호출]
  input_tokens: 500
  cache_creation_input_tokens: 15,000   ← 캐시 생성
  cache_read_input_tokens: 0

[두 번째 호출]
  input_tokens: 800
  cache_creation_input_tokens: 800      ← 새 히스토리만 캐시 생성
  cache_read_input_tokens: 15,000       ← 시스템 프롬프트 캐시 히트!

[세 번째 호출]
  input_tokens: 500
  cache_creation_input_tokens: 500
  cache_read_input_tokens: 16,300       ← 시스템 + 이전 히스토리 캐시 히트!
```

### 실패 징후

매 호출마다 `cache_read_input_tokens: 0`이면:
1. provider가 `"anthropic"`이 맞는지 확인
2. 시스템 프롬프트 앞부분에 변하는 데이터가 끼어있는지 확인
3. LiteLLM 버전이 `cache_control` 변환을 지원하는지 확인

---

## 6. 비용 시뮬레이션

### 시나리오: 하루 200회 호출, 시스템 프롬프트 15,000 토큰

**캐싱 없이 (CLIProxy 사용 시 → API 전환 가정):**

```
입력: 200회 × 15,000 토큰 = 3,000,000 토큰
비용: 3M × $3.00/M = $9.00/일 = $270/월
```

**캐싱 적용 시 (Anthropic API):**

```
캐시 쓰기: 1회 × 15,000 = 15,000 토큰 × $3.75/M = $0.056
캐시 읽기: 199회 × 15,000 = 2,985,000 토큰 × $0.30/M = $0.896
새 입력: 200회 × 500 = 100,000 토큰 × $3.00/M = $0.300
────────────────────────────────────────────────
합계: $1.25/일 = $37.50/월 (86% 절감!)
```

---

## 7. 커스텀 캐싱 로직

Agent Zero의 캐싱은 수정 가능합니다. Anthropic이 정책을 변경하거나, 더 세밀한 제어가 필요할 때 대비하세요.

### 방법 1: Extension Hook으로 캐시 마커 커스텀

`chat_model_call_before` 확장 포인트에서 메시지를 가로채 캐시 마커를 직접 조작할 수 있습니다.

파일: `agent-zero/extensions/python/chat_model_call_before/_80_custom_caching.py`

```python
from helpers.extension import Extension


class CustomCaching(Extension):
    """
    캐시 마커를 커스텀으로 제어하는 확장.
    Anthropic 정책 변경 시 이 파일만 수정하면 됩니다.
    """

    async def execute(self, call_data: dict = {}, **kwargs):
        if not self.agent:
            return

        messages = call_data.get("messages", [])
        if not messages:
            return

        # ── 옵션 A: 기본 캐싱 비활성화 후 직접 제어 ──
        # call_data["explicit_caching"] = False

        # ── 옵션 B: 메시지 변환 후 직접 마커 삽입 ──
        # (unified_call 호출 전에 LangChain 메시지를 수정)

        # ── 옵션 C: 특정 조건에서만 캐싱 ──
        # 예: 시스템 프롬프트가 1024 토큰 미만이면 캐싱 비활성화
        # from helpers import tokens
        # system_text = messages[0].content if messages else ""
        # if tokens.approximate_tokens(system_text) < 1024:
        #     call_data["explicit_caching"] = False
```

### 방법 2: `_convert_messages()` 직접 수정

Docker 이미지 내부 코드를 수정하려면, 커스텀 `models.py`를 마운트합니다.

`docker-compose.yml`에 볼륨 추가:
```yaml
volumes:
  - ./agent-zero/custom_models.py:/a0/models.py
```

수정 포인트 (`/a0/models.py` 374~381행):

```python
# 현재 코드: 시스템 + 마지막 assistant에 마커
if explicit_caching and result:
    if result[0]["role"] == "system":
        result[0]["cache_control"] = {"type": "ephemeral"}
    for i in range(len(result) - 1, -1, -1):
        if result[i]["role"] == "assistant":
            result[i]["cache_control"] = {"type": "ephemeral"}
            break

# 커스텀 예시: 3개 브레이크포인트 (시스템 + 중간 히스토리 + 마지막 assistant)
if explicit_caching and result:
    # 브레이크포인트 1: 시스템 프롬프트
    if result[0]["role"] == "system":
        result[0]["cache_control"] = {"type": "ephemeral"}

    # 브레이크포인트 2: 히스토리 중간 지점 (긴 대화 시 효과적)
    assistant_indices = [i for i, m in enumerate(result) if m["role"] == "assistant"]
    if len(assistant_indices) >= 4:
        mid = assistant_indices[len(assistant_indices) // 2]
        result[mid]["cache_control"] = {"type": "ephemeral"}

    # 브레이크포인트 3: 마지막 assistant
    if assistant_indices:
        result[assistant_indices[-1]]["cache_control"] = {"type": "ephemeral"}
```

### 방법 3: 캐싱 완전 비활성화

Anthropic이 캐싱 정책을 변경하여 오히려 비용이 증가하는 경우:

```python
# agent.py 의 call_chat_model에서:
explicit_caching: bool = False  # True → False로 변경
```

또는 Extension으로:

```python
# extensions/python/chat_model_call_before/_80_disable_caching.py
class DisableCaching(Extension):
    async def execute(self, call_data: dict = {}, **kwargs):
        call_data["explicit_caching"] = False
```

---

## 8. 자주 묻는 질문 (FAQ)

### Q: 캐싱이 대화 품질에 영향을 주나요?
**A: 아니요.** 캐싱은 서버 측 KV Cache 재사용일 뿐, 모델이 받는 프롬프트 내용은 100% 동일합니다.

### Q: 서브 에이전트(sub-agent)도 캐싱이 되나요?
**A: 부분적으로.** 서브 에이전트는 독립 세션이므로 메인 에이전트의 캐시를 공유하지 않습니다. 다만 서브 에이전트 자체의 시스템 프롬프트는 동일하므로, **서브 에이전트끼리는** 캐시가 공유될 수 있습니다 (동일 접두사 조건 충족 시).

### Q: 유틸리티 모델도 캐싱이 되나요?
**A: 기본적으로는 아닙니다.** `call_chat_model()`만 `explicit_caching=True`가 기본입니다. 유틸리티 호출은 별도 경로(`call_utility_model`)를 사용하며, 여기에는 캐싱 마커가 없습니다. 필요하다면 Extension으로 추가 가능합니다.

### Q: `cache_control`이 메시지 최상위(top-level)에 있는데, Anthropic API가 인식하나요?
**A: LiteLLM이 자동 변환합니다.** LiteLLM은 Anthropic provider 사용 시 `cache_control`을 content block 내부로 이동시킵니다. 즉, Agent Zero 코드 수정 없이 Anthropic API 호환이 됩니다.

### Q: Anthropic이 캐싱 가격 정책을 바꾸면?
**A: Extension으로 즉시 대응 가능합니다.**
- 캐시 쓰기 비용이 올라가면 → 마커 개수 줄이기
- 캐싱 자체가 폐지되면 → Extension에서 `explicit_caching=False` 설정
- 새로운 캐싱 방식이 나오면 → `_convert_messages()` 수정

위 섹션 7의 커스텀 방법을 참고하세요.

### Q: OpenAI의 automatic caching과 비교하면?
**A:** OpenAI는 128토큰 이상의 동일 접두사를 **자동으로** 캐싱하며, 별도 마커가 필요 없습니다. 50% 할인입니다. Anthropic은 마커를 명시해야 하지만 **90% 할인**으로 더 큰 폭의 절감이 가능합니다.

---

## 9. 트러블슈팅 체크리스트

캐싱이 작동하지 않을 때:

- [ ] `settings.json`의 `chat_model_provider`가 `"anthropic"`인가?
- [ ] `.env`에 `ANTHROPIC_API_KEY`가 설정되어 있는가?
- [ ] `chat_model_api_base`가 비어있는가? (값이 있으면 CLIProxy 등 다른 곳으로 라우팅)
- [ ] API 응답에 `cache_read_input_tokens` 필드가 있는가?
- [ ] 시스템 프롬프트가 1,024 토큰 이상인가? (미만이면 캐시 생성 안 됨)
- [ ] 5분 이상 호출이 없었는가? (ephemeral TTL 만료)
- [ ] Extension에서 시스템 프롬프트 앞부분에 동적 데이터를 삽입하고 있지 않은가?

---

## 참고 자료

- [Anthropic 프롬프트 캐싱 공식 문서](https://docs.anthropic.com/ko/docs/build-with-claude/prompt-caching)
- [Anthropic 가격표](https://platform.claude.com/docs/ko/about-claude/pricing)
- [LiteLLM Anthropic 캐싱 지원](https://docs.litellm.ai/docs/providers/anthropic#prompt-caching)
