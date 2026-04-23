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
import os
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

try:
    # Shared pricing helper. Ships with the repo as /a0/helpers/pricing.py
    # via docker-compose mount; falls back to a no-cost stub if the mount
    # is missing so task_report never crashes a monologue.
    from helpers.pricing import compute_cost  # type: ignore
except Exception:  # pragma: no cover - only runs inside AZ container
    def compute_cost(model=None, input_tokens=0, output_tokens=0,
                     cache_read_tokens=0, cache_creation_tokens=0) -> float:  # type: ignore
        return 0.0

logger = logging.getLogger(__name__)

TASKS_DIR = Path("/a0/logs/tasks")
DATA_KEY = "task_report"
PENDING_TOOL_KEY = "task_report_pending_tools"

# ended_reason values:
#   "pending"   — task is still running (written by begin_task + periodic saves)
#   "completed" — monologue_end fired normally (finish_task marked it)
#   "orphaned"  — a prior AZ process died or the task was cancelled; set by the
#                 startup sweep on any JSON it finds still in "pending" state.
ENDED_PENDING = "pending"
ENDED_COMPLETED = "completed"
ENDED_ORPHANED = "orphaned"


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
    """Initialize a fresh report blob on agent.data. Called from monologue_start.

    Writes the initial JSON to disk immediately with `ended_reason="pending"`.
    Periodic saves from message_loop_start / tool_execute_after keep it fresh
    so that cancel/kill paths (where monologue_end is skipped and
    asyncio.CancelledError propagates past the @extensible end-hook) still
    leave an up-to-date JSON on disk. The next AZ startup runs
    `sweep_orphans()` to mark any leftover pending files as "orphaned".
    """
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
        "ended_reason": ENDED_PENDING,
    }
    agent.data[DATA_KEY] = report
    agent.data[PENDING_TOOL_KEY] = {}
    # Write the initial snapshot so even an immediate cancel leaves evidence.
    _write_report(report, final=False)
    logger.info(
        f"[task_report] begin {report['task_id']} profile={profile} skills={len(skills)}"
    )
    return report


def get_report(agent):
    return agent.data.get(DATA_KEY)


def _compute_totals(report: dict) -> dict:
    calls = report.get("llm_calls") or []
    return {
        "tool_calls": len(report.get("tool_calls") or []),
        "llm_calls": len(calls),
        "input_tokens": sum(c.get("input_tokens", 0) for c in calls),
        "output_tokens": sum(c.get("output_tokens", 0) for c in calls),
        "cache_read_tokens": sum(c.get("cache_read_tokens", 0) for c in calls),
        "cache_creation_tokens": sum(c.get("cache_creation_tokens", 0) for c in calls),
        # Per-call `cost_usd` is authoritative; we sum rather than re-computing
        # from totals so model-mix inside one task is priced accurately.
        "cost_usd": round(sum(c.get("cost_usd", 0.0) or 0.0 for c in calls), 6),
    }


def _write_report(report: dict, *, final: bool) -> None:
    """Serialize `report` to its task JSON file. Idempotent — safe to call
    repeatedly during a run.

    `final=True` means the write is the terminal one from `finish_task` — we
    compute and store `elapsed_sec` and strip the internal `started_ts`.
    `final=False` writes an in-progress snapshot; `elapsed_sec` reflects time
    since start and `ended_reason` stays as whatever the caller set it to
    (usually "pending").
    """
    if not report or not report.get("task_id"):
        return
    try:
        snapshot = dict(report)
        started_ts = snapshot.get("started_ts")
        if isinstance(started_ts, (int, float)):
            snapshot["elapsed_sec"] = round(time.time() - started_ts, 3)
        snapshot.pop("started_ts", None)
        snapshot["totals"] = _compute_totals(snapshot)
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        path = TASKS_DIR / f"{snapshot['task_id']}.json"
        # Atomic-ish write: rename from .tmp so a crash mid-flush doesn't
        # leave a half-serialized file.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        if final:
            logger.info(
                f"[task_report] wrote {path} final "
                f"(tools={snapshot['totals']['tool_calls']} "
                f"llm={snapshot['totals']['llm_calls']} "
                f"iter={snapshot.get('iterations', 0)} "
                f"elapsed={snapshot.get('elapsed_sec', 0)}s)"
            )
    except Exception as e:
        logger.warning(f"[task_report] write failed: {e}")


def save_task(agent) -> None:
    """Idempotent periodic save. Called on message_loop_start and
    tool_execute_after so that cancel/kill paths still leave a fresh JSON.
    Does NOT pop from agent.data — finish_task owns that lifecycle step.
    """
    r = get_report(agent)
    if r is None:
        return
    _write_report(r, final=False)


def sweep_orphans() -> int:
    """Mark stale/legacy task JSONs with a proper `ended_reason`.

    Called once on AZ startup from an agent_init extension. Two fixes:

    1. Any file with `ended_reason == "pending"` at startup — previous
       process died mid-run or the task was cancelled (monologue_end
       never fired). Promoted to `"orphaned"` with `ended_at=now`.

    2. Legacy files (from before M2.1) that never had an `ended_reason`
       field but do have an `ended_at` timestamp — clearly a completed
       run. Tagged `"completed"` so /today aggregates don't lump them in
       with true pendings. Files lacking BOTH fields are left alone
       (shouldn't happen but best to be conservative).

    Returns the total number of files rewritten.
    """
    count = 0
    try:
        if not TASKS_DIR.exists():
            return 0
        for p in TASKS_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            changed = False
            reason = data.get("ended_reason")
            if reason == ENDED_PENDING:
                data["ended_reason"] = ENDED_ORPHANED
                data.setdefault("ended_at", _now_iso())
                changed = True
            elif reason is None and data.get("ended_at"):
                # Legacy (pre-M2.1) JSON — annotate the completion state
                # instead of defaulting aggregation code to "pending".
                data["ended_reason"] = ENDED_COMPLETED
                changed = True
            if changed:
                try:
                    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    count += 1
                except Exception as e:
                    logger.warning(f"[task_report] sweep write failed for {p}: {e}")
    except Exception as e:
        logger.warning(f"[task_report] sweep_orphans error: {e}")
    if count:
        logger.info(f"[task_report] sweep_orphans: rewrote {count} file(s)")
    return count


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

    # Price the call. When tokens are approximate (no real stream usage was
    # captured), we still compute a cost — it's labelled approximate via
    # `tokens_approximate=True`, and skipping pricing entirely would lose the
    # fallback-path volume in /usage dashboards.
    cost = compute_cost(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
    )

    r["llm_calls"].append({
        "at": _now_iso(),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "cost_usd": cost,
        "tokens_approximate": approximate,
    })


TELEGRAM_BRIDGE_NOTIFY_URL = os.environ.get(
    "TELEGRAM_BRIDGE_URL", "http://telegram-bridge:8443/notify"
)
TASK_SUMMARY_ENABLED = os.environ.get("AZ_TASK_SUMMARY", "1") not in ("0", "false", "False")


def _format_task_summary(snapshot: dict) -> str:
    """Compact Telegram-friendly per-task summary.

    Shown on `monologue_end` after `finish_task` writes the final JSON.
    Opt out with env var `AZ_TASK_SUMMARY=0`.
    """
    totals = snapshot.get("totals") or {}
    elapsed = snapshot.get("elapsed_sec") or 0
    cost_usd = totals.get("cost_usd", 0.0) or 0.0

    # Group llm_calls by model for the breakdown
    by_model: dict[str, dict] = {}
    for c in snapshot.get("llm_calls") or []:
        m = c.get("model") or "unknown"
        bucket = by_model.setdefault(m, {
            "calls": 0, "input": 0, "output": 0,
            "cache_read": 0, "cache_create": 0, "cost": 0.0,
        })
        bucket["calls"] += 1
        bucket["input"] += c.get("input_tokens", 0) or 0
        bucket["output"] += c.get("output_tokens", 0) or 0
        bucket["cache_read"] += c.get("cache_read_tokens", 0) or 0
        bucket["cache_create"] += c.get("cache_creation_tokens", 0) or 0
        bucket["cost"] += c.get("cost_usd", 0.0) or 0.0

    lines = [
        f"✅ 태스크 완료 ({snapshot.get('task_id', '?')})",
        f"⏱ {elapsed:.1f}s  🔧 tools {totals.get('tool_calls', 0)}  💬 LLM {totals.get('llm_calls', 0)}",
        f"💰 ${cost_usd:.4f}",
    ]
    if by_model:
        for model, b in sorted(by_model.items(), key=lambda kv: kv[1]["cost"], reverse=True):
            cache_part = (
                f" | cache r:{b['cache_read']:,} c:{b['cache_create']:,}"
                if (b["cache_read"] or b["cache_create"]) else ""
            )
            lines.append(
                f"  • {model}: {b['calls']}× "
                f"in {b['input']:,} out {b['output']:,}{cache_part} → ${b['cost']:.4f}"
            )
    return "\n".join(lines)


def _post_task_summary(snapshot: dict) -> None:
    if not TASK_SUMMARY_ENABLED:
        return
    try:
        import requests  # local import so we don't pay the cost on happy-path imports
    except Exception:
        return
    try:
        text = _format_task_summary(snapshot)
        requests.post(TELEGRAM_BRIDGE_NOTIFY_URL, json={"text": text}, timeout=3)
    except Exception as e:
        # Never let notification failure impact the monologue shutdown.
        logger.debug(f"[task_report] summary post failed: {e}")


def finish_task(agent) -> dict | None:
    """Finalize report and write to disk. Called from monologue_end on the
    normal completion path. Marks `ended_reason="completed"` and fires a
    Telegram summary (fire-and-forget, disable with `AZ_TASK_SUMMARY=0`).

    NOTE: monologue_end does NOT fire on asyncio.CancelledError (kill via UI
    stop or api_terminate_chat). For those paths, the periodic saves from
    `save_task()` leave the latest snapshot on disk with
    `ended_reason="pending"`, and `sweep_orphans()` at next AZ startup
    promotes them to "orphaned". No summary is posted on cancel by design —
    a cancelled task is incomplete work, not a delivery.
    """
    r = get_report(agent)
    if r is None:
        return None
    r["ended_at"] = _now_iso()
    r["ended_reason"] = ENDED_COMPLETED
    try:
        _write_report(r, final=True)
        # Build a snapshot with computed totals/elapsed for the notification.
        summary_snapshot = dict(r)
        started_ts = summary_snapshot.get("started_ts")
        if isinstance(started_ts, (int, float)):
            summary_snapshot["elapsed_sec"] = round(time.time() - started_ts, 3)
        summary_snapshot.pop("started_ts", None)
        summary_snapshot["totals"] = _compute_totals(summary_snapshot)
        _post_task_summary(summary_snapshot)
    finally:
        agent.data.pop(DATA_KEY, None)
        agent.data.pop(PENDING_TOOL_KEY, None)
    return r
