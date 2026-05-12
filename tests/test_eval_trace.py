"""eval/trace.py 검증.

task_report JSON → Trace 변환의 모든 분기를 다룬다 — totals/iterations,
final_response 폴백, duration 계산, save/load 라운드트립.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from eval.trace import (
    ToolCall,
    Trace,
    make_run_dir,
    now_iso,
    save_trace,
)


def _sample_report() -> dict:
    """대표 task_report JSON 1건. 실제 AZ 출력 포맷을 미러링."""
    return {
        "task_id": "task-20260512-100000-abcdef",
        "started_at": "2026-05-12T01:00:00+00:00",
        "ended_at": "2026-05-12T01:00:12.5+00:00",
        "elapsed_sec": 12.5,
        "iterations": 3,
        "ended_reason": "completed",
        "final_response": "정상 응답 본문입니다.",
        "tool_calls": [
            {
                "name": "code_execution_tool",
                "args_preview": '{"runtime":"python","code":"print(1)"}',
                "duration_ms": 142.7,
            },
            {
                "name": "response",
                "args_preview": "정상 응답 본문입니다.",
                "duration_ms": 5.0,
            },
        ],
        "totals": {
            "tool_calls": 2,
            "llm_calls": 4,
            "input_tokens": 1500,
            "output_tokens": 320,
            "cache_read_tokens": 12000,
            "cache_creation_tokens": 800,
            "reasoning_tokens": 50,
            "cost_usd": 0.0421,
        },
    }


# ── 정상 변환 ───────────────────────────────────────────────────────────


def test_from_task_report_happy_path():
    trace = Trace.from_task_report(
        case_id="my_case",
        task="요약해줘",
        started_at="2026-05-12T01:00:00+00:00",
        ended_at="2026-05-12T01:00:12.5+00:00",
        report=_sample_report(),
    )
    assert trace.case_id == "my_case"
    assert trace.az_task_id == "task-20260512-100000-abcdef"
    assert trace.turns == 3
    assert trace.input_tokens == 1500
    assert trace.cache_read_tokens == 12000
    assert trace.cost_usd == 0.0421
    assert trace.duration_ms == 12500
    assert trace.final_response == "정상 응답 본문입니다."
    assert len(trace.tool_calls) == 2
    assert trace.tool_calls[0].name == "code_execution_tool"
    assert trace.tool_calls[0].duration_ms == 142  # float → int
    assert trace.guard_violations == []
    assert trace.error is None


def test_final_response_falls_back_to_response_tool_args():
    """task_report 가 final_response 를 안 채운 경우 — response 툴의
    args_preview 가 폴백."""
    report = _sample_report()
    del report["final_response"]
    trace = Trace.from_task_report(
        case_id="x",
        task="t",
        started_at=now_iso(),
        ended_at=now_iso(),
        report=report,
    )
    assert trace.final_response == "정상 응답 본문입니다."


def test_final_response_empty_when_no_response_tool():
    report = _sample_report()
    del report["final_response"]
    report["tool_calls"] = [
        tc for tc in report["tool_calls"] if tc["name"] != "response"
    ]
    trace = Trace.from_task_report(
        case_id="x",
        task="t",
        started_at=now_iso(),
        ended_at=now_iso(),
        report=report,
    )
    assert trace.final_response == ""


def test_duration_falls_back_to_iso_diff_when_elapsed_missing():
    report = _sample_report()
    del report["elapsed_sec"]
    trace = Trace.from_task_report(
        case_id="x",
        task="t",
        started_at="2026-05-12T01:00:00+00:00",
        ended_at="2026-05-12T01:00:10+00:00",
        report=report,
    )
    assert trace.duration_ms == 10000


def test_missing_totals_defaults_to_zero():
    """`totals` 키가 통째로 빠져도 트레이스 변환은 정상 동작 (0 으로 채움)."""
    trace = Trace.from_task_report(
        case_id="x",
        task="t",
        started_at=now_iso(),
        ended_at=now_iso(),
        report={"iterations": 1, "ended_reason": "completed"},
    )
    assert trace.input_tokens == 0
    assert trace.cost_usd == 0.0
    assert trace.turns == 1


def test_tool_call_duration_sec_compat():
    """legacy 포맷 (duration_sec 만 있고 duration_ms 가 없는) 도 변환되어야 한다."""
    report = _sample_report()
    report["tool_calls"] = [
        {"name": "x", "args_preview": "y", "duration_sec": 0.5}
    ]
    trace = Trace.from_task_report(
        case_id="x",
        task="t",
        started_at=now_iso(),
        ended_at=now_iso(),
        report=report,
    )
    assert trace.tool_calls[0].duration_ms == 500


# ── 저장 / 라운드트립 ───────────────────────────────────────────────────


def test_save_trace_writes_json(tmp_path: Path):
    trace = Trace.from_task_report(
        case_id="case_a",
        task="t",
        started_at=now_iso(),
        ended_at=now_iso(),
        report=_sample_report(),
    )
    path = save_trace(trace, tmp_path)
    assert path == tmp_path / "case_a.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["case_id"] == "case_a"
    assert on_disk["az_task_id"] == "task-20260512-100000-abcdef"
    assert on_disk["tool_calls"][0]["name"] == "code_execution_tool"


def test_make_run_dir_creates_timestamped_subdir(tmp_path: Path):
    fixed = datetime(2026, 5, 12, 9, 30, 15, tzinfo=timezone.utc)  # noqa: UP017
    d = make_run_dir(tmp_path, started_at=fixed)
    assert d.name == "20260512-093015"
    assert d.is_dir()
    assert d.parent == tmp_path


def test_trace_to_dict_preserves_tool_calls():
    """asdict 변환 시 ToolCall 도 dict 로 풀려야 JSON 직렬화 가능."""
    trace = Trace(
        case_id="x",
        task="t",
        started_at="s",
        ended_at="e",
        duration_ms=0,
        az_task_id=None,
        turns=0,
        tool_calls=[ToolCall(name="t", args_preview="a", duration_ms=1)],
        final_response="",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        reasoning_tokens=0,
        cost_usd=0.0,
    )
    d = trace.to_dict()
    assert d["tool_calls"][0] == {
        "name": "t",
        "args_preview": "a",
        "duration_ms": 1,
    }
    # JSON 직렬화 라운드트립.
    assert json.loads(json.dumps(d))["case_id"] == "x"
