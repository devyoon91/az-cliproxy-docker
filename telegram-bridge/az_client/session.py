"""Agent Zero HTTP session + CSRF + the few read-only poll endpoints.

Phase H carve from bot.py (issue #79). Includes:

- `AZ_API_URL` / `AZ_API_PREFIX` env constants used by every AZ HTTP
  call site. Read at import time, same as bot.py used to.
- The shared `aiohttp.ClientSession` + cookie jar + CSRF token, kept
  via `get_az_session()` / `close_az_session()` — these are the public
  accessors; the internal `_az_session` global never gets touched
  directly outside this module.
- `get_headers()` — adds the v1.8 `Origin` bypass + the cached CSRF
  token. Every AZ POST uses this.
- `fetch_chat_list()` / `sync_log_version()` — read-only AZ poll
  helpers used by the monitor loop and the /chats command. Pure HTTP,
  no telegram or monitor-state side effects, so they're safe to live
  here.
- `cached_contexts` — shared mutable list of the most recent AZ context
  list. Mutated in place (clear+extend) so importers can hold stable
  bindings across `fetch_chat_list()` calls — same pattern as
  `pricing.cost._model_cost_map` etc.

What's NOT here:
- `send_to_agent_zero()`, `check_agent_zero_status()`, the monitor
  loop. Those write to monitor state (`monitor_context`, `monitor_log_version`,
  `monitor_enabled`) which still lives in bot.py. Carving them needs
  the monitor state to move first.
"""
from __future__ import annotations

import logging
import os

import aiohttp
from aiohttp import CookieJar

logger = logging.getLogger(__name__)


# Env-driven endpoints. Match what bot.py used to read. The `/api`
# default for AZ_API_PREFIX matters: AZ v1.8+ exposes its endpoints
# under that prefix (so /csrf_token is actually at /api/csrf_token).
# Defaulting to "" silently breaks every AZ HTTP call with a 404.
AZ_API_URL = os.environ.get("AZ_API_URL", "http://agent-zero:80")
AZ_API_PREFIX = os.environ.get("AZ_API_PREFIX", "/api")


# Module-level state. _az_session is rebound (None ↔ ClientSession),
# so it's deliberately private — public access is via `get_az_session()`.
# csrf_token is a string; rebinding is fine because no one imports it
# directly (callers use `get_headers()` or `get_csrf_token()`).
_az_session: aiohttp.ClientSession | None = None
_csrf_token: str = ""


# Mutated-in-place shared list. Callers (monitor loop, /chats command)
# import this binding and read the current snapshot; updaters
# (`fetch_chat_list`, monitor's poll path) clear+extend rather than
# rebind, so the binding stays valid.
cached_contexts: list = []


def get_csrf_token() -> str:
    """Return the most recently acquired CSRF token (empty string if
    no session has been opened yet). Exists so callers don't have to
    import the private `_csrf_token` global."""
    return _csrf_token


async def get_az_session() -> aiohttp.ClientSession:
    """Open or reuse the AZ-side aiohttp session and refresh CSRF.

    AZ v1.8+ checks the `Origin` header on POST and gates write
    endpoints behind a CSRF token; both have to be in place before
    any subsequent call. We fetch the token on first session creation
    and stash it for `get_headers()` to consume.
    """
    global _az_session, _csrf_token

    if _az_session and not _az_session.closed:
        return _az_session

    jar = CookieJar(unsafe=True)
    _az_session = aiohttp.ClientSession(cookie_jar=jar)

    try:
        async with _az_session.get(
            f"{AZ_API_URL}{AZ_API_PREFIX}/csrf_token",
            headers={"Origin": "http://localhost:50001"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                _csrf_token = data.get("token", "")
                logger.info(f"CSRF token acquired: {_csrf_token[:10]}...")
            else:
                logger.warning(f"CSRF token request failed: {resp.status}")
    except Exception as e:
        logger.error(f"Failed to get CSRF token: {e}")

    return _az_session


async def close_az_session() -> None:
    """Close the active AZ session. The next `get_az_session()` will
    open a fresh one with a new CSRF token. Used by the 403-retry
    path in `send_to_agent_zero`."""
    global _az_session
    if _az_session and not _az_session.closed:
        await _az_session.close()
        _az_session = None


def get_headers() -> dict:
    """Headers that every AZ POST must carry: Origin (v1.8 bypass) +
    the cached CSRF token (when one's been acquired)."""
    headers = {
        "Origin": "http://localhost:50001",
    }
    if _csrf_token:
        headers["X-CSRF-Token"] = _csrf_token
    return headers


# ── Read-only poll helpers ──────────────────────────────────────────


async def fetch_chat_list() -> list:
    """Fetch the active chat (context) list from AZ.

    Updates the shared `cached_contexts` list in place so importers
    keep a stable binding. Returns the list as well for the synchronous
    caller convention.
    """
    try:
        session = await get_az_session()
        headers = get_headers()

        poll_payload = {
            "log_from": 0,
            "context": None,
            "timezone": "Asia/Seoul",
        }

        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/poll",
            json=poll_payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            poll_data = await resp.json()

        contexts = poll_data.get("contexts", [])
        cached_contexts.clear()
        cached_contexts.extend(contexts)
        return contexts

    except Exception as e:
        logger.error(f"Failed to fetch chat list: {e}")
        return []


async def sync_log_version(ctx: str) -> int:
    """Quietly fetch the current `log_version` for a context without
    pulling any history. Used by the monitor to skip past everything
    that already happened before the user pointed it at this chat —
    we only want to surface NEW activity, not replay the backlog.
    """
    try:
        session = await get_az_session()
        headers = get_headers()
        poll_payload = {
            "log_from": 999_999_999,  # huge → server returns no logs, just version
            "context": ctx or None,
            "timezone": "Asia/Seoul",
        }
        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/poll",
            json=poll_payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return 0
            data = await resp.json()
        return int(data.get("log_version", 0) or 0)
    except Exception as e:
        logger.error(f"Failed to sync log_version: {e}")
        return 0
