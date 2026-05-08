"""Streaming-edit Telegram message helpers — Phase R carve from bot.py
(issue #79).

bot.py used to keep two dicts (`streaming_msg_id`, `streaming_text`),
a constant (`STREAM_MAX_CHARS`), and two helpers (`_stream_reset`,
`_stream_extend`) at module scope, plus the `tg_bot` Bot instance
they reached for. Phase R lifts all of this into `streaming/edit.py`,
wired with a small `configure(bot, chat_id)` injection — same pattern
as `notify.telegram` and `budget.engine`.

Public surface:
  - `stream_reset(ctx_key)` — drop the active streamed message slot
  - `stream_extend(ctx_key, new_chunk)` — append/edit; opens new on first
    call, finalizes when length cap hit, falls back on edit failure
  - `configure(bot=, chat_id=)` — wire once at startup

Names lost the leading underscore in the move: in bot.py they were
underscore-prefixed because the module was an entry-point (no public
API). In a dedicated module the helpers ARE the public API.
"""

from .edit import (
    STREAM_MAX_CHARS,
    configure,
    stream_extend,
    stream_reset,
    streaming_msg_id,
    streaming_text,
)

__all__ = [
    "configure",
    "stream_reset",
    "stream_extend",
    "streaming_msg_id",
    "streaming_text",
    "STREAM_MAX_CHARS",
]
