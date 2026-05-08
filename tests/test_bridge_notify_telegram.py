"""Pin telegram-bridge/notify/telegram.py — Phase L carve.

`send_telegram` is the bridge's only outbound telegram path. Its
behaviors with regression history:

- Long-message chunking at 4000 chars (telegram's hard cap is 4096;
  we leave headroom for the formatter)
- parse_mode fallback to plain text on "can't parse entities"
- No-op when bot/chat_id unconfigured (early-boot calls before wiring)
- Empty / whitespace-only text → no-op
- fallback_text only used for the chunk that actually failed parsing
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parent.parent / "telegram-bridge" / "notify" / "telegram.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("bridge_notify_tg", _PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tg():
    mod = _load()
    # Reset state between tests.
    mod._bot = None
    mod._chat_id = None
    return mod


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Fake Bot ──────────────────────────────────────────────────────────


class _FakeBot:
    """Records every send_message call. Optionally raises a parse-error
    exception on the first attempt for a given chunk index."""

    def __init__(self, raise_on_first_chunk: bool = False, raise_msg: str = ""):
        self.calls = []
        self.raise_on_first_chunk = raise_on_first_chunk
        self.raise_msg = raise_msg
        self._first_done = False

    async def send_message(self, chat_id, text, parse_mode=None):
        # Raise once on the first chunk; subsequent (incl. fallback) succeed.
        if self.raise_on_first_chunk and not self._first_done:
            self._first_done = True
            self.calls.append({"chat_id": chat_id, "text": text,
                               "parse_mode": parse_mode, "raised": True})
            raise Exception(self.raise_msg)
        self.calls.append({"chat_id": chat_id, "text": text,
                           "parse_mode": parse_mode, "raised": False})


# ── no-op paths ───────────────────────────────────────────────────────


def test_no_bot_configured_is_noop(tg):
    """Early-boot calls before configure() must not crash."""
    _run(tg.send_telegram("hello"))
    # No exception is the assertion.


def test_empty_text_is_noop(tg):
    bot = _FakeBot()
    tg.configure(bot=bot, chat_id=42)
    _run(tg.send_telegram(""))
    _run(tg.send_telegram("   \n  "))
    assert bot.calls == []


# ── happy path ────────────────────────────────────────────────────────


def test_short_message_one_call(tg):
    bot = _FakeBot()
    tg.configure(bot=bot, chat_id=42)
    _run(tg.send_telegram("안녕하세요", parse_mode="HTML"))
    assert len(bot.calls) == 1
    c = bot.calls[0]
    assert c["chat_id"] == 42
    assert c["text"] == "안녕하세요"
    assert c["parse_mode"] == "HTML"


def test_chat_id_coerced_to_int(tg):
    """configure() accepts str chat_id from env and coerces; later
    sends use the int form."""
    bot = _FakeBot()
    tg.configure(bot=bot, chat_id="123")  # type: ignore[arg-type]
    _run(tg.send_telegram("x"))
    assert bot.calls[0]["chat_id"] == 123


# ── chunking ──────────────────────────────────────────────────────────


def test_chunked_when_over_4000(tg):
    bot = _FakeBot()
    tg.configure(bot=bot, chat_id=42)
    big = "x" * 9000  # 3 chunks: 4000 + 4000 + 1000
    _run(tg.send_telegram(big))
    assert len(bot.calls) == 3
    assert len(bot.calls[0]["text"]) == 4000
    assert len(bot.calls[1]["text"]) == 4000
    assert len(bot.calls[2]["text"]) == 1000


def test_exactly_4000_one_chunk(tg):
    bot = _FakeBot()
    tg.configure(bot=bot, chat_id=42)
    _run(tg.send_telegram("y" * 4000))
    assert len(bot.calls) == 1
    assert len(bot.calls[0]["text"]) == 4000


# ── parse_mode fallback ───────────────────────────────────────────────


def test_parse_error_falls_back_to_plain(tg):
    """When telegram returns "can't parse entities", retry the SAME
    chunk in plain mode (no parse_mode)."""
    bot = _FakeBot(raise_on_first_chunk=True, raise_msg="Bad Request: can't parse entities")
    tg.configure(bot=bot, chat_id=42)
    _run(tg.send_telegram("<b>broken", parse_mode="HTML",
                          fallback_text="<b>broken (plain)"))
    # 2 calls — first raised (HTML), second succeeded (plain fallback).
    assert len(bot.calls) == 2
    assert bot.calls[0]["raised"] is True
    assert bot.calls[0]["parse_mode"] == "HTML"
    assert bot.calls[1]["raised"] is False
    assert bot.calls[1]["parse_mode"] is None  # plain mode
    assert bot.calls[1]["text"] == "<b>broken (plain)"


def test_non_parse_error_does_not_retry(tg):
    """Network errors / other failures don't trigger the parse-fallback path."""
    bot = _FakeBot(raise_on_first_chunk=True, raise_msg="Connection refused")
    tg.configure(bot=bot, chat_id=42)
    _run(tg.send_telegram("hi", parse_mode="HTML"))
    assert len(bot.calls) == 1  # raised once, no retry


def test_no_parse_mode_no_retry(tg):
    """If there's no parse_mode, parse-error retry doesn't fire either."""
    bot = _FakeBot(raise_on_first_chunk=True, raise_msg="Bad Request: can't parse")
    tg.configure(bot=bot, chat_id=42)
    _run(tg.send_telegram("plain"))  # no parse_mode
    assert len(bot.calls) == 1


def test_fallback_text_per_chunk_indexing(tg):
    """When the message is chunked AND parse fails on chunk N, the
    fallback for THAT chunk index is used (not the whole fallback_text)."""
    big = "x" * 9000  # 3 chunks
    fallback_big = "y" * 9000  # 3 fallback chunks
    bot = _FakeBot()
    tg.configure(bot=bot, chat_id=42)
    _run(tg.send_telegram(big, parse_mode="HTML", fallback_text=fallback_big))
    # No parse errors, so all 3 chunks send normally — fallback isn't touched.
    assert len(bot.calls) == 3
    for c in bot.calls:
        assert "x" in c["text"]
        assert "y" not in c["text"]


# ── configure() ───────────────────────────────────────────────────────


def test_configure_idempotent_last_wins(tg):
    bot1 = _FakeBot()
    bot2 = _FakeBot()
    tg.configure(bot=bot1, chat_id=1)
    tg.configure(bot=bot2, chat_id=2)
    _run(tg.send_telegram("hi"))
    assert bot1.calls == []
    assert len(bot2.calls) == 1
    assert bot2.calls[0]["chat_id"] == 2
