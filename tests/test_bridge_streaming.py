"""Pin `streaming/edit.py` — Phase R carve from bot.py (#79).

Why this module is worth pinning carefully despite being small:

  - `streaming_msg_id` and `streaming_text` MUST be mutated in place,
    never reassigned. Importers (the monitor loop, the chat handlers)
    bind to the dict objects at import time. A regression that does
    `streaming_msg_id = {}` somewhere would silently fork state — the
    same trap that bit pricing.usage.usage_today and az_client.session
    .cached_contexts.

  - Length-cap rollover is the user-visible boundary between "this
    message keeps growing" and "we just finalized it, opening a new
    one". A regression here either truncates content or floods the
    chat with separate messages.

  - The edit-failure fallback path is the recovery hatch when
    Telegram refuses an edit (msg too old, deleted, rate-limited).
    "Not modified" is a no-op success and must NOT trigger the
    send-new fallback (would duplicate the user's view).

  - `configure()` must be idempotent and last-call-wins, matching
    notify.telegram and budget.engine — this is the contract bot.py's
    post_init relies on.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge"

_OWN_MODULES = ("streaming", "streaming.edit")


def _load_streaming():
    """Spec-load `streaming.edit` against the on-disk file. The package
    has no external deps beyond `logging`, so no shim wiring needed.
    """
    pkg = sys.modules.get("streaming")
    if pkg is None:
        import types

        pkg = types.ModuleType("streaming")
        pkg.__path__ = [str(_ROOT / "streaming")]  # type: ignore[attr-defined]
        sys.modules["streaming"] = pkg

    spec = importlib.util.spec_from_file_location(
        "streaming.edit", _ROOT / "streaming" / "edit.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["streaming.edit"] = mod
    spec.loader.exec_module(mod)
    pkg.edit = mod  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def edit():
    saved = {n: sys.modules[n] for n in _OWN_MODULES if n in sys.modules}
    mod = _load_streaming()
    # Reset module-level state so each test starts clean.
    mod.streaming_msg_id.clear()
    mod.streaming_text.clear()
    mod._bot = None
    mod._chat_id = None
    yield mod
    for n in _OWN_MODULES:
        if n in saved:
            sys.modules[n] = saved[n]
        else:
            sys.modules.pop(n, None)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── configure() ─────────────────────────────────────────────────────


def test_configure_sets_module_state(edit):
    bot = MagicMock()
    edit.configure(bot=bot, chat_id=42)
    assert edit._bot is bot
    assert edit._chat_id == 42


def test_configure_is_idempotent_last_wins(edit):
    """post_init may re-wire on hot reload; the contract is last-call-wins."""
    edit.configure(bot=MagicMock(name="first"), chat_id=1)
    second_bot = MagicMock(name="second")
    edit.configure(bot=second_bot, chat_id=999)
    assert edit._bot is second_bot
    assert edit._chat_id == 999


def test_configure_coerces_chat_id_to_int(edit):
    """Match notify.telegram's `int(chat_id)` coercion — tolerates the
    env var being passed through as str by mistake."""
    edit.configure(bot=MagicMock(), chat_id="123")
    assert edit._chat_id == 123


# ── stream_reset ────────────────────────────────────────────────────


def test_stream_reset_drops_state(edit):
    edit.streaming_msg_id["ctx-1"] = 100
    edit.streaming_text["ctx-1"] = "hello"
    edit.stream_reset("ctx-1")
    assert "ctx-1" not in edit.streaming_msg_id
    assert "ctx-1" not in edit.streaming_text


def test_stream_reset_safe_when_no_state(edit):
    """No KeyError / AttributeError on first-call-after-fresh-import."""
    edit.stream_reset("never-seen")  # must not raise


def test_stream_reset_only_drops_targeted_key(edit):
    """A reset for ctx-A must not nuke ctx-B's slot — they're independent
    AZ chats and the user could be /switch'ing between them."""
    edit.streaming_msg_id["ctx-A"] = 1
    edit.streaming_text["ctx-A"] = "a"
    edit.streaming_msg_id["ctx-B"] = 2
    edit.streaming_text["ctx-B"] = "b"
    edit.stream_reset("ctx-A")
    assert edit.streaming_msg_id == {"ctx-B": 2}
    assert edit.streaming_text == {"ctx-B": "b"}


# ── stream_extend: no-op cases ──────────────────────────────────────


def test_extend_noop_when_unconfigured(edit):
    """If post_init hasn't wired the bot yet, stream_extend must NOT
    raise — early-boot calls during the wiring window are real (e.g.
    a webhook fires before main()'s post_init finishes)."""
    _run(edit.stream_extend("ctx-1", "hello"))
    # No state should have been written.
    assert edit.streaming_msg_id == {}
    assert edit.streaming_text == {}


def test_extend_noop_on_empty_chunk(edit):
    """Empty chunks shouldn't open a new message — that would create a
    blank Telegram bubble."""
    edit.configure(bot=MagicMock(), chat_id=99)
    _run(edit.stream_extend("ctx-1", ""))
    assert edit.streaming_msg_id == {}


# ── stream_extend: send-new vs edit ─────────────────────────────────


def test_extend_first_call_sends_new(edit):
    """Brand-new ctx_key → send_message path; message_id stashed."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=777))
    edit.configure(bot=bot, chat_id=99)

    _run(edit.stream_extend("ctx-1", "first chunk"))

    bot.send_message.assert_awaited_once_with(chat_id=99, text="first chunk")
    bot.edit_message_text.assert_not_called()
    assert edit.streaming_msg_id["ctx-1"] == 777
    assert edit.streaming_text["ctx-1"] == "first chunk"


def test_extend_subsequent_call_edits(edit):
    """Once a message is open, further chunks edit it with the joined text."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=777))
    bot.edit_message_text = AsyncMock()
    edit.configure(bot=bot, chat_id=99)

    _run(edit.stream_extend("ctx-1", "first"))
    _run(edit.stream_extend("ctx-1", "second"))

    # Second call edits with "first\n\nsecond"
    bot.edit_message_text.assert_awaited_once_with(
        chat_id=99, message_id=777, text="first\n\nsecond"
    )
    assert edit.streaming_text["ctx-1"] == "first\n\nsecond"
    # Still the same message_id — the edit didn't open a new one.
    assert edit.streaming_msg_id["ctx-1"] == 777


# ── stream_extend: length-cap rollover ──────────────────────────────


def test_extend_rolls_over_at_length_cap(edit):
    """When the proposed extended length crosses STREAM_MAX_CHARS, the
    current message is finalized (no further edit) and a NEW message
    opens with just `new_chunk` as its body — not the joined text."""
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=100),
        MagicMock(message_id=200),
    ])
    bot.edit_message_text = AsyncMock()
    edit.configure(bot=bot, chat_id=99)

    # Open with text near the cap.
    near_cap = "x" * (edit.STREAM_MAX_CHARS - 100)
    _run(edit.stream_extend("ctx-1", near_cap))

    # Add a chunk that pushes us over — must roll over.
    pushy = "y" * 200
    _run(edit.stream_extend("ctx-1", pushy))

    # Two send_messages (open initial, then roll over). No edit calls.
    assert bot.send_message.await_count == 2
    bot.edit_message_text.assert_not_called()
    # Second send_message is just the new chunk, NOT the joined text.
    assert bot.send_message.await_args_list[1].kwargs["text"] == pushy
    # State now points at the rolled-over message.
    assert edit.streaming_msg_id["ctx-1"] == 200
    assert edit.streaming_text["ctx-1"] == pushy


# ── stream_extend: edit-failure fallback ────────────────────────────


def test_extend_not_modified_is_silent_success(edit):
    """Telegram returns 'Bad Request: message is not modified' when the
    edit text equals the existing text. That's a benign no-op and must
    NOT trigger the send-new fallback (would duplicate the message)."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot.edit_message_text = AsyncMock(side_effect=Exception(
        "Bad Request: message is not modified"
    ))
    edit.configure(bot=bot, chat_id=99)

    _run(edit.stream_extend("ctx-1", "first"))
    _run(edit.stream_extend("ctx-1", "second"))

    # Edit was attempted but failed silently — state stays as-is.
    bot.edit_message_text.assert_awaited_once()
    # send_message ran exactly once (the initial open). NOT a second
    # time as a fallback.
    assert bot.send_message.await_count == 1


def test_extend_genuine_edit_failure_falls_back_to_send(edit):
    """For any edit failure besides 'not modified' (msg too old, deleted,
    rate limit), drop state and send a fresh message with just the new
    chunk."""
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[
        MagicMock(message_id=100),
        MagicMock(message_id=200),
    ])
    bot.edit_message_text = AsyncMock(side_effect=Exception(
        "Bad Request: message to edit not found"
    ))
    edit.configure(bot=bot, chat_id=99)

    _run(edit.stream_extend("ctx-1", "first"))
    _run(edit.stream_extend("ctx-1", "second"))

    # Edit attempted, failed, fell back to send_message.
    assert bot.send_message.await_count == 2
    # Fallback send carries just the new chunk.
    assert bot.send_message.await_args_list[1].kwargs["text"] == "second"
    # State now reflects the fresh message.
    assert edit.streaming_msg_id["ctx-1"] == 200
    assert edit.streaming_text["ctx-1"] == "second"


# ── Mutation-in-place invariant ─────────────────────────────────────


def test_dicts_are_mutated_in_place(edit):
    """The whole point of clear+update / pop / setitem (vs `dict[...] = {}`):
    importers binding to the dict object see all mutations. Pin the
    binding-stability invariant by holding a snapshot reference and
    confirming it sees the state after operations.
    """
    msgs_ref = edit.streaming_msg_id
    text_ref = edit.streaming_text

    edit.streaming_msg_id["a"] = 1
    edit.streaming_text["a"] = "x"
    assert msgs_ref["a"] == 1
    assert text_ref["a"] == "x"

    edit.stream_reset("a")
    assert "a" not in msgs_ref
    assert "a" not in text_ref

    # Bindings still point at the same objects (not reassigned).
    assert msgs_ref is edit.streaming_msg_id
    assert text_ref is edit.streaming_text
