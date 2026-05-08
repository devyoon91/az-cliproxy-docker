"""Telegram-bridge pricing module — first carve-out from the bot.py
god-file (issue #79 Phase A).

Mirrors the pricing primitives that already live in
`agent-zero/lib/pricing.py` so the bridge's `/track`, `/today`, and
budget paths agree on cost numbers with the per-task JSONs that
agent-zero writes.
"""

from .cost import (
    _load_model_cost_map,
    _model_cost_map,
    _model_info,
    _normalize_model,
    calc_cost,
)
from .usage import (
    track_usage,
    usage_history,
    usage_today,
)

__all__ = [
    "calc_cost",
    "_model_info",
    "_normalize_model",
    "_load_model_cost_map",
    "_model_cost_map",
    "track_usage",
    "usage_today",
    "usage_history",
]
