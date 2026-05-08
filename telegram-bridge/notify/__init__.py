"""Telegram notification helpers — Phase L carve from bot.py (issue #79).

Currently houses `send_telegram(text, parse_mode, fallback_text)` —
the long-message-aware sender with parse-error fallback. bot.py wires
the `Bot` instance + chat ID once at startup via
`notify.telegram.configure(bot=..., chat_id=...)`.

Kept as a package even with one file so future additions (e.g.
typing-indicator helper, document-send wrappers, multi-chat router)
can grow alongside it without renaming.
"""

from .telegram import configure, send_document, send_telegram

__all__ = ["configure", "send_telegram", "send_document"]
