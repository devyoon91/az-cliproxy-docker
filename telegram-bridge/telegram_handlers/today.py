"""`/today`, `/week`, `/tasks` Telegram commands.

Phase I carve from bot.py (issue #79). These three are the smallest
self-contained cmd handler group: their only bot.py-internal dep was
`CHAT_ID`, which we read from the same env var bot.py uses (single
source of truth — `TELEGRAM_CHAT_ID`).

Everything else flows through task_agg/agg.py:
- `_load_task_jsons`, `_filter_date_range`, `_aggregate`
- `_format_agg_block`, `_format_model_breakdown`, `_format_profile_breakdown`
- `_quality_banner`, `_data_quality_summary`
- `_cache_efficiency`, `_format_cache_line`
- `_kst_now`

`_parse_by_flag` (the `/today by:model` / `by:profile` arg parser)
moves with the handlers since it's only used by /today and /week.
"""
from __future__ import annotations

import os
from datetime import timedelta

from task_agg.agg import (
    _aggregate,
    _cache_efficiency,
    _data_quality_summary,
    _filter_date_range,
    _format_agg_block,
    _format_cache_line,
    _format_model_breakdown,
    _format_profile_breakdown,
    _kst_now,
    _load_task_jsons,
    _quality_banner,
)
from telegram import Update
from telegram.ext import ContextTypes

# Read CHAT_ID directly from env. bot.py uses the same env var; both
# stay in lockstep without an explicit cross-import.
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])


def _parse_by_flag(args) -> str | None:
    """Parse `by:model` / `by:profile` from Telegram command args.

    Supports:
      /today                → None        (default compact view)
      /today by:model       → "model"
      /today by:profile     → "profile"
      /today model          → "model"     (shorthand, same as by:model)
      /today profile        → "profile"

    Unknown keys return None so the command gracefully falls back to the
    default view instead of erroring.
    """
    if not args:
        return None
    a = args[0].strip().lower()
    if a in ("by:model", "model", "by:models", "models"):
        return "model"
    if a in ("by:profile", "profile", "by:profiles", "profiles"):
        return "profile"
    return None


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Today's task aggregate from on-disk JSONs (KST boundary).

    Supports an optional breakdown flag (issue #20):
      /today              — default summary + compact model list
      /today by:model     — detailed per-model table (replaces compact list)
      /today by:profile   — per-profile table

    NOTE on `effective_message`: python-telegram-bot's `CommandHandler` fires
    on BOTH new messages and edits (edits go through `update.edited_message`,
    not `update.message`). Reaching for `update.message.reply_text` directly
    crashes with NoneType when the user edits a previous /today (e.g. "/today"
    → "/today by:model" to test variants). `effective_message` resolves to
    whichever variant actually carries the command text.
    """
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return
    tasks = _load_task_jsons()
    today_start = _kst_now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)
    todays = _filter_date_range(tasks, today_start, tomorrow)
    agg = _aggregate(todays)

    lines = _format_agg_block(f"오늘 ({today_start.strftime('%Y-%m-%d')} KST)", agg)

    # Data quality caveat (issue #24)
    banner = _quality_banner(_data_quality_summary(todays))
    if banner:
        lines.insert(1, banner)

    # Cache efficiency line — Anthropic-only, suppressed when no anthropic
    # traffic in window (issue #22).
    ce = _cache_efficiency(agg)
    if ce:
        lines.append(_format_cache_line(ce))

    # Breakdown mode — `/today by:model` or `by:profile` replaces the default
    # compact model list. Default stays as today's pre-Wave-3 format so the
    # unflagged command remains familiar.
    mode = _parse_by_flag(context.args)
    if mode == "model":
        lines.append("")
        lines.extend(_format_model_breakdown(agg, title="모델별 (상세)"))
    elif mode == "profile":
        lines.append("")
        lines.extend(_format_profile_breakdown(agg))
    else:
        by_model = agg.get("by_model", {})
        if by_model:
            lines.append("\n🤖 모델별:")
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

    await msg.reply_text("\n".join(lines))


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Last 7 days: daily breakdown + grand totals (KST boundary).

    Supports an optional breakdown flag (issue #20):
      /week              — default daily rows + compact weekly model list
      /week by:model     — detailed per-model table for the week
      /week by:profile   — per-profile table for the week

    See cmd_today for the `effective_message` rationale (edit-aware).
    """
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return
    all_tasks = _load_task_jsons()
    today_start = _kst_now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)  # last 7 days including today
    week_end = today_start + timedelta(days=1)
    window = _filter_date_range(all_tasks, week_start, week_end)

    lines = [
        f"📈 최근 7일 ({week_start.strftime('%m-%d')} ~ {today_start.strftime('%m-%d')} KST)"
    ]

    # Data quality caveat (issue #24)
    banner = _quality_banner(_data_quality_summary(window))
    if banner:
        lines.append(banner)
    lines.append("")

    # Per-day rows
    days_with_data = 0
    for i in range(7):
        day_start = week_start + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_tasks = _filter_date_range(window, day_start, day_end)
        if not day_tasks:
            continue
        days_with_data += 1
        agg = _aggregate(day_tasks)
        lines.append(
            f"  {day_start.strftime('%m-%d')}: "
            f"{agg['tasks']}건 · {agg['llm_calls']} LLM · ${agg['cost_usd']:.4f}"
        )

    if days_with_data == 0:
        lines.append("  (데이터 없음)")

    # Grand total
    grand = _aggregate(window)
    lines.append("")
    lines.extend(_format_agg_block("주간 합계", grand))

    # Cache efficiency for the full week (Anthropic only).
    ce = _cache_efficiency(grand)
    if ce:
        lines.append(_format_cache_line(ce))

    mode = _parse_by_flag(context.args)
    if mode == "model":
        lines.append("")
        lines.extend(_format_model_breakdown(grand, title="모델별 (주간 상세)"))
    elif mode == "profile":
        lines.append("")
        lines.extend(_format_profile_breakdown(grand, title="프로파일별 (주간)"))
    else:
        by_model = grand.get("by_model", {})
        if by_model:
            lines.append("\n🤖 모델별 (주간):")
            for model, b in sorted(
                by_model.items(),
                key=lambda kv: (kv[1]["cost"], kv[1]["calls"]),
                reverse=True,
            ):
                lines.append(f"  • {model}: {b['calls']}× → ${b['cost']:.4f}")

    await msg.reply_text("\n".join(lines))


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the most recent N tasks with individual summaries (default 10)."""
    if update.effective_chat.id != CHAT_ID:
        return
    n = 10
    args = context.args
    if args:
        try:
            n = max(1, min(50, int(args[0])))
        except ValueError:
            pass
    tasks = _load_task_jsons()
    recent = list(reversed(tasks[-n:]))
    if not recent:
        await update.message.reply_text("최근 태스크가 없습니다.")
        return

    lines = [f"🗂 최근 {len(recent)}개 태스크\n"]
    status_icon = {"completed": "✅", "orphaned": "⚠️", "pending": "⏳"}
    for t in recent:
        tid = t.get("task_id", "?")
        reason = t.get("ended_reason", "pending")
        icon = status_icon.get(reason, "•")
        elapsed = t.get("elapsed_sec", 0) or 0
        totals = t.get("totals") or {}
        cost = totals.get("cost_usd", 0.0) or 0.0
        # Short HH:MM:SS from task_id (task-YYYYMMDD-HHMMSS-xxxxxx)
        parts = tid.split("-")
        when = "?"
        if len(parts) >= 3 and len(parts[2]) == 6:
            when = f"{parts[2][:2]}:{parts[2][2:4]}:{parts[2][4:6]}"
        lines.append(
            f"{icon} {when} {elapsed:.0f}s · "
            f"LLM {totals.get('llm_calls', 0)} · "
            f"도구 {totals.get('tool_calls', 0)} · "
            f"${cost:.4f}"
        )
    await update.message.reply_text("\n".join(lines))
