"""Pin telegram-bridge/budget/engine.py — Phase K carve.

Pinning the threshold-walk semantics + cooldown-key bookkeeping.
`_budget_check_window` is the load-bearing piece — its exact behavior
defines when users get alerted vs not. Two regression-prone properties:

1. Highest crossed threshold fires first; lower ones skipped that call.
2. Once a (period, threshold) key is in `alerts_fired`, it never fires
   again that period (avoids spam on every /track).

`_compute_window_cost` is mostly window-math + delegation to task_agg
which is already pinned. Pin its shape so callers can rely on the
field names.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge"


def _load_engine_module(tmp_path):
    """Wire pricing.cost + task_agg.agg + budget.core, then load
    budget.engine against the on-disk file."""
    pricing_pkg = types.ModuleType("pricing")
    pricing_pkg.__path__ = [str(_ROOT / "pricing")]  # type: ignore[attr-defined]
    sys.modules["pricing"] = pricing_pkg
    cost_spec = importlib.util.spec_from_file_location(
        "pricing.cost", _ROOT / "pricing" / "cost.py"
    )
    assert cost_spec and cost_spec.loader
    cost_mod = importlib.util.module_from_spec(cost_spec)
    sys.modules["pricing.cost"] = cost_mod
    cost_spec.loader.exec_module(cost_mod)

    tasks_pkg = types.ModuleType("task_agg")
    tasks_pkg.__path__ = [str(_ROOT / "task_agg")]  # type: ignore[attr-defined]
    sys.modules["task_agg"] = tasks_pkg
    agg_spec = importlib.util.spec_from_file_location(
        "task_agg.agg", _ROOT / "task_agg" / "agg.py"
    )
    assert agg_spec and agg_spec.loader
    agg_mod = importlib.util.module_from_spec(agg_spec)
    sys.modules["task_agg.agg"] = agg_mod
    agg_spec.loader.exec_module(agg_mod)
    agg_mod.TASKS_DIR = str(tmp_path / "tasks")

    budget_pkg = types.ModuleType("budget")
    budget_pkg.__path__ = [str(_ROOT / "budget")]  # type: ignore[attr-defined]
    sys.modules["budget"] = budget_pkg
    core_spec = importlib.util.spec_from_file_location(
        "budget.core", _ROOT / "budget" / "core.py"
    )
    assert core_spec and core_spec.loader
    core_mod = importlib.util.module_from_spec(core_spec)
    sys.modules["budget.core"] = core_mod
    core_spec.loader.exec_module(core_mod)
    core_mod.BUDGET_DIR = str(tmp_path)
    core_mod.BUDGET_PATH = str(tmp_path / "budget.json")

    eng_spec = importlib.util.spec_from_file_location(
        "budget.engine", _ROOT / "budget" / "engine.py"
    )
    assert eng_spec and eng_spec.loader
    eng = importlib.util.module_from_spec(eng_spec)
    sys.modules["budget.engine"] = eng
    eng_spec.loader.exec_module(eng)
    return eng, core_mod, agg_mod


def _write_task(tasks_dir: Path, task_id: str, started_iso: str, cost: float) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps({
            "task_id": task_id,
            "started_at": started_iso,
            "ended_reason": "completed",
            "totals": {"cost_usd": cost, "tool_calls": 0, "llm_calls": 1},
            "llm_calls": [{"model": "claude-sonnet-4-5", "cost_usd": cost}],
        }),
        encoding="utf-8",
    )


@pytest.fixture
def engine(tmp_path, monkeypatch):
    eng, core, agg = _load_engine_module(tmp_path)
    # Reset state between tests.
    core._budget.clear()
    core._budget.update(core._budget_default())
    eng._send_alert = None
    # Pin "now" so windowed maths is deterministic.
    KST = timezone(timedelta(hours=9))
    fixed_now = datetime(2026, 5, 8, 12, 0, tzinfo=KST).replace(tzinfo=None)
    monkeypatch.setattr(eng, "_kst_now", lambda: fixed_now)
    return {"eng": eng, "core": core, "agg": agg, "tmp": tmp_path}


# ── _compute_window_cost ─────────────────────────────────────────────


def test_compute_window_cost_day_shape(engine):
    """All five contract fields present, regardless of task data."""
    info = engine["eng"]._compute_window_cost("day")
    assert set(info.keys()) == {"cost_usd", "tasks", "top_model", "period_id", "label"}
    assert info["period_id"] == "2026-05-08"
    assert "오늘" in info["label"]
    assert info["cost_usd"] == 0.0
    assert info["tasks"] == 0
    assert info["top_model"] is None


def test_compute_window_cost_week_label(engine):
    info = engine["eng"]._compute_window_cost("week")
    assert "최근 7일" in info["label"]
    # period_id is the ISO week string (G-W format)
    assert info["period_id"].startswith("2026-W")


def test_compute_window_cost_unknown_window_raises(engine):
    with pytest.raises(ValueError, match="unknown window"):
        engine["eng"]._compute_window_cost("month")


def test_compute_window_cost_picks_top_model(engine):
    """When tasks contain LLM calls, top_model returns (name, cost) of
    the highest-cost model in the window."""
    started = "2026-05-08T08:00:00+00:00"  # 17:00 KST 2026-05-08, in window
    _write_task(engine["tmp"] / "tasks", "t1", started, 1.0)
    info = engine["eng"]._compute_window_cost("day")
    assert info["tasks"] == 1
    assert info["cost_usd"] == pytest.approx(1.0)
    assert info["top_model"] is not None
    name, cost = info["top_model"]
    assert name == "claude-sonnet-4-5"
    assert cost == pytest.approx(1.0)


# ── _budget_check_window ─────────────────────────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_check_window_no_limit_returns_false(engine):
    """No limit configured → no alert ever fires."""
    engine["core"]._budget["day_limit_usd"] = None
    sent = _run(engine["eng"]._budget_check_window("day"))
    assert sent is False


def test_check_window_no_cost_returns_false(engine):
    """Limit set, but no spend in window → no alert."""
    engine["core"]._budget["day_limit_usd"] = 5.0
    sent = _run(engine["eng"]._budget_check_window("day"))
    assert sent is False


def test_check_window_fires_highest_crossed(engine):
    """When 100% is crossed (and 80% is also crossed implicitly), the
    HIGHEST level fires; lower thresholds also marked fired so they
    don't cascade on the next call. Test calls fake send_alert."""
    engine["core"]._budget["day_limit_usd"] = 5.0
    started = "2026-05-08T08:00:00+00:00"  # in window
    _write_task(engine["tmp"] / "tasks", "t1", started, 5.5)  # 110% of limit

    sent_msgs = []

    async def fake_send(msg):
        sent_msgs.append(msg)

    engine["eng"]._send_alert = fake_send
    sent = _run(engine["eng"]._budget_check_window("day"))
    assert sent is True
    # Exactly ONE telegram message — the 100% (highest crossed) one.
    assert len(sent_msgs) == 1
    assert "❌ 초과" in sent_msgs[0]  # 100% level_label

    # Both 80% and 100% keys marked fired (so next /track doesn't re-trigger).
    fired = engine["core"]._budget["alerts_fired"]
    assert "2026-05-08:day:80%" in fired
    assert "2026-05-08:day:100%" in fired


def test_check_window_cooldown_blocks_repeat(engine):
    """After the first alert fires, a second call same-period must NOT
    re-send even if spend keeps climbing."""
    engine["core"]._budget["day_limit_usd"] = 5.0
    started = "2026-05-08T08:00:00+00:00"
    _write_task(engine["tmp"] / "tasks", "t1", started, 4.5)  # 90% — crosses 80%

    sent_msgs = []

    async def fake_send(msg):
        sent_msgs.append(msg)

    engine["eng"]._send_alert = fake_send

    # First call: 80% fires (warning level).
    assert _run(engine["eng"]._budget_check_window("day")) is True
    assert len(sent_msgs) == 1
    assert "⚠️ 주의" in sent_msgs[0]
    assert "2026-05-08:day:80%" in engine["core"]._budget["alerts_fired"]

    # Second call same-period: cooldown blocks it even though still over 80%.
    assert _run(engine["eng"]._budget_check_window("day")) is False
    assert len(sent_msgs) == 1  # unchanged


def test_check_window_higher_threshold_after_lower(engine):
    """Realistic scenario: cost ramps from 80% to 110% across two
    calls. First call fires 80%, second fires 100% (since 100% wasn't
    yet in fired-set)."""
    engine["core"]._budget["day_limit_usd"] = 5.0
    sent_msgs = []

    async def fake_send(msg):
        sent_msgs.append(msg)

    engine["eng"]._send_alert = fake_send

    # First call: at 90% spend → 80% (warning) level fires.
    _write_task(engine["tmp"] / "tasks", "t1", "2026-05-08T08:00:00+00:00", 4.5)
    _run(engine["eng"]._budget_check_window("day"))
    assert "⚠️ 주의" in sent_msgs[0]

    # More spending pushes to 110%. Second call fires 100% (next-up
    # threshold that wasn't yet in fired-set).
    _write_task(engine["tmp"] / "tasks", "t2", "2026-05-08T08:30:00+00:00", 1.0)
    _run(engine["eng"]._budget_check_window("day"))
    assert len(sent_msgs) == 2
    assert "❌ 초과" in sent_msgs[1]


def test_configure_sets_send_alert(engine):
    """`configure(send_alert=...)` is the one public way to wire
    the callback. Without it, alerts log but don't propagate."""
    cb_calls = []

    async def cb(msg):
        cb_calls.append(msg)

    engine["eng"].configure(send_alert=cb)
    assert engine["eng"]._send_alert is cb


def test_check_window_no_callback_marks_cooldown_anyway(engine):
    """When no `_send_alert` is wired, the threshold still marks the
    cooldown — otherwise a misconfigured deploy would alert on every
    single /track in a tight loop."""
    engine["core"]._budget["day_limit_usd"] = 5.0
    _write_task(engine["tmp"] / "tasks", "t1", "2026-05-08T08:00:00+00:00", 4.5)
    engine["eng"]._send_alert = None

    sent = _run(engine["eng"]._budget_check_window("day"))
    assert sent is True
    assert "2026-05-08:day:80%" in engine["core"]._budget["alerts_fired"]
