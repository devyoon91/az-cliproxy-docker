"""`_build_stats(range_days)` — task-JSON aggregate in dashboard JSON shape.

Single source of truth for both `/api/stats` (this module's HTTP path)
and the bridge's `cmd_today` / `cmd_week` Telegram commands — same
`_aggregate` pipeline, so dashboard numbers track Telegram numbers
exactly. The carve from bot.py (issue #79 Phase E) is enabled by Phase D
(`task_agg/agg.py` providing the load + filter + aggregate primitives).
"""
from __future__ import annotations

from datetime import timedelta

from task_agg.agg import (
    _aggregate,
    _filter_date_range,
    _kst_now,
    _load_task_jsons,
)


def _build_stats(range_days: int = 30) -> dict:
    """Aggregate the read-only task JSONs into the shape the dashboard JS
    expects.

    Shape:
      {
        "now": "...",                 # KST ISO timestamp
        "range_days": 30,
        "totals": {tasks, llm_calls, tool_calls, cost_usd, ...},
        "daily":   [{date: "YYYY-MM-DD", tasks, cost, llm_calls}],
        "by_model_7d": [{model, calls, cost, input, output, cache_read,
                         cache_create}],
        "scatter":  [{task_id, elapsed_sec, cost_usd, profile, ended_reason}],
      }
    """
    now = _kst_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    range_start = today_start - timedelta(days=range_days - 1)
    range_end = today_start + timedelta(days=1)

    all_tasks = _load_task_jsons()
    window_tasks = _filter_date_range(all_tasks, range_start, range_end)

    daily = []
    for i in range(range_days):
        day_start = range_start + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_tasks = _filter_date_range(window_tasks, day_start, day_end)
        agg = _aggregate(day_tasks)
        daily.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "tasks": agg["tasks"],
            "cost": round(agg["cost_usd"], 6),
            "llm_calls": agg["llm_calls"],
        })

    # Per-model bucket over the last 7 days (separate window from the
    # 30-day daily series — short window catches recent shift in mix).
    week_start = today_start - timedelta(days=6)
    week_tasks = _filter_date_range(all_tasks, week_start, range_end)
    week_agg = _aggregate(week_tasks)
    by_model = []
    for model, b in week_agg.get("by_model", {}).items():
        by_model.append({
            "model": model,
            "calls": b["calls"],
            "cost": round(b["cost"], 6),
            "input": b["input"],
            "output": b["output"],
            "cache_read": b["cache_read"],
            "cache_create": b["cache_create"],
        })
    by_model.sort(key=lambda r: r["cost"], reverse=True)

    # Scatter: one point per task in the range. Useful to spot tasks that
    # are slow without being expensive (probably stuck) or expensive without
    # being slow (cache miss / context bloat).
    scatter = []
    for t in window_tasks:
        totals = t.get("totals") or {}
        scatter.append({
            "task_id": t.get("task_id"),
            "elapsed_sec": t.get("elapsed_sec") or 0,
            "cost_usd": round(float(totals.get("cost_usd", 0.0) or 0.0), 6),
            "profile": t.get("profile") or "default",
            "ended_reason": t.get("ended_reason") or "unknown",
        })

    full_agg = _aggregate(window_tasks)
    return {
        "now": now.isoformat(),
        "range_days": range_days,
        "totals": {
            "tasks": full_agg["tasks"],
            "llm_calls": full_agg["llm_calls"],
            "tool_calls": full_agg["tool_calls"],
            "input_tokens": full_agg["input_tokens"],
            "output_tokens": full_agg["output_tokens"],
            "cache_read_tokens": full_agg["cache_read_tokens"],
            "cache_creation_tokens": full_agg["cache_creation_tokens"],
            "cost_usd": round(full_agg["cost_usd"], 6),
        },
        "daily": daily,
        "by_model_7d": by_model,
        "scatter": scatter,
    }
