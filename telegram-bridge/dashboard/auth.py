"""Dashboard auth — DASHBOARD_TOKEN + constant-time check.

The user can pass the token as `?token=...` query param OR
`X-Dashboard-Token` header. Query is convenient for `curl` and
bookmarks; header is recommended for any real deployment since query
strings end up in proxy access logs.
"""
from __future__ import annotations

import hmac
import os

# Captured at module import. The bridge process inherits DASHBOARD_TOKEN
# from `.env` via docker-compose's `env_file:`. Empty/unset disables
# both endpoints (handlers return 404).
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()


def _check_dashboard_auth(request) -> bool:
    """Return True if the request carries the configured token. When
    DASHBOARD_TOKEN is empty/unset both endpoints are disabled (handlers
    return 404), so this only runs when a token IS configured."""
    if not DASHBOARD_TOKEN:
        return False
    presented = request.query.get("token") or request.headers.get("X-Dashboard-Token")
    if not presented:
        return False
    # Constant-time compare so timing doesn't leak the token byte-by-byte.
    return hmac.compare_digest(presented, DASHBOARD_TOKEN)
