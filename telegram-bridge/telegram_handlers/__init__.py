"""Telegram command handlers — Phase I+ carve from bot.py (issue #79).

The /today, /week, /tasks trio gets carved here first because they're
the most self-contained group: they only depend on task_agg + the
chat-ID env var. Future phases can carve more cmd handlers (budget,
pricing, usage, system, monitor toggles, …) into their own files
under this package as their own bot.py-internal deps come unstuck.
"""

from .cost import cmd_budget, cmd_pricing, cmd_usage
from .files import cmd_docs
from .system import cmd_help, cmd_start
from .today import _parse_by_flag, cmd_tasks, cmd_today, cmd_week

__all__ = [
    "_parse_by_flag", "cmd_tasks", "cmd_today", "cmd_week",
    "cmd_budget", "cmd_pricing", "cmd_usage",
    "cmd_help", "cmd_start",
    "cmd_docs",
]
