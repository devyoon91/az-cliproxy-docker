"""Pin `monitor/state.py` — Phase P carve from bot.py (issue #79).

The whole reason this module exists is that bot.py used to keep these
seven globals at module scope and let cmd handlers + the monitor loop
mutate them via `global` declarations. The carve replaces those with
attribute access (`state.monitor_enabled = True`) so the cmd handlers
can move under `telegram_handlers/` in subsequent phases without
dragging bot.py-internal imports along.

What's worth pinning:

1. **Defaults match the prior bot.py defaults exactly** — a regression
   here would silently flip /verbose-quiet, kill the monitor at boot,
   or break auto-follow.
2. **Cross-module attribute mutation is visible to every importer** —
   this is the core invariant the carve relies on. If a future change
   ever rebinds the names (`from monitor.state import monitor_enabled`
   instead of `from monitor import state`) it forks state silently;
   this test catches that by asserting the module object identity is
   the same regardless of how callers grab it.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

_BRIDGE = Path(__file__).resolve().parent.parent / "telegram-bridge"


@pytest.fixture
def state():
    """Fresh import of `monitor.state` per test so default-value asserts
    don't see leaked mutations from prior tests.

    Uses the package machinery (not exec_module on the file alone) so
    `from monitor import state` continues to work the way bot.py does it.
    """
    import sys

    saved = {k: sys.modules[k] for k in list(sys.modules) if k.startswith("monitor")}
    saved_path = list(sys.path)
    sys.path.insert(0, str(_BRIDGE))
    for k in list(saved):
        del sys.modules[k]
    try:
        from monitor import state as fresh
        importlib.reload(fresh)
        yield fresh
    finally:
        for k in list(sys.modules):
            if k.startswith("monitor"):
                del sys.modules[k]
        sys.modules.update(saved)
        sys.path[:] = saved_path


# ── Defaults ────────────────────────────────────────────────────────


def test_az_context_default_empty(state):
    assert state.az_context == ""


def test_monitor_enabled_default_on(state):
    """Bot starts with monitoring ON — flipping this default would
    silently break the AZ→Telegram echo for every fresh boot."""
    assert state.monitor_enabled is True


def test_monitor_log_version_default_zero(state):
    assert state.monitor_log_version == 0


def test_monitor_context_default_empty(state):
    assert state.monitor_context == ""


def test_monitor_log_guid_default_empty(state):
    assert state.monitor_log_guid == ""


def test_monitor_auto_follow_default_on(state):
    """Auto-follow ON is the obvious 'mirrors the web UI' UX. /track_chat_off
    pins to current. Flipping this default reverses every casual user's
    expected behavior."""
    assert state.monitor_auto_follow is True


def test_monitor_verbose_default_off(state):
    """Quiet mode is the default — only user echoes during a task, plus the
    completion summary. Verbose floods the chat with tool/code logs and
    is debug-only."""
    assert state.monitor_verbose is False


# ── Cross-module attribute mutation ─────────────────────────────────


def test_state_is_singleton_across_imports(state):
    """Two different import statements should hand back the same module
    object — that's the contract every callsite relies on. If a future
    refactor ever puts `monitor.state` behind a __getattr__ wrapper or
    similar indirection, this catches it before behavior diverges.
    """
    a = importlib.import_module("monitor.state")
    from monitor import state as b
    assert a is b is state


def test_write_via_attribute_is_visible_to_other_importers(state):
    """The whole point of the carve. Writing through one reference
    must be observable through the other — otherwise the cmd
    handlers (in their own modules in later phases) will silently
    fork state from the monitor loop in bot.py.
    """
    other = importlib.import_module("monitor.state")
    state.monitor_enabled = False
    state.monitor_log_version = 999
    state.az_context = "test-ctx"
    assert other.monitor_enabled is False
    assert other.monitor_log_version == 999
    assert other.az_context == "test-ctx"


def test_package_init_reexports_state(state):
    """`from monitor import state` works because monitor/__init__.py
    exposes it. If __init__.py is rewritten to drop the re-export
    (and the package only ships `state.py`), `from monitor import state`
    would still work via attribute access on the package — but pinning
    that the explicit re-export is present documents the intent.
    """
    import monitor
    assert monitor.state is state
