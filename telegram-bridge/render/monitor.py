"""Monitor-feed message formatting.

Phase J carve from bot.py (issue #79). Two pure helpers used by the
monitor loop and a handful of cmd handlers:

- `format_monitor_message(log_type, heading, content, *, verbose)` —
  one-line / multi-line Telegram render of a single AZ log entry.
  Quiet mode (default): only `user` lines echo through. Verbose mode
  surfaces every log type — useful when debugging a stuck task.
- `short_id(ctx_id, max_len=12)` — trim AZ context IDs for display.

Verbose state used to be a `monitor_verbose` global read inside the
function. After the carve the function is pure: callers pass
`verbose=monitor_verbose` explicitly.
"""
from __future__ import annotations


def short_id(ctx_id: str | None, max_len: int = 12) -> str:
    """Render a context ID for display.

    AZ's `helpers.guids.generate_id` defaults to length=8, so today's IDs
    are already 8 chars total. The previous code did `id[:8] + "..."`
    which was a UX lie — the ellipsis suggested truncation when nothing
    had been truncated, and users couldn't tell where the ID ended.

    Behavior:
      - empty / None → "없음"
      - len(id) <= max_len → return as-is, no ellipsis
      - longer → truncate to max_len + "..." (real truncation, real marker)
    """
    if not ctx_id:
        return "없음"
    if len(ctx_id) <= max_len:
        return ctx_id
    return ctx_id[:max_len] + "..."


def format_monitor_message(
    log_type: str,
    heading: str,
    content: str,
    *,
    verbose: bool = False,
) -> str | None:
    """Format one AZ log entry for the Telegram monitor feed.

    Quiet mode (`verbose=False`, default): only `user` log types echo
    through. The user-facing answer + metrics card are sent at task
    completion via `task_report.py`'s `_post_task_response` /
    `_post_task_summary`, so the in-progress monitor doesn't need to
    repeat what's coming anyway.

    Verbose mode (`verbose=True`, toggled via /verbose_on): every log
    type formats and forwards as before — useful when debugging an AZ
    profile or a stuck task.

    Returns the formatted text or `None` to skip this entry entirely.
    """
    if not content and not heading:
        return None

    if log_type == "user":
        return f"👤 사용자: {content}"

    if not verbose:
        # Quiet path — let task completion drive the actual answer + metrics.
        return None

    if log_type in ("response", "ai", "agent"):
        if len(content) > 2000:
            content = content[:2000] + "\n...(생략)"
        return f"🤖 Agent Zero:\n{content}"
    if log_type == "code_exe":
        code_preview = content[:500] if content else ""
        return f"⚙️ 코드 실행: {heading}\n```\n{code_preview}\n```"
    if log_type == "tool":
        return f"🔧 도구: {heading}\n{content[:500] if content else ''}"
    if log_type == "info":
        return f"ℹ️ {heading}: {content[:500] if content else ''}"
    if log_type == "error":
        return f"❌ 오류: {heading}\n{content[:500] if content else ''}"
    if log_type == "warning":
        return f"⚠️ 경고: {heading}\n{content[:500] if content else ''}"

    return None
