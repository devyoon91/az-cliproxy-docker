# Eval 가이드 — 골든셋 기반 하네스 품질 측정

하네스(프롬프트·도구·MCP·모델·설정)를 바꿀 때마다 \"품질이 떨어지지 않았는가\"를
숫자로 답하기 위한 시스템. 마일스톤
[`Quality Evals 2026-05`](https://github.com/devyoon91/az-cliproxy-docker/milestone/2)
의 결과물 — 외부 도구 (LangSmith / Langfuse 등) 없이 우리 코드 안에서 회귀를
감지한다.

> **TL;DR**: `python -m eval.runner --all` 로 골든셋을 돌리고,
> `python -m eval.judge --run-dir <…>` 로 채점한다. 또는 폰에서 `/eval`.
> `eval/baseline.json` 을 한 번 기록해두면 CI 가 자동 회귀 감지.

---

## 1. 4축 지표

| 축 | 지표 | 어떤 회귀를 잡나 |
|---|---|---|
| **품질** | 골든셋 통과율, LLM-as-judge 점수 | 시스템 프롬프트 변경 / 모델 변경으로 답 품질 하락 |
| **효율** | 태스크당 토큰·비용, cache hit rate | 컨텍스트 누수, cache 마커 깨짐, 도구 정의 비대화 |
| **지연** | TTFT, e2e 완료 시간 | 도구 호출 폭증, MCP 응답 지연, 모델 다운그레이드 |
| **행동** | 턴 수, 도구 에러율 | 무한 루프, 잘못된 도구 선택, retry 폭주 |

이 가이드는 **품질** 축에 가장 무게를 둔다 (다른 축은 부산물로 함께 잡힘).

---

## 2. 디렉토리

```
eval/
├── schema.py             # EvalCase dataclass + YAML 로더
├── trace.py              # 트레이스 (task_report → Trace)
├── az_client.py          # Agent Zero HTTP 클라이언트
├── runner.py             # 케이스 실행 (CLI: python -m eval.runner)
├── judge.py              # LLM-as-judge 채점 (CLI: python -m eval.judge)
├── cases/                # 골든셋 케이스 YAML (10개 + _example.yaml 템플릿)
├── runs/                 # 회차별 결과 — gitignored
│   └── <YYYYmmdd-HHMMSS>/
│       ├── <case_id>.json        # 트레이스 (runner)
│       ├── <case_id>.judge.json  # 채점 결과 (judge)
│       └── _summary.json         # 회차 합계
├── baseline.json         # CI 회귀 비교 기준선 (커밋 대상)
└── README.md             # 개발자 시점 개요

.github/
├── scripts/eval_compare.py    # baseline 비교 스크립트
└── workflows/eval.yml          # CI 회귀 워크플로우

telegram-bridge/
├── telegram_handlers/eval.py   # /eval 명령
└── dashboard/eval_*            # /dashboard/eval 페이지
```

---

## 3. 케이스 작성 — 30초 가이드

새 케이스를 추가하려면 [`eval/cases/_example.yaml`](../eval/cases/_example.yaml)
을 복사해 `eval/cases/<id>.yaml` 로 저장하면 된다.

### 3.1 최소 YAML

```yaml
task: |
  여기에 에이전트에 전달할 메시지를 그대로 적는다.
  여러 줄 가능.
expected_behaviors:
  - 첫 번째 기대 행동 (구체적으로)
  - 두 번째 기대 행동
judge_criteria: |
  채점자(judge)에게 줄 자유 형식 기준 — "정확히 X 가 있어야 한다", "Y 면 0점" 같이.
```

이게 전부다. 나머지 필드는 모두 기본값 있음.

### 3.2 전체 필드 레퍼런스

| 필드 | 필수 | 기본 | 설명 |
|---|---|---|---|
| `id` | 권장 | 파일명 stem | 영소문자/숫자/`_` 만. 미지정 시 파일명에서 유추 |
| `task` | ✓ | — | 에이전트에 전달할 메시지. 한국어/영어 무관 |
| `tags` | | `[]` | 분류 라벨 (예: `[code, korean]`). 카테고리별 통과율 보기용 |
| `expected_behaviors` | ✓ | — | judge 가 평가할 행동 목록 (1개 이상). 골든셋 정책상 2개 이상 권장 |
| `judge_criteria` | 권장 | `""` | judge 프롬프트에 함께 들어가는 자유 형식 기준 |
| `max_turns` | | 10 | 이 턴 수 넘으면 가드 위반 (1..50) |
| `max_cost_usd` | | 0.50 | 비용 가드 (0..10, 골든셋 정책상 0.20 이하) |
| `timeout_sec` | | 120 | e2e 타임아웃 (1..1800) |

스키마 위반은 `EvalSchemaError` 로 빠르게 거절 — 자세한 규칙은
[`eval/schema.py`](../eval/schema.py) 의 `EvalCase.__post_init__` 참조.

### 3.3 예시 — `markdown_table_format`

```yaml
# 사용자가 표를 요청했을 때 실제 마크다운 표를 생성하는가.
task: |
  Python 의 `list` 와 `tuple` 의 차이를 마크다운 표로 정리해줘.
  컬럼은 "항목 / list / tuple" 3개로.
tags:
  - format
  - markdown
expected_behaviors:
  - 마크다운 표 문법(`|` 와 `-` 구분선)을 사용한다
  - '"항목", "list", "tuple" 3개 컬럼이 있다'
  - 최소 3행 이상의 비교 내용이 있다
  - 한국어로 작성되어 있다
judge_criteria: |
  실제 GitHub-flavored 마크다운 표여야 한다. 단순 글머리표나 평문
  나열이면 fail. 3컬럼 헤더가 명시적이어야 한다.
max_turns: 2
max_cost_usd: 0.05
timeout_sec: 45
```

> **YAML 트랩**: 리스트 항목이 따옴표로 시작하면 (`- "파리" 라는 ...`) PyYAML 이
> block scalar 로 오해해 parse 실패. 위 예시처럼 single-quote 로 감싸 `- '"파리"
> 라는 ...'` 식으로 작성한다.

### 3.4 스키마 검증

PR 올리기 전에 로컬에서:

```bash
python -m pytest tests/test_eval_golden_set.py -v
```

이 테스트는 다음을 강제:

- 10개 이상의 케이스 존재
- 모든 케이스 `expected_behaviors >= 2`
- 모든 케이스 `judge_criteria` 채워짐
- `max_cost_usd <= $0.20`
- id 충돌 없음
- 알려진 10개 id 가 모두 존재

새 케이스를 추가하면 `test_known_case_present` parametrize 도 함께 갱신.

---

## 4. 로컬 실행

### 4.1 전체 골든셋 실행

```bash
# Agent Zero 가 실행 중이어야 함 (docker compose up -d agent-zero)
python -m eval.runner --all

# 결과: eval/runs/<YYYYmmdd-HHMMSS>/
#   - <case_id>.json     (트레이스)
#   - _summary.json      (회차 요약)
```

### 4.2 단건 디버깅

```bash
python -m eval.runner --case markdown_table_format
```

### 4.3 채점

```bash
RUN_DIR=$(ls -dt eval/runs/*/ | head -1)
python -m eval.judge --run-dir "$RUN_DIR"

# 결과:
#   - <case_id>.judge.json   (점수/통과 여부/근거)
#   - <case_id>.json         의 judge_cost_usd 갱신
```

종료 코드: 모든 케이스가 `passed=True` + 오류 0건 → 0, 그 외 → 1.

### 4.4 환경 변수

| 변수 | 기본 | 용도 |
|---|---|---|
| `AZ_API_URL` | `http://localhost:50001` | Agent Zero HTTP 엔드포인트 |
| `EVAL_TASKS_DIR` | `agent-zero/logs/tasks` | task_report JSON 폴링 경로 |
| `EVAL_CASES_DIR` | `eval/cases` | 케이스 YAML 디렉토리 |
| `EVAL_RUNS_DIR` | `eval/runs` | 결과 출력 디렉토리 |
| `EVAL_BASELINE_PATH` | `eval/baseline.json` | baseline 위치 |
| `ANTHROPIC_API_KEY` | — | judge 호출용 (필수) |

---

## 5. Telegram `/eval`

폰에서 한 번에 실행.

```
/eval                  골든셋 전체 + 채점
/eval <case_id>        단건
/eval baseline         전체 실행 + 결과를 eval/baseline.json 으로 저장
```

진행 중에는 같은 메시지가 in-place 로 갱신되며 (예: `🧪 [5/10] markdown_table_format 실행 중...`),
종료 후 통과율 / 비용 / 케이스별 결과가 한 메시지로 정리된다.

**전제**:
- `docker-compose.yml` 에 `./eval:/app/eval` 마운트가 있어야 함 (기본 포함).
- `.env` 에 `ANTHROPIC_API_KEY` 설정.

---

## 6. 대시보드 — `/dashboard/eval`

브라우저에서 [`http://localhost:8443/dashboard/eval?token=<DASHBOARD_TOKEN>`](http://localhost:8443/dashboard/eval).
비용 대시보드 (`/dashboard`) 와 같은 토큰 사용, 우측 상단 \"🧪 eval →\" 링크로
서로 이동 가능.

차트:

| 카드 | 차트 |
|---|---|
| 📌 최신 회차 | 통과율 / 실행·채점 비용 / 소요 / 가드 위반 |
| 📈 통과율 추이 | 회차별 % (라인) |
| 💰 케이스별 평균 비용 | 실행 + 채점 stacked bar |
| ⏱ 케이스별 지연 분포 | p50 / p95 / max 그룹 바 |

데이터는 `eval/runs/*/_summary.json` 에서 집계. 회차가 많아질수록 추이 신호가 또렷해진다.

---

## 7. CI 회귀 자동 감지

[`.github/workflows/eval.yml`](../.github/workflows/eval.yml) 가
[`eval/baseline.json`](../eval/baseline.json) 대비 통과율 회귀를 감지하면
워크플로우를 실패시킨다.

### 7.1 트리거

| 트리거 | 동작 |
|---|---|
| `workflow_dispatch` (수동) | Actions 탭 → \"Eval Regression\" → Run workflow. 임계값 입력 가능 |
| `push` to `main` | `eval/**`, `agent-zero/prompts/**`, settings example, 워크플로우 파일 변경 시에만 |
| PR 라벨 `eval-required` | 해당 PR 에서만 실행, 결과를 PR 코멘트로 |

비용: 풀 실행 1회 ≈ \$0.50 (Sonnet + Haiku, 10케이스). path-filter + 라벨 옵트인으로
\"매 PR\" 비용 폭주 방지.

### 7.2 필수 secret

GitHub repo Settings → Secrets and variables → Actions:

| 이름 | 용도 |
|---|---|
| `ANTHROPIC_API_KEY` | runner + judge 모두 Anthropic Direct API 호출 |

### 7.3 baseline 기록

첫 회차에는 `eval/baseline.json` 이 없으므로 워크플로우는 \"no baseline\" 노티스만
출력하고 통과 (의도). 기준선을 기록하려면:

```bash
# 옵션 A: 폰에서
/eval baseline

# 옵션 B: 로컬 CLI
python -m eval.runner --all
RUN_DIR=$(ls -dt eval/runs/*/ | head -1)
python -m eval.judge --run-dir "$RUN_DIR"
cp "$RUN_DIR/_summary.json" eval/baseline.json
git add eval/baseline.json
git commit -m \"eval: record baseline (run <timestamp>)\"
```

목표 통과율: **≥ 70%** (Sonnet 4.6 기준). 미만이면 시스템 프롬프트나 케이스 설계
재점검 신호.

### 7.4 회귀 판정

[`.github/scripts/eval_compare.py`](../.github/scripts/eval_compare.py) 가
회귀를 결정:

- 통과율이 baseline 대비 **임계값(기본 10pp) 이상 하락**하면 회귀 → 워크플로우 실패
- 새로 실패한 케이스 / 새로 통과한 케이스 목록을 마크다운 리포트로 정리
- PR 트리거이면 결과를 PR 코멘트로 자동 게시

### 7.5 로컬에서 비교만 따로

```bash
python .github/scripts/eval_compare.py \
  --run-dir eval/runs/20260512-093015 \
  --baseline eval/baseline.json \
  --threshold-pp 10 \
  --output report.md
# 0=ok / no baseline, 1=regression, 2=error
```

---

## 8. 트러블슈팅

### \"Agent Zero 5분 안에 ready 안 됨\" (CI)

- `docker compose logs agent-zero | tail -100` 으로 부팅 로그 확인.
- 보통 settings.json 의 모델 이름 오타 / API 키 누락 / 메모리 부족.
- 로컬에서 `docker compose up -d --no-deps --build agent-zero` 로 같은 절차 재현.

### \"ANTHROPIC_API_KEY secret 미설정\"

CI 워크플로우 첫 단계가 fast-fail. repo Settings → Secrets and variables → Actions
에 추가하면 해결.

### `/eval` 이 \"eval 모듈을 import 할 수 없어\" 라고 거절

`docker-compose.yml` 에 `./eval:/app/eval` 마운트가 있는지 확인. 누락이면 추가 후
`docker compose up -d --force-recreate telegram-bridge`.

### judge 가 \"judge response not parseable\" 만 뱉음

util 모델이 JSON 응답 형식을 못 지킴. 가능한 원인:

1. judge_criteria 가 너무 모호 — 모델이 일반 텍스트 답변을 시도.
2. case `expected_behaviors` 가 매우 추상적이라 점수 산출 안 됨.
3. util 모델 자체 변경 (Haiku → 다른 모델) 후 형식 준수율 하락.

해결: 케이스의 `judge_criteria` 를 \"JSON으로만 답하되, score 는 0-1 사이 float\"
같이 명시 강화. 또는 [`eval/judge.py`](../eval/judge.py) 의 `_SYSTEM_PROMPT` 보강.

### CI 가 \"no baseline\" 으로만 계속 통과

`eval/baseline.json` 이 아직 커밋 안 됨. § 7.3 절차로 기록.

### 통과율은 같은데 회귀로 분류

`max_cost_usd` 또는 `max_turns` 가드를 새로 위반했을 수 있음 — 워크플로우 로그의
\"newly_failing\" 리스트 확인.

### 케이스 추가 PR 에서 \"새로 통과\" 로만 분류

baseline 에 그 케이스가 없었기 때문에 자연스러운 동작. 케이스 추가는
baseline 갱신과 함께 별도 PR 로 가는 게 정석 (\"eval: record baseline\" 같은
커밋으로).

---

## 9. 설계 메모

| 결정 | 이유 |
|---|---|
| **task_report 를 1차 소스로** | AZ 가 이미 정확한 토큰/비용 기록 — 우리가 카운팅 재구현하면 drift 위험 |
| **stdlib dataclass** | pydantic 의존을 피해 로컬 pytest 환경에서도 별도 설치 없이 동작 |
| **Protocol + Fake/HTTP 클라이언트** | HTTP/디스크 의존 없이 unit-test 가능 — runner, judge 양쪽 동일 패턴 |
| **`_` 프리픽스 파일은 runner 에서 제외** | 템플릿 (`_example.yaml`) / 드래프트 안전 보관 |
| **\"no baseline\" 은 exit 0** | fresh repo 가 CI 영구 실패 안 함 |
| **path-filtered main-push + 라벨 옵트인 PR** | 1회 \$0.50 비용을 \"매 PR\" 로 곱하지 않음 |
