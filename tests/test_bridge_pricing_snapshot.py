"""Pin telegram-bridge/pricing/snapshot.py — Phase G carve.

Pinning the pure helpers around the LiteLLM price-table snapshot +
drift detection. Async paths (`_fetch_litellm_table`, the orchestrator
`take_pricing_snapshot`) and the `daily_pricing_snapshot` scheduler
loop aren't pinned here — they're either network-bound or telegram-
bound and integration testing is the right harness for them.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge"


def _load_snapshot_module(tmp_path):
    """Load `pricing.snapshot` after wiring up its sibling deps.

    Snapshot imports `pricing.cost._model_cost_map`, `budget.core.BUDGET_DIR`,
    and `task_agg.agg.{_filter_date_range,_kst_now,_load_task_jsons}`.
    Set them up before loading.
    """
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

    budget_pkg = types.ModuleType("budget")
    budget_pkg.__path__ = [str(_ROOT / "budget")]  # type: ignore[attr-defined]
    sys.modules["budget"] = budget_pkg
    budget_core_spec = importlib.util.spec_from_file_location(
        "budget.core", _ROOT / "budget" / "core.py"
    )
    assert budget_core_spec and budget_core_spec.loader
    budget_core_mod = importlib.util.module_from_spec(budget_core_spec)
    sys.modules["budget.core"] = budget_core_mod
    budget_core_spec.loader.exec_module(budget_core_mod)
    # Redirect BUDGET_DIR for tests so /app/data isn't created on host.
    budget_core_mod.BUDGET_DIR = str(tmp_path)

    task_pkg = types.ModuleType("task_agg")
    task_pkg.__path__ = [str(_ROOT / "task_agg")]  # type: ignore[attr-defined]
    sys.modules["task_agg"] = task_pkg
    agg_spec = importlib.util.spec_from_file_location(
        "task_agg.agg", _ROOT / "task_agg" / "agg.py"
    )
    assert agg_spec and agg_spec.loader
    agg_mod = importlib.util.module_from_spec(agg_spec)
    sys.modules["task_agg.agg"] = agg_mod
    agg_spec.loader.exec_module(agg_mod)
    agg_mod.TASKS_DIR = str(tmp_path / "tasks")

    snapshot_spec = importlib.util.spec_from_file_location(
        "pricing.snapshot", _ROOT / "pricing" / "snapshot.py"
    )
    assert snapshot_spec and snapshot_spec.loader
    snap = importlib.util.module_from_spec(snapshot_spec)
    sys.modules["pricing.snapshot"] = snap
    snapshot_spec.loader.exec_module(snap)
    # Redirect snapshot dir into tmp so files don't escape.
    snap.PRICING_DIR = str(tmp_path / "pricing-snapshots")
    return snap, cost_mod, agg_mod


@pytest.fixture
def snap(tmp_path):
    s, _cost, _agg = _load_snapshot_module(tmp_path)
    return s


# ── _resolve_litellm_key ────────────────────────────────────────────


def test_resolve_litellm_key_known_alias(snap):
    """When the canonical key exists in the cost map, alias maps to it."""
    snap._model_cost_map.clear()
    snap._model_cost_map.update({"claude-sonnet-4-5-20250929": {}})
    assert (
        snap._resolve_litellm_key("anthropic/claude-sonnet-4-6")
        == "claude-sonnet-4-5-20250929"
    )
    assert (
        snap._resolve_litellm_key("claude-sonnet-4-6")
        == "claude-sonnet-4-5-20250929"
    )


def test_resolve_litellm_key_strip_anthropic_prefix(snap):
    snap._model_cost_map.clear()
    snap._model_cost_map.update({"some-model": {}})
    assert snap._resolve_litellm_key("anthropic/some-model") == "some-model"


def test_resolve_litellm_key_unknown(snap):
    snap._model_cost_map.clear()
    assert snap._resolve_litellm_key("totally-made-up") is None
    assert snap._resolve_litellm_key("") is None
    assert snap._resolve_litellm_key(None) is None  # type: ignore[arg-type]


# ── _select_for_snapshot ────────────────────────────────────────────


def test_select_for_snapshot_filters_to_interested(snap):
    table = {
        "modelA": {
            "input_cost_per_token": 3e-6,
            "output_cost_per_token": 1.5e-5,
            "context_window": 200000,  # not in PRICING_DIFF_FIELDS — drop
        },
        "modelB": {"input_cost_per_token": 1e-7},
        "irrelevant": {"input_cost_per_token": 5e-6},
    }
    interested = {"modelA": ["a/Model"], "modelB": ["b"]}
    out = snap._select_for_snapshot(table, interested)
    assert set(out.keys()) == {"modelA", "modelB"}
    assert "context_window" not in out["modelA"]
    assert out["modelA"]["az_aliases"] == ["a/Model"]


def test_select_for_snapshot_drops_models_with_no_priced_fields(snap):
    table = {"weirdmodel": {"context_window": 32000}}  # no rates
    out = snap._select_for_snapshot(table, {"weirdmodel": ["w"]})
    assert out == {}


# ── _diff_snapshots ─────────────────────────────────────────────────


def test_diff_snapshots_no_change(snap):
    s = {"models": {"m1": {"input_cost_per_token": 3e-6, "az_aliases": ["m1"]}}}
    assert snap._diff_snapshots(s, s) == []


def test_diff_snapshots_rate_change_with_pct(snap):
    prev = {"models": {"m1": {"input_cost_per_token": 3e-6, "az_aliases": ["x"]}}}
    curr = {"models": {"m1": {"input_cost_per_token": 2.5e-6, "az_aliases": ["x"]}}}
    out = snap._diff_snapshots(prev, curr)
    assert len(out) == 1
    ch = out[0]
    assert ch["model"] == "m1"
    assert ch["field"] == "input_cost_per_token"
    assert ch["before"] == pytest.approx(3e-6)
    assert ch["after"] == pytest.approx(2.5e-6)
    assert ch["pct_change"] == pytest.approx(-100 / 6, rel=1e-3)  # ~-16.67%


def test_diff_snapshots_new_field_reported(snap):
    """A field present in curr but not prev surfaces with before=None."""
    prev = {"models": {"m1": {"input_cost_per_token": 3e-6, "az_aliases": ["x"]}}}
    curr = {"models": {"m1": {"input_cost_per_token": 3e-6,
                              "cache_read_input_token_cost": 3e-7,
                              "az_aliases": ["x"]}}}
    out = snap._diff_snapshots(prev, curr)
    fields = {ch["field"] for ch in out}
    assert "cache_read_input_token_cost" in fields
    new_field = next(c for c in out if c["field"] == "cache_read_input_token_cost")
    assert new_field["before"] is None
    assert new_field["pct_change"] is None


def test_diff_snapshots_skips_non_numeric_pct(snap):
    """Non-numeric or zero before → pct_change is None (no division)."""
    prev = {"models": {"m1": {"input_cost_per_token": 0, "az_aliases": ["x"]}}}
    curr = {"models": {"m1": {"input_cost_per_token": 3e-6, "az_aliases": ["x"]}}}
    out = snap._diff_snapshots(prev, curr)
    assert out[0]["pct_change"] is None


# ── _format_pricing_diff ────────────────────────────────────────────


def test_format_pricing_diff_lines(snap):
    changes = [
        {"model": "claude-sonnet-4-5", "alias": "claude-sonnet-4-6",
         "field": "input_cost_per_token", "before": 3e-6, "after": 2.5e-6,
         "pct_change": -16.7},
        {"model": "claude-sonnet-4-5", "alias": "claude-sonnet-4-6",
         "field": "output_cost_per_token", "before": 1.5e-5, "after": 1.5e-5,
         "pct_change": 0.0},
    ]
    text = snap._format_pricing_diff(changes, "2026-04-27", "2026-04-28")
    assert "💱 가격 변동 감지 (2026-04-27 → 2026-04-28)" in text
    assert "claude-sonnet-4-5" in text
    assert "(claude-sonnet-4-6)" in text
    assert "$3.00/1M → $2.50/1M" in text
    assert "(-16.7%)" in text


def test_format_pricing_diff_handles_none_rates(snap):
    """A new field whose before is None should display as `—`."""
    changes = [{"model": "x", "alias": "x", "field": "cache_read_input_token_cost",
                "before": None, "after": 3e-7, "pct_change": None}]
    text = snap._format_pricing_diff(changes, "2026-04-27", "2026-04-28")
    assert "— → $0.30/1M" in text
    # No pct because before is None
    assert "%" not in text.split("$0.30/1M")[1].split("\n")[0]


# ── _save_snapshot / _load_snapshot / _list_snapshots ───────────────


def test_save_and_load_round_trip(snap):
    models = {"m1": {"input_cost_per_token": 3e-6, "az_aliases": ["x"]}}
    snap._save_snapshot("2026-05-08", models)
    out = snap._load_snapshot("2026-05-08")
    assert out is not None
    assert out["snapshot_date"] == "2026-05-08"
    assert out["models"] == models
    assert "fetched_at" in out
    assert out["source_url"] == snap.LITELLM_PRICE_URL


def test_load_missing_returns_none(snap):
    assert snap._load_snapshot("9999-99-99") is None


def test_list_snapshots_sorted_descending(snap):
    snap._save_snapshot("2026-05-01", {})
    snap._save_snapshot("2026-05-08", {})
    snap._save_snapshot("2026-05-05", {})
    assert snap._list_snapshots() == ["2026-05-08", "2026-05-05", "2026-05-01"]


def test_list_snapshots_skips_tmp_files(snap):
    snap._save_snapshot("2026-05-08", {})
    # Simulate a leftover .tmp from a partial write
    Path(snap.PRICING_DIR, "2026-05-09.json.tmp").write_text("{}", encoding="utf-8")
    assert snap._list_snapshots() == ["2026-05-08"]


# ── _previous_snapshot ──────────────────────────────────────────────


def test_previous_snapshot_finds_yesterday(snap):
    """`_save_snapshot` already wraps the dict under {'models': ...}; pass
    the per-model rates directly (not pre-wrapped)."""
    snap._save_snapshot("2026-05-07", {"m1": {"x": 1}})
    snap._save_snapshot("2026-05-08", {"m1": {"x": 2}})
    prev = snap._previous_snapshot(before="2026-05-08")
    assert prev is not None
    date, data = prev
    assert date == "2026-05-07"
    assert data["models"]["m1"]["x"] == 1


def test_previous_snapshot_none_when_first(snap):
    snap._save_snapshot("2026-05-08", {})
    assert snap._previous_snapshot(before="2026-05-08") is None


# ── _rotate_pricing_snapshots ───────────────────────────────────────


def test_rotate_drops_old_files(snap, monkeypatch):
    """Pin: snapshots older than `keep_days` removed; recent ones kept."""
    from datetime import datetime

    # Save with the real _kst_now (so the fetched_at field is happy),
    # then pin _kst_now to a fixed date for the rotate calculation.
    snap._save_snapshot("2026-05-08", {})  # today, keep
    snap._save_snapshot("2026-04-25", {})  # 13 days ago, keep at default 30
    snap._save_snapshot("2026-03-01", {})  # 68 days, drop at default 30
    snap._save_snapshot("2025-01-01", {})  # ages, drop

    monkeypatch.setattr(snap, "_kst_now", lambda: datetime(2026, 5, 8, 12, 0))

    removed = snap._rotate_pricing_snapshots(keep_days=30)
    assert removed == 2
    remaining = snap._list_snapshots()
    assert "2026-05-08" in remaining
    assert "2026-04-25" in remaining
    assert "2026-03-01" not in remaining


def test_rotate_no_dir_returns_zero(snap):
    """If PRICING_DIR doesn't exist, rotate is a no-op."""
    snap.PRICING_DIR = str(Path(snap.PRICING_DIR).parent / "no-such-dir")
    assert snap._rotate_pricing_snapshots() == 0


# ── _interested_models ──────────────────────────────────────────────


def test_interested_models_from_task_jsons(snap, tmp_path):
    """End-to-end of the task→model rollup. Writes fake task JSON,
    reads it through `_load_task_jsons`, expects a {key: aliases} map."""
    snap._model_cost_map.clear()
    snap._model_cost_map.update({"claude-sonnet-4-5-20250929": {}})

    # Set up task dir matching `agg_mod.TASKS_DIR`.
    from datetime import datetime, timedelta, timezone
    KST = timezone(timedelta(hours=9))
    started_kst = datetime.now(KST) - timedelta(hours=12)
    started_iso = started_kst.replace(tzinfo=None).isoformat() + "+00:00"

    tasks_dir = Path(tmp_path / "tasks")
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": "t1",
        "started_at": started_iso,
        "llm_calls": [
            {"model": "claude-sonnet-4-6"},
            {"model": "anthropic/claude-sonnet-4-6"},
            {"model": "totally-unknown-model"},  # not in _model_cost_map → skip
        ],
    }
    (tasks_dir / "t1.json").write_text(json.dumps(payload), encoding="utf-8")

    out = snap._interested_models(window_days=7)
    # Only the resolvable model surfaces; both aliases (sorted) are kept.
    assert list(out.keys()) == ["claude-sonnet-4-5-20250929"]
    assert out["claude-sonnet-4-5-20250929"] == [
        "anthropic/claude-sonnet-4-6",
        "claude-sonnet-4-6",
    ]
