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
from .snapshot import (
    LITELLM_PRICE_URL,
    PRICING_DIFF_FIELDS,
    PRICING_DIR,
    PRICING_RETENTION_DAYS,
    _diff_snapshots,
    _fetch_litellm_table,
    _format_pricing_diff,
    _interested_models,
    _list_snapshots,
    _load_snapshot,
    _previous_snapshot,
    _resolve_litellm_key,
    _rotate_pricing_snapshots,
    _save_snapshot,
    _select_for_snapshot,
    _snapshot_path,
    take_pricing_snapshot,
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
    "PRICING_DIR",
    "PRICING_RETENTION_DAYS",
    "LITELLM_PRICE_URL",
    "PRICING_DIFF_FIELDS",
    "_resolve_litellm_key",
    "_interested_models",
    "_fetch_litellm_table",
    "_select_for_snapshot",
    "_snapshot_path",
    "_save_snapshot",
    "_load_snapshot",
    "_list_snapshots",
    "_previous_snapshot",
    "_diff_snapshots",
    "_format_pricing_diff",
    "_rotate_pricing_snapshots",
    "take_pricing_snapshot",
]
