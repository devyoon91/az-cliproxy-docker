"""Pin telegram-bridge/pricing/cost.py — the bridge's mirror of
`agent-zero/lib/pricing.py`.

These two files MUST agree on the math: the bridge's `/track` and
agent-zero's `task_report.llm_call` both compute cost from the same
inputs and any drift shows up as `/today` ≠ Anthropic Console. The
shared regression series (#51 cache double-count, #64 reasoning
at output rate) hit both files at once because they're mirrors —
this test catches the bridge half independently.

Test was deferred from #77 because at that point all the pricing
logic lived inside the 3,500-line bot.py with import-heavy deps.
After #79 Phase A carved `pricing/cost.py` out, the function loads
cleanly with no agent-zero / telegram / aiohttp imports needed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_COST_PATH = (
    Path(__file__).resolve().parent.parent / "telegram-bridge" / "pricing" / "cost.py"
)


def _load_cost():
    spec = importlib.util.spec_from_file_location("bridge_pricing_cost", _COST_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cost():
    return _load_cost()


# A model name guaranteed to fall back to the inline rates inside calc_cost
# regardless of whether _load_model_cost_map() ran. Keeps the test
# deterministic across remote price-table changes.
FALLBACK_MODEL = "model-that-does-not-exist-xyz"


def test_pure_input_output(cost):
    c = cost.calc_cost(FALLBACK_MODEL, 1_000_000, 1_000_000)
    # $3/1M input + $15/1M output = $18.00
    assert c == pytest.approx(18.0, rel=1e-9)


def test_cache_no_double_count(cost):
    """PR #51 regression — prompt_tokens INCLUDES the cache buckets."""
    c = cost.calc_cost(
        FALLBACK_MODEL,
        input_tokens=10_000,
        output_tokens=0,
        cache_read_tokens=8_000,
        cache_creation_tokens=1_000,
    )
    expected = (1_000 * 3e-6) + (8_000 * 0.3e-6) + (1_000 * 3.75e-6)
    assert c == pytest.approx(expected, rel=1e-6)


def test_reasoning_at_output_rate(cost):
    """PR #64 — reasoning_tokens billed at the output rate."""
    base = cost.calc_cost(FALLBACK_MODEL, 1_000, 100, reasoning_tokens=0)
    with_thinking = cost.calc_cost(FALLBACK_MODEL, 1_000, 100, reasoning_tokens=500)
    # 500 tokens × $15/1M = $0.0075
    assert with_thinking - base == pytest.approx(500 * 1.5e-5, rel=1e-9)


def test_negative_regular_clamps(cost):
    """If cache_read + cache_creation > input_tokens, regular bucket
    must clamp to 0 — never go negative."""
    c = cost.calc_cost(
        FALLBACK_MODEL,
        input_tokens=1_000,
        output_tokens=0,
        cache_read_tokens=900,
        cache_creation_tokens=900,
    )
    expected = (900 * 0.3e-6) + (900 * 3.75e-6)
    assert c == pytest.approx(expected, rel=1e-6)
    assert c > 0


def test_normalize_model_strips_anthropic_prefix(cost):
    assert cost._normalize_model("anthropic/claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert cost._normalize_model("claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert cost._normalize_model("") == ""
    assert cost._normalize_model(None) is None  # type: ignore[arg-type]


# ── family-aware fallback (#141) ────────────────────────────────────
# With the LiteLLM map empty (fetch failed / not yet loaded), Anthropic
# models price by family tier instead of the flat $3/$15 fallback —
# which undercounted Opus ~1.7x ($5/$25 actual) and Fable 5 ~3.3x
# ($10/$50 actual).


def test_family_fallback_opus(cost):
    cost._model_cost_map.clear()
    c = cost.calc_cost("claude-opus-4-8", 1_000_000, 1_000_000)
    assert c == pytest.approx(5.0 + 25.0, rel=1e-9)


def test_family_fallback_fable_with_prefix(cost):
    cost._model_cost_map.clear()
    c = cost.calc_cost("anthropic/claude-fable-5", 1_000_000, 1_000_000)
    assert c == pytest.approx(10.0 + 50.0, rel=1e-9)


def test_family_fallback_cache_rates_derive_from_input(cost):
    """Cache rates derive from the family input rate: read 0.1x, write 1.25x."""
    cost._model_cost_map.clear()
    c = cost.calc_cost(
        "claude-opus-4-8",
        input_tokens=2_000_000,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
    )
    # regular = 0; read 1M * $0.50/1M + write 1M * $6.25/1M
    assert c == pytest.approx(0.5 + 6.25, rel=1e-9)


def test_family_fallback_not_applied_to_unknown_models(cost):
    cost._model_cost_map.clear()
    c = cost.calc_cost(FALLBACK_MODEL, 1_000_000, 1_000_000)
    assert c == pytest.approx(3.0 + 15.0, rel=1e-9)  # generic fallback intact
