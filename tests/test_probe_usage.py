"""Pin _91_chunk_usage_probe — usage extraction + cumulative-max merge.

Regression history:
- PR #54: non-stream usage was being missed (sentinel in=1/out=3). The probe
  now wraps both stream and non-stream paths.
- PR #61: sync `litellm.completion` was unwrapped, missing util-model calls.
- PR #63: probe + _90 both POSTed to /track, doubling /usage.
- PR #64: reasoning_tokens (extended thinking) added — these come via
  completion_tokens_details and are billed at output rate.

What we're pinning here:
1. `_extract_usage` returns the 5-field shape {prompt, completion, cache_read,
   cache_creation, reasoning_tokens} from BOTH the object form and the dict form.
2. `_merge_chunk_usage` takes the cumulative MAX across stream chunks
   (Anthropic emits cumulative completion_tokens; naive sum would double).
3. Reasoning tokens propagate through both paths.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PROBE_PATH = (
    Path(__file__).resolve().parent.parent
    / "agent-zero" / "extensions" / "python" / "agent_init"
    / "_91_chunk_usage_probe.py"
)


def _load_probe():
    spec = importlib.util.spec_from_file_location("probe", _PROBE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    return _load_probe()


# ── _extract_usage ──────────────────────────────────────────────────


class _FakeReasoningDetails:
    reasoning_tokens = 4500


class _FakeUsageWithReasoning:
    prompt_tokens = 30000
    completion_tokens = 1200
    cache_read_input_tokens = 8000
    cache_creation_input_tokens = 5000
    prompt_tokens_details = None
    completion_tokens_details = _FakeReasoningDetails()


class _FakeUsageNoReasoning:
    prompt_tokens = 100
    completion_tokens = 50
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    prompt_tokens_details = None
    completion_tokens_details = None


class _FakeResponse:
    def __init__(self, usage):
        self.usage = usage


def test_extract_usage_object_with_reasoning(probe):
    u = probe._extract_usage(_FakeResponse(_FakeUsageWithReasoning()))
    assert u == {
        "prompt_tokens": 30000,
        "completion_tokens": 1200,
        "cache_read_input_tokens": 8000,
        "cache_creation_input_tokens": 5000,
        "reasoning_tokens": 4500,
    }


def test_extract_usage_no_reasoning_details(probe):
    u = probe._extract_usage(_FakeResponse(_FakeUsageNoReasoning()))
    assert u["reasoning_tokens"] == 0
    assert u["prompt_tokens"] == 100
    assert u["completion_tokens"] == 50


# ── _merge_chunk_usage cumulative-max ───────────────────────────────


class _FakeChunk:
    """Anthropic-style streaming: each `message_delta` emits a CUMULATIVE
    usage view, not an incremental one. Naive summation double-counts."""

    def __init__(self, usage_dict):
        self.model_extra = {"usage": usage_dict}


def test_merge_takes_cumulative_max_completion(probe):
    """Two chunks with completion=5 then completion=50 → final 50, not 55."""
    acc = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    probe._merge_chunk_usage(_FakeChunk({
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }), acc)
    probe._merge_chunk_usage(_FakeChunk({
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }), acc)
    assert acc["completion_tokens"] == 50, "cumulative-max would be defeated by sum"
    assert acc["prompt_tokens"] == 100


def test_merge_reasoning_tokens_cumulative_max(probe):
    """Same cumulative semantics apply to reasoning_tokens — confirmed in PR #64."""
    acc = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    probe._merge_chunk_usage(_FakeChunk({
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "completion_tokens_details": {"reasoning_tokens": 200},
    }), acc)
    probe._merge_chunk_usage(_FakeChunk({
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "completion_tokens_details": {"reasoning_tokens": 1500},
    }), acc)
    assert acc["reasoning_tokens"] == 1500
