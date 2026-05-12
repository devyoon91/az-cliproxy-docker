"""커밋된 골든셋 10개가 항상 스키마를 통과하도록 가드.

`load_cases("eval/cases")` 만 하면 모든 케이스 YAML 이 한 번에 검증된다 —
케이스 추가/수정 시 잘못된 필드나 가드 위반 값을 PR 단계에서 즉시 차단.

별도로 우리가 골든셋의 모양에 대해 명시한 약속들도 함께 핀:
- 10개 이상 (마일스톤 #113 acceptance: "10개 작성")
- 각 케이스가 expected_behaviors >= 2 (judge 가 부분 점수 줄 여지 필요)
- 각 케이스의 max_cost_usd 가 운영 가드라인($0.20) 이내
- 모든 케이스 id 가 unique
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eval.schema import load_cases

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CASES_DIR = _REPO_ROOT / "eval" / "cases"

# 개별 케이스의 max_cost_usd 운영 가드.  골든셋 1회 풀 실행이 ~$2 이내로
# 유지되도록 — 케이스가 늘어도 cost 폭주를 막는 1차 방어.
_PER_CASE_BUDGET_USD = 0.20


def test_golden_set_loads():
    """모든 케이스가 스키마 통과 + 골든셋 크기 >= 10."""
    cases = load_cases(_CASES_DIR)
    assert len(cases) >= 10, f"expected >=10 cases, got {len(cases)}"


def test_all_case_ids_unique():
    cases = load_cases(_CASES_DIR)
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


def test_each_case_has_multiple_expected_behaviors():
    """expected_behaviors 가 한 개뿐이면 judge 가 binary 평가밖에 못 한다 —
    골든셋 케이스에는 최소 2개 행동을 요구해서 부분 점수 산출이 가능하게."""
    cases = load_cases(_CASES_DIR)
    too_few = [c.id for c in cases if len(c.expected_behaviors) < 2]
    assert not too_few, f"cases with <2 expected_behaviors: {too_few}"


def test_each_case_within_budget():
    cases = load_cases(_CASES_DIR)
    over = [
        (c.id, c.max_cost_usd)
        for c in cases
        if c.max_cost_usd > _PER_CASE_BUDGET_USD
    ]
    assert not over, (
        f"cases over ${_PER_CASE_BUDGET_USD} budget guard: {over}"
    )


def test_each_case_has_judge_criteria():
    """judge_criteria 가 비면 judge 가 expected_behaviors 만 보고 채점하게
    되는데, 모호한 케이스에서 분산이 커진다.  골든셋은 항상 명시."""
    cases = load_cases(_CASES_DIR)
    missing = [c.id for c in cases if not c.judge_criteria.strip()]
    assert not missing, f"cases without judge_criteria: {missing}"


@pytest.mark.parametrize(
    "expected_id",
    [
        "simple_arithmetic",
        "code_review_off_by_one",
        "pr_title_korean",
        "korean_response_to_english",
        "markdown_table_format",
        "concise_three_line_summary",
        "structured_json_output",
        "refuse_obvious_jailbreak",
        "clarification_when_ambiguous",
        "error_diagnosis_missing_path",
    ],
)
def test_known_case_present(expected_id: str):
    """README 표에 등재된 케이스 10종이 실제 파일로 존재해야 한다.
    이름이 바뀌면 README / 다운스트림 baseline 비교도 깨지므로 이름을 핀."""
    cases = load_cases(_CASES_DIR)
    ids = {c.id for c in cases}
    assert expected_id in ids, f"missing case: {expected_id}"
