"""
Record a utility-model LLM call (Haiku, naming/summarization side-calls)
after each `call_utility_model()` invocation.

Context
-------
Agent Zero v1.9 invokes `util_model_call_after` at agent.py:788 with
`call_data=call_data, response=response`, but ships no extensions for it —
so util_model calls (usually the lighter Haiku model) never landed in the
per-task JSON, even though chat_model calls did. Result: Task JSON and
per-task cost summaries undercounted the util-model tail by the full
number of Haiku calls (measured: ~71 calls / day on an active agent).

The LiteLLM stream probe (agent_init/_91_chunk_usage_probe.py) already
wraps `litellm.acompletion` at module load — it intercepts util streams
too, and stashes real token usage into the shared `last_stream_usage`
ContextVar. Routing the util path through the same `llm_call()` helper as
chat_model is enough: the helper consumes that ContextVar and falls back
to `approximate_tokens` when unavailable.

Coverage matrix after this hook:
    path             stream?   Task JSON    Telegram /usage
    chat_model_call  yes       ✓ (chat hook)   ✓ (probe POST)
    util_model_call  yes       ✓ (this hook)   ✓ (probe POST)
    util_model_call  no        ✓ (this hook)*  ✓ (_90 success cb)
    * approximate tokens; real usage lands if LiteLLM success callback
      fires between unified_call return and this hook.

See issue #24 Wave 2.
"""

from helpers.extension import Extension
from helpers.task_report import llm_call


class TaskReportUtilLLM(Extension):
    async def execute(self, call_data=None, response=None, **kwargs):
        # Same shape as chat_model_call_after minus the `reasoning` arg —
        # util_model doesn't expose reasoning content. `response` is the
        # assembled completion string, mirroring the chat path.
        llm_call(self.agent, call_data, response)
