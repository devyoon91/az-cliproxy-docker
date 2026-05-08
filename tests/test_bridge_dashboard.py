"""Pin telegram-bridge/dashboard/ — Phase E carve.

What's pinned:
1. `_check_dashboard_auth` — token-required, accepts query param,
   accepts header, rejects mismatch, rejects missing.
2. `_build_stats` — output shape contract that the dashboard JS
   depends on (charts blow up silently if fields disappear).
3. `DASHBOARD_HTML` template loads (no FileNotFound on import).

The async aiohttp request handlers (`stats_api_handler`,
`dashboard_handler`) aren't unit-tested here — they're thin wrappers
over auth + _build_stats and would need a full aiohttp test client
setup. Live verification via the `/api/stats` probe in the PR test
plan covers that path end-to-end.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge"


def _load_dashboard_pkg(tmp_path):
    """Spec-load `dashboard.auth` + `dashboard.stats` after wiring up
    `task_agg.agg` and `pricing.cost` (transitive dep)."""
    # pricing.cost
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

    # task_agg.agg
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
    agg_mod.TASKS_DIR = str(tmp_path)

    # dashboard package
    dash_pkg = types.ModuleType("dashboard")
    dash_pkg.__path__ = [str(_ROOT / "dashboard")]  # type: ignore[attr-defined]
    sys.modules["dashboard"] = dash_pkg

    auth_spec = importlib.util.spec_from_file_location(
        "dashboard.auth", _ROOT / "dashboard" / "auth.py"
    )
    assert auth_spec and auth_spec.loader
    auth_mod = importlib.util.module_from_spec(auth_spec)
    sys.modules["dashboard.auth"] = auth_mod
    auth_spec.loader.exec_module(auth_mod)

    stats_spec = importlib.util.spec_from_file_location(
        "dashboard.stats", _ROOT / "dashboard" / "stats.py"
    )
    assert stats_spec and stats_spec.loader
    stats_mod = importlib.util.module_from_spec(stats_spec)
    sys.modules["dashboard.stats"] = stats_mod
    stats_spec.loader.exec_module(stats_mod)

    return {"auth": auth_mod, "stats": stats_mod, "agg": agg_mod}


@pytest.fixture
def dash(tmp_path):
    return _load_dashboard_pkg(tmp_path)


# ── auth ────────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, query=None, headers=None):
        self.query = query or {}
        self.headers = headers or {}


def test_auth_disabled_when_token_unset(dash, monkeypatch):
    monkeypatch.setattr(dash["auth"], "DASHBOARD_TOKEN", "")
    assert dash["auth"]._check_dashboard_auth(_FakeRequest()) is False
    assert (
        dash["auth"]._check_dashboard_auth(_FakeRequest(query={"token": "anything"}))
        is False
    )


def test_auth_accepts_query_param(dash, monkeypatch):
    monkeypatch.setattr(dash["auth"], "DASHBOARD_TOKEN", "secret-xyz")
    assert (
        dash["auth"]._check_dashboard_auth(_FakeRequest(query={"token": "secret-xyz"}))
        is True
    )


def test_auth_accepts_header(dash, monkeypatch):
    monkeypatch.setattr(dash["auth"], "DASHBOARD_TOKEN", "secret-xyz")
    assert (
        dash["auth"]._check_dashboard_auth(
            _FakeRequest(headers={"X-Dashboard-Token": "secret-xyz"})
        )
        is True
    )


def test_auth_rejects_wrong_token(dash, monkeypatch):
    monkeypatch.setattr(dash["auth"], "DASHBOARD_TOKEN", "secret-xyz")
    assert (
        dash["auth"]._check_dashboard_auth(_FakeRequest(query={"token": "wrong"}))
        is False
    )


def test_auth_rejects_missing_token(dash, monkeypatch):
    monkeypatch.setattr(dash["auth"], "DASHBOARD_TOKEN", "secret-xyz")
    assert dash["auth"]._check_dashboard_auth(_FakeRequest()) is False


# ── _build_stats shape ───────────────────────────────────────────────


def _write_task(tmp_path: Path, task_id: str, started_at: str, **kwargs) -> None:
    payload = {"task_id": task_id, "started_at": started_at, **kwargs}
    (tmp_path / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_build_stats_empty_returns_full_shape(dash):
    """No tasks at all — every field the JS reads must still be present
    so chart.js doesn't crash on undefined."""
    s = dash["stats"]._build_stats(range_days=7)
    assert "now" in s
    assert s["range_days"] == 7
    # totals object with every field the dashboard JS expects
    expected_totals = {
        "tasks", "llm_calls", "tool_calls",
        "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_creation_tokens",
        "cost_usd",
    }
    assert set(s["totals"].keys()) == expected_totals
    assert s["totals"]["tasks"] == 0
    # daily array has one entry per day in range
    assert isinstance(s["daily"], list)
    assert len(s["daily"]) == 7
    for d in s["daily"]:
        assert {"date", "tasks", "cost", "llm_calls"} == set(d.keys())
    # by_model_7d + scatter present (empty when no tasks)
    assert s["by_model_7d"] == []
    assert s["scatter"] == []


def test_build_stats_clamps_range(dash):
    """The /api/stats handler caps range at 1..90 — but _build_stats
    doesn't clamp itself. The handler test (live) covers the clamp.
    Here we just confirm `_build_stats` honors the range_days argument."""
    s = dash["stats"]._build_stats(range_days=3)
    assert s["range_days"] == 3
    assert len(s["daily"]) == 3


def test_build_stats_includes_real_task(dash, tmp_path):
    """One task in the window should appear in totals + scatter +
    by_model_7d."""
    # Place the task within the past 24h KST so the default 30-day
    # window catches it. Use yesterday to avoid clock drift edge cases.
    from datetime import timedelta
    now = dash["agg"]._kst_now()
    started_kst = now - timedelta(hours=12)
    started_utc = started_kst.replace(tzinfo=None).isoformat() + "+00:00"
    _write_task(
        tmp_path,
        "task-x",
        started_utc,
        ended_reason="completed",
        elapsed_sec=12.5,
        profile="ecs-lead",
        totals={"tasks": 1, "tool_calls": 3, "llm_calls": 2,
                "input_tokens": 5000, "output_tokens": 200,
                "cache_read_tokens": 4000, "cache_creation_tokens": 0,
                "cost_usd": 0.05},
        llm_calls=[
            {"model": "claude-sonnet-4-5", "input_tokens": 5000,
             "output_tokens": 200, "cache_read_tokens": 4000,
             "cache_creation_tokens": 0, "cost_usd": 0.05},
        ],
    )
    s = dash["stats"]._build_stats(range_days=7)
    assert s["totals"]["tasks"] == 1
    assert s["totals"]["cost_usd"] == pytest.approx(0.05)
    assert len(s["scatter"]) == 1
    assert s["scatter"][0]["task_id"] == "task-x"
    assert s["scatter"][0]["profile"] == "ecs-lead"
    # by_model_7d — sorted desc by cost
    assert len(s["by_model_7d"]) == 1
    assert s["by_model_7d"][0]["model"] == "claude-sonnet-4-5"


# ── template ─────────────────────────────────────────────────────────


def test_template_html_loaded():
    """Sanity check that the static HTML loads at import — without this,
    `dashboard.handlers` import would fail outright."""
    template_path = _ROOT / "dashboard" / "template.html"
    assert template_path.is_file()
    text = template_path.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert "AZ Cost Dashboard" in text
    assert text.rstrip().endswith("</html>")
