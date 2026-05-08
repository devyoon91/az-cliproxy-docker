"""Task-JSON aggregation — Phase D carve from bot.py (issue #79).

Reads agent-zero/logs/tasks/*.json (mounted at /app/tasks) and turns
them into windowed aggregates that drive `/today`, `/week`, `/tasks`,
the bridge web dashboard's `_build_stats`, and the budget engine's
`_compute_window_cost`. All four were duplicating roughly the same
load → filter → aggregate pipeline before this carve, with subtle
divergence risk (the `daily_usage_reporter` even reached straight
into `usage_today` and would miss in-progress tasks). Single source of
truth now lives here.

Package name is `task_agg`, not `tasks`, because docker-compose mounts
the read-only AZ task-JSON volume at `/app/tasks` — naming a Python
package `tasks/` had the bind-mount silently shadow our code at
runtime, and the bridge crashed at module load with
`ModuleNotFoundError: No module named 'tasks.agg'` even though the
files were in the image. `task_agg` is short for "task aggregation
primitives" and avoids the collision.
"""

from .agg import (
    KST,
    TASKS_DIR,
    _aggregate,
    _cache_efficiency,
    _data_quality_summary,
    _filter_date_range,
    _format_agg_block,
    _format_cache_line,
    _format_model_breakdown,
    _format_profile_breakdown,
    _is_anthropic_model,
    _kst_now,
    _load_task_jsons,
    _quality_banner,
)

__all__ = [
    "KST",
    "TASKS_DIR",
    "_aggregate",
    "_cache_efficiency",
    "_data_quality_summary",
    "_filter_date_range",
    "_format_agg_block",
    "_format_cache_line",
    "_format_model_breakdown",
    "_format_profile_breakdown",
    "_is_anthropic_model",
    "_kst_now",
    "_load_task_jsons",
    "_quality_banner",
]
