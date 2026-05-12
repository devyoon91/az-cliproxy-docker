"""Pin `telegram-bridge/dashboard/eval_stats.py` — eval 회차 집계 (#115).

`_build_eval_stats` 의 4가지 output 키 (latest, pass_rate_trend,
per_case_avg, duration_distribution) 가 fixture 회차 JSON 들로부터
정확한 모양으로 나오는지 핀.  HTML 핸들러 자체는 thin wrapper 라
(test_bridge_dashboard.py 의 cost dashboard 와 동일 정책) unit-test 제외.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge"


def _load_eval_stats(tmp_runs_dir: Path):
    """Spec-load `dashboard.eval_stats` with EVAL_RUNS_DIR pointed at the
    test fixture directory.  template HTML 은 import 시점에 읽히지 않으므로
    eval_handlers 는 로드하지 않는다 — stats 만."""
    # 패키지 컨테이너 — 빈 namespace.
    if "dashboard" not in sys.modules:
        dash_pkg = types.ModuleType("dashboard")
        dash_pkg.__path__ = [str(_ROOT / "dashboard")]  # type: ignore[attr-defined]
        sys.modules["dashboard"] = dash_pkg

    spec = importlib.util.spec_from_file_location(
        "dashboard.eval_stats", _ROOT / "dashboard" / "eval_stats.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dashboard.eval_stats"] = mod
    spec.loader.exec_module(mod)
    mod.EVAL_RUNS_DIR = tmp_runs_dir  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def stats(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return _load_eval_stats(runs_dir)


def _make_summary(
    *,
    started_at: datetime,
    cases: list[dict],
    total_run_cost: float = 0.1,
    total_judge_cost: float = 0.001,
    elapsed_sec: float = 10.0,
) -> dict:
    """build_run_summary (#114) 와 같은 모양의 dict 만들기."""
    passed = sum(1 for c in cases if c.get("passed") is True)
    failed = sum(
        1
        for c in cases
        if c.get("passed") is False and not c.get("judge_error")
    )
    errored = sum(1 for c in cases if c.get("judge_error"))
    guard = sum(
        1
        for c in cases
        if c.get("guard_violations") or c.get("runner_error")
    )
    return {
        "run_dir": "/tmp/x",
        "started_at": started_at.isoformat(),
        "elapsed_sec": elapsed_sec,
        "total": len(cases),
        "passed_judges": passed,
        "failed_judges": failed,
        "errored": errored,
        "guard_violations": guard,
        "total_run_cost_usd": total_run_cost,
        "total_judge_cost_usd": total_judge_cost,
        "cases": cases,
    }


def _write_run(runs_dir: Path, name: str, summary: dict) -> None:
    d = runs_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)  # noqa: UP017


# ── 빈 상태 ─────────────────────────────────────────────────────────────


def test_no_runs_returns_empty_skeleton(stats):
    out = stats._build_eval_stats(range_days=30)
    assert out["total_runs"] == 0
    assert out["latest"] is None
    assert out["pass_rate_trend"] == []
    assert out["per_case_avg"] == []
    assert out["duration_distribution"] == []


def test_missing_runs_dir_returns_empty(tmp_path):
    """디렉토리 자체가 없을 때도 깨지지 않고 빈 결과."""
    mod = _load_eval_stats(tmp_path / "nonexistent")
    out = mod._build_eval_stats(range_days=30)
    assert out["total_runs"] == 0


def test_skips_run_dir_without_summary_json(stats, tmp_path):
    """summary.json 없는 빈 회차 디렉토리는 무시."""
    (tmp_path / "runs" / "20260512-100000").mkdir()
    out = stats._build_eval_stats(range_days=30)
    assert out["total_runs"] == 0


def test_skips_malformed_summary_json(stats, tmp_path):
    """깨진 JSON 도 silently skip."""
    bad_dir = tmp_path / "runs" / "20260512-110000"
    bad_dir.mkdir()
    (bad_dir / "_summary.json").write_text(
        "this is not json", encoding="utf-8"
    )
    out = stats._build_eval_stats(range_days=30)
    assert out["total_runs"] == 0


# ── 단일 회차 ───────────────────────────────────────────────────────────


def test_single_run_populates_latest(stats, tmp_path):
    started = _now_utc() - timedelta(hours=1)
    summary = _make_summary(
        started_at=started,
        cases=[
            {"case_id": "a", "passed": True, "score": 0.9, "run_cost_usd": 0.01,
             "judge_cost_usd": 0.0001, "duration_ms": 1000},
            {"case_id": "b", "passed": False, "score": 0.3, "run_cost_usd": 0.02,
             "judge_cost_usd": 0.0002, "duration_ms": 2000},
        ],
    )
    _write_run(tmp_path / "runs", "20260512-120000", summary)

    out = stats._build_eval_stats(range_days=30)
    assert out["total_runs"] == 1
    assert out["latest"] is not None
    assert out["latest"]["passed_judges"] == 1
    # trend 도 1 entry.
    assert len(out["pass_rate_trend"]) == 1
    assert out["pass_rate_trend"][0]["pass_rate"] == 0.5
    assert out["pass_rate_trend"][0]["passed"] == 1
    assert out["pass_rate_trend"][0]["total"] == 2


# ── 다중 회차 + 정렬 ────────────────────────────────────────────────────


def test_multiple_runs_sorted_by_started_at(stats, tmp_path):
    # 디스크 순서와 started_at 순서를 다르게 — 정렬이 시각 기준인지 검증.
    t1 = _now_utc() - timedelta(hours=3)
    t2 = _now_utc() - timedelta(hours=2)
    t3 = _now_utc() - timedelta(hours=1)
    _write_run(
        tmp_path / "runs",
        "z-third",
        _make_summary(
            started_at=t3,
            cases=[{"case_id": "x", "passed": True, "duration_ms": 100}],
        ),
    )
    _write_run(
        tmp_path / "runs",
        "a-first",
        _make_summary(
            started_at=t1,
            cases=[{"case_id": "x", "passed": False, "duration_ms": 100}],
        ),
    )
    _write_run(
        tmp_path / "runs",
        "m-second",
        _make_summary(
            started_at=t2,
            cases=[{"case_id": "x", "passed": True, "duration_ms": 100}],
        ),
    )

    out = stats._build_eval_stats(range_days=30)
    trend = out["pass_rate_trend"]
    assert len(trend) == 3
    assert trend[0]["pass_rate"] == 0.0  # t1
    assert trend[1]["pass_rate"] == 1.0  # t2
    assert trend[2]["pass_rate"] == 1.0  # t3
    # latest 는 가장 최근 (t3).
    assert out["latest"]["started_at"] == t3.isoformat()


# ── range 필터 ──────────────────────────────────────────────────────────


def test_range_days_filters_old_runs(stats, tmp_path):
    """범위 밖 회차는 trend/avg 에서 제외, latest 도 윈도우 내 가장 최근."""
    old = _now_utc() - timedelta(days=40)
    recent = _now_utc() - timedelta(days=2)
    _write_run(
        tmp_path / "runs",
        "old",
        _make_summary(
            started_at=old,
            cases=[{"case_id": "x", "passed": True, "duration_ms": 100}],
        ),
    )
    _write_run(
        tmp_path / "runs",
        "recent",
        _make_summary(
            started_at=recent,
            cases=[{"case_id": "x", "passed": False, "duration_ms": 100}],
        ),
    )

    out = stats._build_eval_stats(range_days=30)
    assert out["total_runs"] == 1  # old 제외
    assert len(out["pass_rate_trend"]) == 1
    assert out["pass_rate_trend"][0]["pass_rate"] == 0.0  # 'recent' 만


def test_range_clamp_min_one(stats, tmp_path):
    """range_days=0 같은 가장자리 입력에 대해서도 안전."""
    # 함수 자체는 range_days 를 그대로 받는다 (HTTP 핸들러가 clamp).
    # 0 을 줘도 _filter_window 가 cutoff=now → 빈 결과만 반환.
    out = stats._build_eval_stats(range_days=0)
    assert out["total_runs"] == 0


# ── per_case_avg ────────────────────────────────────────────────────────


def test_per_case_avg_aggregates_across_runs(stats, tmp_path):
    runs_dir = tmp_path / "runs"
    t1 = _now_utc() - timedelta(hours=3)
    t2 = _now_utc() - timedelta(hours=2)

    _write_run(
        runs_dir,
        "r1",
        _make_summary(
            started_at=t1,
            cases=[
                {"case_id": "alpha", "passed": True, "run_cost_usd": 0.01,
                 "judge_cost_usd": 0.0001, "duration_ms": 1000},
                {"case_id": "beta", "passed": False, "run_cost_usd": 0.02,
                 "judge_cost_usd": 0.0002, "duration_ms": 2000},
            ],
        ),
    )
    _write_run(
        runs_dir,
        "r2",
        _make_summary(
            started_at=t2,
            cases=[
                {"case_id": "alpha", "passed": True, "run_cost_usd": 0.03,
                 "judge_cost_usd": 0.0003, "duration_ms": 1500},
                {"case_id": "beta", "passed": True, "run_cost_usd": 0.02,
                 "judge_cost_usd": 0.0002, "duration_ms": 2200},
            ],
        ),
    )

    out = stats._build_eval_stats(range_days=30)
    by_case = {r["case_id"]: r for r in out["per_case_avg"]}

    # alpha: 2회 다 통과, 평균 비용 0.02, 평균 채점 0.0002, 평균 1250ms
    assert by_case["alpha"]["runs"] == 2
    assert by_case["alpha"]["pass_rate"] == 1.0
    assert by_case["alpha"]["avg_run_cost_usd"] == 0.02
    assert by_case["alpha"]["avg_judge_cost_usd"] == 0.0002
    assert by_case["alpha"]["avg_duration_ms"] == 1250

    # beta: 1/2 통과 (50%)
    assert by_case["beta"]["pass_rate"] == 0.5
    assert by_case["beta"]["avg_run_cost_usd"] == 0.02


# ── duration_distribution ───────────────────────────────────────────────


def test_duration_distribution_percentiles(stats, tmp_path):
    runs_dir = tmp_path / "runs"
    # 같은 케이스를 5번 — 100, 200, 300, 400, 500 ms.
    for i, ms in enumerate([100, 200, 300, 400, 500]):
        _write_run(
            runs_dir,
            f"run-{i:02d}",
            _make_summary(
                started_at=_now_utc() - timedelta(hours=10 - i),
                cases=[{"case_id": "x", "passed": True, "duration_ms": ms}],
            ),
        )

    out = stats._build_eval_stats(range_days=30)
    dist = {r["case_id"]: r for r in out["duration_distribution"]}
    # nearest-rank: 5 samples, p50 = index round(0.5*4)=2 → 300
    # p95 = index round(0.95*4)=4 → 500. max = 500.
    assert dist["x"]["p50_ms"] == 300
    assert dist["x"]["p95_ms"] == 500
    assert dist["x"]["max_ms"] == 500
    assert dist["x"]["runs"] == 5


def test_duration_percentile_single_sample(stats, tmp_path):
    """샘플 1개면 p50 == p95 == max."""
    _write_run(
        tmp_path / "runs",
        "only",
        _make_summary(
            started_at=_now_utc() - timedelta(minutes=10),
            cases=[{"case_id": "solo", "passed": True, "duration_ms": 777}],
        ),
    )
    out = stats._build_eval_stats(range_days=30)
    dist = {r["case_id"]: r for r in out["duration_distribution"]}
    assert dist["solo"]["p50_ms"] == 777
    assert dist["solo"]["p95_ms"] == 777
    assert dist["solo"]["max_ms"] == 777


# ── parse helpers ──────────────────────────────────────────────────────


def test_parse_started_at_handles_z_suffix(stats):
    """ISO 'Z' suffix 도 fromisoformat 호환되도록 처리."""
    s = stats._parse_started_at({"started_at": "2026-05-12T10:00:00Z"})
    assert s is not None
    assert s.tzinfo is not None


def test_parse_started_at_returns_none_on_bad_value(stats):
    assert stats._parse_started_at({"started_at": None}) is None
    assert stats._parse_started_at({}) is None
    assert stats._parse_started_at({"started_at": "not a date"}) is None


# ── case 항목 결손 케이스 ───────────────────────────────────────────────


def test_cases_missing_case_id_are_ignored(stats, tmp_path):
    _write_run(
        tmp_path / "runs",
        "weird",
        _make_summary(
            started_at=_now_utc() - timedelta(hours=1),
            cases=[
                {"passed": True, "duration_ms": 100},  # no case_id
                {"case_id": "ok", "passed": True, "duration_ms": 200},
            ],
        ),
    )
    out = stats._build_eval_stats(range_days=30)
    assert {r["case_id"] for r in out["per_case_avg"]} == {"ok"}
