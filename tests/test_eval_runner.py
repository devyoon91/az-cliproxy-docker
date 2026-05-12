"""eval/runner.py 검증 — FakeAZClient 로 HTTP/디스크 의존 없이 동작 검증.

테스트 시나리오:
- 단일 케이스 정상 실행 → trace JSON + summary JSON 생성
- 가드 위반 분기 (max_turns, max_cost, timeout)
- 클라이언트 오류 분기 (error 필드 채움)
- 전체 실행 + summary 집계
- CLI 인자 파싱 (--case, --all, --cases-dir, --az-url, --tasks-dir)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from eval.az_client import FakeAZClient, RunResult
from eval.runner import (
    RunSummary,
    _parse_args,
    _resolve_cases,
    run_all,
    run_case,
)
from eval.schema import EvalCase, EvalSchemaError


def _make_case(**overrides) -> EvalCase:
    """테스트용 기본 케이스. overrides 로 가드 필드를 좁힐 수 있다."""
    defaults = dict(
        id="t1",
        task="hello",
        expected_behaviors=["responds with hello"],
        judge_criteria="",
        tags=[],
        max_turns=10,
        max_cost_usd=0.50,
        timeout_sec=120,
    )
    defaults.update(overrides)
    return EvalCase(**defaults)


def _make_report(
    *,
    iterations: int = 1,
    cost: float = 0.001,
    input_tokens: int = 100,
    elapsed_sec: float = 1.0,
    response: str = "hi",
) -> dict:
    return {
        "task_id": "task-test-001",
        "started_at": "2026-05-12T01:00:00+00:00",
        "ended_at": "2026-05-12T01:00:01+00:00",
        "elapsed_sec": elapsed_sec,
        "iterations": iterations,
        "ended_reason": "completed",
        "final_response": response,
        "tool_calls": [
            {"name": "response", "args_preview": response, "duration_ms": 1}
        ],
        "totals": {
            "tool_calls": 1,
            "llm_calls": 1,
            "input_tokens": input_tokens,
            "output_tokens": 10,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": cost,
        },
    }


# ── run_case 단위 ───────────────────────────────────────────────────────


def test_run_case_success(tmp_path: Path):
    case = _make_case()
    client = FakeAZClient(
        [
            RunResult(
                task_report=_make_report(),
                started_at_iso="2026-05-12T01:00:00+00:00",
                ended_at_iso="2026-05-12T01:00:01+00:00",
            )
        ]
    )
    trace = asyncio.run(run_case(case, client, run_dir=tmp_path))

    assert client.received_tasks == ["hello"]
    assert client.received_timeouts == [120]
    assert trace.case_id == "t1"
    assert trace.turns == 1
    assert trace.cost_usd == 0.001
    assert trace.error is None
    assert trace.guard_violations == []
    # 디스크에도 저장.
    saved = json.loads((tmp_path / "t1.json").read_text(encoding="utf-8"))
    assert saved["case_id"] == "t1"


def test_run_case_records_client_error(tmp_path: Path):
    case = _make_case()
    client = FakeAZClient(
        [
            RunResult(
                task_report=None,
                started_at_iso="2026-05-12T01:00:00+00:00",
                ended_at_iso="2026-05-12T01:02:00+00:00",
                error="timed out waiting for task_report (120s)",
            )
        ]
    )
    trace = asyncio.run(run_case(case, client, run_dir=tmp_path))
    assert trace.error and "timed out" in trace.error
    assert trace.turns == 0
    assert trace.cost_usd == 0.0
    assert trace.duration_ms == 120_000


# ── 가드 검사 ───────────────────────────────────────────────────────────


def test_guard_max_turns_violation(tmp_path: Path):
    case = _make_case(max_turns=2)
    client = FakeAZClient(
        [
            RunResult(
                task_report=_make_report(iterations=5),
                started_at_iso="s",
                ended_at_iso="e",
            )
        ]
    )
    trace = asyncio.run(run_case(case, client, run_dir=tmp_path))
    assert any("max_turns_exceeded" in v for v in trace.guard_violations)


def test_guard_max_cost_violation(tmp_path: Path):
    case = _make_case(max_cost_usd=0.01)
    client = FakeAZClient(
        [
            RunResult(
                task_report=_make_report(cost=0.50),
                started_at_iso="s",
                ended_at_iso="e",
            )
        ]
    )
    trace = asyncio.run(run_case(case, client, run_dir=tmp_path))
    assert any("max_cost_exceeded" in v for v in trace.guard_violations)


def test_guard_timeout_violation(tmp_path: Path):
    """duration_ms 가 timeout_sec*1000 을 넘으면 timeout_exceeded 가드.

    클라이언트가 자체적으로 timeout 으로 끊었으면 task_report 가 None 이라
    error 분기로 들어가지만, AZ 가 응답하긴 했는데 너무 오래 걸린 경우는
    여기에 잡힌다."""
    case = _make_case(timeout_sec=1)
    client = FakeAZClient(
        [
            RunResult(
                task_report=_make_report(elapsed_sec=5.0),
                started_at_iso="s",
                ended_at_iso="e",
            )
        ]
    )
    trace = asyncio.run(run_case(case, client, run_dir=tmp_path))
    assert any("timeout_exceeded" in v for v in trace.guard_violations)


def test_multiple_guard_violations_all_reported(tmp_path: Path):
    case = _make_case(max_turns=1, max_cost_usd=0.001, timeout_sec=1)
    client = FakeAZClient(
        [
            RunResult(
                task_report=_make_report(
                    iterations=5, cost=0.5, elapsed_sec=5.0
                ),
                started_at_iso="s",
                ended_at_iso="e",
            )
        ]
    )
    trace = asyncio.run(run_case(case, client, run_dir=tmp_path))
    assert len(trace.guard_violations) == 3


# ── run_all ──────────────────────────────────────────────────────────────


def test_run_all_aggregates_summary(tmp_path: Path):
    cases = [
        _make_case(id="a"),
        _make_case(id="b", max_cost_usd=0.0001),  # 일부러 위반시킴
        _make_case(id="c"),
    ]
    client = FakeAZClient(
        [
            RunResult(
                task_report=_make_report(cost=0.001),
                started_at_iso="s",
                ended_at_iso="e",
            ),
            RunResult(
                task_report=_make_report(cost=0.01),  # max_cost 0.0001 위반
                started_at_iso="s",
                ended_at_iso="e",
            ),
            RunResult(
                task_report=_make_report(cost=0.002),
                started_at_iso="s",
                ended_at_iso="e",
            ),
        ]
    )
    summary: RunSummary = asyncio.run(
        run_all(cases, client, runs_dir=tmp_path)
    )
    assert summary.total == 3
    assert summary.passed_guards == 2
    assert summary.failed_guards == 1
    assert summary.total_cost_usd == pytest.approx(0.013, rel=1e-3)
    # run_dir 안에 3개 trace + summary.
    assert (summary.run_dir / "a.json").is_file()
    assert (summary.run_dir / "b.json").is_file()
    assert (summary.run_dir / "c.json").is_file()
    assert (summary.run_dir / "_summary.json").is_file()
    summary_json = json.loads(
        (summary.run_dir / "_summary.json").read_text(encoding="utf-8")
    )
    assert summary_json["passed_guards"] == 2
    assert summary_json["cases"][1]["case_id"] == "b"


def test_run_all_empty_cases_list(tmp_path: Path):
    client = FakeAZClient(
        [RunResult(task_report=_make_report(), started_at_iso="s", ended_at_iso="e")]
    )
    summary = asyncio.run(run_all([], client, runs_dir=tmp_path))
    assert summary.total == 0
    assert summary.passed_guards == 0
    assert client.received_tasks == []


# ── CLI 파싱 ─────────────────────────────────────────────────────────────


def test_parse_args_case_and_all_mutually_exclusive():
    with pytest.raises(SystemExit):
        _parse_args(["--case", "x", "--all"])


def test_parse_args_requires_one():
    with pytest.raises(SystemExit):
        _parse_args([])


def test_parse_args_case_with_overrides():
    ns = _parse_args(
        [
            "--case",
            "my_case",
            "--cases-dir",
            "custom/cases",
            "--runs-dir",
            "custom/runs",
            "--az-url",
            "http://example:9999",
            "--tasks-dir",
            "/tmp/tasks",
        ]
    )
    assert ns.case == "my_case"
    assert ns.cases_dir == "custom/cases"
    assert ns.runs_dir == "custom/runs"
    assert ns.az_url == "http://example:9999"
    assert ns.tasks_dir == "/tmp/tasks"


def test_parse_args_all():
    ns = _parse_args(["--all"])
    assert ns.all is True
    assert ns.case is None


# ── _resolve_cases ───────────────────────────────────────────────────────


def test_resolve_cases_all(tmp_path: Path):
    (tmp_path / "x.yaml").write_text(
        "task: t\nexpected_behaviors: [b]\n", encoding="utf-8"
    )
    (tmp_path / "y.yaml").write_text(
        "task: t\nexpected_behaviors: [b]\n", encoding="utf-8"
    )
    ns = _parse_args(["--all", "--cases-dir", str(tmp_path)])
    cases = _resolve_cases(ns)
    assert sorted(c.id for c in cases) == ["x", "y"]


def test_resolve_cases_single_by_id(tmp_path: Path):
    (tmp_path / "alpha.yaml").write_text(
        "task: t\nexpected_behaviors: [b]\n", encoding="utf-8"
    )
    ns = _parse_args(["--case", "alpha", "--cases-dir", str(tmp_path)])
    cases = _resolve_cases(ns)
    assert [c.id for c in cases] == ["alpha"]


def test_resolve_cases_single_by_path(tmp_path: Path):
    p = tmp_path / "beta.yaml"
    p.write_text("task: t\nexpected_behaviors: [b]\n", encoding="utf-8")
    ns = _parse_args(["--case", str(p)])
    cases = _resolve_cases(ns)
    assert [c.id for c in cases] == ["beta"]


def test_resolve_cases_missing_id(tmp_path: Path):
    ns = _parse_args(["--case", "ghost", "--cases-dir", str(tmp_path)])
    with pytest.raises(EvalSchemaError, match="case not found"):
        _resolve_cases(ns)


# ── FakeAZClient 동작 ───────────────────────────────────────────────────


def test_fake_client_replays_last_response_when_queue_empty():
    """캔드 응답 큐가 비면 마지막 응답을 반복 — 동일 결과 시뮬레이션 편의."""
    only = RunResult(
        task_report=_make_report(),
        started_at_iso="s",
        ended_at_iso="e",
    )
    client = FakeAZClient([only])
    r1 = asyncio.run(client.send_and_wait("a", timeout_sec=10))
    r2 = asyncio.run(client.send_and_wait("b", timeout_sec=20))
    assert r1 is only and r2 is only
    assert client.received_tasks == ["a", "b"]


def test_fake_client_requires_responses():
    with pytest.raises(ValueError):
        FakeAZClient([])
