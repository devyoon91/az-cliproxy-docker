"""aiohttp handlers for the bridge's `:8443` HTTP server.

Phase M carve from bot.py (issue #79). Pure code motion — every
dependency was carved earlier, so the handlers themselves now read
identical to what bot.py served, just from a smaller module.
"""
from __future__ import annotations

import logging

from aiohttp import web
from budget.engine import budget_check_all
from dashboard import DASHBOARD_TOKEN, dashboard_handler, stats_api_handler
from notify.telegram import send_telegram
from pricing.usage import track_usage, usage_history, usage_today
from render import md_to_telegram_html

logger = logging.getLogger(__name__)


# Map of `kind` → UI prefix emoji + space. Keep tiny; this is purely
# presentation. Empty/unknown kind → no prefix.
_KIND_PREFIX = {
    "task_response": "🤖 ",
}


async def webhook_handler(request):
    """HTTP POST → Telegram forwarding.

    Payload:
      {
        "text": "...",
        "markdown": true,        # optional — convert text from markdown to
                                 # Telegram-safe HTML before sending
        "parse_mode": "HTML",    # optional — sent verbatim if you've
                                 # already-formatted HTML/MarkdownV2
        "kind": "task_response"  # optional — adds a UI prefix emoji
                                 # AFTER markdown conversion so leading
                                 # `## header` / `| table |` aren't
                                 # displaced from line-start
      }

    `markdown: true` is the easy path: senders write plain markdown
    (```code```, **bold**, etc.) and the bridge handles conversion +
    fallback to plain text on parse failure. AZ's task_report uses this
    with kind="task_response" so the 🤖 emoji lands AFTER conversion.
    """
    try:
        data = await request.json()
        raw_text = data.get("text", data.get("message", str(data)))
        parse_mode = data.get("parse_mode")
        kind = data.get("kind")
        prefix = _KIND_PREFIX.get(kind, "")

        if data.get("markdown"):
            # Convert THEN prefix — order matters. If we prefixed first,
            # leading "## header" would no longer be at line-start, breaking
            # the converter's `^#` regex. Same goes for table rows
            # starting with `|`.
            converted = md_to_telegram_html(raw_text)
            if prefix:
                converted = f"{prefix}{converted}"
                fallback = f"{prefix}{raw_text}"
            else:
                fallback = raw_text
            await send_telegram(
                converted,
                parse_mode="HTML",
                fallback_text=fallback,
            )
        else:
            text = f"{prefix}{raw_text}" if prefix else raw_text
            await send_telegram(text, parse_mode=parse_mode)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def usage_track_handler(request):
    """HTTP POST 로 토큰 사용량 기록 (cache + reasoning 토큰 포함).

    Payload: {
        "model": "anthropic/claude-sonnet-4-6",
        "input_tokens": 1500,
        "output_tokens": 500,
        "cache_read_tokens": 0,        # optional (Anthropic prompt caching)
        "cache_creation_tokens": 0,    # optional
        "reasoning_tokens": 0          # optional (Claude 4.x extended thinking,
                                       #          OpenAI o-series; billed at output rate)
    }
    """
    try:
        data = await request.json()
        model = data.get("model", "unknown")
        input_tokens = int(data.get("input_tokens", 0))
        output_tokens = int(data.get("output_tokens", 0))
        cache_read = int(data.get("cache_read_tokens", 0))
        cache_creation = int(data.get("cache_creation_tokens", 0))
        reasoning = int(data.get("reasoning_tokens", 0))
        track_usage(
            model, input_tokens, output_tokens,
            cache_read, cache_creation, reasoning,
        )
        # Budget check fires AFTER track_usage so the cumulative cost in the
        # on-disk task JSONs has caught up. Fire-and-forget — never let a
        # failed alert reject the /track request itself.
        try:
            await budget_check_all()
        except Exception as e:
            logger.warning(f"[budget] post-track check failed: {e}")
        return web.json_response({
            "ok": True,
            "today": usage_today,
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def usage_get_handler(request):
    """GET 현재 사용량 조회"""
    return web.json_response({
        "today": usage_today,
        "history": usage_history[-7:],
    })


async def run_webhook_server() -> None:
    """Start the aiohttp app on :8443 with all bridge routes.

    Routes:
      POST /notify         — webhook_handler (AZ → Telegram)
      POST /track          — usage_track_handler (cost accounting)
      GET  /usage          — usage_get_handler (snapshot)
      GET  /api/stats      — dashboard JSON (token-gated, 404 if disabled)
      GET  /dashboard      — dashboard HTML (token-gated, 404 if disabled)
    """
    app = web.Application()
    app.router.add_post("/notify", webhook_handler)
    app.router.add_post("/track", usage_track_handler)
    app.router.add_get("/usage", usage_get_handler)
    # M5-E: web dashboard (issue #23). Both routes 404 when DASHBOARD_TOKEN
    # is unset, so registering them is harmless when disabled.
    app.router.add_get("/api/stats", stats_api_handler)
    app.router.add_get("/dashboard", dashboard_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8443)
    await site.start()
    routes_msg = "/notify, /track, /usage"
    if DASHBOARD_TOKEN:
        routes_msg += ", /dashboard, /api/stats (token-protected)"
    else:
        routes_msg += " (dashboard disabled — set DASHBOARD_TOKEN to enable)"
    logger.info(f"Webhook server started on :8443 ({routes_msg})")
