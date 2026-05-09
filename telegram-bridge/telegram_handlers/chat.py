"""Chat-management + monitor-toggle Telegram commands — bot.py carve.

Houses (Phase Q):
  - `/chats`           — list AZ contexts, mark the one the monitor is watching
  - `/monitor_on`      — resume AZ→Telegram echo (skips past history)
  - `/monitor_off`     — pause AZ→Telegram echo
  - `/track_chat_on`   — auto-follow the AZ web UI's active chat
  - `/track_chat_off`  — pin the monitor to its current chat
  - `/verbose_on`      — pass through every AZ log type (debug)
  - `/verbose_off`     — quiet mode: only `user` echoes during a task

Houses (Phase R):
  - `/switch [번호]`    — pin both az_context and monitor_context to a listed chat
  - `/new`             — create a fresh AZ chat via /api/chat_create + pin to it

The Q group became carveable once Phase P moved monitor state into
`monitor/state.py`. The R pair (switch / new) needed Phase R's
streaming carve too — they call `stream_reset` to drop the in-flight
streamed message before swapping context.
"""
from __future__ import annotations

import logging
import os

import aiohttp
from az_client.session import (
    AZ_API_PREFIX,
    AZ_API_URL,
    cached_contexts,
    close_az_session,
    fetch_chat_list,
    get_az_session,
    get_headers,
    sync_log_version,
)
from monitor import state
from render.monitor import short_id as _short_id
from streaming import stream_reset
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Same env source bot.py uses — keeps the carve identical to the rest of
# telegram_handlers/. Optional (issue #106): when telegram is disabled,
# bot.py never registers these handlers, so CHAT_ID stays None safely.
_chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
CHAT_ID: int | None = int(_chat_id_raw) if _chat_id_raw else None


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


async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """채팅 전환 — pin both `az_context` and `monitor_context` to the
    chosen entry from `/chats`.

    Reads from `cached_contexts` first (the live list the monitor loop
    refreshes every poll) and only falls back to `fetch_chat_list` when
    the cache is empty. Closes any in-flight streamed message via
    `stream_reset` so the new chat's first log opens its own message
    instead of extending one tied to the previous chat.
    """
    if update.effective_chat.id != CHAT_ID:
        return

    args = context.args
    if not args:
        await update.message.reply_text("사용법: /switch [번호]\n/chats 로 목록을 먼저 확인하세요.")
        return

    try:
        idx = int(args[0]) - 1
    except ValueError:
        await update.message.reply_text("숫자를 입력하세요. 예: /switch 1")
        return

    contexts = cached_contexts if cached_contexts else await fetch_chat_list()

    if idx < 0 or idx >= len(contexts):
        await update.message.reply_text(f"1~{len(contexts)} 범위에서 선택하세요.")
        return

    target = contexts[idx]
    target_id = target.get("id", "")
    target_name = target.get("name", "이름 없음")

    # Close any in-progress stream tied to the previous chat before swapping.
    stream_reset(state.monitor_context or "_default")

    state.az_context = target_id
    state.monitor_context = target_id
    state.monitor_log_guid = ""
    # Skip past current log_version so we don't re-stream historical logs.
    state.monitor_log_version = await sync_log_version(target_id)

    await update.message.reply_text(f"✅ 채팅 전환: {target_name}\nID: {_short_id(target_id)}")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a brand-new Agent Zero chat context and switch to it.

    Previous behavior: just zeroed the local az_context / monitor_context
    and relied on the next user message to lazily create a context. That
    meant:
      - The reply "새 대화를 시작합니다" was misleading (no chat created yet)
      - /chats wouldn't show the new chat until something was sent
      - With monitor_auto_follow ON, the next poll would latch back onto
        AZ's previously-active context — defeating the reset entirely

    Fixed behavior: calls AZ's /api/chat_create (the same endpoint the
    web UI's "New Chat" button uses), gets the real ctxid back, then
    pins both az_context and monitor_context to it before replying.
    """
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return

    new_ctxid = ""
    try:
        session = await get_az_session()
        headers = get_headers()
        # Pass current_context so AZ can carry over project / model-override
        # data per its chat_inherit_project setting (matches web UI behavior).
        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/chat_create",
            json={"current_context": state.az_context or ""},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 403:
                await close_az_session()
                session = await get_az_session()
                headers = get_headers()
                async with session.post(
                    f"{AZ_API_URL}{AZ_API_PREFIX}/chat_create",
                    json={"current_context": state.az_context or ""},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as retry:
                    if retry.status == 200:
                        new_ctxid = (await retry.json()).get("ctxid", "")
            elif resp.status == 200:
                new_ctxid = (await resp.json()).get("ctxid", "")
    except Exception as e:
        logger.error(f"chat_create failed: {e}")

    if not new_ctxid:
        await msg.reply_text("⚠️ 새 대화 생성 실패 — Agent Zero 응답 확인 필요.")
        return

    # Close any in-progress stream from the previous chat — new chat's
    # logs should open their own message, not extend the old one.
    stream_reset(state.monitor_context or "_default")

    state.az_context = new_ctxid
    state.monitor_context = new_ctxid
    state.monitor_log_guid = ""
    # Skip past whatever log_version the new context starts at so we don't
    # re-stream the (empty) initial state. Mirrors /switch behavior.
    state.monitor_log_version = await sync_log_version(new_ctxid)

    await msg.reply_text(
        f"✅ 새 대화 시작됨\nID: {_short_id(new_ctxid)}"
    )
