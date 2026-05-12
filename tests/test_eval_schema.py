"""eval/schema.py 검증.

이 모듈은 agent-zero 내부 의존이 없는 stdlib + pyyaml 만 사용하므로
tests/conftest.py 의 stub 인프라와 무관하게 그대로 임포트 가능하다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eval.schema import EvalCase, EvalSchemaError, load_case, load_cases

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE = _REPO_ROOT / "eval" / "cases" / "_example.yaml"


# ── 정상 경로 ────────────────────────────────────────────────────────────


def test_example_case_loads():
    """레포에 커밋된 템플릿이 항상 스키마를 통과해야 한다 — 이게 깨지면
    `_example.yaml` 을 보고 케이스를 작성하는 사용자가 동일하게 실패한다."""
    case = load_case(_EXAMPLE)
    assert case.id == "example_pr_title_check"
    assert "PR 제목" in case.task
    assert len(case.expected_behaviors) >= 1
    assert "korean" in case.tags


def test_id_defaults_to_stem(tmp_path: Path):
    p = tmp_path / "my_case.yaml"
    p.write_text(
        "task: hello\nexpected_behaviors: [does the thing]\n",
        encoding="utf-8",
    )
    case = load_case(p)
    assert case.id == "my_case"


def test_load_cases_skips_underscore_prefix(tmp_path: Path):
    """`_example.yaml`, `_draft_xyz.yaml` 같은 템플릿은 자동 제외."""
    (tmp_path / "real.yaml").write_text(
        "task: t\nexpected_behaviors: [b1]\n", encoding="utf-8"
    )
    (tmp_path / "_skip.yaml").write_text(
        "task: t\nexpected_behaviors: [b1]\n", encoding="utf-8"
    )
    cases = load_cases(tmp_path)
    assert [c.id for c in cases] == ["real"]


def test_load_cases_sorted_by_filename(tmp_path: Path):
    for name in ["c.yaml", "a.yaml", "b.yaml"]:
        (tmp_path / name).write_text(
            "task: t\nexpected_behaviors: [b]\n", encoding="utf-8"
        )
    cases = load_cases(tmp_path)
    assert [c.id for c in cases] == ["a", "b", "c"]


# ── 검증 실패 경로 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_id",
    [
        "Has-Caps",
        "with space",
        "with-dash",
        "",
        "한글",
    ],
)
def test_invalid_id_rejected(bad_id: str):
    with pytest.raises(EvalSchemaError, match="id must match"):
        EvalCase(id=bad_id, task="t", expected_behaviors=["b"])


def test_task_required():
    with pytest.raises(EvalSchemaError, match="task is required"):
        EvalCase(id="x", task="", expected_behaviors=["b"])


def test_task_whitespace_only_rejected():
    with pytest.raises(EvalSchemaError, match="task is required"):
        EvalCase(id="x", task="   \n  ", expected_behaviors=["b"])


def test_expected_behaviors_required():
    with pytest.raises(EvalSchemaError, match="expected_behaviors"):
        EvalCase(id="x", task="t", expected_behaviors=[])


def test_max_turns_out_of_range():
    with pytest.raises(EvalSchemaError, match="max_turns"):
        EvalCase(id="x", task="t", expected_behaviors=["b"], max_turns=0)
    with pytest.raises(EvalSchemaError, match="max_turns"):
        EvalCase(id="x", task="t", expected_behaviors=["b"], max_turns=999)


def test_max_cost_guardrail():
    """비용 폭탄 방지 — 케이스 하나가 $10 를 넘게 책정되면 거부."""
    with pytest.raises(EvalSchemaError, match="max_cost_usd"):
        EvalCase(
            id="x", task="t", expected_behaviors=["b"], max_cost_usd=100.0
        )


def test_unknown_field_rejected(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "task: t\nexpected_behaviors: [b]\nunknown_field: oops\n",
        encoding="utf-8",
    )
    with pytest.raises(EvalSchemaError, match="unknown fields"):
        load_case(p)


def test_duplicate_id_rejected(tmp_path: Path):
    (tmp_path / "a.yaml").write_text(
        "id: same\ntask: t\nexpected_behaviors: [b]\n", encoding="utf-8"
    )
    (tmp_path / "b.yaml").write_text(
        "id: same\ntask: t\nexpected_behaviors: [b]\n", encoding="utf-8"
    )
    with pytest.raises(EvalSchemaError, match="duplicate case id"):
        load_cases(tmp_path)


def test_missing_file():
    with pytest.raises(EvalSchemaError, match="not found"):
        load_case("/nonexistent/path/case.yaml")


def test_non_mapping_yaml_rejected(tmp_path: Path):
    p = tmp_path / "list.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(EvalSchemaError, match="must be a mapping"):
        load_case(p)
