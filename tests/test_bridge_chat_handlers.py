"""Pin `telegram_handlers/chat.py` — Phase Q carve from bot.py (#79).

What's worth testing:

1. The six toggle handlers (`/monitor_on/off`, `/track_chat_on/off`,
   `/verbose_on/off`) flip exactly one `state.X` attribute and reply
   with a known string — that's their entire contract. A regression
   here would silently flip semantics (e.g. `/verbose_on` writing to
   `monitor_auto_follow` after a typo refactor).

2. The chat-id gate (`update.effective_chat.id != CHAT_ID`) early-returns
   without touching state. Critical for the toggle handlers: without
   the gate any Telegram user could turn off this user's monitor.

3. `cmd_chats` formats the list with the `→` marker on the active
   context. The marker logic is purely string-formatting but it's
   the only handler in this module with non-trivial output.

`cmd_monitor_on` calls `sync_log_version` (async); the test patches
it so we don't hit the network. Other 5 toggles + cmd_chats are
patch-free.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge"


# Modules our fixture injects into sys.modules. Captured at fixture
# entry, restored on teardown — otherwise the `render` package shim
# we install (with just `render.monitor.short_id`) shadows the real
# render package and breaks test_pdf_export when it runs after this
# file. Same pattern as test_bridge_webhooks.
_OWN_MODULES = (
    "telegram", "telegram.ext",
    "aiohttp",
    "az_client", "az_client.session",
    "monitor", "monitor.state",
    "render", "render.monitor",
    "streaming",
    "telegram_handlers", "telegram_handlers.chat",
)


def _wire_chat_module(monkeypatch):
    """Spec-load `telegram_handlers.chat` after stubbing the modules
    its top-of-file imports pull in. The real `telegram` and
    `aiohttp` libraries aren't on the test host.

    Returns (chat_module, state_module, fetch_chat_list_mock,
    sync_log_version_mock) so individual tests can drive the bits
    they care about.
    """
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")

    # Fake `telegram` + `telegram.ext` — same shape today.py tests use.
    if "telegram" not in sys.modules:
        telegram_pkg = types.ModuleType("telegram")
        telegram_pkg.Update = object  # type: ignore[attr-defined]
        sys.modules["telegram"] = telegram_pkg
        ext = types.ModuleType("telegram.ext")

        class _CTX:
            DEFAULT_TYPE = object

        ext.ContextTypes = _CTX  # type: ignore[attr-defined]
        sys.modules["telegram.ext"] = ext

    # Fake `aiohttp` — chat.py imports it for the cmd_new HTTP timeout
    # context. We don't drive cmd_new in these tests so a no-op shim
    # is enough; cmd_new behavior gets covered by an integration test
    # later when we have a real aiohttp test harness.
    if "aiohttp" not in sys.modules:
        aiohttp_mod = types.ModuleType("aiohttp")

        class _Timeout:
            def __init__(self, *a, **kw):
                pass

        aiohttp_mod.ClientTimeout = _Timeout  # type: ignore[attr-defined]
        sys.modules["aiohttp"] = aiohttp_mod

    # Fake az_client.session — chat.py imports a fuller surface now
    # (Phase R added cmd_switch/cmd_new which need cached_contexts,
    # AZ_API_*, get_az_session, etc.). Return values get patched per
    # test where needed.
    az_client_pkg = types.ModuleType("az_client")
    az_client_pkg.__path__ = [str(_ROOT / "az_client")]  # type: ignore[attr-defined]
    sys.modules["az_client"] = az_client_pkg
    session_mod = types.ModuleType("az_client.session")
    session_mod.AZ_API_URL = "http://fake-az"
    session_mod.AZ_API_PREFIX = "/api"
    session_mod.cached_contexts = []
    session_mod.fetch_chat_list = AsyncMock(return_value=[])
    session_mod.sync_log_version = AsyncMock(return_value=42)
    session_mod.get_az_session = AsyncMock()
    session_mod.get_headers = lambda: {}
    session_mod.close_az_session = AsyncMock()
    sys.modules["az_client.session"] = session_mod

    # Fake streaming module — only `stream_reset` is hit by the toggle
    # and /chats handlers. cmd_switch/cmd_new also call it; integration
    # tests for those go in their own file (out of scope here).
    streaming_pkg = types.ModuleType("streaming")
    streaming_pkg.stream_reset = lambda ctx_key: None  # type: ignore[attr-defined]
    sys.modules["streaming"] = streaming_pkg

    # Real monitor.state — mutating this is precisely what the handlers
    # do, so we want the genuine module here, not a stub.
    monitor_pkg = types.ModuleType("monitor")
    monitor_pkg.__path__ = [str(_ROOT / "monitor")]  # type: ignore[attr-defined]
    sys.modules["monitor"] = monitor_pkg
    state_spec = importlib.util.spec_from_file_location(
        "monitor.state", _ROOT / "monitor" / "state.py"
    )
    assert state_spec and state_spec.loader
    state_mod = importlib.util.module_from_spec(state_spec)
    sys.modules["monitor.state"] = state_mod
    state_spec.loader.exec_module(state_mod)
    monitor_pkg.state = state_mod  # so `from monitor import state` works

    # Fake render.monitor with just `short_id` — the formatter is tested
    # in test_bridge_render_monitor.py; here we only need the symbol.
    render_pkg = types.ModuleType("render")
    render_pkg.__path__ = [str(_ROOT / "render")]  # type: ignore[attr-defined]
    sys.modules["render"] = render_pkg
    rmon = types.ModuleType("render.monitor")
    rmon.short_id = lambda x: (x[:8] if x else "없음")
    sys.modules["render.monitor"] = rmon

    # telegram_handlers package shim.
    handlers_pkg = types.ModuleType("telegram_handlers")
    handlers_pkg.__path__ = [str(_ROOT / "telegram_handlers")]  # type: ignore[attr-defined]
    sys.modules["telegram_handlers"] = handlers_pkg

    spec = importlib.util.spec_from_file_location(
        "telegram_handlers.chat", _ROOT / "telegram_handlers" / "chat.py"
    )
    assert spec and spec.loader
    chat_mod = importlib.util.module_from_spec(spec)
    sys.modules["telegram_handlers.chat"] = chat_mod
    spec.loader.exec_module(chat_mod)

    # Reset state defaults so tests don't see leakage from prior runs.
    state_mod.az_context = ""
    state_mod.monitor_enabled = True
    state_mod.monitor_log_version = 0
    state_mod.monitor_context = ""
    state_mod.monitor_log_guid = ""
    state_mod.monitor_auto_follow = True
    state_mod.monitor_verbose = False

    return chat_mod, state_mod, session_mod


@pytest.fixture
def chat_env(monkeypatch):
    saved = {n: sys.modules[n] for n in _OWN_MODULES if n in sys.modules}
    bag = _wire_chat_module(monkeypatch)
    yield bag
    # Restore originals; drop anything we newly added so other test
    # files (notably test_pdf_export, which expects the real `render`
    # package) see a clean module table.
    for n in _OWN_MODULES:
        if n in saved:
            sys.modules[n] = saved[n]
        else:
            sys.modules.pop(n, None)


def _make_update(chat_id: int = 999, text: str = "") -> MagicMock:
    """Minimal Update double — only the attributes our handlers touch."""
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.message.reply_text = AsyncMock()
    return upd


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Chat-id gate ────────────────────────────────────────────────────


def test_monitor_on_gates_unauthorized_chats(chat_env):
    chat_mod, state_mod, session_mod = chat_env
    upd = _make_update(chat_id=12345)  # not 999

    _run(chat_mod.cmd_monitor_on(upd, MagicMock()))

    # State unchanged — no flip from default True
    assert state_mod.monitor_enabled is True
    # No reply, no AZ call
    upd.message.reply_text.assert_not_called()
    session_mod.sync_log_version.assert_not_called()


def test_verbose_on_gates_unauthorized_chats(chat_env):
    """Spot-check a different toggle to confirm the gate isn't a
    cmd_monitor_on-only quirk."""
    chat_mod, state_mod, _ = chat_env
    upd = _make_update(chat_id=1)

    _run(chat_mod.cmd_verbose_on(upd, MagicMock()))

    assert state_mod.monitor_verbose is False
    upd.message.reply_text.assert_not_called()


# ── Toggle semantics — each handler flips its one slot ──────────────


def test_monitor_on_enables_and_resyncs(chat_env):
    chat_mod, state_mod, session_mod = chat_env
    state_mod.monitor_enabled = False  # start OFF
    state_mod.monitor_context = "ctx-abc"
    upd = _make_update()

    _run(chat_mod.cmd_monitor_on(upd, MagicMock()))

    assert state_mod.monitor_enabled is True
    # log_version re-anchored from sync_log_version(monitor_context)
    session_mod.sync_log_version.assert_awaited_once_with("ctx-abc")
    assert state_mod.monitor_log_version == 42
    upd.message.reply_text.assert_awaited_once()


def test_monitor_off_only_flips_enabled(chat_env):
    """No `sync_log_version` call on disable — that's a re-anchor for
    re-enable, not a teardown."""
    chat_mod, state_mod, session_mod = chat_env
    upd = _make_update()

    _run(chat_mod.cmd_monitor_off(upd, MagicMock()))

    assert state_mod.monitor_enabled is False
    session_mod.sync_log_version.assert_not_called()
    upd.message.reply_text.assert_awaited_once()


def test_track_chat_on_flips_auto_follow(chat_env):
    chat_mod, state_mod, _ = chat_env
    state_mod.monitor_auto_follow = False
    upd = _make_update()

    _run(chat_mod.cmd_track_chat_on(upd, MagicMock()))

    assert state_mod.monitor_auto_follow is True


def test_track_chat_off_flips_auto_follow(chat_env):
    chat_mod, state_mod, _ = chat_env
    upd = _make_update()

    _run(chat_mod.cmd_track_chat_off(upd, MagicMock()))

    assert state_mod.monitor_auto_follow is False


def test_verbose_on_flips_verbose(chat_env):
    chat_mod, state_mod, _ = chat_env
    upd = _make_update()

    _run(chat_mod.cmd_verbose_on(upd, MagicMock()))

    assert state_mod.monitor_verbose is True


def test_verbose_off_flips_verbose(chat_env):
    chat_mod, state_mod, _ = chat_env
    state_mod.monitor_verbose = True
    upd = _make_update()

    _run(chat_mod.cmd_verbose_off(upd, MagicMock()))

    assert state_mod.monitor_verbose is False


# ── /chats formatting ───────────────────────────────────────────────


def test_chats_empty_says_no_chats(chat_env, monkeypatch):
    chat_mod, _, _ = chat_env
    # `chat.py` did `from az_client.session import fetch_chat_list` at
    # module load — the binding lives on the chat module, not on
    # az_client.session. Patch the bound name to control return value.
    monkeypatch.setattr(chat_mod, "fetch_chat_list", AsyncMock(return_value=[]))
    upd = _make_update()

    _run(chat_mod.cmd_chats(upd, MagicMock()))

    upd.message.reply_text.assert_awaited_once_with("활성 채팅이 없습니다.")


def test_chats_marks_active_context(chat_env, monkeypatch):
    """The `→` arrow goes on the context whose id matches
    `state.monitor_context`, not on the first-listed chat. Trivial-
    looking but easy to flip if someone refactors the marker logic."""
    chat_mod, state_mod, _ = chat_env
    state_mod.monitor_context = "ctx-2"
    monkeypatch.setattr(chat_mod, "fetch_chat_list", AsyncMock(return_value=[
        {"id": "ctx-1", "name": "first"},
        {"id": "ctx-2", "name": "second"},
        {"id": "ctx-3", "name": "third"},
    ]))
    upd = _make_update()

    _run(chat_mod.cmd_chats(upd, MagicMock()))

    sent = upd.message.reply_text.await_args.args[0]
    # Active marker on second only
    assert "→ 2. second" in sent
    assert "  1. first" in sent
    assert "  3. third" in sent
    # Footer points at active context
    assert "현재 추적 중: ctx-2" in sent
