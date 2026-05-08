"""Per-AZ-context streaming-edit Telegram messages — Phase R carve.

The AZ web-chat monitor batches each /api/poll cycle's logs and
either edits the active Telegram message for that context (so a
single AZ "turn" stays as one message) or opens a new one when a
boundary is hit:

  • A `user`-type log appears in the poll batch — that's a new turn
  • Telegram's 4096-char limit hit — close current, open next
  • Active chat switches (/switch, /new, monitor auto-follow)
  • Edit fails for any non-recoverable reason — drop state, send-new

`streaming_msg_id` and `streaming_text` are mutated in place (never
reassigned) so importers holding a binding to the dict object stay
in sync — same clear+update pattern that `pricing.usage.usage_today`
and `az_client.session.cached_contexts` use.

Why a `configure(bot, chat_id)` injection instead of importing
`notify.telegram` directly: notify.telegram's `send_telegram` does
chunking + parse_mode fallback for one-shot sends, which is a
different contract from streaming-edit (need the message_id back,
no chunking — the cap-handling here is intentionally different).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── Module-level state ──
#
# Per AZ context, track the last Telegram message we sent for the
# active AZ "turn". Both dicts mutated in place (clear / pop / item
# assignment) — never reassigned. Test fixtures and callers can pin
# bindings to these objects without forking state.
streaming_msg_id: dict[str, int] = {}   # ctx_key → Telegram message_id
streaming_text: dict[str, str] = {}     # ctx_key → text we last sent/edited

# Telegram's hard cap is 4096 chars; leave headroom for the next chunk
# join and any sneaky widening from formatter changes.
STREAM_MAX_CHARS = 3800


# ── Configure-once injection slots ──
#
# bot.py's main()/post_init wires these via `configure(bot=tg_bot,
# chat_id=CHAT_ID)` once `Application.builder().build()` produces the
# bot object. Until then `stream_extend` is a no-op (handy for tests +
# any early-boot calls during the wiring window).
_bot = None
_chat_id: int | None = None


def configure(*, bot, chat_id: int) -> None:
    """Wire the Telegram `Bot` instance + chat ID. Idempotent — last
    call wins. Call from `main()` after the Bot object is available.
    """
    global _bot, _chat_id
    _bot = bot
    _chat_id = int(chat_id)


def stream_reset(ctx_key: str) -> None:
    """Forget the active streamed message for `ctx_key`. The next
    `stream_extend` for that key sends a brand-new Telegram message.
    Safe to call when no state exists.
    """
    streaming_msg_id.pop(ctx_key, None)
    streaming_text.pop(ctx_key, None)


async def stream_extend(ctx_key: str, new_chunk: str) -> None:
    """Append `new_chunk` to the active streamed message — editing it
    in place. Falls back to a brand-new send when:

      • There's no active message yet
      • The edit would push past `STREAM_MAX_CHARS` (close + reopen)
      • Telegram refuses the edit (message too old, deleted, etc.)

    No-op if `configure()` hasn't run or `new_chunk` is empty.
    """
    if not new_chunk or _bot is None or _chat_id is None:
        return

    cur_text = streaming_text.get(ctx_key, "")
    cur_msg_id = streaming_msg_id.get(ctx_key)
    sep = "\n\n" if cur_text else ""
    extended = cur_text + sep + new_chunk

    # Length cap — finalize current message, start a fresh one with new_chunk.
    if cur_msg_id and len(extended) > STREAM_MAX_CHARS:
        cur_msg_id = None
        cur_text = ""
        extended = new_chunk

    if cur_msg_id:
        try:
            await _bot.edit_message_text(
                chat_id=_chat_id,
                message_id=cur_msg_id,
                text=extended,
            )
            streaming_text[ctx_key] = extended
            return
        except Exception as e:
            # "Message is not modified" is a no-op success; everything
            # else means the message can't be edited (too old / deleted /
            # rate limit). Drop state and fall through to send-new —
            # better the user gets a fresh message than no update at all.
            err = str(e).lower()
            if "not modified" in err:
                return
            logger.debug(f"[stream] edit failed for {ctx_key!r}: {e}; sending new")
            stream_reset(ctx_key)

    # Send-new path (also taken when length cap forced a fresh message).
    try:
        msg = await _bot.send_message(chat_id=_chat_id, text=new_chunk)
        streaming_msg_id[ctx_key] = msg.message_id
        streaming_text[ctx_key] = new_chunk
    except Exception as e:
        logger.error(f"[stream] send failed: {e}")
