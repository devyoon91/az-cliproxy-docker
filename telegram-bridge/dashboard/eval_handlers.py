"""aiohttp 핸들러 — `/dashboard/eval` + `/api/eval-stats` (#115).

[`handlers.py`](./handlers.py) 의 cost dashboard 와 동일 패턴:
- 템플릿은 모듈 import 시점에 한 번 읽고,
- API 는 `_build_eval_stats` 호출 결과를 그대로 json_response 로 감싼다.
- 인증은 기존 DASHBOARD_TOKEN 재사용 (cost / eval 양쪽 모두 같은 토큰).
"""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .auth import DASHBOARD_TOKEN, _check_dashboard_auth
from .eval_stats import _build_eval_stats


def _load_eval_template() -> str:
    return (Path(__file__).resolve().parent / "eval_template.html").read_text(
        encoding="utf-8"
    )


EVAL_DASHBOARD_HTML = _load_eval_template()


async def eval_stats_api_handler(request):
    """GET /api/eval-stats?range=30d&token=..."""
    if not DASHBOARD_TOKEN:
        return web.Response(status=404, text="dashboard disabled")
    if not _check_dashboard_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    raw_range = (request.query.get("range") or "30d").lower().strip()
    try:
        n = int(raw_range[:-1] if raw_range.endswith("d") else raw_range)
    except ValueError:
        n = 30
    n = max(1, min(n, 90))
    payload = _build_eval_stats(range_days=n)
    return web.json_response(payload)


async def eval_dashboard_handler(request):
    """GET /dashboard/eval?token=..."""
    if not DASHBOARD_TOKEN:
        return web.Response(status=404, text="dashboard disabled")
    if not _check_dashboard_auth(request):
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
        text=EVAL_DASHBOARD_HTML,
    )
