"""Cost calculation for the Telegram bridge.

Mirrors `agent-zero/lib/pricing.py:compute_cost` so the bridge's `/track`
and `/today` paths produce the same numbers per task that agent-zero
writes into the task JSONs. When the cache math or reasoning rate
changes upstream, both files have to update together — see PRs #51
(cache no-double-count) and #64 (reasoning at output rate) for the
shared regressions that fired in both copies at once.

This module is a Phase A carve-out from the 3,500-line bot.py
(issue #79). What's here:

- `_model_cost_map` — the LiteLLM price table, populated at process
  startup by `_load_model_cost_map()`. The dict is mutated in place
  (clear+update) rather than reassigned so importers can hold a stable
  binding. See the comment in `_load_model_cost_map`.
- `_load_model_cost_map()` — fetch + populate.
- `_model_info(model)` — alias-aware lookup against the table.
- `_normalize_model(model)` — strip `anthropic/` prefix so by_model
  rows don't split.
- `calc_cost(...)` — cache-aware, reasoning-aware cost in USD.

What stays in bot.py for now:

- `track_usage()` and the `usage_today` global — they own per-day
  state and are entangled with `usage_history`. Phase B carve.
- `_resolve_litellm_key()` and `take_pricing_snapshot()` — sibling
  consumers of `_model_cost_map`. They access the map via
  `pricing.cost._model_cost_map` (dynamic attribute lookup, stays
  fresh through reloads).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# LiteLLM model price table. Populated once by `_load_model_cost_map()`
# at startup. Mutated in place (clear+update) rather than reassigned so
# `from pricing.cost import _model_cost_map` bindings stay valid through
# the load. (Reassignment would leave importers pointing at the empty
# pre-load dict forever.)
_model_cost_map: dict = {}


def _load_model_cost_map() -> None:
    """Fetch LiteLLM's latest price table and populate `_model_cost_map`.

    Called once from bot.py main. Network failure logs a warning and
    leaves the map empty — `_model_info` then falls back to the inline
    per-token rates baked into `calc_cost`.
    """
    try:
        import httpx

        resp = httpx.get(
            "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
            timeout=10,
        )
        if resp.status_code == 200:
            _model_cost_map.clear()
            _model_cost_map.update(resp.json())
            logger.info(f"[Cost] Loaded {len(_model_cost_map)} model prices from LiteLLM")
            return
    except Exception as e:
        logger.warning(f"[Cost] Failed to fetch remote prices: {e}")

    _model_cost_map.clear()
    logger.warning("[Cost] Using fallback cost estimation")


# Anthropic family rates (input, output) per token, used when the LiteLLM
# table is unavailable but the model name still tells us the tier (#141).
# Mirrors agent-zero/lib/pricing.py:_FAMILY_FALLBACK — keep in sync.
_FAMILY_FALLBACK = {
    "claude-fable": (0.00001, 0.00005),    # $10 / $50 per 1M
    "claude-opus": (0.000005, 0.000025),   # $5 / $25
    "claude-sonnet": (0.000003, 0.000015),  # $3 / $15
    "claude-haiku": (0.000001, 0.000005),  # $1 / $5
}


def _model_info(model: str) -> dict:
    """Resolve a model name against the LiteLLM price map with AZ aliasing.

    LiteLLM carries native keys for the current Anthropic lineup
    (claude-sonnet-4-6, claude-opus-4-6/7/8, claude-fable-5) as well as
    bare legacy names, so exact match plus `anthropic/` prefix-strip
    covers everything AZ emits. The old alias dict mapped
    claude-sonnet-4-6 onto the dated 4-5 snapshot key, shadowing the
    native key (#141).
    """
    if model in _model_cost_map:
        return _model_cost_map[model]
    if model.startswith("anthropic/"):
        tail = model.split("/", 1)[1]
        if tail in _model_cost_map:
            return _model_cost_map[tail]
    return {}


def _family_rates(model: str) -> tuple[float, float] | None:
    """Tier-accurate (input, output) rates from the model name alone."""
    name = model.split("/", 1)[1] if model.startswith("anthropic/") else model
    for prefix, rates in _FAMILY_FALLBACK.items():
        if name.startswith(prefix):
            return rates
    return None


def calc_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> float:
    r"""Cache-aware cost calc.

    `input_tokens` here is LiteLLM's normalized `prompt_tokens` — the
    TOTAL prompt token count INCLUDING the cache_read and cache_creation
    buckets. The earlier assumption that Anthropic's split usage stayed
    split through LiteLLM was wrong; LiteLLM normalizes everything into
    OpenAI-style `prompt_tokens` semantics where the number is one
    aggregate.

    Verified against Anthropic Console on 2026-04-30:
        raw inputs:  prompt=1.224M  cache_read=638k  cache_create=316k
        pre-fix:  \$5.626  (double-counted cache)
        post-fix: \$2.763  ≈ Console \$2.70

    Same fix lives in agent-zero/lib/pricing.py:compute_cost.
    """
    info = _model_info(model)
    family = _family_rates(model) if not info else None
    fb_in, fb_out = family or (0.000003, 0.000015)  # generic $3/$15 last resort
    in_rate = info.get("input_cost_per_token", fb_in)
    out_rate = info.get("output_cost_per_token", fb_out)
    read_rate = info.get("cache_read_input_token_cost", in_rate * 0.10)
    create_rate = info.get("cache_creation_input_token_cost", in_rate * 1.25)
    inp = max(0, int(input_tokens))
    cr = max(0, int(cache_read_tokens))
    cc = max(0, int(cache_creation_tokens))
    rt = max(0, int(reasoning_tokens))
    regular = max(0, inp - cr - cc)
    # Reasoning / extended-thinking tokens (Claude 4.x, OpenAI o-series) are
    # billed at output rate. Mirrors agent-zero/lib/pricing.py:compute_cost.
    return (
        regular * in_rate
        + (max(0, int(output_tokens)) + rt) * out_rate
        + cr * read_rate
        + cc * create_rate
    )


def _normalize_model(model: str) -> str:
    """Canonicalize model name before aggregating.

    LiteLLM's kwargs["model"] and the stream-probe POST sometimes carry the
    provider prefix (`anthropic/claude-sonnet-4-6`) and sometimes don't
    (`claude-sonnet-4-6`). Without this the `by_model` dict splits one model
    into two rows with mismatched cache/cost stats. Mirrors the same helper
    now living in agent-zero/lib/task_report.py (issue #24 Wave 2).
    """
    if not isinstance(model, str) or not model:
        return model
    if model.startswith("anthropic/"):
        return model.split("/", 1)[1]
    return model
