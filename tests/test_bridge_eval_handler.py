"""Pin `telegram-bridge/telegram_handlers/eval.py` pure helpers (#114).

Async `cmd_eval` 자체는 today.py 패턴을 따라 mock 하네스 부재로 unit-test
대상 외 — 실제 통합은 사용자가 /eval 로 실행해서 검증한다. 이 파일은
다음 pure helper 들을 핀:

- `parse_eval_args` (mode + target 결정)
- `format_report` (통과율 / 비용 / 케이스별 결과 — Telegram 메시지 본문)
- `build_run_summary` (회차 summary dict — _summary.json + baseline.json)
- `save_baseline` (baseline.json 파일 쓰기)

또한 import-time _EVAL_AVAILABLE 가드가 ImportError 에도 모듈이 깨끗하게
로드되는지를 한 케이스로 확인.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HANDLER_PATH = (
    _ROOT / "telegram-bridge" / "telegram_handlers" / "eval.py"
)


def _wire_eval_handler(monkeypatch) -> object:
    """Spec-load `telegram_handlers.eval` after fake `telegram` 모듈을 심는다.

    eval 패키지 자체는 repo 루트의 eval/ 가 sys.path 에 있어 자연스럽게
    import 됨 (conftest.py 가 repo 루트를 sys.path 에 추가).
    """
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")

    # 가짜 telegram + telegram.ext — today.py / chat.py 테스트와 동일 형태.
    if "telegram" not in sys.modules:
        telegram_pkg = types.ModuleType("telegram")
        telegram_pkg.Update = object  # type: ignore[attr-defined]
        sys.modules["telegram"] = telegram_pkg
        ext = types.ModuleType("telegram.ext")

        class _CTX:
            DEFAULT_TYPE = object

        ext.ContextTypes = _CTX  # type: ignore[attr-defined]
        sys.modules["telegram.ext"] = ext

    # telegram_handlers 패키지로 인지되도록 빈 패키지를 심는다.
    if "telegram_handlers" not in sys.modules:
        pkg = types.ModuleType("telegram_handlers")
        pkg.__path__ = [str(_HANDLER_PATH.parent)]  # type: ignore[attr-defined]
        sys.modules["telegram_handlers"] = pkg

    spec = importlib.util.spec_from_file_location(
        "telegram_handlers.eval", _HANDLER_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["telegram_handlers.eval"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler(monkeypatch):
    return _wire_eval_handler(monkeypatch)


# ── parse_eval_args ─────────────────────────────────────────────────────


def test_parse_args_empty_is_all(handler):
    assert handler.parse_eval_args([]) == ("all", None)


def test_parse_args_baseline(handler):
    assert handler.parse_eval_args(["baseline"]) == ("baseline", None)


def test_parse_args_single_case_id(handler):
    assert handler.parse_eval_args(["pr_title_korean"]) == (
        "single",
        "pr_title_korean",
    )


def test_parse_args_too_many_returns_error(handler):
    out = handler.parse_eval_args(["a", "b"])
    assert isinstance(out, str)
    assert "사용법" in out


def test_parse_args_whitespace_only_treated_as_all(handler):
    """``/eval     `` 같이 args 가 공백 토큰 하나면 'all' 로 본다."""
    assert handler.parse_eval_args(["   "]) == ("all", None)


# ── format_report ───────────────────────────────────────────────────────


def _fake_trace(
    *,
    case_id: str,
    cost_usd: float = 0.01,
    judge_cost_usd: float = 0.001,
    guard_violations: list[str] | None = None,
    error: str | None = None,
    turns: int = 1,
    duration_ms: int = 1000,
) -> object:
    """duck-typed trace — 핸들러는 .case_id, .cost_usd, .judge_cost_usd,
    .guard_violations, .error, .turns, .duration_ms 만 본다."""

    class _T:
        pass

    t = _T()
    t.case_id = case_id
    t.cost_usd = cost_usd
    t.judge_cost_usd = judge_cost_usd
    t.guard_violations = guard_violations or []
    t.error = error
    t.turns = turns
    t.duration_ms = duration_ms
    return t


def _fake_judge(
    *,
    case_id: str,
    score: float = 1.0,
    passed: bool = True,
    reasoning: str = "ok",
    cost_usd: float = 0.001,
    error: str | None = None,
) -> object:
    class _J:
        pass

    j = _J()
    j.case_id = case_id
    j.score = score
    j.passed = passed
    j.reasoning = reasoning
    j.cost_usd = cost_usd
    j.error = error
    return j


def test_format_report_all_passed(handler):
    traces = [
        _fake_trace(case_id="a"),
        _fake_trace(case_id="b"),
    ]
    judges = [
        _fake_judge(case_id="a", score=0.9),
        _fake_judge(case_id="b", score=0.85),
    ]
    out = handler.format_report(
        traces, judges, mode="all", elapsed_sec=12.5
    )
    assert "2/2 (100%)" in out
    assert "✅" in out
    assert "12.5s" in out


def test_format_report_mixed_pass_fail(handler):
    traces = [
        _fake_trace(case_id="ok_case"),
        _fake_trace(case_id="fail_case"),
        _fake_trace(case_id="guard_case", guard_violations=["max_turns_exceeded (5>3)"]),
        _fake_trace(case_id="error_case", error="timed out waiting for task_report"),
    ]
    judges = [
        _fake_judge(case_id="ok_case", passed=True, score=0.9),
        _fake_judge(case_id="fail_case", passed=False, score=0.3, reasoning="기준 미달"),
        _fake_judge(
            case_id="guard_case",
            passed=False,
            score=0.0,
            error="guard 위반",
        ),
        # error_case 는 judge 결과 없음 (runner error 라 judge 가 안 돔)
    ]
    out = handler.format_report(
        traces, judges, mode="all", elapsed_sec=20.0
    )
    # 통과는 ok_case 1건만.
    assert "1/4 (25%)" in out
    # 각 케이스 라인이 존재.
    assert "ok_case" in out
    assert "fail_case" in out
    assert "guard_case" in out
    assert "error_case" in out
    # 가드 위반 라벨.
    assert "guard:" in out
    # runner 오류 라벨.
    assert "runner:" in out


def test_format_report_caps_at_4000_chars(handler):
    """매우 많은 케이스가 들어와도 Telegram 4096 한계 이내로 truncate.

    한 라인의 길이가 충분히 길도록 reasoning 을 채워 둔다 — case_id 만
    짧은 케이스 200개 정도로는 한계에 미치지 못해 truncate 가 안 일어남."""
    long_reason = "긴 reasoning 텍스트 " * 5  # ~50 chars
    traces = [_fake_trace(case_id=f"case_id_long_{i}") for i in range(200)]
    judges = [
        _fake_judge(
            case_id=f"case_id_long_{i}",
            passed=False,
            score=0.3,
            reasoning=long_reason,
        )
        for i in range(200)
    ]
    out = handler.format_report(
        traces, judges, mode="all", elapsed_sec=1.0
    )
    assert len(out) <= 4096
    assert "잘림" in out


def test_format_report_cost_aggregation(handler):
    traces = [
        _fake_trace(case_id="a", cost_usd=0.02),
        _fake_trace(case_id="b", cost_usd=0.03),
    ]
    judges = [
        _fake_judge(case_id="a", cost_usd=0.0005),
        _fake_judge(case_id="b", cost_usd=0.0007),
    ]
    out = handler.format_report(
        traces, judges, mode="all", elapsed_sec=5.0
    )
    assert "$0.0500" in out  # 실행 합계
    assert "$0.0012" in out  # 채점 합계


# ── build_run_summary ───────────────────────────────────────────────────


def test_build_run_summary_structure(handler):
    from datetime import datetime, timezone

    started_at = datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
    traces = [
        _fake_trace(case_id="a", cost_usd=0.01, judge_cost_usd=0.001, turns=2, duration_ms=2000),
        _fake_trace(case_id="b", cost_usd=0.02, error="boom"),
    ]
    judges = [
        _fake_judge(case_id="a", passed=True, score=0.9, cost_usd=0.001),
        # b 는 judge 결과 없음.
    ]
    run_dir = Path("tmp") / "runs" / "2026"
    summary = handler.build_run_summary(
        traces,
        judges,
        run_dir=run_dir,
        started_at=started_at,
        elapsed_sec=15.5,
    )

    # 경로 비교는 OS 호환을 위해 str(Path) 로.
    assert summary["run_dir"] == str(run_dir)
    assert summary["started_at"] == "2026-05-12T10:00:00+00:00"
    assert summary["elapsed_sec"] == 15.5
    assert summary["total"] == 2
    assert summary["passed_judges"] == 1
    assert summary["failed_judges"] == 0
    assert summary["errored"] == 0
    assert summary["guard_violations"] == 1  # b 가 error
    assert summary["total_run_cost_usd"] == 0.03
    assert summary["total_judge_cost_usd"] == 0.001
    # 케이스별.
    assert len(summary["cases"]) == 2
    a_case = next(c for c in summary["cases"] if c["case_id"] == "a")
    assert a_case["passed"] is True
    assert a_case["score"] == 0.9
    assert a_case["turns"] == 2
    b_case = next(c for c in summary["cases"] if c["case_id"] == "b")
    assert b_case["runner_error"] == "boom"
    assert b_case["passed"] is None  # judge 결과 없음
    assert b_case["score"] is None


def test_build_run_summary_counts_judge_errors(handler):
    from datetime import datetime, timezone

    traces = [_fake_trace(case_id="a"), _fake_trace(case_id="b")]
    judges = [
        _fake_judge(case_id="a", passed=False, error="parse failed"),
        _fake_judge(case_id="b", passed=False, score=0.3),
    ]
    summary = handler.build_run_summary(
        traces,
        judges,
        run_dir=Path("/r"),
        started_at=datetime.now(timezone.utc),  # noqa: UP017
        elapsed_sec=1.0,
    )
    assert summary["errored"] == 1  # a
    assert summary["failed_judges"] == 1  # b
    assert summary["passed_judges"] == 0


# ── save_baseline ───────────────────────────────────────────────────────


def test_save_baseline_writes_json_with_parent_creation(
    handler, tmp_path
):
    summary = {
        "total": 5,
        "passed_judges": 4,
        "total_run_cost_usd": 0.123,
    }
    target = tmp_path / "nested" / "baseline.json"
    handler.save_baseline(summary, target)
    assert target.is_file()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["passed_judges"] == 4


def test_save_baseline_overwrites(handler, tmp_path):
    target = tmp_path / "baseline.json"
    target.write_text('{"old": true}', encoding="utf-8")
    handler.save_baseline({"new": True}, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == {"new": True}


# ── 모듈 로드 가드 ──────────────────────────────────────────────────────


def test_module_exposes_eval_available_flag(handler):
    """import 가 성공하면 _EVAL_AVAILABLE 가 True 여야 한다.  실패 시 cmd_eval
    이 친절한 에러를 내도록 별도로 처리됨."""
    assert hasattr(handler, "_EVAL_AVAILABLE")
    # 로컬 테스트 환경에서는 eval/ 가 repo 루트에 있고 conftest 가 sys.path
    # 에 넣어주므로 True 여야 한다.
    assert handler._EVAL_AVAILABLE is True
    assert handler._EVAL_IMPORT_ERROR is None
