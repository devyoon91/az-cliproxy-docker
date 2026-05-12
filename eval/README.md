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
├── schema.py          # 케이스 YAML 스키마 + 로더 (이번 단계)
├── cases/             # 골든셋 케이스 YAML
│   └── _example.yaml  # 템플릿 (런너 대상에서 제외)
└── README.md
```

진행 예정 (마일스톤 #2):

- `eval/runner.py` — 케이스 실행 + 트레이스 캡처 ([#111](https://github.com/devyoon91/az-cliproxy-docker/issues/111))
- `eval/judge.py` — LLM-as-judge 채점 ([#112](https://github.com/devyoon91/az-cliproxy-docker/issues/112))
- 기본 골든셋 10개 ([#113](https://github.com/devyoon91/az-cliproxy-docker/issues/113))
- `/eval` Telegram 명령 ([#114](https://github.com/devyoon91/az-cliproxy-docker/issues/114))
- Dashboard eval 페이지 ([#115](https://github.com/devyoon91/az-cliproxy-docker/issues/115))
- CI 회귀 워크플로우 ([#116](https://github.com/devyoon91/az-cliproxy-docker/issues/116))
- `docs/eval.md` 가이드 ([#117](https://github.com/devyoon91/az-cliproxy-docker/issues/117))

## 케이스 작성

`eval/cases/_example.yaml` 을 복사해서 `eval/cases/<id>.yaml` 로 저장한다.
필드 설명은 템플릿 파일 상단 주석 참조.

| 필드 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `id` | 권장 | 파일명 stem | 영소문자/숫자/_ |
| `task` | ✓ | — | 에이전트 입력 메시지 |
| `tags` | | `[]` | 분류 라벨 |
| `expected_behaviors` | ✓ | — | judge 평가 대상 행동 (1개 이상) |
| `judge_criteria` | 권장 | `""` | 자유 형식 채점 기준 |
| `max_turns` | | 10 | 턴 수 가드 |
| `max_cost_usd` | | 0.50 | 비용 가드 |
| `timeout_sec` | | 120 | e2e 타임아웃 |

`_` 로 시작하는 파일은 템플릿으로 간주되어 런너에서 제외된다.

## 로드 검증 (현재 단계)

```python
from eval.schema import load_case, load_cases

case = load_case("eval/cases/_example.yaml")
cases = load_cases("eval/cases")  # _example.yaml 은 자동 제외
```

스키마 위반 시 `EvalSchemaError` 가 raise 된다. 자세한 규칙은
[`schema.py`](schema.py) 의 dataclass `EvalCase` 참조.
