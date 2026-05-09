"""Pin telegram-bridge optional-telegram path — issue #106.

Verifies that bot.py and the telegram_handlers/* modules can be imported
(and bot.py's TELEGRAM_ENABLED flag computed correctly) when the
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars are missing or empty.
The container must keep running (with only the aiohttp dashboard +
webhook server) instead of crashing on KeyError at import time.

We don't drive bot.py's main() here — `loop.run_forever()` would block
the test. The flag check is enough to prove the disabled branch is
reached; live verification of the actual run is in the PR test plan.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_BRIDGE = Path(__file__).resolve().parent.parent / "telegram-bridge"


def _purge_bridge_modules() -> None:
    """Drop any previously-loaded bridge modules so the next spec_load
    re-runs module top-level code with the current env. Without this,
    pytest accumulates state across tests (env-derived module globals
    cache the FIRST test's env)."""
    for name in list(sys.modules):
        if name == "bot" or name.startswith((
            "telegram_handlers",
            "az_client",
            "monitor",
            "render",
            "streaming",
            "notify",
            "pricing",
            "task_agg",
            "dashboard",
            "webhooks",
            "budget",
        )):
            del sys.modules[name]


def _load_bridge_module(name: str, relpath: str):
    """Spec-load a telegram-bridge module by name. Adds the bridge dir
    to sys.path first so transitive `from monitor import state` etc.
    resolve."""
    if str(_BRIDGE) not in sys.path:
        sys.path.insert(0, str(_BRIDGE))
    spec = importlib.util.spec_from_file_location(name, _BRIDGE / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Each test gets a clean module slate so env changes take effect."""
    _purge_bridge_modules()
    yield
    _purge_bridge_modules()


# ── bot.py env handling ──────────────────────────────────────────────


def test_bot_imports_without_telegram_env(monkeypatch):
    """The reported failure: import-time KeyError when env unset."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    bot = _load_bridge_module("bot", "bot.py")
    assert bot.BOT_TOKEN is None
    assert bot.CHAT_ID is None
    assert bot.TELEGRAM_ENABLED is False


def test_bot_imports_with_empty_string_env(monkeypatch):
    """`.env` with `TELEGRAM_BOT_TOKEN=` (no value) sends an empty
    string, not unset. Must be treated as disabled."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    bot = _load_bridge_module("bot", "bot.py")
    assert bot.BOT_TOKEN is None
    assert bot.CHAT_ID is None
    assert bot.TELEGRAM_ENABLED is False


def test_bot_disabled_when_only_token_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    bot = _load_bridge_module("bot", "bot.py")
    assert bot.BOT_TOKEN == "fake-token"
    assert bot.CHAT_ID is None
    assert bot.TELEGRAM_ENABLED is False


def test_bot_disabled_when_only_chat_id_set(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    bot = _load_bridge_module("bot", "bot.py")
    assert bot.BOT_TOKEN is None
    assert bot.CHAT_ID == 12345
    assert bot.TELEGRAM_ENABLED is False


def test_bot_enabled_when_both_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    bot = _load_bridge_module("bot", "bot.py")
    assert bot.BOT_TOKEN == "fake-token"
    assert bot.CHAT_ID == 12345
    assert bot.TELEGRAM_ENABLED is True


# ── telegram_handlers/* env handling (covered transitively) ──────────
# bot.py imports all five handler modules (system, chat, cost, files,
# today — see grep `from telegram_handlers` in bot.py). So the bot.py
# import tests above implicitly verify every handler's CHAT_ID parsing
# is safe under missing env. A separate parametrized test would have to
# fight a pre-existing circular import (pricing ↔ task_agg) that only
# resolves cleanly when bot.py-style top-down loading runs first.
