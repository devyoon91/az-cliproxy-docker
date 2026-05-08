"""GET (or POST) /api/plugins/dashboard_link/get_token

Returns the DASHBOARD_TOKEN that the telegram-bridge expects on
`/dashboard?token=...` and `/api/stats?token=...`. The token lives in
the agent-zero container's environment (loaded via `env_file: .env`
in docker-compose.yml — same place the bridge reads it from).

Status codes:
- 200 { "token": "<value>", "port": 8443 }
- 503 if DASHBOARD_TOKEN is unset/empty (dashboard intentionally
  disabled per the README — see /dashboard 404 behavior on the bridge).

The plugin frontend builds the final URL using the user's current
browser hostname so SSH-tunneled (`ssh -L 8443:localhost:8443`) and
direct setups both work without configuration.
"""
from __future__ import annotations

import os

from flask import jsonify
from helpers.api import ApiHandler, Input, Output, Request

# Default port for the bridge dashboard. Matches docker-compose.yml's
# `ports: - "8443:8443"` mapping. Override via env if a custom mapping
# is in use.
_DEFAULT_PORT = 8443


class GetToken(ApiHandler):
    """Return the dashboard token + port so the frontend can build the URL."""

    @classmethod
    def get_methods(cls) -> list[str]:
        # POST is what ApiHandler defaults to, but the call is read-only —
        # accept GET too so a curl probe works without crafting a body.
        return ["GET", "POST"]

    async def process(self, input: Input, request: Request) -> Output:
        token = (os.environ.get("DASHBOARD_TOKEN") or "").strip()
        if not token:
            return (
                jsonify(
                    {
                        "error": "DASHBOARD_TOKEN not set",
                        "hint": (
                            "Set DASHBOARD_TOKEN in az-cliproxy-docker/.env "
                            "and recreate the agent-zero container."
                        ),
                    }
                ),
                503,
            )

        port_raw = (os.environ.get("DASHBOARD_PORT") or "").strip()
        try:
            port = int(port_raw) if port_raw else _DEFAULT_PORT
        except ValueError:
            port = _DEFAULT_PORT

        return jsonify({"token": token, "port": port})
