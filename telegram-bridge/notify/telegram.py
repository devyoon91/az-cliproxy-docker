"""`send_telegram` — long-aware Telegram sender with parse-error fallback.

Phase L carve from bot.py (issue #79). The function used to read the
module-level `tg_bot` and `CHAT_ID` globals from bot.py directly.
After the carve it relies on a small `configure()` injection so the
module stays import-clean of the bot.py state surface (same pattern
as `pricing/snapshot.py:take_pricing_snapshot` and
`budget/engine.py`).

Why a configure() slot rather than passing bot+chat_id every call:
the function fans out across ~30 call sites (cmd_*, handlers, monitor
loop, budget engine via callback, etc.). Threading the bot reference
through every one of those would balloon this PR. configure() runs
exactly once at startup right after `application.bot` becomes
available, and the call sites stay one-arg.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Module-level injection slots. bot.py's main() / post_init wires
# these once via `configure(bot=tg_bot, chat_id=CHAT_ID)`. Until then,
# `send_telegram` is a no-op (handy for tests + early-boot calls
# during the wiring window).
_bot = None
_chat_id: int | None = None


def configure(*, bot, chat_id: int) -> None:
    """Wire the Telegram `Bot` instance + chat ID. Call once during
    `main()` after `Application.builder().build()` produces the bot
    object. Idempotent — last call wins.
    """
    global _bot, _chat_id
    _bot = bot
    _chat_id = int(chat_id)


async def send_telegram(
    text: str,
    parse_mode: str | None = None,
    fallback_text: str | None = None,
) -> None:
    """Send `text` to Telegram with optional `parse_mode` (HTML / Markdown).

    `fallback_text` (recommended when parse_mode is set): a plain-text
    version sent if Telegram rejects the formatted payload with a parse
    error. Useful when AZ output produces malformed HTML/markdown despite
    our converter — better the user gets the raw text than nothing.

    Long messages (>4000 chars) are split. The fallback is only retried
    for the chunk that actually failed parsing; other chunks aren't
    re-sent so the user doesn't get duplicates.
    """
    if _bot is None or _chat_id is None or not text.strip():
        return

    chunks = (
        [text[i : i + 4000] for i in range(0, len(text), 4000)]
        if len(text) > 4000 else [text]
    )
    fallback_chunks = None
    if fallback_text and len(text) > 4000:
        fallback_chunks = [
            fallback_text[i : i + 4000] for i in range(0, len(fallback_text), 4000)
        ]

    for idx, chunk in enumerate(chunks):
        try:
            await _bot.send_message(
                chat_id=_chat_id, text=chunk, parse_mode=parse_mode,
            )
        except Exception as e:
            err = str(e).lower()
            # Telegram returns "Bad Request: can't parse entities" on bad
            # HTML/Markdown. Retry the SAME chunk in plain mode.
            if parse_mode and ("can't parse" in err or "parse entities" in err):
                fb = (
                    fallback_chunks[idx] if fallback_chunks
                    else (fallback_text if fallback_text and len(chunks) == 1 else chunk)
                )
                logger.warning(
                    f"[telegram] parse_mode={parse_mode} rejected, "
                    f"retrying chunk {idx} as plain"
                )
                try:
                    await _bot.send_message(chat_id=_chat_id, text=fb)
                except Exception as e2:
                    logger.error(f"[telegram] plain fallback also failed: {e2}")
            else:
                logger.error(f"[telegram] send failed: {e}")


async def send_document(
    document,
    *,
    filename: str | None = None,
    caption: str | None = None,
) -> None:
    """Send a file/document to the configured chat.

    Thin wrapper around `Bot.send_document` so callers (cmd_logs,
    cmd_docs, cmd_backup) don't need to reach for `tg_bot` + CHAT_ID
    globals — same configure() injection as send_telegram. No-op when
    bot/chat_id unconfigured (early-boot or test).

    `document` can be anything telegram-bot's send_document accepts:
    a `BytesIO`, an open file handle, or a path-like object.
    """
    if _bot is None or _chat_id is None:
        return
    try:
        await _bot.send_document(
            chat_id=_chat_id,
            document=document,
            filename=filename,
            caption=caption,
        )
    except Exception as e:
        logger.error(f"[telegram] send_document failed: {e}")
