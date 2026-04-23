"""
Stream usage capture — wraps litellm.acompletion to capture real token
usage (including Anthropic cache fields) from streaming chat_model calls.

Why this exists:
  Agent Zero v1.9 streams chat_model responses via litellm.acompletion with
  stream=True. Two layers strip usage before it can reach AZ:

  1. LiteLLM's `CustomStreamWrapper.__anext__` (streaming_handler.py) removes
     the `usage` field from each yielded ModelResponseStream chunk "to only
     send usage on the final chunk." But the pre-strip chunk is appended to
     `wrapper.chunks` first — so the history retains usage.

  2. LiteLLM's success-callback path requires
     `async_complete_streaming_response` to be present in model_call_details
     (litellm_logging.py:2357). AZ's stream loop shape doesn't satisfy that
     gate, so `async_log_success_event` silently never fires for Sonnet —
     meaning `standard_logging_object` never reaches our CustomLogger in
     `_90_usage_tracker.py`.

Result without this patch: `chat_model_call_after` receives only the
assembled completion text (a `str`, not a ModelResponse) with no usage
metadata, so task_report falls back to `approximate_tokens` and cache
fields are always 0. Telegram /usage undercounts by the entire
chat_model volume.

Fix: patch `litellm.acompletion` at module load. When stream=True:
  • Wrap the returned CustomStreamWrapper in our async generator that
    yields chunks unchanged to AZ.
  • Agent Zero's unified_call aclose()'s the stream early (as soon as the
    response tool is detected). Our `finally` block drains the underlying
    wrapper to completion so Anthropic's final `message_delta` event
    lands in `wrapper.chunks` with the true output_tokens total.
  • Extract usage from `wrapper.chunks` — each chunk is a
    ModelResponseStream with `usage` kept in `model_extra` (Pydantic
    extra='allow'). Take max across chunks since Anthropic emits
    cumulative output_tokens on each message_delta, but input/cache
    tokens only on message_start.
  • Stash the aggregated usage into a ContextVar (`last_stream_usage`) so
    task_report's `chat_model_call_after` hook overrides its approximate
    fallback with real values + cache fields.
  • Fire-and-forget POST to telegram-bridge /track so /usage covers
    Sonnet as well as Haiku.

Also re-points `models.acompletion` (AZ imports it at module load, so the
litellm.acompletion assignment alone doesn't reach the already-bound name).

See issue #13 (M2).
"""

from helpers.extension import Extension
from helpers.task_report import last_stream_usage

import litellm

try:
    import requests  # used to fire-and-forget post to Telegram bridge
except Exception:  # pragma: no cover
    requests = None  # type: ignore

TELEGRAM_BRIDGE_URL = "http://telegram-bridge:8443/track"


_original_acompletion = None
_patched = False


def _extract_usage(assembled) -> dict | None:
    """Return a plain dict of usage fields from an assembled ModelResponse."""
    if assembled is None:
        return None
    usage = getattr(assembled, "usage", None)
    if usage is None:
        return None

    def g(k):
        if isinstance(usage, dict):
            return usage.get(k, 0) or 0
        return getattr(usage, k, 0) or 0

    data = {
        "prompt_tokens": g("prompt_tokens"),
        "completion_tokens": g("completion_tokens"),
        "cache_read_input_tokens": g("cache_read_input_tokens"),
        "cache_creation_input_tokens": g("cache_creation_input_tokens"),
    }
    # Fold OpenAI-style cached_tokens into cache_read if present
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
        data["cache_read_input_tokens"] += cached
    return data


def _merge_chunk_usage(chunk, acc: dict) -> None:
    """Inspect chunk usage (set by Anthropic handler on message_start /
    message_delta chunks, per llms/anthropic/chat/handler.py:770) and fold
    cumulative maxes into acc. Anthropic emits cumulative output_tokens on
    each message_delta; input_tokens and cache fields only arrive on
    message_start. Taking max(existing, observed) handles both.

    NOTE: ModelResponseStream does not declare `usage` as a Pydantic field,
    so the Anthropic handler's `ModelResponseStream(..., usage=usage)` lands
    in `model_extra` (extra='allow' config). `getattr(chunk, 'usage')` returns
    None; the real usage lives at `chunk.model_extra['usage']`.
    """
    usage = None
    extra = getattr(chunk, "model_extra", None) or getattr(chunk, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        usage = extra.get("usage")
    if usage is None:
        # Fallback to direct attribute (future-proof if LiteLLM adds it)
        usage = getattr(chunk, "usage", None)
    if usage is None:
        return

    def g(k):
        if isinstance(usage, dict):
            return usage.get(k, 0) or 0
        return getattr(usage, k, 0) or 0

    fields = [
        "prompt_tokens",
        "completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ]
    for f in fields:
        v = int(g(f) or 0)
        if v > acc.get(f, 0):
            acc[f] = v

    # OpenAI-style cache falls in prompt_tokens_details
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        if cached > acc.get("cache_read_input_tokens", 0):
            acc["cache_read_input_tokens"] = cached


async def _wrap_stream(wrapper, model_name: str):
    """Async generator: yields chunks unchanged to AZ, then extracts usage
    from `wrapper.chunks` after iteration completes.

    Why `wrapper.chunks` and not per-yielded-chunk: LiteLLM's
    `CustomStreamWrapper.__anext__` strips `usage` from every chunk before
    yielding it (streaming_handler.py, "remove usage from chunk, only send on
    final chunk"). BUT it appends the pre-strip chunk to `self.chunks` first.
    So by the time our async-for receives a chunk, usage is already gone —
    but `wrapper.chunks` retains the originals with usage intact (including
    Anthropic native `cache_read_input_tokens` / `cache_creation_input_tokens`
    from message_start / message_delta events).

    Usage cumulation: Anthropic emits cumulative output_tokens on each
    message_delta; input/cache tokens only on message_start. Taking max
    across chunks captures both.
    """
    acc: dict = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    n_chunks = 0
    n_swallowed = 0
    n_drained = 0
    try:
        async for chunk in wrapper:
            n_chunks += 1
            # LiteLLM emits a final empty-choices chunk carrying usage when
            # `stream_options={"include_usage": True}` is set. AZ's
            # _parse_chunk in models.py does chunk["choices"][0] blindly and
            # would IndexError. Swallow such chunks.
            choices = getattr(chunk, "choices", None)
            if not choices:
                n_swallowed += 1
                continue
            yield chunk
    finally:
        # Agent Zero's unified_call aclose()'s the stream when response_callback
        # signals stop (e.g., tool call complete). That triggers GeneratorExit
        # here BEFORE Anthropic's final `message_delta` event arrives with the
        # true `output_tokens` total. Drain the underlying wrapper to pull the
        # remaining events into `wrapper.chunks`, so output/usage are complete.
        try:
            while True:
                try:
                    drained = await wrapper.__anext__()
                    n_drained += 1
                    _ = drained  # discard; we only want its side effect on wrapper.chunks
                except StopAsyncIteration:
                    break
                except Exception:
                    break
        except Exception:
            pass
        # Best-effort close to release the underlying aiohttp session.
        try:
            if hasattr(wrapper, "aclose"):
                await wrapper.aclose()
        except Exception:
            pass
        try:
            # Retrieve pre-strip chunks from the wrapper itself.
            inner_chunks = getattr(wrapper, "chunks", None) or []
            n_with_usage = 0
            for c in inner_chunks:
                extra = getattr(c, "model_extra", None) or getattr(c, "__pydantic_extra__", None)
                u = None
                if isinstance(extra, dict):
                    u = extra.get("usage")
                if u is None:
                    u = getattr(c, "usage", None)
                if u is not None:
                    n_with_usage += 1
                _merge_chunk_usage(c, acc)

            if acc["prompt_tokens"] > 0 or acc["completion_tokens"] > 0:
                acc["model"] = model_name
                last_stream_usage.set(acc)
                # Also forward to Telegram bridge so /usage includes the
                # chat_model (streaming) as well as util_model. The LiteLLM
                # success-callback path fires only for non-stream calls in
                # AZ v1.9 — see _90_usage_tracker.py comments + issue #13.
                if requests is not None:
                    try:
                        requests.post(
                            TELEGRAM_BRIDGE_URL,
                            json={
                                "model": model_name,
                                "input_tokens": acc["prompt_tokens"],
                                "output_tokens": acc["completion_tokens"],
                                "cache_read_tokens": acc["cache_read_input_tokens"],
                                "cache_creation_tokens": acc["cache_creation_input_tokens"],
                            },
                            timeout=3,
                        )
                    except Exception:
                        pass
                print(
                    f"[StreamUsageCapture] model={model_name} "
                    f"in={acc['prompt_tokens']} out={acc['completion_tokens']} "
                    f"cache_read={acc['cache_read_input_tokens']} "
                    f"cache_creation={acc['cache_creation_input_tokens']} "
                    f"chunks={n_chunks} inner={len(inner_chunks)} drained={n_drained} "
                    f"usage_chunks={n_with_usage} swallowed={n_swallowed}",
                    flush=True,
                )
            else:
                print(
                    f"[StreamUsageCapture] NO USAGE model={model_name} "
                    f"chunks={n_chunks} inner={len(inner_chunks)} "
                    f"usage_chunks={n_with_usage} swallowed={n_swallowed}",
                    flush=True,
                )
        except Exception as e:
            print(f"[StreamUsageCapture] aggregation error: {e}", flush=True)


async def _wrapped_acompletion(*args, **kwargs):
    # Force LiteLLM to emit a final usage-bearing chunk. Without this,
    # CustomStreamWrapper strips `usage` from per-chunk output and only
    # reveals it via `standard_logging_object` — which never reaches
    # our callback due to AZ's stream loop shape (see issue #13).
    if kwargs.get("stream"):
        opts = dict(kwargs.get("stream_options") or {})
        opts["include_usage"] = True
        kwargs["stream_options"] = opts
    result = await _original_acompletion(*args, **kwargs)
    if kwargs.get("stream"):
        return _wrap_stream(result, kwargs.get("model", "unknown"))
    return result


class StreamUsageCapture(Extension):
    def execute(self, **kwargs):
        global _original_acompletion, _patched
        if _patched:
            return
        _original_acompletion = litellm.acompletion
        litellm.acompletion = _wrapped_acompletion
        # Also re-point models.acompletion which is already imported
        try:
            import models as _az_models
            _az_models.acompletion = _wrapped_acompletion
        except Exception:
            pass
        _patched = True
        print("[StreamUsageCapture] wrapped litellm.acompletion", flush=True)
