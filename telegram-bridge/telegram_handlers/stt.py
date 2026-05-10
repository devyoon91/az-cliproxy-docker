"""Speech-to-text adapter for Telegram voice/audio messages.

Single-engine for now (OpenAI Whisper API). Isolated behind a small
async surface so swapping engines (faster-whisper, Google, Azure) means
editing only this file — voice.py keeps calling `transcribe()`.

Engine selection rationale: see plan doc. Operator-only bot, low call
volume, Korean accuracy needs — Whisper API beats local models on every
axis except offline.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

STT_MODEL = os.environ.get("STT_MODEL") or "whisper-1"
STT_LANGUAGE = os.environ.get("STT_LANGUAGE") or "ko"

_client = None


class STTUnavailable(RuntimeError):
    """Raised when STT cannot run because OPENAI_API_KEY is missing.

    Voice handler catches this separately to give a clear setup hint
    instead of a generic API error.
    """


def _get_client():
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise STTUnavailable("OPENAI_API_KEY is not set")

    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        raise STTUnavailable(f"openai package not installed: {e}") from e

    _client = AsyncOpenAI(api_key=api_key)
    return _client


async def transcribe(audio_path: str, *, language: str | None = None) -> str:
    """Transcribe an audio file to text via OpenAI Whisper.

    Returns the stripped transcript. Raises STTUnavailable if the
    engine isn't configured; lets other openai exceptions bubble so the
    caller can surface a useful error to the user.
    """
    client = _get_client()
    lang = language or STT_LANGUAGE

    with open(audio_path, "rb") as f:
        result = await client.audio.transcriptions.create(
            model=STT_MODEL,
            file=f,
            language=lang,
        )

    text = (getattr(result, "text", None) or "").strip()
    logger.info("STT transcribed %d chars (lang=%s, model=%s)", len(text), lang, STT_MODEL)
    return text
