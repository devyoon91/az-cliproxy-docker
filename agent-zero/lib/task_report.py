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
from datetime import datetime, timezone
from pathlib import Path

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


def llm_call(agent, call_data, response) -> None:
    r = get_report(agent)
    if r is None:
        return
    model = None
    if isinstance(call_data, dict):
        model = call_data.get("model")
    usage = getattr(response, "usage", None) if response is not None else None
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    if usage is not None:
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cache_read = getattr(details, "cached_tokens", 0) or 0
    r["llm_calls"].append({
        "at": _now_iso(),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
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
