"""aiohttp request handlers for `/dashboard` and `/api/stats`.

The HTML template lives in `template.html` (next to this file). It's
loaded once at module import — Chart.js is fetched from CDN at the
client, so there's no build step or asset pipeline to maintain.
"""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .auth import DASHBOARD_TOKEN, _check_dashboard_auth
from .stats import _build_stats


def _load_template() -> str:
    """Read the HTML once at import. Failure here means the package is
    broken — let the import error propagate so the bridge crashes loud
    rather than serving an empty page."""
    return (Path(__file__).resolve().parent / "template.html").read_text(
        encoding="utf-8"
    )


DASHBOARD_HTML = _load_template()


async def stats_api_handler(request):
    """GET /api/stats?range=30d&token=... — JSON used by the dashboard JS
    to populate charts. Also useful directly for a curl-based pipe to
    elsewhere (Grafana, jq) once the user pulls the token out of band."""
    if not DASHBOARD_TOKEN:
        return web.Response(status=404, text="dashboard disabled")
    if not _check_dashboard_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    raw_range = (request.query.get("range") or "30d").lower().strip()
    try:
        # Accept "30d", "30", "7d" — strip trailing 'd' if present.
        n = int(raw_range[:-1] if raw_range.endswith("d") else raw_range)
    except ValueError:
        n = 30
    n = max(1, min(n, 90))  # cap so a malformed query can't OOM the bridge
    payload = _build_stats(range_days=n)
    return web.json_response(payload)


async def dashboard_handler(request):
    """GET /dashboard?token=... — static HTML, JS does the fetch.

    Returns 404 when DASHBOARD_TOKEN is unset (no advertising the endpoint
    exists). Returns 401 when token is missing/wrong (after which the page
    JS shows the "add ?token=..." hint).
    """
    if not DASHBOARD_TOKEN:
        return web.Response(status=404, text="dashboard disabled")
    if not _check_dashboard_auth(request):
        # Distinct from /api/stats — the HTML itself loads even on a bad
        # token so the user sees an in-page hint instead of a bare 401
        # body. The fetch then fails with 401 and the JS surfaces the
        # error inline.
        if not request.query.get("token"):
            return web.Response(
                status=401,
                content_type="text/html",
                charset="utf-8",
                text=(
                    "<h1>🔒 unauthorized</h1>"
                    "<p>?token=... 쿼리 파라미터를 붙여 다시 접속하세요.</p>"
                ),
            )
    return web.Response(
        status=200,
        content_type="text/html",
        charset="utf-8",
        text=DASHBOARD_HTML,
    )
