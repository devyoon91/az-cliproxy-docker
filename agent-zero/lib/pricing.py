"""
Model pricing lookup — computes USD cost from real token counts.

Loads LiteLLM's `model_prices_and_context_window.json` once at import time
(GitHub raw, with bundled fallback) and exposes `compute_cost()` that
accepts Anthropic-aware cache fields. Used by:

  - `task_report.llm_call()` — per-call `cost_usd` written into every task JSON
  - `_compute_totals()` — task-level `cost_usd` roll-up
  - (telegram-bridge keeps its own copy of the same logic for /track)

Pricing fields (per-token USD, LiteLLM schema):
  input_cost_per_token
  output_cost_per_token
  cache_read_input_token_cost        (90% discount on Anthropic)
  cache_creation_input_token_cost    (25% premium on Anthropic)

Cache math:
  regular_input = prompt_tokens - cache_read - cache_creation
  cost = regular_input  * input_cost_per_token
       + cache_read     * cache_read_input_token_cost
       + cache_creation * cache_creation_input_token_cost
       + completion     * output_cost_per_token

Mounted into the container at /a0/helpers/pricing.py alongside task_report.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback rates in per-token USD. Used only when the remote table cannot
# be fetched and the model isn't in the bundled snapshot. Values sit in
# the middle of the 2026-04 Anthropic/OpenAI ranges so the error-bar is
# symmetric either way.
_FALLBACK = {
    "input_cost_per_token": 0.000003,            # $3 / 1M
    "output_cost_per_token": 0.000015,           # $15 / 1M
    "cache_read_input_token_cost": 0.0000003,    # $0.30 / 1M (90% off input)
    "cache_creation_input_token_cost": 0.00000375,  # $3.75 / 1M (25% premium)
}

# Common model-name aliases → canonical LiteLLM key. AZ sometimes prefixes
# with "anthropic/" or uses shorthand like "claude-sonnet-4-6" when the
# LiteLLM key is "claude-sonnet-4-20250929". Extend as needed.
_ALIASES = {
    "anthropic/claude-sonnet-4-6": "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-6": "claude-sonnet-4-5-20250929",
    "anthropic/claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
    "anthropic/claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "anthropic/claude-opus-4-5": "claude-opus-4-5-20250929",
    "claude-opus-4-5": "claude-opus-4-5-20250929",
}

_PRICE_TABLE: dict = {}
_LOADED = False


def _remote_url() -> str:
    return os.environ.get(
        "LITELLM_PRICE_URL",
        "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
    )


def _load_remote() -> dict | None:
    try:
        import requests  # noqa: WPS433
    except Exception:  # pragma: no cover
        return None
    try:
        resp = requests.get(_remote_url(), timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"[pricing] remote fetch failed: {e}")
    return None


def _load_bundled() -> dict | None:
    # Looks next to this file for a snapshot shipped with the repo.
    path = Path(__file__).parent / "model_prices.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[pricing] bundled load failed: {e}")
    return None


def _ensure_loaded() -> None:
    global _PRICE_TABLE, _LOADED
    if _LOADED:
        return
    table = _load_remote() or _load_bundled() or {}
    if table:
        _PRICE_TABLE = table
        logger.info(f"[pricing] loaded {len(table)} model rates")
    else:
        logger.warning("[pricing] no table — all lookups use fallback rates")
    _LOADED = True


def _resolve_key(model: str | None) -> str | None:
    if not model:
        return None
    if model in _PRICE_TABLE:
        return model
    if model in _ALIASES:
        return _ALIASES[model]
    # LiteLLM stores Anthropic keys without the "anthropic/" prefix; strip & retry.
    if model.startswith("anthropic/"):
        tail = model.split("/", 1)[1]
        if tail in _PRICE_TABLE:
            return tail
    return None


def get_rates(model: str | None) -> dict:
    """Return the 4-rate dict for a model, falling back to `_FALLBACK`."""
    _ensure_loaded()
    key = _resolve_key(model)
    info = _PRICE_TABLE.get(key) if key else None
    if info is None:
        return dict(_FALLBACK)
    # Anthropic entries may omit cache fields for models without prompt caching.
    # Default creation to input*1.25, read to input*0.1 per Anthropic's schedule.
    in_rate = info.get("input_cost_per_token", _FALLBACK["input_cost_per_token"])
    out_rate = info.get("output_cost_per_token", _FALLBACK["output_cost_per_token"])
    read_rate = info.get("cache_read_input_token_cost", in_rate * 0.10)
    create_rate = info.get("cache_creation_input_token_cost", in_rate * 1.25)
    return {
        "input_cost_per_token": in_rate,
        "output_cost_per_token": out_rate,
        "cache_read_input_token_cost": read_rate,
        "cache_creation_input_token_cost": create_rate,
    }


def compute_cost(
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Compute total USD cost for one LLM call.

    `input_tokens` in AZ's task_report is the RAW `prompt_tokens` from the
    provider — on Anthropic this is already the billed regular-input count
    (cache_read and cache_creation are NOT double-counted inside it). On
    OpenAI, `prompt_tokens_details.cached_tokens` IS included in
    prompt_tokens, so subtract it to avoid billing the same tokens twice.

    The stream-usage probe normalizes to the Anthropic convention, so we
    treat `input_tokens` as regular-only here. If callers pass a raw OpenAI
    total they should subtract cache_read first.
    """
    rates = get_rates(model)
    cost = (
        max(0, int(input_tokens)) * rates["input_cost_per_token"]
        + max(0, int(output_tokens)) * rates["output_cost_per_token"]
        + max(0, int(cache_read_tokens)) * rates["cache_read_input_token_cost"]
        + max(0, int(cache_creation_tokens)) * rates["cache_creation_input_token_cost"]
    )
    return round(cost, 6)


def format_usd(cost: float) -> str:
    """Human-friendly cost string — 4 decimals for cents, scientific for sub-cent."""
    if cost >= 0.01:
        return f"${cost:.4f}"
    if cost >= 0.0001:
        return f"${cost:.6f}"
    return f"${cost:.2e}"
