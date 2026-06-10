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

# Anthropic family rates (input, output) per token, used when the LiteLLM
# table is unavailable but the model name still tells us the tier (#141).
# Without this, a table-load failure prices Opus 4.x at the flat $3/$15
# fallback — a ~1.7x undercount ($5/$25 actual), and Fable 5 at ~3.3x off
# ($10/$50 actual). Cache rates derive from input: read 0.1x, write 1.25x.
_FAMILY_FALLBACK = {
    "claude-fable": (0.00001, 0.00005),    # $10 / $50 per 1M
    "claude-opus": (0.000005, 0.000025),   # $5 / $25
    "claude-sonnet": (0.000003, 0.000015),  # $3 / $15
    "claude-haiku": (0.000001, 0.000005),  # $1 / $5
}

# Model-name aliases → canonical LiteLLM key. Only needed when the LiteLLM
# key differs from the AZ-side name after stripping the "anthropic/" prefix.
# LiteLLM now keys every model AZ uses natively — bare names for the whole
# lineup including claude-sonnet-4-6, claude-opus-4-6/7/8, claude-fable-5 —
# so exact match + prefix strip covers everything. The old entries mapped
# claude-sonnet-4-6 onto the dated 4-5 snapshot key, shadowing the native
# key for prefixed forms (#141). Extend only for genuine key mismatches.
_ALIASES: dict[str, str] = {}

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


def _family_rates(model: str | None) -> dict | None:
    """Tier-accurate rates from the model name when the table has no entry."""
    if not model:
        return None
    name = model.split("/", 1)[1] if model.startswith("anthropic/") else model
    for prefix, (in_rate, out_rate) in _FAMILY_FALLBACK.items():
        if name.startswith(prefix):
            return {
                "input_cost_per_token": in_rate,
                "output_cost_per_token": out_rate,
                "cache_read_input_token_cost": in_rate * 0.10,
                "cache_creation_input_token_cost": in_rate * 1.25,
            }
    return None


def get_rates(model: str | None) -> dict:
    """Return the 4-rate dict for a model, falling back to `_FALLBACK`."""
    _ensure_loaded()
    key = _resolve_key(model)
    info = _PRICE_TABLE.get(key) if key else None
    if info is None:
        return _family_rates(model) or dict(_FALLBACK)
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
    reasoning_tokens: int = 0,
) -> float:
    r"""Compute total USD cost for one LLM call.

    `input_tokens` here is LiteLLM's normalized `prompt_tokens`, which —
    contrary to an earlier assumption in this docstring — is the TOTAL
    prompt token count INCLUDING the cache_read and cache_creation
    buckets. (LiteLLM normalizes Anthropic's split usage into OpenAI-style
    `prompt_tokens` semantics: a single number that represents
    everything billed on the input side.)

    Verified against Anthropic Console on 2026-04-30:
      raw inputs:  prompt=1.224M  cache_read=638k  cache_create=316k
      OUR previous logic (pre-fix): \$5.626  ← double-counts cache
      THIS function (post-fix):     \$2.763
      Anthropic Console:            \$2.70

    So we have to subtract the cache buckets from `input_tokens` before
    pricing the regular bucket — otherwise the cache tokens get billed
    twice (once at input rate via input_tokens, once at cache rate
    via the dedicated buckets).

    Negative regular-input is clamped to 0 in case a provider one day
    sends overlapping numbers we can't reconcile.

    `reasoning_tokens` (Claude 4.x extended thinking, OpenAI o-series) are
    billed at the OUTPUT rate per Anthropic's docs. LiteLLM's normalized
    `usage.completion_tokens` typically does NOT include the reasoning
    portion (they're surfaced separately in `completion_tokens_details`),
    so we add reasoning on top of output_tokens here. Closes the residual
    ~3-5% Sonnet undercount we saw vs Anthropic Console after PR #51.
    """
    rates = get_rates(model)
    inp = max(0, int(input_tokens))
    cr = max(0, int(cache_read_tokens))
    cc = max(0, int(cache_creation_tokens))
    rt = max(0, int(reasoning_tokens))
    regular = max(0, inp - cr - cc)
    cost = (
        regular * rates["input_cost_per_token"]
        + (max(0, int(output_tokens)) + rt) * rates["output_cost_per_token"]
        + cr * rates["cache_read_input_token_cost"]
        + cc * rates["cache_creation_input_token_cost"]
    )
    return round(cost, 6)


def format_usd(cost: float) -> str:
    """Human-friendly cost string — 4 decimals for cents, scientific for sub-cent."""
    if cost >= 0.01:
        return f"${cost:.4f}"
    if cost >= 0.0001:
        return f"${cost:.6f}"
    return f"${cost:.2e}"
