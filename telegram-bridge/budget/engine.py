"""Budget engine — windowed cost computation + threshold-crossing alerts.

Phase K carve from bot.py (issue #79). Builds on `budget/core.py`
(state + persistence + pure formatters, Phase C) and `task_agg/agg.py`
(load/filter/aggregate, Phase D).

What's here:
- `_compute_window_cost(window)` — sum cost over "day" or "week" using
  the same task-JSON aggregate path that drives /today, /week, and the
  dashboard. Single source of truth so `/budget` and `/today` never
  disagree.
- `_budget_check_window(window)` — fire-once-per-period alert with a
  cooldown key. Walks the threshold ladder (150% / 100% / 80%) and
  surfaces the highest crossed level.
- `budget_check_all()` — entrypoint for /track + the hourly sweep.
- `hourly_budget_sweep()` — 24h background task.

Telegram dependency injection:
The engine doesn't import `send_telegram` directly — bot.py passes a
callback once at startup via `configure(send_alert=...)`. Same pattern
as `pricing/snapshot.py`. Without configure(), alerts are silently
dropped (handy for tests).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from task_agg.agg import _aggregate, _filter_date_range, _kst_now, _load_task_jsons

from budget.core import (
    BUDGET_THRESHOLDS,
    _budget,
    _save_budget,
    alert_key,
    format_alert,
)

logger = logging.getLogger(__name__)


# Module-level injection slot for the telegram alert sender. bot.py
# wires this once at startup via `configure(send_alert=send_telegram)`.
# Default None disables alerts (useful for tests + smoke runs).
_send_alert = None


def configure(*, send_alert) -> None:
    """Wire the telegram alert callback. Call once at startup before
    any /track or sweep runs. Idempotent — last call wins."""
    global _send_alert
    _send_alert = send_alert


def _compute_window_cost(window: str) -> dict:
    """Sum cost over a window from on-disk task JSONs.

    Authoritative source — uses the same `_aggregate` pipeline as /today
    /week, so budget alerts and dashboards never disagree. Going through
    `usage_today` (RAM-only) would miss any in-progress task and wouldn't
    survive a bridge restart.

    Returns:
        {
          "cost_usd":  float,
          "tasks":     int,
          "top_model": (name, cost) | None,
          "period_id": str,        # for cooldown key
          "label":     str,        # human-readable for the alert text
        }
    """
    now = _kst_now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "day":
        start = today
        end = today + timedelta(days=1)
        period_id = today.strftime("%Y-%m-%d")
        label = f"오늘 ({period_id} KST)"
    elif window == "week":
        start = today - timedelta(days=6)
        end = today + timedelta(days=1)
        # ISO week — naturally rotates Mon→Mon, but we just want a stable
        # bucket for cooldown so 7-day rolling window's date string is fine.
        period_id = today.strftime("%G-W%V")
        label = f"최근 7일 ({start.strftime('%m-%d')}~{today.strftime('%m-%d')} KST)"
    else:
        raise ValueError(f"unknown window: {window!r}")

    tasks = _filter_date_range(_load_task_jsons(), start, end)
    agg = _aggregate(tasks)
    by_model = agg.get("by_model") or {}
    top = None
    if by_model:
        m, b = max(by_model.items(), key=lambda kv: kv[1]["cost"])
        top = (m, b["cost"])
    return {
        "cost_usd": float(agg["cost_usd"]),
        "tasks": agg["tasks"],
        "top_model": top,
        "period_id": period_id,
        "label": label,
    }


async def _budget_check_window(window: str) -> bool:
    """Check one window's spend vs its limit. Fires at most ONE alert per
    call (the highest crossed threshold). Cooldown: per (period_id, window,
    threshold) — once today's 80% fires, won't fire again today even if
    spend keeps climbing.

    Returns True if any alert was sent (useful for tests / hourly logging).
    """
    limit_key = f"{window}_limit_usd"
    limit = _budget.get(limit_key)
    if not limit or limit <= 0:
        return False
    info = _compute_window_cost(window)
    cost = info["cost_usd"]
    if cost <= 0:
        return False
    ratio = cost / float(limit)
    period_id = info["period_id"]

    fired = _budget.setdefault("alerts_fired", {})
    sent_any = False
    # Walk thresholds high-to-low; fire the highest one crossed that hasn't
    # been fired yet for this period. Mark all lower not-yet-fired thresholds
    # as fired too so we don't trigger a cascade on the next call.
    for thresh, level_label, pct_label in BUDGET_THRESHOLDS:
        key = alert_key(window, pct_label, period_id)
        if ratio >= thresh and key not in fired:
            if not sent_any:
                msg = format_alert(window, info, float(limit), level_label, ratio)
                if _send_alert is not None:
                    try:
                        await _send_alert(msg)
                        sent_any = True
                    except Exception as e:
                        logger.warning(f"[budget] alert send failed: {e}")
                        return False
                else:
                    # No callback wired (tests, dry-runs). Treat as a successful
                    # "would-have-sent" so the cooldown still gets marked —
                    # avoids a tight alert loop on misconfiguration.
                    sent_any = True
            fired[key] = True
    if sent_any:
        _save_budget()
    return sent_any


async def budget_check_all() -> None:
    """Public entrypoint: check both day + week windows. Wired into
    /track (per-call) and the hourly sweep (catches missed alerts and
    week-rollover edge cases)."""
    try:
        await _budget_check_window("day")
        await _budget_check_window("week")
    except Exception as e:
        logger.warning(f"[budget] sweep error: {e}")


async def hourly_budget_sweep() -> None:
    """Hourly background task. Defensive — `_budget_check_window` is also
    called from /track, but that path can be skipped if AZ batches /track
    or fails-quiet. Hourly cadence is plenty for a 24h-budget signal."""
    logger.info("Hourly budget sweep started")
    while True:
        try:
            await asyncio.sleep(3600)  # 1 hour
            await budget_check_all()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[budget] hourly sweep error: {e}")
