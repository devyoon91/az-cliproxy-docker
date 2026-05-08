"""Pin telegram-bridge/pricing/usage.py — per-day accumulator + rotation.

Phase B carve-out from bot.py (issue #79). What's pinned:

1. `track_usage` increments all 7 token fields + cost on `usage_today`.
2. `track_usage` accumulates by_model totals separately from the
   top-level totals (and they stay consistent).
3. Daily rollover: when KST date advances, `usage_today` resets in
   place; previous day archived into `usage_history` (if non-empty).
4. `usage_history` capped at 7 entries.
5. `usage_today` and `usage_history` bindings stay valid through
   rollovers — that's the whole point of the clear+update pattern.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PRICING_DIR = Path(__file__).resolve().parent.parent / "telegram-bridge" / "pricing"


def _load_usage_module():
    """Load `pricing.usage` along with its `pricing.cost` dependency.

    We can't just `spec_from_file_location` usage.py — it does
    `from .cost import _normalize_model, calc_cost`, which needs the
    `pricing` package present. Easiest path: spec-load both and stitch
    them into sys.modules under the package name.
    """
    import sys
    import types

    pkg = types.ModuleType("bridge_pricing")
    pkg.__path__ = [str(_PRICING_DIR)]  # type: ignore[attr-defined]
    sys.modules["bridge_pricing"] = pkg

    cost_spec = importlib.util.spec_from_file_location(
        "bridge_pricing.cost", _PRICING_DIR / "cost.py"
    )
    assert cost_spec and cost_spec.loader
    cost_mod = importlib.util.module_from_spec(cost_spec)
    sys.modules["bridge_pricing.cost"] = cost_mod
    cost_spec.loader.exec_module(cost_mod)

    # usage.py says `from .cost import ...` — we need to give it the
    # right relative-import target. Easiest: rewrite the import on load
    # by patching its source at parse time. But cleaner: just register
    # the cost module under the same package name usage.py expects.
    src = (_PRICING_DIR / "usage.py").read_text(encoding="utf-8")
    # The relative import resolves against __package__. Setting it to
    # "bridge_pricing" makes `.cost` resolve to bridge_pricing.cost.
    usage_spec = importlib.util.spec_from_loader(
        "bridge_pricing.usage",
        loader=importlib.util.spec_from_file_location(
            "bridge_pricing.usage", _PRICING_DIR / "usage.py"
        ).loader,
    )
    assert usage_spec
    usage_mod = importlib.util.module_from_spec(usage_spec)
    usage_mod.__package__ = "bridge_pricing"
    sys.modules["bridge_pricing.usage"] = usage_mod
    exec(compile(src, str(_PRICING_DIR / "usage.py"), "exec"), usage_mod.__dict__)
    return usage_mod


@pytest.fixture
def usage(monkeypatch):
    mod = _load_usage_module()
    # Reset module state between tests (the module-load order means
    # state could otherwise leak across cases).
    mod.usage_today.clear()
    mod.usage_today.update(mod._empty_today_bucket("2026-05-08"))
    mod.usage_history.clear()
    # Pin "today" so tests are deterministic regardless of when CI runs.
    monkeypatch.setattr(
        mod, "_kst_now",
        lambda: datetime(2026, 5, 8, 12, 0, tzinfo=timezone(timedelta(hours=9))),
    )
    return mod


# ── tests ───────────────────────────────────────────────────────────


def test_track_usage_accumulates_top_level(usage):
    usage.track_usage("claude-fake-xyz", 1_000, 100, reasoning_tokens=500)
    assert usage.usage_today["input_tokens"] == 1_000
    assert usage.usage_today["output_tokens"] == 100
    assert usage.usage_today["reasoning_tokens"] == 500
    assert usage.usage_today["requests"] == 1
    # 1000 * $3/M + (100 + 500) * $15/M = $0.012
    assert usage.usage_today["cost_usd"] == pytest.approx(0.012, rel=1e-6)


def test_track_usage_accumulates_by_model(usage):
    usage.track_usage("claude-fake-a", 100, 10)
    usage.track_usage("claude-fake-a", 200, 20)
    usage.track_usage("claude-fake-b", 300, 30)
    by_model = usage.usage_today["by_model"]
    assert set(by_model.keys()) == {"claude-fake-a", "claude-fake-b"}
    assert by_model["claude-fake-a"]["requests"] == 2
    assert by_model["claude-fake-a"]["input_tokens"] == 300  # 100+200
    assert by_model["claude-fake-b"]["requests"] == 1
    # Top-level totals match sum of by_model
    assert usage.usage_today["requests"] == 3
    assert usage.usage_today["input_tokens"] == 600


def test_normalize_model_collapses_anthropic_prefix(usage):
    """A by_model split between 'anthropic/foo' and 'foo' was the
    original PR #24 Wave 2 bug — pin it."""
    usage.track_usage("anthropic/claude-fake-a", 100, 10)
    usage.track_usage("claude-fake-a", 200, 20)
    by_model = usage.usage_today["by_model"]
    # Both calls land in the same bucket.
    assert list(by_model.keys()) == ["claude-fake-a"]
    assert by_model["claude-fake-a"]["requests"] == 2


def test_daily_rollover_archives_previous_day(usage, monkeypatch):
    """When the KST date advances, usage_today resets and the prior
    day moves into usage_history (when there was actual activity)."""
    KST = timezone(timedelta(hours=9))
    monkeypatch.setattr(
        usage, "_kst_now", lambda: datetime(2026, 5, 8, 23, 50, tzinfo=KST)
    )
    usage.track_usage("claude-fake", 1_000, 100)
    assert usage.usage_today["date"] == "2026-05-08"
    assert usage.usage_today["requests"] == 1
    assert len(usage.usage_history) == 0

    # Cross midnight KST.
    monkeypatch.setattr(
        usage, "_kst_now", lambda: datetime(2026, 5, 9, 0, 5, tzinfo=KST)
    )
    usage.track_usage("claude-fake", 50, 5)

    assert usage.usage_today["date"] == "2026-05-09"
    assert usage.usage_today["requests"] == 1  # only the new day's call
    assert len(usage.usage_history) == 1
    archived = usage.usage_history[0]
    assert archived["date"] == "2026-05-08"
    assert archived["requests"] == 1


def test_history_cap_at_seven(usage, monkeypatch):
    """Rotation must drop oldest beyond 7 entries."""
    KST = timezone(timedelta(hours=9))
    for day in range(1, 11):  # 10 days
        monkeypatch.setattr(
            usage, "_kst_now",
            lambda d=day: datetime(2026, 5, d, 12, 0, tzinfo=KST),
        )
        usage.track_usage("claude-fake", 100, 10)
    # After 10 distinct days each with 1 request, history holds last 7,
    # usage_today is the 10th.
    assert len(usage.usage_history) == 7
    assert usage.usage_today["date"] == "2026-05-10"
    # Oldest archived should be day 3 (days 1+2 fell off the front).
    archived_dates = [h["date"] for h in usage.usage_history]
    assert archived_dates[0] == "2026-05-03"
    assert archived_dates[-1] == "2026-05-09"


def test_empty_day_not_archived(usage, monkeypatch):
    """A day with zero requests shouldn't pollute history with an empty bucket."""
    KST = timezone(timedelta(hours=9))
    # Day 1: no track_usage calls, stays empty.
    # Day 2: first track call triggers rollover — but day 1 had no
    # requests, so it should NOT enter history.
    monkeypatch.setattr(
        usage, "_kst_now", lambda: datetime(2026, 5, 9, 0, 5, tzinfo=KST)
    )
    usage.track_usage("claude-fake", 100, 10)
    assert usage.usage_today["date"] == "2026-05-09"
    assert len(usage.usage_history) == 0  # empty day 5/8 not archived


def test_bindings_survive_rollover(usage, monkeypatch):
    """The whole point of clear+update — `from pricing.usage import
    usage_today` outside callers must keep seeing fresh data after a
    rollover, not the snapshot of the old bucket."""
    KST = timezone(timedelta(hours=9))
    captured = usage.usage_today  # simulates bot.py's `from ... import usage_today`
    monkeypatch.setattr(
        usage, "_kst_now", lambda: datetime(2026, 5, 9, 0, 5, tzinfo=KST)
    )
    usage.track_usage("claude-fake", 100, 10)
    # `captured` is the SAME dict object — clear+update preserves identity.
    assert captured is usage.usage_today
    assert captured["date"] == "2026-05-09"
    assert captured["requests"] == 1
