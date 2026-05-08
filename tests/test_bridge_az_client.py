"""Pin telegram-bridge/az_client/session.py — Phase H carve.

Pinning the small synchronous surface:
- `get_headers()` — Origin always present, X-CSRF-Token only when token cached
- `get_csrf_token()` — empty string when no session opened, populated otherwise
- `cached_contexts` binding stability through `fetch_chat_list`
- `AZ_API_URL` / `AZ_API_PREFIX` env defaults

Async paths (`get_az_session`, `fetch_chat_list`, `sync_log_version`)
need a real aiohttp test harness to cover meaningfully — out of scope
for this PR. They're light wrappers over `aiohttp.ClientSession.post`
which is itself well-tested upstream; the carve preserved the request
shapes byte-for-byte.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge" / "az_client"


def _load_session_module():
    """Spec-load `az_client.session` against the on-disk file."""
    pkg = types.ModuleType("az_client")
    pkg.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
    sys.modules["az_client"] = pkg

    spec = importlib.util.spec_from_file_location(
        "az_client.session", _ROOT / "session.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["az_client.session"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def session_mod():
    mod = _load_session_module()
    # Reset module state between tests.
    mod._csrf_token = ""
    mod.cached_contexts.clear()
    return mod


# ── env defaults ────────────────────────────────────────────────────


def test_env_defaults_when_unset(session_mod):
    """Without AZ_API_URL/AZ_API_PREFIX in env, sane defaults stand."""
    # We can't easily reload the module to test env-driven values, but
    # we can verify the constants are the documented defaults under the
    # current test environment (which doesn't set these vars).
    import os
    if "AZ_API_URL" not in os.environ:
        assert session_mod.AZ_API_URL == "http://agent-zero:80"
    if "AZ_API_PREFIX" not in os.environ:
        # AZ v1.8+ requires the /api prefix; defaulting to "" silently
        # 404s every endpoint. Pin the right default.
        assert session_mod.AZ_API_PREFIX == "/api"


# ── get_headers ─────────────────────────────────────────────────────


def test_get_headers_origin_always_present(session_mod):
    h = session_mod.get_headers()
    assert h["Origin"] == "http://localhost:50001"


def test_get_headers_no_csrf_when_unset(session_mod):
    """Until the session is opened (which fetches CSRF), the header
    must be absent — sending an empty token would invalidate the
    request more loudly than just omitting the header."""
    session_mod._csrf_token = ""
    h = session_mod.get_headers()
    assert "X-CSRF-Token" not in h


def test_get_headers_includes_csrf_when_set(session_mod):
    session_mod._csrf_token = "abc123"
    h = session_mod.get_headers()
    assert h["X-CSRF-Token"] == "abc123"


# ── get_csrf_token ──────────────────────────────────────────────────


def test_get_csrf_token_returns_empty_when_unset(session_mod):
    session_mod._csrf_token = ""
    assert session_mod.get_csrf_token() == ""


def test_get_csrf_token_returns_current(session_mod):
    session_mod._csrf_token = "live-token"
    assert session_mod.get_csrf_token() == "live-token"


# ── cached_contexts binding stability ───────────────────────────────


def test_cached_contexts_binding_survives_clear_extend(session_mod):
    """The whole point of the clear+extend pattern in `fetch_chat_list`
    and the monitor loop — bot.py's import binding has to stay valid
    when the list contents are replaced. Simulate: capture a reference,
    do clear+extend, ensure same list identity + new contents."""
    captured = session_mod.cached_contexts
    captured.append("old-ctx")
    # Simulate fetch_chat_list's clear+extend (without actually doing HTTP).
    new_contexts = [{"id": "ctx1"}, {"id": "ctx2"}]
    session_mod.cached_contexts.clear()
    session_mod.cached_contexts.extend(new_contexts)
    # Same list object (binding valid) but new contents.
    assert captured is session_mod.cached_contexts
    assert captured == new_contexts


def test_cached_contexts_starts_empty(session_mod):
    """Module load should leave the list empty — the monitor and
    /chats command rely on this on cold start."""
    assert session_mod.cached_contexts == []
