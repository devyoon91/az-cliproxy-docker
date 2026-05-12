""".github/scripts/eval_compare.py 검증.

stand-alone CLI 이므로 dynamic import 로 로드.  비교 로직 (compare),
markdown 포맷터 (format_markdown), main() 의 분기들을 모두 핀.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / ".github" / "scripts" / "eval_compare.py"


def _load_compare_module():
    """`.github/scripts/eval_compare.py` 를 모듈로 spec-load.

    이름에 점이 없는 단일 파일 — 매번 새로 로드해서 sys.modules 오염 회피.
    """
    spec = importlib.util.spec_from_file_location(
        "eval_compare", _SCRIPT_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_compare"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load_compare_module()


def _summary(
    *,
    total: int = 10,
    passed: int = 8,
    cases: list[dict] | None = None,
    run_cost: float = 0.4,
    judge_cost: float = 0.004,
) -> dict:
    return {
        "total": total,
        "passed_judges": passed,
        "total_run_cost_usd": run_cost,
        "total_judge_cost_usd": judge_cost,
        "cases": cases or [],
    }


# ── compare ─────────────────────────────────────────────────────────────


def test_compare_no_change_is_ok(mod):
    s = _summary(total=10, passed=8)
    r = mod.compare(s, s, threshold_pp=10.0)
    assert r["verdict"] == "ok"
    assert r["delta_pp"] == 0.0


def test_compare_pass_rate_improvement(mod):
    base = _summary(total=10, passed=5)
    cur = _summary(total=10, passed=9)
    r = mod.compare(cur, base, threshold_pp=10.0)
    assert r["verdict"] == "ok"
    assert r["delta_pp"] == 40.0  # 50% → 90%


def test_compare_drop_below_threshold_is_ok(mod):
    base = _summary(total=10, passed=10)
    cur = _summary(total=10, passed=9)  # -10pp (정확히 임계값)
    r = mod.compare(cur, base, threshold_pp=10.0)
    # `< -threshold_pp` 조건이므로 정확히 -10pp 는 아직 OK.
    assert r["verdict"] == "ok"
    assert r["delta_pp"] == -10.0


def test_compare_drop_above_threshold_is_regression(mod):
    base = _summary(total=10, passed=10)
    cur = _summary(total=10, passed=8)  # -20pp
    r = mod.compare(cur, base, threshold_pp=10.0)
    assert r["verdict"] == "regression"
    assert r["delta_pp"] == -20.0


def test_compare_custom_threshold_strict(mod):
    """임계값을 5pp 로 좁히면 같은 변화도 회귀로 분류."""
    base = _summary(total=10, passed=10)
    cur = _summary(total=10, passed=9)  # -10pp
    r = mod.compare(cur, base, threshold_pp=5.0)
    assert r["verdict"] == "regression"


def test_compare_zero_total_handled(mod):
    """current 가 0건 (전부 fail/error) 이거나 baseline 이 0건이어도
    ZeroDivisionError 안 난다."""
    base = _summary(total=0, passed=0)
    cur = _summary(total=10, passed=5)
    r = mod.compare(cur, base, threshold_pp=10.0)
    # base rate = 0, cur rate = 0.5 → +50pp → ok
    assert r["verdict"] == "ok"
    assert r["delta_pp"] == 50.0
    # 반대 방향 (base 정상, cur 비어있음).
    r = mod.compare(_summary(total=0, passed=0), base, threshold_pp=10.0)
    assert r["delta_pp"] == 0.0


def test_compare_cost_delta_reported(mod):
    base = _summary(run_cost=0.5, judge_cost=0.005)  # total 0.505
    cur = _summary(run_cost=0.6, judge_cost=0.006)  # total 0.606
    r = mod.compare(cur, base, threshold_pp=10.0)
    # +20%
    assert r["cost_delta_pct"] == 20.0


def test_compare_cost_delta_zero_base(mod):
    """baseline 비용이 0 이어도 ZeroDivisionError 안 남."""
    base = _summary(run_cost=0.0, judge_cost=0.0)
    cur = _summary(run_cost=0.1, judge_cost=0.001)
    r = mod.compare(cur, base, threshold_pp=10.0)
    assert r["cost_delta_pct"] == 0.0  # divide-by-zero fallback


def test_compare_lists_newly_failing(mod):
    base = _summary(
        cases=[
            {"case_id": "a", "passed": True},
            {"case_id": "b", "passed": True},
            {"case_id": "c", "passed": True},
        ]
    )
    cur = _summary(
        cases=[
            {"case_id": "a", "passed": True},
            {"case_id": "b", "passed": False},  # 새로 실패
            {"case_id": "c", "passed": False},  # 새로 실패
        ]
    )
    r = mod.compare(cur, base, threshold_pp=50.0)  # 임계 넉넉히
    assert r["newly_failing"] == ["b", "c"]
    assert r["newly_passing"] == []


def test_compare_lists_newly_passing(mod):
    base = _summary(
        cases=[
            {"case_id": "a", "passed": False},
            {"case_id": "b", "passed": True},
        ]
    )
    cur = _summary(
        cases=[
            {"case_id": "a", "passed": True},  # 새로 통과
            {"case_id": "b", "passed": True},
        ]
    )
    r = mod.compare(cur, base, threshold_pp=10.0)
    assert r["newly_passing"] == ["a"]
    assert r["newly_failing"] == []


def test_compare_handles_missing_case_in_one_side(mod):
    """베이스라인엔 있는데 현재엔 빠진 케이스(또는 그 반대) 도 안전."""
    base = _summary(cases=[{"case_id": "removed", "passed": True}])
    cur = _summary(cases=[{"case_id": "added", "passed": False}])
    r = mod.compare(cur, base, threshold_pp=50.0)
    # 'removed' 는 base 에서 pass 였는데 cur 엔 없음 → cur_p=False → newly_failing
    assert "removed" in r["newly_failing"]
    # 'added' 는 base 에 없음 + cur 에서 fail → 양쪽 다 False → 변화 없음
    assert r["newly_passing"] == []


def test_compare_ignores_cases_without_id(mod):
    """case_id 없는 항목은 비교 대상에서 빠진다 — baseline 의 dict
    keying 단계에서 걸러져 newly_failing/passing 어느 쪽도 만들지 않는다."""
    base = _summary(cases=[{"passed": True}])  # id 없음 — 비교 모집단 0건
    cur = _summary(cases=[{"case_id": "x", "passed": True}])
    r = mod.compare(cur, base, threshold_pp=10.0)
    # x 는 base 에 없으므로 base_p=False, cur_p=True → newly_passing.
    # 이건 의도된 동작 (새로 추가된 케이스가 첫 회차에 통과한 상태).
    assert r["newly_failing"] == []
    assert r["newly_passing"] == ["x"]


def test_compare_case_only_in_current_treated_as_newly_passing(mod):
    """baseline 에 없는 케이스가 현재에서 통과 → newly_passing 으로 분류.

    의미적으로: 새 케이스를 추가한 PR 에서 그 케이스가 통과한 상태 → 리포트에
    '새로 통과' 로 보여 reviewer 가 한눈에 확인 가능.  실패라면 newly_failing.
    """
    base = _summary(cases=[])
    cur = _summary(cases=[{"case_id": "new_case", "passed": True}])
    r = mod.compare(cur, base, threshold_pp=10.0)
    assert r["newly_passing"] == ["new_case"]


def test_compare_case_only_in_baseline_treated_as_newly_failing(mod):
    """baseline 엔 통과로 있던 케이스가 현재 회차엔 빠짐 → newly_failing.
    누군가 케이스를 의도적으로 지웠을 때 reviewer 가 알아챌 수 있게."""
    base = _summary(cases=[{"case_id": "removed", "passed": True}])
    cur = _summary(cases=[])
    r = mod.compare(cur, base, threshold_pp=50.0)
    assert r["newly_failing"] == ["removed"]


# ── format_markdown ─────────────────────────────────────────────────────


def test_markdown_ok_path(mod):
    r = mod.compare(_summary(passed=9), _summary(passed=9), 10.0)
    md = mod.format_markdown(r)
    assert "✅" in md
    assert "Eval OK" in md
    assert "통과율" in md


def test_markdown_regression_path(mod):
    base = _summary(total=10, passed=10)
    cur = _summary(total=10, passed=6)  # -40pp
    r = mod.compare(cur, base, 10.0)
    md = mod.format_markdown(r)
    assert "❌" in md
    assert "Eval Regression Detected" in md
    assert "-40.0pp" in md


def test_markdown_lists_newly_failing_section(mod):
    base = _summary(cases=[{"case_id": "alpha", "passed": True}])
    cur = _summary(cases=[{"case_id": "alpha", "passed": False}])
    r = mod.compare(cur, base, 10.0)
    md = mod.format_markdown(r)
    assert "새로 실패" in md
    assert "`alpha`" in md


def test_markdown_no_change_sections_omitted(mod):
    r = mod.compare(_summary(), _summary(), 10.0)
    md = mod.format_markdown(r)
    assert "새로 실패" not in md
    assert "새로 통과" not in md


def test_markdown_no_baseline(mod):
    md = mod.format_no_baseline_markdown()
    assert "no baseline" in md.lower()
    assert "/eval baseline" in md


# ── main() / CLI ────────────────────────────────────────────────────────


def _write_summary(path: Path, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_summary(**kwargs), ensure_ascii=False),
        encoding="utf-8",
    )


def test_main_returns_0_when_no_baseline(mod, tmp_path, capsys):
    run_dir = tmp_path / "run"
    _write_summary(run_dir / "_summary.json")
    out_path = tmp_path / "report.md"
    rc = mod.main(
        [
            "--run-dir",
            str(run_dir),
            "--baseline",
            str(tmp_path / "missing_baseline.json"),
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    md = out_path.read_text(encoding="utf-8")
    assert "no baseline" in md.lower()


def test_main_returns_1_on_regression(mod, tmp_path):
    run_dir = tmp_path / "run"
    _write_summary(run_dir / "_summary.json", total=10, passed=5)
    baseline = tmp_path / "baseline.json"
    _write_summary(baseline, total=10, passed=10)
    rc = mod.main(
        [
            "--run-dir",
            str(run_dir),
            "--baseline",
            str(baseline),
            "--threshold-pp",
            "10",
            "--output",
            str(tmp_path / "report.md"),
        ]
    )
    assert rc == 1


def test_main_returns_0_on_ok(mod, tmp_path):
    run_dir = tmp_path / "run"
    _write_summary(run_dir / "_summary.json", total=10, passed=9)
    baseline = tmp_path / "baseline.json"
    _write_summary(baseline, total=10, passed=10)
    rc = mod.main(
        [
            "--run-dir",
            str(run_dir),
            "--baseline",
            str(baseline),
            "--threshold-pp",
            "20",
        ]
    )
    assert rc == 0


def test_main_returns_2_when_current_missing(mod, tmp_path):
    rc = mod.main(
        [
            "--run-dir",
            str(tmp_path / "no-such-dir"),
            "--baseline",
            str(tmp_path / "baseline.json"),
        ]
    )
    assert rc == 2


def test_main_requires_run_dir_or_current(mod):
    with pytest.raises(SystemExit):
        mod.main(["--baseline", "x"])


def test_main_supports_current_arg(mod, tmp_path):
    cur = tmp_path / "cur.json"
    _write_summary(cur)
    rc = mod.main(
        [
            "--current",
            str(cur),
            "--baseline",
            str(tmp_path / "missing.json"),
            "--output",
            str(tmp_path / "out.md"),
        ]
    )
    assert rc == 0
