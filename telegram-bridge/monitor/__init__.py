"""Monitor state package — Phase P carve from bot.py (issue #79).

Houses the seven monitor-loop globals that bot.py used to own at module
level (`az_context`, `monitor_enabled`, `monitor_log_version`,
`monitor_context`, `monitor_log_guid`, `monitor_auto_follow`,
`monitor_verbose`).

Why a dedicated `state` submodule rather than just module-level names
on `monitor` itself: callers do `from monitor import state` and then
write `state.monitor_enabled = True`. Attribute access on a module
object is shared by every importer — read+write stays consistent
across bot.py, the monitor loop, the cmd handlers, and any future
carves into `telegram_handlers/`. A bare `from monitor import
monitor_enabled` would give each importer its own rebound local and
silently fork the state, which is exactly the trap that bit
`pricing.usage.usage_today` (the clear+update pattern there).

This phase is purely a name-rebinding refactor — no behavior change.
The point is to unstick the cmd handlers (cmd_chats / cmd_switch /
cmd_new / cmd_logs / cmd_backup / cmd_monitor_on/off /
cmd_track_chat_on/off / cmd_verbose_on/off) so subsequent phases can
move them under `telegram_handlers/` without dragging bot.py-internal
imports into those modules.

The `monitor` package is intentionally separate from `render/monitor.py`
(the pure formatter `format_monitor_message`). Different concerns:
state lives here, formatting lives there.
"""

from . import state

__all__ = ["state"]
