# Eval — 골든셋 기반 하네스 품질 측정

마일스톤 [`Quality Evals 2026-05`](https://github.com/devyoon91/az-cliproxy-docker/milestone/2).
하네스(프롬프트·도구·모델·설정) 변경의 회귀를 외부 도구 없이 우리 코드 안에서
정량 감지하는 시스템이다.

## 4축 지표

| 축 | 지표 |
|---|---|
| 품질 | 골든셋 통과율, LLM-as-judge 점수 |
| 효율 | 태스크당 토큰/비용, cache hit rate |
| 지연 | TTFT, e2e 완료 시간 |
| 행동 | 턴 수, 도구 에러율 |

## 디렉토리

```
eval/
├── __init__.py
├── schema.py          # 케이스 YAML 스키마 + 로더
├── trace.py           # 트레이스 dataclass + task_report 변환
├── az_client.py       # AZClient Protocol + HTTPAZClient + FakeAZClient
├── runner.py          # 케이스 실행 + 가드 검사 + CLI
├── judge.py           # LLM-as-judge 채점기 + CLI
├── cases/             # 골든셋 케이스 YAML
│   ├── _example.yaml  # 템플릿 (런너 대상에서 제외)
│   └── *.yaml         # 실제 케이스 10개
├── runs/              # 회차별 결과 (gitignored)
│   └── <YYYYmmdd-HHMMSS>/
│       ├── <case_id>.json        # 트레이스 (runner)
│       ├── <case_id>.judge.json  # 채점 결과 (judge)
│       └── _summary.json         # 회차 합계
└── README.md
```

## 기본 골든셋 10개 (#113)

| id | 카테고리 | 검증 항목 |
|---|---|---|
| `simple_arithmetic` | basic | 기본 산술 + 짧은 응답 |
| `code_review_off_by_one` | code | 의도적 버그 코드 리뷰 |
| `pr_title_korean` | project_convention | 한국어 PR 제목 컨벤션 (70자 이내, 3개) |
| `korean_response_to_english` | korean | 영문 입력 → 한국어 응답 |
| `markdown_table_format` | format | 마크다운 표 형식 준수 |
| `concise_three_line_summary` | format | 길이 제약 (3줄) 준수 |
| `structured_json_output` | format | JSON 정확성 (타입 보존) |
| `refuse_obvious_jailbreak` | safety | 시스템 프롬프트 우회 거부 |
| `clarification_when_ambiguous` | communication | 모호한 요청에 확인 질문 |
| `error_diagnosis_missing_path` | error_recovery | 존재하지 않는 파일 진단 + 환각 방지 |

설계 원칙:
- **portable**: 클린 AZ 인스턴스에서 환경 의존(스킬·메모리·MCP 사전 설정) 없이 동작.
- **저렴**: 케이스당 `max_cost_usd` 가 $0.03~0.07 — 전체 1회 실행이 ~$0.50 이내.
- **빠름**: 대부분 `max_turns=2`, `timeout=30~60s`.

## 케이스 작성

`eval/cases/_example.yaml` 을 복사해서 `eval/cases/<id>.yaml` 로 저장.

| 필드 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `id` | 권장 | 파일명 stem | 영소문자/숫자/_ |
| `task` | ✓ | — | 에이전트 입력 메시지 |
| `tags` | | `[]` | 분류 라벨 |
| `expected_behaviors` | ✓ | — | judge 평가 대상 행동 (1개 이상) |
| `judge_criteria` | 권장 | `""` | 자유 형식 채점 기준 |
| `max_turns` | | 10 | 턴 수 가드 |
| `max_cost_usd` | | 0.50 | 비용 가드 (상한 $10) |
| `timeout_sec` | | 120 | e2e 타임아웃 |

`_` 로 시작하는 파일은 템플릿으로 간주되어 런너에서 제외된다.

## 실행

### 1. 전체 회차 실행

```bash
# AZ 컨테이너가 떠 있어야 함 (docker compose up -d)
python -m eval.runner --all
# → eval/runs/<timestamp>/<case_id>.json 생성

python -m eval.judge --run-dir eval/runs/<timestamp>
# → 같은 디렉토리에 <case_id>.judge.json 추가 + trace 의 judge_cost_usd 갱신
```

### 2. 단건 디버깅

```bash
python -m eval.runner --case pr_title_korean
python -m eval.judge --run-dir eval/runs/<timestamp> --pass-threshold 0.8
```

### 3. 환경 변수

| 변수 | 용도 | 기본 |
|---|---|---|
| `AZ_API_URL` | AZ HTTP 엔드포인트 | `http://localhost:50001` |
| `EVAL_TASKS_DIR` | AZ task_report JSON 디렉토리 | `agent-zero/logs/tasks` |
| `ANTHROPIC_API_KEY` | judge 호출용 | — |
| `EVAL_LOG_LEVEL` | 로깅 레벨 | `INFO` |

## baseline 기록 (수동)

CI 회귀 비교 ([#116](https://github.com/devyoon91/az-cliproxy-docker/issues/116)) 의 기준이 될
baseline 은 한 번 사람이 실행해 기록한다 — Anthropic API 비용 발생.

```bash
# 1) 전체 실행
python -m eval.runner --all
RUN_DIR=$(ls -dt eval/runs/*/ | head -1)

# 2) 채점
python -m eval.judge --run-dir "$RUN_DIR"

# 3) summary 복사
cp "$RUN_DIR/_summary.json" eval/baseline.json
git add eval/baseline.json && git commit -m "eval: record baseline (run <timestamp>)"
```

목표 baseline 통과율: **≥ 70%** (Sonnet 4.6 기준). 미만이면 시스템 프롬프트나
케이스 작성을 점검할 신호.

## 로드 검증

```python
from eval.schema import load_case, load_cases

case = load_case("eval/cases/pr_title_korean.yaml")
cases = load_cases("eval/cases")  # _example.yaml 은 자동 제외
```

스키마 위반 시 `EvalSchemaError` 가 raise 된다. 자세한 규칙은
[`schema.py`](schema.py) 의 dataclass `EvalCase` 참조.
