"""Pin telegram-bridge/webhooks/handlers.py — Phase M carve.

Pure code-motion carve: every dep was carved earlier, so handler
behavior didn't change. Pinning the JSON I/O contracts each route
fulfills, since these are what AZ-side code (task_report, probe) is
written against:

- POST /track  → 200 with current `usage_today` snapshot in body
- GET  /usage  → 200 with `usage_today` + last 7 entries of history
- POST /notify → 200 on happy path; calls send_telegram

webhook_handler's markdown→HTML conversion is already covered by
test_bridge_render_markdown. Here we just confirm the dispatch wiring.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge"


_OWN_MODULES = (
    "render", "render.markdown", "render.monitor",
    "notify", "notify.telegram",
    "pricing", "pricing.cost", "pricing.usage",
    "task_agg", "task_agg.agg",
    "budget", "budget.core", "budget.engine",
    "dashboard", "dashboard.auth", "dashboard.stats", "dashboard.handlers",
    "dashboard.eval_stats", "dashboard.eval_handlers",
    "webhooks", "webhooks.handlers",
)


def _load_webhooks_pkg(tmp_path):
    """Load handlers.py against fully-wired sibling deps."""
    # render
    render_pkg = types.ModuleType("render")
    render_pkg.__path__ = [str(_ROOT / "render")]  # type: ignore[attr-defined]
    sys.modules["render"] = render_pkg
    md_spec = importlib.util.spec_from_file_location(
        "render.markdown", _ROOT / "render" / "markdown.py"
    )
    assert md_spec and md_spec.loader
    md_mod = importlib.util.module_from_spec(md_spec)
    sys.modules["render.markdown"] = md_mod
    md_spec.loader.exec_module(md_mod)
    mon_spec = importlib.util.spec_from_file_location(
        "render.monitor", _ROOT / "render" / "monitor.py"
    )
    assert mon_spec and mon_spec.loader
    mon_mod = importlib.util.module_from_spec(mon_spec)
    sys.modules["render.monitor"] = mon_mod
    mon_spec.loader.exec_module(mon_mod)
    render_pkg.md_to_telegram_html = md_mod.md_to_telegram_html  # type: ignore[attr-defined]
    render_pkg.format_monitor_message = mon_mod.format_monitor_message  # type: ignore[attr-defined]
    render_pkg.short_id = mon_mod.short_id  # type: ignore[attr-defined]

    # notify
    notify_pkg = types.ModuleType("notify")
    notify_pkg.__path__ = [str(_ROOT / "notify")]  # type: ignore[attr-defined]
    sys.modules["notify"] = notify_pkg
    notify_spec = importlib.util.spec_from_file_location(
        "notify.telegram", _ROOT / "notify" / "telegram.py"
    )
    assert notify_spec and notify_spec.loader
    notify_mod = importlib.util.module_from_spec(notify_spec)
    sys.modules["notify.telegram"] = notify_mod
    notify_spec.loader.exec_module(notify_mod)

    # pricing.cost (transitive dep of usage)
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

    # pricing.usage
    usage_spec = importlib.util.spec_from_file_location(
        "pricing.usage", _ROOT / "pricing" / "usage.py"
    )
    assert usage_spec and usage_spec.loader
    usage_mod = importlib.util.module_from_spec(usage_spec)
    sys.modules["pricing.usage"] = usage_mod
    usage_spec.loader.exec_module(usage_mod)

    # task_agg.agg (transitive for budget.engine + dashboard.stats)
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

    # budget.core
    budget_pkg = types.ModuleType("budget")
    budget_pkg.__path__ = [str(_ROOT / "budget")]  # type: ignore[attr-defined]
    sys.modules["budget"] = budget_pkg
    bcore_spec = importlib.util.spec_from_file_location(
        "budget.core", _ROOT / "budget" / "core.py"
    )
    assert bcore_spec and bcore_spec.loader
    bcore_mod = importlib.util.module_from_spec(bcore_spec)
    sys.modules["budget.core"] = bcore_mod
    bcore_spec.loader.exec_module(bcore_mod)
    bcore_mod.BUDGET_DIR = str(tmp_path)
    bcore_mod.BUDGET_PATH = str(tmp_path / "budget.json")

    # budget.engine
    beng_spec = importlib.util.spec_from_file_location(
        "budget.engine", _ROOT / "budget" / "engine.py"
    )
    assert beng_spec and beng_spec.loader
    beng_mod = importlib.util.module_from_spec(beng_spec)
    sys.modules["budget.engine"] = beng_mod
    beng_spec.loader.exec_module(beng_mod)

    # dashboard
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
    handlers_spec = importlib.util.spec_from_file_location(
        "dashboard.handlers", _ROOT / "dashboard" / "handlers.py"
    )
    assert handlers_spec and handlers_spec.loader
    dh_mod = importlib.util.module_from_spec(handlers_spec)
    sys.modules["dashboard.handlers"] = dh_mod
    handlers_spec.loader.exec_module(dh_mod)
    dash_pkg.DASHBOARD_TOKEN = auth_mod.DASHBOARD_TOKEN  # type: ignore[attr-defined]
    dash_pkg.dashboard_handler = dh_mod.dashboard_handler  # type: ignore[attr-defined]
    dash_pkg.stats_api_handler = dh_mod.stats_api_handler  # type: ignore[attr-defined]

    # dashboard.eval_stats / eval_handlers (#115) — webhooks.handlers imports
    # eval_dashboard_handler + eval_stats_api_handler from `dashboard`.
    eval_stats_spec = importlib.util.spec_from_file_location(
        "dashboard.eval_stats", _ROOT / "dashboard" / "eval_stats.py"
    )
    assert eval_stats_spec and eval_stats_spec.loader
    eval_stats_mod = importlib.util.module_from_spec(eval_stats_spec)
    sys.modules["dashboard.eval_stats"] = eval_stats_mod
    eval_stats_spec.loader.exec_module(eval_stats_mod)
    eval_handlers_spec = importlib.util.spec_from_file_location(
        "dashboard.eval_handlers", _ROOT / "dashboard" / "eval_handlers.py"
    )
    assert eval_handlers_spec and eval_handlers_spec.loader
    eh_mod = importlib.util.module_from_spec(eval_handlers_spec)
    sys.modules["dashboard.eval_handlers"] = eh_mod
    eval_handlers_spec.loader.exec_module(eh_mod)
    dash_pkg.eval_dashboard_handler = eh_mod.eval_dashboard_handler  # type: ignore[attr-defined]
    dash_pkg.eval_stats_api_handler = eh_mod.eval_stats_api_handler  # type: ignore[attr-defined]

    # webhooks.handlers
    wh_pkg = types.ModuleType("webhooks")
    wh_pkg.__path__ = [str(_ROOT / "webhooks")]  # type: ignore[attr-defined]
    sys.modules["webhooks"] = wh_pkg
    wh_spec = importlib.util.spec_from_file_location(
        "webhooks.handlers", _ROOT / "webhooks" / "handlers.py"
    )
    assert wh_spec and wh_spec.loader
    wh = importlib.util.module_from_spec(wh_spec)
    sys.modules["webhooks.handlers"] = wh
    wh_spec.loader.exec_module(wh)

    return {
        "wh": wh, "usage": usage_mod, "notify": notify_mod,
        "budget_engine": beng_mod, "agg": agg_mod,
    }


@pytest.fixture
def env(tmp_path):
    # Snapshot modules that already exist so we don't permanently displace them.
    saved: dict = {name: sys.modules[name] for name in _OWN_MODULES if name in sys.modules}

    bag = _load_webhooks_pkg(tmp_path)
    bag["usage"].usage_today.clear()
    bag["usage"].usage_today.update(
        bag["usage"]._empty_today_bucket("2026-05-09"),
    )
    bag["usage"].usage_history.clear()

    yield bag

    # Restore any modules we shadowed; remove the new ones we injected.
    # This keeps test_pdf_export (which uses its own chat_pdf_export-side
    # `render.render` module) working when run after this file.
    for name in _OWN_MODULES:
        if name in saved:
            sys.modules[name] = saved[name]
        else:
            sys.modules.pop(name, None)


# ── helpers ──────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


def _read_response_body(resp) -> dict:
    """aiohttp's `web.json_response` returns Response with body bytes."""
    body = resp.body
    if hasattr(body, "_value"):
        body = body._value
    return json.loads(body.decode("utf-8"))


# ── /track ───────────────────────────────────────────────────────────


def test_track_handler_accumulates_usage(env):
    resp = _run(env["wh"].usage_track_handler(_FakeRequest({
        "model": "claude-sonnet-4-5",
        "input_tokens": 1000,
        "output_tokens": 100,
        "reasoning_tokens": 500,
    })))
    assert resp.status == 200
    body = _read_response_body(resp)
    assert body["ok"] is True
    today = body["today"]
    assert today["input_tokens"] == 1000
    assert today["output_tokens"] == 100
    assert today["reasoning_tokens"] == 500
    assert today["requests"] == 1
    # cost = 1000 * $3/M + (100+500) * $15/M = 0.012
    assert today["cost_usd"] == pytest.approx(0.012, rel=1e-6)


def test_track_handler_invalid_payload_500(env):
    """Bad request body bubbles up as 500 with `ok: false` so AZ-side
    knows to retry / log without taking the bridge down."""

    class _BadReq:
        async def json(self):
            raise ValueError("not json")

    resp = _run(env["wh"].usage_track_handler(_BadReq()))
    assert resp.status == 500
    body = _read_response_body(resp)
    assert body["ok"] is False
    assert "error" in body


def test_track_handler_missing_fields_default_zero(env):
    """Missing token fields default to 0 (defensive — old AZ versions
    might not send all fields)."""
    resp = _run(env["wh"].usage_track_handler(_FakeRequest({
        "model": "claude-sonnet-4-5",
        "input_tokens": 100,
    })))
    assert resp.status == 200
    body = _read_response_body(resp)
    assert body["today"]["output_tokens"] == 0
    assert body["today"]["cache_read_tokens"] == 0
    assert body["today"]["reasoning_tokens"] == 0


def test_track_handler_budget_check_failure_doesnt_500(env, monkeypatch):
    """Budget check is fire-and-forget — a failed alert path must NOT
    cause /track to reject. Crucial: AZ retries failed /track which
    would double-count usage."""

    async def boom():
        raise Exception("simulated alert failure")

    monkeypatch.setattr(env["wh"], "budget_check_all", boom)
    resp = _run(env["wh"].usage_track_handler(_FakeRequest({
        "model": "x", "input_tokens": 1, "output_tokens": 1,
    })))
    assert resp.status == 200  # /track still 200 despite budget exception


# ── /usage ───────────────────────────────────────────────────────────


def test_usage_get_handler_returns_today_and_history(env):
    """GET /usage shape is what the dashboard JS depends on."""
    # Seed some history
    env["usage"].usage_history.append({"date": "2026-05-08", "requests": 5})
    env["usage"].usage_today["requests"] = 3

    resp = _run(env["wh"].usage_get_handler(_FakeRequest({})))
    body = _read_response_body(resp)
    assert "today" in body
    assert "history" in body
    assert body["today"]["requests"] == 3
    assert isinstance(body["history"], list)
    assert body["history"][0]["date"] == "2026-05-08"


def test_usage_get_handler_history_capped_at_7(env):
    """GET /usage returns last 7 history entries even if the in-memory
    list has more (defensive — daily reset is supposed to cap this but
    if someone's been hand-mutating, the response stays bounded)."""
    for i in range(15):
        env["usage"].usage_history.append({"date": f"day-{i}", "requests": 1})

    resp = _run(env["wh"].usage_get_handler(_FakeRequest({})))
    body = _read_response_body(resp)
    assert len(body["history"]) == 7
    # Returns the LAST 7 (most recent), not first 7.
    assert body["history"][0]["date"] == "day-8"
    assert body["history"][-1]["date"] == "day-14"


# ── /notify ──────────────────────────────────────────────────────────


def test_notify_handler_calls_send_telegram(env):
    """POST /notify forwards `text` to send_telegram and returns 200."""
    sent = []

    async def fake_send(text, parse_mode=None, fallback_text=None):
        sent.append({"text": text, "parse_mode": parse_mode,
                     "fallback_text": fallback_text})

    env["wh"].send_telegram = fake_send  # patch at module level

    resp = _run(env["wh"].webhook_handler(_FakeRequest({
        "text": "hello from AZ",
    })))
    assert resp.status == 200
    body = _read_response_body(resp)
    assert body["ok"] is True
    assert len(sent) == 1
    assert sent[0]["text"] == "hello from AZ"


def test_notify_handler_markdown_conversion(env):
    """`markdown: true` runs md_to_telegram_html before send. Prefix
    (kind=task_response → 🤖) is added AFTER conversion so headers
    parse cleanly."""
    sent = []

    async def fake_send(text, parse_mode=None, fallback_text=None):
        sent.append({"text": text, "parse_mode": parse_mode,
                     "fallback_text": fallback_text})

    env["wh"].send_telegram = fake_send

    _run(env["wh"].webhook_handler(_FakeRequest({
        "text": "## Title\n\n**bold**",
        "markdown": True,
        "kind": "task_response",
    })))
    assert len(sent) == 1
    assert sent[0]["parse_mode"] == "HTML"
    # md_to_telegram_html converted ## and ** before the 🤖 prefix
    assert "<b>Title</b>" in sent[0]["text"]
    assert "<b>bold</b>" in sent[0]["text"]
    assert sent[0]["text"].startswith("🤖 ")
    # fallback_text has the prefix + raw markdown (parse-error safety net)
    assert sent[0]["fallback_text"].startswith("🤖 ")


def test_notify_handler_unknown_kind_no_prefix(env):
    """Unknown kind → no prefix injected. Defensive: extending KIND_PREFIX
    later shouldn't break old senders."""
    sent = []

    async def fake_send(text, parse_mode=None, fallback_text=None):
        sent.append(text)

    env["wh"].send_telegram = fake_send

    _run(env["wh"].webhook_handler(_FakeRequest({
        "text": "plain", "kind": "unknown",
    })))
    assert sent == ["plain"]
