"""Spend-budget engine for the Telegram bridge.

Phase C carve-out from bot.py (issue #79). What lives here:

- The threshold ladder (80% / 100% / 150%) and the JSON state file at
  `/app/data/budget.json` (mounted read-write per docker-compose).
- Pure helpers: empty-shape default, cooldown-key formatter, alert
  text renderer.
- Load + save IO with crash-safe atomic write.

What's NOT here (lives in bot.py):
- `_compute_window_cost()` — depends on task-JSON aggregation, due
  to be carved in Phase D.
- `_budget_check_window()`, `budget_check_all()`, `hourly_budget_sweep()` —
  async, depend on `send_telegram()`. Pure decision logic could be split
  out cleanly, but for Phase C minimum the threshold loop stays caller-side.
- `cmd_budget` — Telegram command handler.
"""

from .core import (
    BUDGET_DIR,
    BUDGET_PATH,
    BUDGET_THRESHOLDS,
    _budget,
    _budget_default,
    _load_budget,
    _save_budget,
    alert_key,
    format_alert,
)
from .engine import (
    _budget_check_window,
    _compute_window_cost,
    budget_check_all,
    configure,
    hourly_budget_sweep,
)

__all__ = [
    "BUDGET_DIR",
    "BUDGET_PATH",
    "BUDGET_THRESHOLDS",
    "_budget",
    "_budget_default",
    "_load_budget",
    "_save_budget",
    "alert_key",
    "format_alert",
    "_compute_window_cost",
    "_budget_check_window",
    "budget_check_all",
    "hourly_budget_sweep",
    "configure",
]
