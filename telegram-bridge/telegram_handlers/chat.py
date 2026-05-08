"""Chat-listing + monitor-toggle Telegram commands — Phase Q carve from
bot.py (issue #79).

Houses:
  - `/chats`       — list AZ contexts, mark the one the monitor is watching
  - `/monitor_on`  — resume AZ→Telegram echo (skips past history)
  - `/monitor_off` — pause AZ→Telegram echo
  - `/track_chat_on`  — auto-follow the AZ web UI's active chat
  - `/track_chat_off` — pin the monitor to its current chat
  - `/verbose_on`  — pass through every AZ log type (debug)
  - `/verbose_off` — quiet mode: only `user` echoes during a task

All seven became carve-able once Phase P moved the seven monitor-state
globals to `monitor/state.py` — the handlers used to take `global …`
declarations + bare reads from bot.py module scope. Now they read +
write `state.X` and depend only on already-carved modules.

`/switch` and `/new` stay in bot.py for now because they call
`_stream_reset` (the streaming-edit module is still bot.py-internal).
That's the next phase.
"""
from __future__ import annotations

import os

from az_client.session import fetch_chat_list, sync_log_version
from monitor import state
from render.monitor import short_id as _short_id
from telegram import Update
from telegram.ext import ContextTypes

# Same env source bot.py uses — keeps the carve identical to the rest of
# telegram_handlers/. Re-reading rather than threading CHAT_ID through is
# fine because the env is set once at container start and these handlers
# only fire under the running event loop.
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """채팅 목록 조회 — marks the currently-tracked context with `→ `.

    Reads the live `cached_contexts` indirectly via `fetch_chat_list()`
    (which hits AZ when the cache is empty and updates the same list
    object the monitor loop populates). The `→` marker is anchored on
    `state.monitor_context` so it stays in sync regardless of which
    module last mutated it.
    """
    if update.effective_chat.id != CHAT_ID:
        return

    contexts = await fetch_chat_list()
    if not contexts:
        await update.message.reply_text("활성 채팅이 없습니다.")
        return

    lines = ["📋 채팅 목록:\n"]
    for i, ctx in enumerate(contexts):
        ctx_id = ctx.get("id", "")
        name = ctx.get("name", "이름 없음")
        is_current = "→ " if ctx_id == state.monitor_context else "  "
        lines.append(f"{is_current}{i+1}. {name}\n   ID: {_short_id(ctx_id)}")

    lines.append(f"\n현재 추적 중: {_short_id(state.monitor_context)}")
    lines.append("\n채팅 전환: /switch [번호]")

    await update.message.reply_text("\n".join(lines))


async def cmd_monitor_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume AZ→Telegram echo. Re-anchors `monitor_log_version` to the
    current cursor so the user doesn't get blasted with history that
    accumulated while the monitor was off."""
    if update.effective_chat.id != CHAT_ID:
        return
    state.monitor_enabled = True
    state.monitor_log_version = await sync_log_version(state.monitor_context)
    await update.message.reply_text("✅ 웹 채팅 모니터링이 켜졌습니다. (현재 시점부터)")


async def cmd_monitor_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause the AZ→Telegram echo without tearing down the poll loop —
    the loop just sees `state.monitor_enabled is False` and idles."""
    if update.effective_chat.id != CHAT_ID:
        return
    state.monitor_enabled = False
    await update.message.reply_text("🔇 웹 채팅 모니터링이 꺼졌습니다.")


async def cmd_track_chat_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-track which AZ chat the monitor watches.

    Renamed from /follow_on for clarity — old name was ambiguous about
    direction (does "follow" mean Telegram→AZ or AZ→Telegram?). Both
    /monitor and /track_chat are AZ→Telegram concerns; /track_chat
    specifically controls whether the monitor switches its target when
    the AZ web UI activates a different chat.

    /follow_on is kept registered as an alias in bot.py main() for
    muscle memory — that's a registration concern, not a handler one.
    """
    if update.effective_chat.id != CHAT_ID:
        return
    state.monitor_auto_follow = True
    await update.message.reply_text(
        "✅ 채팅 자동 추적 켜짐: 웹에서 채팅 전환 시 모니터가 따라갑니다."
    )


async def cmd_track_chat_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pin the monitor to its current chat regardless of AZ web's active
    chat. See cmd_track_chat_on for the rename rationale."""
    if update.effective_chat.id != CHAT_ID:
        return
    state.monitor_auto_follow = False
    await update.message.reply_text(
        "📌 채팅 자동 추적 꺼짐: 현재 채팅만 고정 추적합니다."
    )


async def cmd_verbose_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show every AZ log (info / tool / code_exe / response / error /
    warning) during a task — useful when debugging a stuck profile.
    Default-quiet behavior (only user echoes + task-completion summary
    from task_report) is the normal mode."""
    if update.effective_chat.id != CHAT_ID:
        return
    state.monitor_verbose = True
    await update.message.reply_text(
        "🔊 상세 모니터 켜짐: AZ 활동 로그(도구/코드/info 등) 모두 텔레그램으로 전송."
    )


async def cmd_verbose_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to quiet mode — only user echoes during task, completion-time
    answer + metrics card from task_report."""
    if update.effective_chat.id != CHAT_ID:
        return
    state.monitor_verbose = False
    await update.message.reply_text(
        "🔇 상세 모니터 꺼짐: 진행 중엔 조용, 완료 시 답변+메트릭만."
    )
