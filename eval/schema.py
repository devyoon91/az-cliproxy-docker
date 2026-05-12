"""Eval case schema — YAML 로드 + 검증.

stdlib dataclass 기반. pydantic 의존을 피해 로컬 pytest 환경에서도
별도 설치 없이 돌아간다. 필요한 검증 규칙은 `EvalCase.__post_init__`
와 `load_case()` 에서 명시적으로 처리한다.

YAML 예시는 `eval/cases/_example.yaml` 참조.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 케이스 id 규칙: 영문 소문자/숫자/언더스코어 — 파일 이름·DB key 안전.
_ID_RE = re.compile(r"^[a-z0-9_]+$")

# 합리적 상한. 폭주하는 케이스로 비용 폭탄 나는 걸 막는 가드레일.
_MAX_TURNS_CEIL = 50
_MAX_COST_CEIL_USD = 10.0


class EvalSchemaError(ValueError):
    """케이스 YAML 이 스키마를 위반했을 때."""


@dataclass(frozen=True)
class EvalCase:
    """단일 골든셋 케이스.

    필드:
    - id: 케이스 식별자 (파일명과 일치 권장, 영소문자/숫자/_)
    - task: 에이전트에 입력할 사용자 메시지 (그대로 전달됨)
    - tags: 분류용 라벨 — 카테고리별 통과율 집계에 사용
    - expected_behaviors: 응답이 충족해야 할 행동 목록 (judge 가 평가)
    - judge_criteria: judge 채점 기준 (자유 형식 문장 — judge 프롬프트에 포함)
    - max_turns: 이 턴 수를 넘으면 실패 처리 (기본 10)
    - max_cost_usd: 이 비용을 넘으면 실패 처리 (기본 0.50)
    - timeout_sec: e2e 타임아웃 (기본 120초)
    """

    id: str
    task: str
    tags: list[str] = field(default_factory=list)
    expected_behaviors: list[str] = field(default_factory=list)
    judge_criteria: str = ""
    max_turns: int = 10
    max_cost_usd: float = 0.50
    timeout_sec: int = 120

    def __post_init__(self) -> None:
        if not self.id or not _ID_RE.match(self.id):
            raise EvalSchemaError(
                f"id must match {_ID_RE.pattern!r}, got {self.id!r}"
            )
        if not self.task or not self.task.strip():
            raise EvalSchemaError(f"task is required (case={self.id})")
        if not self.expected_behaviors:
            raise EvalSchemaError(
                f"expected_behaviors must be non-empty (case={self.id})"
            )
        if not isinstance(self.tags, list) or any(
            not isinstance(t, str) for t in self.tags
        ):
            raise EvalSchemaError(f"tags must be list[str] (case={self.id})")
        if self.max_turns <= 0 or self.max_turns > _MAX_TURNS_CEIL:
            raise EvalSchemaError(
                f"max_turns must be 1..{_MAX_TURNS_CEIL} (case={self.id}, "
                f"got {self.max_turns})"
            )
        if self.max_cost_usd <= 0 or self.max_cost_usd > _MAX_COST_CEIL_USD:
            raise EvalSchemaError(
                f"max_cost_usd must be (0, {_MAX_COST_CEIL_USD}] "
                f"(case={self.id}, got {self.max_cost_usd})"
            )
        if self.timeout_sec <= 0 or self.timeout_sec > 1800:
            raise EvalSchemaError(
                f"timeout_sec must be 1..1800 (case={self.id}, "
                f"got {self.timeout_sec})"
            )


def load_case(path: str | Path) -> EvalCase:
    """단일 YAML 파일을 EvalCase 로 로드."""
    p = Path(path)
    if not p.is_file():
        raise EvalSchemaError(f"case file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise EvalSchemaError(f"case file must be a mapping: {p}")

    # id 미지정 시 파일명(스템)에서 유추 — `eval/cases/foo.yaml` → id=foo
    data.setdefault("id", p.stem)

    known = {
        "id",
        "task",
        "tags",
        "expected_behaviors",
        "judge_criteria",
        "max_turns",
        "max_cost_usd",
        "timeout_sec",
    }
    unknown = set(data.keys()) - known
    if unknown:
        raise EvalSchemaError(
            f"unknown fields in {p.name}: {sorted(unknown)}"
        )

    return EvalCase(**data)


def load_cases(directory: str | Path) -> list[EvalCase]:
    """디렉토리 내 모든 `*.yaml` (단, `_` 로 시작하는 파일은 제외) 로드.

    `_example.yaml` 같이 언더스코어로 시작하는 파일은 템플릿이므로
    골든셋 실행 대상에서 제외한다.
    """
    d = Path(directory)
    if not d.is_dir():
        raise EvalSchemaError(f"cases directory not found: {d}")

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for path in sorted(d.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        case = load_case(path)
        if case.id in seen_ids:
            raise EvalSchemaError(f"duplicate case id: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)
    return cases
