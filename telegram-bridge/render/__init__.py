"""Telegram-bridge rendering helpers.

Phase F carve from bot.py (issue #79). Currently exports the markdown
→ Telegram-HTML converter. Future phases may add more rendering here
(e.g. table prettifiers, message chunking heuristics).
"""

from .markdown import md_to_telegram_html

__all__ = ["md_to_telegram_html"]
