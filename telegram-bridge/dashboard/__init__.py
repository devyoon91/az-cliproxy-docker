"""Web dashboard — Phase E carve from bot.py (issue #79).

Serves an HTML page + JSON stats API at `/dashboard` and `/api/stats` on
the same aiohttp server already running for `/notify`, `/track`, `/usage`.
Auth via `DASHBOARD_TOKEN` env (empty/unset → both endpoints 404).

What's here:
- `auth.py` — token check + the DASHBOARD_TOKEN constant
- `stats.py` — `_build_stats(range_days)` aggregating task JSONs
- `handlers.py` — aiohttp request handlers + the loaded HTML template
- `template.html` — static HTML/CSS/JS, Chart.js loaded from CDN

Phase D's task_agg/ was carved first to break a circular dep —
`_build_stats` calls `_aggregate`, `_filter_date_range`, `_load_task_jsons`
which now live in task_agg.agg. See PR #89 for the dependency note.
"""

from .auth import DASHBOARD_TOKEN, _check_dashboard_auth
from .eval_handlers import (
    EVAL_DASHBOARD_HTML,
    eval_dashboard_handler,
    eval_stats_api_handler,
)
from .eval_stats import _build_eval_stats
from .handlers import DASHBOARD_HTML, dashboard_handler, stats_api_handler
from .stats import _build_stats

__all__ = [
    "DASHBOARD_TOKEN",
    "DASHBOARD_HTML",
    "EVAL_DASHBOARD_HTML",
    "_check_dashboard_auth",
    "_build_stats",
    "_build_eval_stats",
    "dashboard_handler",
    "stats_api_handler",
    "eval_dashboard_handler",
    "eval_stats_api_handler",
]
