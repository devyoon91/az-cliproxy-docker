"""Monitor state singleton — Phase P carve from bot.py (issue #79).

Holds the seven module-level globals the AZ web-chat monitor loop and
its companion command handlers used to keep on bot.py.

Access pattern (read+write):
    from monitor import state
    if state.monitor_enabled:
        ...
    state.monitor_log_version = await sync_log_version(state.monitor_context)

Why module attributes — not a `@dataclass` or a singleton class: the
old call sites used bare `monitor_enabled = True` assignments, and
`module.attr = value` keeps that one-liner shape while remaining
visible to every importer. A class instance would force callers to
wire a singleton through configure() (like notify.telegram does for
its Bot reference) — overkill for what is genuinely process-wide
mutable state.

Keep this file free of imports beyond `from __future__` — the whole
point is that callers can `from monitor import state` without dragging
telegram, aiohttp, or budget machinery in along the way.
"""
from __future__ import annotations

# ── Agent Zero session ──
# Active context the bridge sends user messages to via /message_async.
# Empty string at boot — AZ creates a fresh context on first message.
az_context: str = ""

# ── Monitor loop state ──
# Master kill-switch for the AZ→Telegram log echo. /monitor_off pauses
# the poll loop without tearing it down. Default ON so a fresh boot
# starts streaming immediately.
monitor_enabled: bool = True

# AZ's `log_version` cursor we last consumed from /api/poll. Strictly
# monotonic per `monitor_log_guid` window. Reset to "current" via
# `sync_log_version` whenever we switch context, so the user never
# gets blasted with stale history on a chat switch / monitor toggle.
monitor_log_version: int = 0

# Which AZ context the monitor is currently watching. Empty == let AZ
# pick (default web-UI active chat). Tracked separately from
# `az_context` because the user can pin the monitor to one chat while
# sending to another (rare, but supported via /track_chat_off).
monitor_context: str = ""

# AZ's per-context log GUID. When this changes mid-session it means
# the chat got reset / cleared from the web UI; we re-anchor
# `monitor_log_version` to the current cursor so we don't replay the
# new history.
monitor_log_guid: str = ""

# When True, the monitor loop hops to whatever context AZ's web UI
# activates next. /track_chat_off pins the monitor to its current
# chat. Default ON so casual users get the obvious "Telegram mirrors
# the web UI" behavior.
monitor_auto_follow: bool = True

# When False (the default), only `user`-type logs echo through to
# Telegram during a task — AZ's intermediate activity (info / tool /
# code_exe / response / error / warning) is suppressed and the user
# instead gets two clean messages at task completion (AZ's answer +
# the metrics card from task_report.py). When True, every log type
# passes through unchanged for debugging. Toggled via /verbose_on,
# /verbose_off.
monitor_verbose: bool = False
