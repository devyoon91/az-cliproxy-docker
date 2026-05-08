"""Pin pricing.compute_cost.

Three properties that have all regressed in production at least once:
1. Cache math: regular_input = prompt_tokens - cache_read - cache_creation
   (PR #51 — the previous formula double-counted cache).
2. Reasoning_tokens billed at the OUTPUT rate, not as a 5th bucket
   (PR #64 — closes residual ~4-5% Sonnet undercount vs Console).
3. Negative regular-input clamps to 0 (defensive — providers occasionally
   send overlapping numbers).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PRICING_PATH = Path(__file__).resolve().parent.parent / "agent-zero" / "lib" / "pricing.py"


def _load_pricing():
    spec = importlib.util.spec_from_file_location("pricing", _PRICING_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pricing():
    return _load_pricing()


# Use a model name guaranteed to fall back to _FALLBACK so the test is
# stable regardless of LiteLLM's remote price table.
FALLBACK_MODEL = "model-that-does-not-exist-xyz"


def test_pure_input_output(pricing):
    """No cache, no reasoning — vanilla input + output cost."""
    cost = pricing.compute_cost(
        FALLBACK_MODEL, input_tokens=1_000_000, output_tokens=1_000_000,
    )
    # Fallback rates: $3/1M input, $15/1M output.
    assert cost == pytest.approx(3.0 + 15.0, rel=1e-9)


def test_cache_does_not_double_count(pricing):
    """The PR #51 regression. prompt_tokens INCLUDES cache_read + cache_creation
    in LiteLLM's normalized usage; we must subtract them before pricing the
    regular bucket."""
    cost = pricing.compute_cost(
        FALLBACK_MODEL,
        input_tokens=10_000,         # the WHOLE prompt bucket
        output_tokens=0,
        cache_read_tokens=8_000,     # 8k of those 10k were a cache hit
        cache_creation_tokens=1_000, # and 1k were a cache write
    )
    # regular = 10k - 8k - 1k = 1k * $3/1M = $0.003
    # cache_read   = 8k * $0.30/1M  = $0.0024
    # cache_create = 1k * $3.75/1M  = $0.00375
    # total = $0.00915
    expected = (1_000 * 3e-6) + (8_000 * 0.3e-6) + (1_000 * 3.75e-6)
    assert cost == pytest.approx(expected, rel=1e-6)


def test_reasoning_billed_at_output_rate(pricing):
    """PR #64 — extended-thinking tokens come in completion_tokens_details
    and are billed at the OUTPUT rate per Anthropic's schedule."""
    no_reasoning = pricing.compute_cost(
        FALLBACK_MODEL, input_tokens=1_000, output_tokens=100, reasoning_tokens=0,
    )
    with_reasoning = pricing.compute_cost(
        FALLBACK_MODEL, input_tokens=1_000, output_tokens=100, reasoning_tokens=500,
    )
    delta = with_reasoning - no_reasoning
    # 500 reasoning_tokens at $15/1M = $0.0075
    assert delta == pytest.approx(500 * 1.5e-5, rel=1e-9)


def test_negative_regular_clamps_to_zero(pricing):
    """If a provider ever reports cache_read + cache_creation > prompt_tokens,
    don't go negative."""
    cost = pricing.compute_cost(
        FALLBACK_MODEL,
        input_tokens=1_000,
        output_tokens=0,
        cache_read_tokens=900,
        cache_creation_tokens=900,  # 900 + 900 > 1000 — overlapping
    )
    # regular clamped to 0; only the cache buckets count.
    expected = (900 * 0.3e-6) + (900 * 3.75e-6)
    assert cost == pytest.approx(expected, rel=1e-6)
    assert cost > 0  # sanity — we didn't return a negative


def test_zero_everything(pricing):
    cost = pricing.compute_cost(FALLBACK_MODEL)
    assert cost == 0.0


def test_format_usd_breakpoints(pricing):
    """Display helper — ensures the three formatting branches don't drift."""
    assert pricing.format_usd(1.2345) == "$1.2345"          # >= 0.01 → 4dp
    assert pricing.format_usd(0.001234) == "$0.001234"      # >= 0.0001 → 6dp
    assert pricing.format_usd(0.000005) == "$5.00e-06"      # otherwise scientific
