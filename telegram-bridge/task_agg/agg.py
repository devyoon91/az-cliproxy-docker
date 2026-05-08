"""Task-JSON load + aggregate + format primitives.

Phase D carve from bot.py (issue #79). All callers (`/today`, `/week`,
`/tasks`, dashboard `_build_stats`, budget `_compute_window_cost`) now
funnel through this module so windowed numbers stay consistent.

What's here:

- `_load_task_jsons()` — read every `.json` under TASKS_DIR, sorted.
- `_filter_date_range(tasks, start, end)` — KST wall-clock filter.
- `_aggregate(tasks)` — sum totals + group by model + group by profile.
- `_is_anthropic_model(model)` / `_cache_efficiency(agg)` /
  `_format_cache_line(ce)` — Anthropic prompt-cache reporting.
- `_format_model_breakdown` / `_format_profile_breakdown` /
  `_format_agg_block` — Telegram-friendly text renders.
- `_data_quality_summary` / `_quality_banner` — observability gap warnings.
- `KST` + `_kst_now()` — time helpers used by aggregation. Currently
  duplicated with `pricing/usage.py` (Phase B). When carve-out
  stabilizes, dedupe via a `timeutil.py` shared by both — leaving the
  duplication for now to keep this PR focused.

What's NOT here (lives in bot.py for now):
- `_parse_by_flag` — Telegram command-arg parser, not aggregation.
- `cmd_today` / `cmd_week` / `cmd_tasks` — Telegram handlers.
- The dashboard's `_build_stats` and the budget's `_compute_window_cost`
  CALL into here but stay in bot.py because they own HTTP / async-
  alerting concerns. Likely move in Phase E when their callers move.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from pricing.cost import _model_info

logger = logging.getLogger(__name__)


KST = ZoneInfo("Asia/Seoul")


def _kst_now() -> datetime:
    """Return current naive KST wall-clock time.

    Naive on purpose: the existing `_filter_date_range` comparison logic
    was written against naive datetimes treated as KST wall clock. Going
    aware here would require updating every call site. Mirrored in
    `pricing/usage.py` for the same reason — see Phase B notes.
    """
    return datetime.now(KST).replace(tzinfo=None)


# Mounted from `agent-zero/logs/tasks/` per docker-compose.yml. Kept as a
# module-level binding so tests can monkeypatch it onto a tmp dir.
TASKS_DIR = "/app/tasks"


def _load_task_jsons() -> list[dict]:
    """Read every task JSON from the mounted AZ logs dir.

    Returns a list of parsed dicts sorted by `started_at` ascending.
    Malformed files are skipped silently; they show up in logs once and
    then get ignored so /today doesn't crash on a single bad file.
    """
    if not os.path.isdir(TASKS_DIR):
        return []
    items: list[dict] = []
    for name in os.listdir(TASKS_DIR):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        path = os.path.join(TASKS_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                items.append(json.load(f))
        except Exception as e:
            logger.debug(f"skip bad task json {name}: {e}")
    items.sort(key=lambda r: r.get("started_at") or "")
    return items


def _filter_date_range(tasks: list[dict], start, end) -> list[dict]:
    """Filter tasks whose `started_at` (ISO, UTC) falls in [start, end).

    `start` and `end` are naive `datetime` objects treated as **KST wall clock**.
    AZ writes started_at in UTC with +00:00 offset; we convert each task's
    UTC instant to KST before comparing, so "today" means "today in KST"
    regardless of the container OS timezone.
    """
    out = []
    for t in tasks:
        started = t.get("started_at")
        if not started:
            continue
        try:
            ts = datetime.fromisoformat(started)
        except Exception:
            continue
        if ts.tzinfo:
            ts_local = ts.astimezone(KST).replace(tzinfo=None)
        else:
            ts_local = ts
        if start <= ts_local < end:
            out.append(t)
    return out


def _aggregate(tasks: list[dict]) -> dict:
    """Sum totals across a list of tasks, also grouping by model, profile, and status."""
    agg = {
        "tasks": len(tasks),
        "completed": 0,
        "orphaned": 0,
        "pending": 0,
        "tool_calls": 0,
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
        "by_model": {},
        "by_profile": {},
    }
    for t in tasks:
        reason = t.get("ended_reason", "pending")
        if reason in agg:
            agg[reason] += 1
        totals = t.get("totals") or {}
        for k in ("tool_calls", "llm_calls", "input_tokens", "output_tokens",
                  "cache_read_tokens", "cache_creation_tokens",
                  "reasoning_tokens"):
            agg[k] += int(totals.get(k, 0) or 0)
        agg["cost_usd"] += float(totals.get("cost_usd", 0.0) or 0.0)

        # Profile bucket — one task can only have one profile, so we aggregate
        # the task's own totals (not per-call). Includes tool_calls so the
        # breakdown can surface which profile is doing the most work, not
        # just spending the most money.
        prof = t.get("profile") or "default"
        pbucket = agg["by_profile"].setdefault(prof, {
            "tasks": 0, "llm_calls": 0, "tool_calls": 0,
            "input": 0, "output": 0,
            "cache_read": 0, "cache_create": 0, "cost": 0.0,
        })
        pbucket["tasks"] += 1
        pbucket["llm_calls"] += int(totals.get("llm_calls", 0) or 0)
        pbucket["tool_calls"] += int(totals.get("tool_calls", 0) or 0)
        pbucket["input"] += int(totals.get("input_tokens", 0) or 0)
        pbucket["output"] += int(totals.get("output_tokens", 0) or 0)
        pbucket["cache_read"] += int(totals.get("cache_read_tokens", 0) or 0)
        pbucket["cache_create"] += int(totals.get("cache_creation_tokens", 0) or 0)
        pbucket["cost"] += float(totals.get("cost_usd", 0.0) or 0.0)

        for c in t.get("llm_calls") or []:
            m = c.get("model") or "unknown"
            bucket = agg["by_model"].setdefault(m, {
                "calls": 0, "input": 0, "output": 0,
                "cache_read": 0, "cache_create": 0, "cost": 0.0,
            })
            bucket["calls"] += 1
            bucket["input"] += int(c.get("input_tokens", 0) or 0)
            bucket["output"] += int(c.get("output_tokens", 0) or 0)
            bucket["cache_read"] += int(c.get("cache_read_tokens", 0) or 0)
            bucket["cache_create"] += int(c.get("cache_creation_tokens", 0) or 0)
            bucket["cost"] += float(c.get("cost_usd", 0.0) or 0.0)
    agg["cost_usd"] = round(agg["cost_usd"], 6)
    return agg


def _is_anthropic_model(model: str) -> bool:
    """Anthropic models get prompt caching; others (OpenAI, etc.) don't —
    so cache hit-ratio and `cache_saved_usd` should only count Anthropic
    rows. Match on "claude" substring plus the explicit anthropic/ prefix,
    which is broad enough to catch Sonnet/Haiku/Opus variants we use now
    without hand-maintaining a whitelist."""
    if not isinstance(model, str):
        return False
    m = model.lower()
    return m.startswith("anthropic/") or "claude" in m


def _cache_efficiency(agg: dict) -> dict | None:
    """Compute Anthropic prompt-cache efficiency from an aggregate.

    Returns `None` when there's no Anthropic traffic in the window —
    the caller should skip rendering the cache line entirely in that case.

    Fields:
      anthropic_calls      — number of llm_calls against Anthropic models
      input_tokens         — sum of prompt_tokens on Anthropic calls
      cache_read_tokens    — sum of cache_read on Anthropic calls
      cache_creation       — sum of cache_creation on Anthropic calls
      hit_ratio            — cache_read / (input + cache_read)
                             (0..1; None if denominator is 0)
      saved_usd            — Σ cache_read × (input_rate − cache_read_rate)
                             per model, using the live LiteLLM price table

    Ratio interpretation (from issue #22):
      ≥0.70  ✅ healthy cache reuse
      0.40..0.70  ⚠️ prompt likely drifting between calls
      <0.40  🚨 cache busted (system prompt / tool schema changing)
    """
    anthropic_calls = 0
    total_input = 0
    total_cache_read = 0
    total_cache_create = 0
    saved = 0.0
    for model, b in (agg.get("by_model") or {}).items():
        if not _is_anthropic_model(model):
            continue
        anthropic_calls += b["calls"]
        total_input += b["input"]
        total_cache_read += b["cache_read"]
        total_cache_create += b["cache_create"]
        if b["cache_read"] > 0:
            info = _model_info(model)
            in_rate = info.get("input_cost_per_token", 0.000003)
            read_rate = info.get("cache_read_input_token_cost", in_rate * 0.10)
            # What we WOULD have paid at full input rate minus what we DID
            # pay at cache-read rate = the caching savings for this model.
            saved += b["cache_read"] * max(in_rate - read_rate, 0.0)
    if anthropic_calls == 0:
        return None
    denom = total_input + total_cache_read
    hit_ratio = (total_cache_read / denom) if denom > 0 else None
    return {
        "anthropic_calls": anthropic_calls,
        "input_tokens": total_input,
        "cache_read_tokens": total_cache_read,
        "cache_creation_tokens": total_cache_create,
        "hit_ratio": hit_ratio,
        "saved_usd": saved,
    }


def _format_cache_line(ce: dict) -> str:
    """One-line cache efficiency summary, Telegram-friendly.

    Shape: `🧠 캐시: 78% ✅ (117k/150k read · 절감 $2.81)`
    """
    ratio = ce.get("hit_ratio")
    if ratio is None:
        emoji = "❔"
        ratio_str = "N/A"
    else:
        if ratio >= 0.70:
            emoji = "✅"
        elif ratio >= 0.40:
            emoji = "⚠️"
        else:
            emoji = "🚨"
        ratio_str = f"{ratio * 100:.0f}%"
    read_k = ce["cache_read_tokens"] / 1000.0
    denom_k = (ce["input_tokens"] + ce["cache_read_tokens"]) / 1000.0
    return (
        f"🧠 캐시: {ratio_str} {emoji} "
        f"({read_k:.0f}k/{denom_k:.0f}k read · 절감 ${ce['saved_usd']:.4f})"
    )


def _format_model_breakdown(agg: dict, *, title: str = "모델별") -> list[str]:
    """Detailed model table — used by `/today by:model` and `/week by:model`.

    Includes zero-cost rows (call count only) per issue #20 acceptance.
    """
    by_model = agg.get("by_model") or {}
    if not by_model:
        return [f"🤖 {title}: (데이터 없음)"]
    lines = [f"🤖 {title}:"]
    # Sort by cost desc, secondary by call count so free models don't jumble.
    for model, b in sorted(
        by_model.items(),
        key=lambda kv: (kv[1]["cost"], kv[1]["calls"]),
        reverse=True,
    ):
        cache = (
            f" | cache r:{b['cache_read']:,} c:{b['cache_create']:,}"
            if (b["cache_read"] or b["cache_create"]) else ""
        )
        lines.append(
            f"  • {model}: {b['calls']}× "
            f"in {b['input']:,} out {b['output']:,}{cache} → ${b['cost']:.4f}"
        )
    return lines


def _format_profile_breakdown(agg: dict, *, title: str = "프로파일별") -> list[str]:
    """Detailed profile table — used by `/today by:profile` / `/week by:profile`.

    Profile-level aggregates task counts, tools, and LLM calls in addition
    to cost — profiles differ by agent behavior, so tool volume is often
    the more informative signal than raw cost.
    """
    by_profile = agg.get("by_profile") or {}
    if not by_profile:
        return [f"👤 {title}: (데이터 없음)"]
    lines = [f"👤 {title}:"]
    for prof, b in sorted(
        by_profile.items(),
        key=lambda kv: (kv[1]["cost"], kv[1]["tasks"]),
        reverse=True,
    ):
        lines.append(
            f"  • {prof}: {b['tasks']}태스크 · "
            f"LLM {b['llm_calls']} · 도구 {b['tool_calls']} "
            f"→ ${b['cost']:.4f}"
        )
    return lines


def _data_quality_summary(tasks: list[dict]) -> dict:
    """Scan a task list for known observability gaps (M1/M2/M3 evolution).

    These gaps mean the per-task cost / token numbers drift from Anthropic
    Console reality. We surface the counts in /today and /week so the user
    knows when to trust the number.

    Returns:
        {
          "total":       total tasks in window,
          "legacy_cost": tasks without totals.cost_usd (pre-M3 schema),
          "approximate": tasks with any approximate-token LLM call (pre-M2 str
                         response bug),
        }
    """
    legacy_cost = 0
    approximate = 0
    for t in tasks:
        totals = t.get("totals") or {}
        if "cost_usd" not in totals:
            legacy_cost += 1
        for c in t.get("llm_calls") or []:
            if c.get("tokens_approximate"):
                approximate += 1
                break
    return {
        "total": len(tasks),
        "legacy_cost": legacy_cost,
        "approximate": approximate,
    }


def _quality_banner(dq: dict) -> str | None:
    """Return a one-line caveat if any data quality gap is present, else None."""
    if dq["legacy_cost"] == 0 and dq["approximate"] == 0:
        return None
    parts = []
    if dq["legacy_cost"]:
        parts.append(f"M3 이전 {dq['legacy_cost']}건")
    if dq["approximate"]:
        parts.append(f"근사 토큰 {dq['approximate']}건")
    return (
        "⚠️ 신뢰도: " + " / ".join(parts)
        + " — Anthropic 대시보드와 차이 있을 수 있음"
    )


def _format_agg_block(title: str, agg: dict) -> list[str]:
    """Render an aggregate dict as a Telegram-friendly block."""
    lines = [
        f"📊 {title}",
        f"  태스크: {agg['tasks']}건 "
        f"(✅{agg['completed']} ⚠️{agg['orphaned']} ⏳{agg['pending']})",
    ]
    if agg["tasks"] == 0:
        return lines
    lines.append(
        f"  LLM 호출: {agg['llm_calls']}건 · 도구: {agg['tool_calls']}건"
    )
    lines.append(
        f"  토큰: in {agg['input_tokens']:,} · out {agg['output_tokens']:,}"
    )
    if agg["cache_read_tokens"] or agg["cache_creation_tokens"]:
        lines.append(
            f"  캐시: read {agg['cache_read_tokens']:,} · "
            f"create {agg['cache_creation_tokens']:,}"
        )
    rt = agg.get("reasoning_tokens", 0)
    if rt:
        # Reasoning / extended-thinking tokens — already folded into cost
        # via output rate, shown separately so the breakdown is honest.
        lines.append(f"  사고 토큰: {rt:,} (출력 요율 청구)")
    lines.append(f"  💰 ${agg['cost_usd']:.4f}")
    return lines
