"""
Task-level instrumentation for Agent Zero monologues.

Captures profile, skills, tool calls, LLM calls, and iterations during a
single monologue() invocation, then writes a JSON file to
/a0/logs/tasks/<task_id>.json on monologue_end.

This is M1 of the task post-report system — raw capture only, no analysis
or reporting yet. See https://github.com/devyoon91/az-cliproxy-docker/issues/1

Mounted into the container at /a0/python/helpers/task_report.py so that
extension files can `from helpers.task_report import ...`.
"""

import hashlib
import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

# ContextVar that the `_91_chunk_usage_probe` agent_init extension writes to
# at stream end with real LiteLLM-assembled usage (incl. Anthropic cache
# fields). Read here in `llm_call()` to override approximate tokens with
# real values when available. Lives in helpers/task_report.py so both the
# agent_init extension and chat_model_call_after extension can share it
# via `from helpers.task_report import last_stream_usage`.
last_stream_usage: ContextVar = ContextVar("az_last_stream_usage", default=None)

try:
    # Agent Zero ships this helper; same function it uses internally in
    # models.py to size cache-history chunks. Graceful fallback if absent.
    from helpers.tokens import approximate_tokens  # type: ignore
except Exception:  # pragma: no cover - only runs inside AZ container
    def approximate_tokens(text: str) -> int:  # type: ignore
        return 0

logger = logging.getLogger(__name__)

TASKS_DIR = Path("/a0/logs/tasks")
DATA_KEY = "task_report"
PENDING_TOOL_KEY = "task_report_pending_tools"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_task_id() -> str:
    return f"task-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _args_hash(args) -> str:
    try:
        canon = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        canon = repr(args)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def _preview(args, max_len: int = 120) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        s = repr(args)
    return s if len(s) <= max_len else s[:max_len] + "…"


def begin_task(agent) -> dict:
    """Initialize a fresh report blob on agent.data. Called from monologue_start."""
    skills = agent.data.get("loaded_skills") or []
    profile = getattr(getattr(agent, "config", None), "profile", None)
    report = {
        "task_id": _new_task_id(),
        "started_at": _now_iso(),
        "started_ts": time.time(),
        "agent_name": getattr(agent, "agent_name", None),
        "agent_number": getattr(agent, "number", None),
        "profile": profile,
        "skills_loaded": list(skills),
        "iterations": 0,
        "llm_calls": [],
        "tool_calls": [],
    }
    agent.data[DATA_KEY] = report
    agent.data[PENDING_TOOL_KEY] = {}
    logger.info(
        f"[task_report] begin {report['task_id']} profile={profile} skills={len(skills)}"
    )
    return report


def get_report(agent):
    return agent.data.get(DATA_KEY)


def record_iteration(agent, iteration) -> None:
    r = get_report(agent)
    if r is None:
        return
    try:
        r["iterations"] = max(r["iterations"], int(iteration or 0))
    except (TypeError, ValueError):
        pass


def tool_start(agent, tool_name: str, tool_args) -> None:
    pending = agent.data.get(PENDING_TOOL_KEY)
    if pending is None:
        return
    pending[tool_name] = {
        "name": tool_name,
        "args_hash": _args_hash(tool_args),
        "args_preview": _preview(tool_args),
        "started_at": _now_iso(),
        "_started_ts": time.time(),
    }


def tool_end(agent, tool_name: str, response) -> None:
    r = get_report(agent)
    pending = agent.data.get(PENDING_TOOL_KEY)
    if r is None or pending is None:
        return
    entry = pending.pop(tool_name, None) or {"name": tool_name}
    end_ts = time.time()
    msg = getattr(response, "message", "") if response is not None else ""
    if not isinstance(msg, str):
        msg = str(msg) if msg is not None else ""
    started_ts = entry.pop("_started_ts", end_ts)
    entry.update({
        "ended_at": _now_iso(),
        "duration_ms": round((end_ts - started_ts) * 1000, 1),
        "result_size": len(msg),
        "break_loop": bool(getattr(response, "break_loop", False)) if response is not None else False,
    })
    r["tool_calls"].append(entry)


def _extract_cache_tokens(usage) -> tuple[int, int]:
    """Extract (cache_read, cache_creation) from LiteLLM-normalized usage.

    Supports both formats:
    - OpenAI: prompt_tokens_details.cached_tokens
    - Anthropic: cache_read_input_tokens / cache_creation_input_tokens
      (LiteLLM passes through)
    """
    read = 0
    creation = 0
    if usage is None:
        return read, creation
    # Anthropic fields (passed through by LiteLLM)
    read += getattr(usage, "cache_read_input_tokens", 0) or 0
    creation += getattr(usage, "cache_creation_input_tokens", 0) or 0
    # OpenAI fields
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        read += getattr(details, "cached_tokens", 0) or 0
    return read, creation


def _estimate_input_tokens(call_data) -> int:
    if not isinstance(call_data, dict):
        return 0
    messages = call_data.get("messages") or []
    try:
        text = json.dumps(messages, ensure_ascii=False, default=str)
    except Exception:
        text = str(messages)
    try:
        return int(approximate_tokens(text) or 0)
    except Exception:
        return 0


def _estimate_output_tokens(response, reasoning) -> int:
    parts = []
    if isinstance(response, str) and response:
        parts.append(response)
    if isinstance(reasoning, str) and reasoning:
        parts.append(reasoning)
    if not parts:
        return 0
    try:
        return int(approximate_tokens("\n".join(parts)) or 0)
    except Exception:
        return 0


def llm_call(agent, call_data, response, reasoning=None) -> None:
    """Record an LLM call.

    Agent Zero v1.9 note: the `chat_model_call_after` hook receives `response`
    as a *string* (the assembled completion text), not a LiteLLM
    `ModelResponse`. `_parse_chunk` in /a0/models.py strips `usage` from
    streaming chunks, so real token counts and Anthropic cache fields are
    unreachable from this hook.

    As a stopgap we approximate input/output via `helpers.tokens.approximate_tokens`
    (same function Agent Zero uses itself) and mark the entry with
    `tokens_approximate=True`. Real cache tokens require bridging from the
    LiteLLM callback layer (`_90_usage_tracker.py`) — tracked separately.
    """
    r = get_report(agent)
    if r is None:
        return
    # Resolve model name as a string. In v1.9, `response` is a str, so fall
    # back to call_data["model"].model_name (LiteLLMChatWrapper attribute).
    model = None
    if isinstance(call_data, dict):
        m = call_data.get("model")
        model = getattr(m, "model_name", None) or getattr(m, "name", None)
    # Belt-and-suspenders: if a future version ever passes a ModelResponse.
    if not isinstance(model, str) and response is not None:
        rm = getattr(response, "model", None)
        if isinstance(rm, str):
            model = rm
    if not isinstance(model, str):
        model = None

    # Priority:
    # 1) ContextVar from stream-usage-capture extension (real + cache fields)
    # 2) response.usage if a future AZ version passes a ModelResponse
    # 3) approximate_tokens fallback
    stream_usage = None
    try:
        stream_usage = last_stream_usage.get()
    except Exception:
        stream_usage = None

    usage = getattr(response, "usage", None) if response is not None else None

    if stream_usage:
        input_tokens = int(stream_usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(stream_usage.get("completion_tokens", 0) or 0)
        cache_read = int(stream_usage.get("cache_read_input_tokens", 0) or 0)
        cache_creation = int(stream_usage.get("cache_creation_input_tokens", 0) or 0)
        approximate = False
        # Consume so the next call in the same monologue does not re-read
        # stale values. Stream-capture writes fresh values each call.
        try:
            last_stream_usage.set(None)
        except Exception:
            pass
    elif usage is not None:
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        cache_read, cache_creation = _extract_cache_tokens(usage)
        approximate = False
    else:
        input_tokens = _estimate_input_tokens(call_data)
        output_tokens = _estimate_output_tokens(response, reasoning)
        cache_read = 0
        cache_creation = 0
        approximate = True

    r["llm_calls"].append({
        "at": _now_iso(),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "tokens_approximate": approximate,
    })


def finish_task(agent) -> dict | None:
    """Finalize report and write to disk. Called from monologue_end.

    NOTE: monologue_end is skipped on cancel/kill. A fallback via the implicit
    @extensible hook (_functions/agent/Agent/monologue/end) is left for a
    follow-up — see issue #1.
    """
    r = get_report(agent)
    if r is None:
        return None
    r["ended_at"] = _now_iso()
    started_ts = r.pop("started_ts", time.time())
    r["elapsed_sec"] = round(time.time() - started_ts, 3)
    r["totals"] = {
        "tool_calls": len(r["tool_calls"]),
        "llm_calls": len(r["llm_calls"]),
        "input_tokens": sum(c["input_tokens"] for c in r["llm_calls"]),
        "output_tokens": sum(c["output_tokens"] for c in r["llm_calls"]),
        "cache_read_tokens": sum(c["cache_read_tokens"] for c in r["llm_calls"]),
        "cache_creation_tokens": sum(c.get("cache_creation_tokens", 0) for c in r["llm_calls"]),
    }
    try:
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        path = TASKS_DIR / f"{r['task_id']}.json"
        path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            f"[task_report] wrote {path} "
            f"(tools={r['totals']['tool_calls']} llm={r['totals']['llm_calls']} "
            f"iter={r['iterations']} elapsed={r['elapsed_sec']}s)"
        )
    except Exception as e:
        logger.warning(f"[task_report] write failed: {e}")
    finally:
        agent.data.pop(DATA_KEY, None)
        agent.data.pop(PENDING_TOOL_KEY, None)
    return r
