"""Pin telegram-bridge/tasks/agg.py — Phase D carve.

These primitives feed `/today`, `/week`, `/tasks`, the dashboard's
`_build_stats`, and the budget engine's `_compute_window_cost`. A drift
between any two callers used to mean `/today` and the dashboard would
disagree — single source of truth now lives in agg.py and pinning it
catches regressions before they show up as user-visible inconsistency.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

_TASKS_DIR_FILE = (
    Path(__file__).resolve().parent.parent / "telegram-bridge" / "task_agg" / "agg.py"
)
_PRICING_DIR = (
    Path(__file__).resolve().parent.parent / "telegram-bridge" / "pricing"
)


def _load_agg_module(tmp_path):
    """Spec-load `tasks.agg` after wiring up its `pricing.cost` dependency.

    Same trick as `test_bridge_usage` and `test_bridge_budget`: register
    the package in sys.modules under a unique name so relative imports
    inside agg.py (`from pricing.cost import _model_info`) resolve.
    """
    # pricing.cost first — agg.py imports from it.
    pricing_pkg = types.ModuleType("pricing")
    pricing_pkg.__path__ = [str(_PRICING_DIR)]  # type: ignore[attr-defined]
    sys.modules["pricing"] = pricing_pkg

    cost_spec = importlib.util.spec_from_file_location(
        "pricing.cost", _PRICING_DIR / "cost.py"
    )
    assert cost_spec and cost_spec.loader
    cost_mod = importlib.util.module_from_spec(cost_spec)
    sys.modules["pricing.cost"] = cost_mod
    cost_spec.loader.exec_module(cost_mod)

    # Now tasks.agg.
    tasks_pkg = types.ModuleType("task_agg")
    tasks_pkg.__path__ = [str(_TASKS_DIR_FILE.parent)]  # type: ignore[attr-defined]
    sys.modules["task_agg"] = tasks_pkg

    spec = importlib.util.spec_from_file_location("task_agg.agg", _TASKS_DIR_FILE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["task_agg.agg"] = mod
    spec.loader.exec_module(mod)

    # Redirect TASKS_DIR for IO tests.
    mod.TASKS_DIR = str(tmp_path)
    return mod


@pytest.fixture
def agg(tmp_path):
    return _load_agg_module(tmp_path)


def _write_task(tmp_path: Path, task_id: str, started_at: str, payload: dict) -> None:
    payload = {"task_id": task_id, "started_at": started_at, **payload}
    (tmp_path / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


# ── _aggregate ─────────────────────────────────────────────────────────


def test_aggregate_empty(agg):
    out = agg._aggregate([])
    assert out["tasks"] == 0
    assert out["cost_usd"] == 0.0
    assert out["by_model"] == {}
    assert out["by_profile"] == {}


def test_aggregate_single_task(agg):
    task = {
        "task_id": "t1",
        "ended_reason": "completed",
        "profile": "ecs-lead",
        "totals": {
            "tool_calls": 3, "llm_calls": 4,
            "input_tokens": 1000, "output_tokens": 200,
            "cache_read_tokens": 800, "cache_creation_tokens": 100,
            "reasoning_tokens": 50,
            "cost_usd": 0.12,
        },
        "llm_calls": [
            {"model": "claude-sonnet-4-5", "input_tokens": 800, "output_tokens": 150,
             "cache_read_tokens": 700, "cache_creation_tokens": 100, "cost_usd": 0.10},
            {"model": "claude-haiku-4-5", "input_tokens": 200, "output_tokens": 50,
             "cache_read_tokens": 100, "cache_creation_tokens": 0, "cost_usd": 0.02},
        ],
    }
    out = agg._aggregate([task])
    assert out["tasks"] == 1
    assert out["completed"] == 1
    assert out["llm_calls"] == 4
    assert out["cost_usd"] == pytest.approx(0.12)
    assert out["reasoning_tokens"] == 50
    # by_model populated by per-call iteration (note llm_calls list, not totals)
    assert set(out["by_model"].keys()) == {"claude-sonnet-4-5", "claude-haiku-4-5"}
    assert out["by_model"]["claude-sonnet-4-5"]["calls"] == 1
    # by_profile aggregated from totals
    assert "ecs-lead" in out["by_profile"]
    assert out["by_profile"]["ecs-lead"]["tool_calls"] == 3


def test_aggregate_status_buckets(agg):
    tasks = [
        {"ended_reason": "completed", "totals": {}, "llm_calls": []},
        {"ended_reason": "completed", "totals": {}, "llm_calls": []},
        {"ended_reason": "orphaned", "totals": {}, "llm_calls": []},
        {"ended_reason": "pending", "totals": {}, "llm_calls": []},
    ]
    out = agg._aggregate(tasks)
    assert out["completed"] == 2
    assert out["orphaned"] == 1
    assert out["pending"] == 1


# ── _filter_date_range ─────────────────────────────────────────────────


def test_filter_date_range_inclusive_start_exclusive_end(agg):
    """Start-inclusive, end-exclusive — pinned because date-range bugs are
    classic and silently miss the boundary task."""
    tasks = [
        {"task_id": "before", "started_at": "2026-05-07T23:59:59+00:00"},
        {"task_id": "start", "started_at": "2026-05-08T00:00:00+00:00"},
        {"task_id": "during", "started_at": "2026-05-08T12:30:00+00:00"},
        {"task_id": "edge_end", "started_at": "2026-05-09T00:00:00+00:00"},
        {"task_id": "after", "started_at": "2026-05-09T00:00:01+00:00"},
    ]
    # Window: 2026-05-08 09:00 KST → 2026-05-09 09:00 KST (a "day" in KST)
    # 2026-05-08T00:00:00+00:00 → 09:00 KST → AT the start, included.
    # 2026-05-09T00:00:00+00:00 → 09:00 KST → AT end (exclusive), excluded.
    start = datetime(2026, 5, 8, 9, 0)
    end = datetime(2026, 5, 9, 9, 0)
    ids = [t["task_id"] for t in agg._filter_date_range(tasks, start, end)]
    assert "before" not in ids
    assert "start" in ids
    assert "during" in ids
    assert "edge_end" not in ids  # exclusive end
    assert "after" not in ids


def test_filter_date_range_drops_no_started_at(agg):
    tasks = [
        {"task_id": "ok", "started_at": "2026-05-08T12:00:00+00:00"},
        {"task_id": "missing"},
        {"task_id": "junk", "started_at": "this is not a timestamp"},
    ]
    out = agg._filter_date_range(tasks, datetime(2026, 1, 1), datetime(2027, 1, 1))
    ids = [t["task_id"] for t in out]
    assert ids == ["ok"]


# ── _is_anthropic_model ────────────────────────────────────────────────


def test_is_anthropic_model(agg):
    assert agg._is_anthropic_model("claude-sonnet-4-5") is True
    assert agg._is_anthropic_model("anthropic/claude-haiku-4-5") is True
    assert agg._is_anthropic_model("Claude-Sonnet-4-5") is True  # case-insensitive
    assert agg._is_anthropic_model("gpt-4o") is False
    assert agg._is_anthropic_model("") is False
    assert agg._is_anthropic_model(None) is False  # type: ignore[arg-type]


# ── _cache_efficiency ──────────────────────────────────────────────────


def test_cache_efficiency_no_anthropic_returns_none(agg):
    a = {"by_model": {"gpt-4o": {"calls": 1, "input": 100, "cache_read": 0,
                                  "cache_create": 0, "output": 50, "cost": 0.1}}}
    assert agg._cache_efficiency(a) is None


def test_cache_efficiency_hit_ratio_math(agg):
    a = {"by_model": {
        "claude-sonnet-4-5": {"calls": 10, "input": 100_000, "cache_read": 700_000,
                              "cache_create": 50_000, "output": 5_000, "cost": 1.0},
    }}
    ce = agg._cache_efficiency(a)
    assert ce is not None
    assert ce["anthropic_calls"] == 10
    # hit_ratio = 700k / (100k + 700k) = 0.875
    assert ce["hit_ratio"] == pytest.approx(0.875)


# ── _format_cache_line ─────────────────────────────────────────────────


def test_format_cache_line_emoji_branches(agg):
    base = {"input_tokens": 100, "cache_read_tokens": 100, "saved_usd": 0.5}
    # Healthy
    healthy = agg._format_cache_line({**base, "hit_ratio": 0.85})
    assert "✅" in healthy
    # Warning
    warning = agg._format_cache_line({**base, "hit_ratio": 0.55})
    assert "⚠️" in warning
    # Busted
    busted = agg._format_cache_line({**base, "hit_ratio": 0.20})
    assert "🚨" in busted
    # No data
    none = agg._format_cache_line({**base, "hit_ratio": None})
    assert "❔" in none


# ── data quality ───────────────────────────────────────────────────────


def test_data_quality_summary_clean(agg):
    tasks = [
        {"totals": {"cost_usd": 0.1}, "llm_calls": [{"model": "x"}]},
    ]
    dq = agg._data_quality_summary(tasks)
    assert dq == {"total": 1, "legacy_cost": 0, "approximate": 0}


def test_data_quality_legacy_cost_and_approximate(agg):
    tasks = [
        {"totals": {}, "llm_calls": []},  # no cost_usd → legacy
        {"totals": {"cost_usd": 0.01}, "llm_calls": [{"tokens_approximate": True}]},
    ]
    dq = agg._data_quality_summary(tasks)
    assert dq["legacy_cost"] == 1
    assert dq["approximate"] == 1


def test_quality_banner_clean_returns_none(agg):
    assert agg._quality_banner({"legacy_cost": 0, "approximate": 0}) is None


def test_quality_banner_dirty_includes_count(agg):
    text = agg._quality_banner({"legacy_cost": 3, "approximate": 5})
    assert "M3 이전 3건" in text
    assert "근사 토큰 5건" in text


# ── _load_task_jsons ───────────────────────────────────────────────────


def test_load_task_jsons_reads_sorted(agg, tmp_path):
    _write_task(tmp_path, "later", "2026-05-08T15:00:00+00:00", {})
    _write_task(tmp_path, "earlier", "2026-05-08T09:00:00+00:00", {})
    out = agg._load_task_jsons()
    assert [t["task_id"] for t in out] == ["earlier", "later"]


def test_load_task_jsons_skips_bad_files(agg, tmp_path):
    (tmp_path / "good.json").write_text(
        json.dumps({"task_id": "g", "started_at": "2026-05-08T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")
    (tmp_path / "ignored.tmp").write_text("ignored", encoding="utf-8")
    (tmp_path / "not-json.txt").write_text("nope", encoding="utf-8")
    out = agg._load_task_jsons()
    assert [t["task_id"] for t in out] == ["g"]


def test_load_task_jsons_missing_dir_returns_empty(agg, tmp_path):
    agg.TASKS_DIR = str(tmp_path / "does-not-exist")
    assert agg._load_task_jsons() == []


# ── _format_agg_block ──────────────────────────────────────────────────


def test_format_agg_block_zero_tasks(agg):
    a = {"tasks": 0, "completed": 0, "orphaned": 0, "pending": 0,
         "llm_calls": 0, "tool_calls": 0,
         "input_tokens": 0, "output_tokens": 0,
         "cache_read_tokens": 0, "cache_creation_tokens": 0,
         "reasoning_tokens": 0, "cost_usd": 0.0}
    lines = agg._format_agg_block("오늘", a)
    # When zero tasks, only the title + status row.
    assert len(lines) == 2
    assert "0건" in lines[1]


def test_format_agg_block_with_reasoning(agg):
    a = {"tasks": 5, "completed": 5, "orphaned": 0, "pending": 0,
         "llm_calls": 30, "tool_calls": 12,
         "input_tokens": 100_000, "output_tokens": 5_000,
         "cache_read_tokens": 80_000, "cache_creation_tokens": 5_000,
         "reasoning_tokens": 2_000, "cost_usd": 0.45}
    lines = agg._format_agg_block("오늘", a)
    full = "\n".join(lines)
    assert "사고 토큰: 2,000" in full
    assert "💰 $0.4500" in full
    assert "캐시:" in full
