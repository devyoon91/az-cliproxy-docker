"""Voice / audio Telegram handler — STT then forward to Agent Zero.

Telegram VOICE (mic OGG/Opus) and AUDIO (mp3/m4a attachment) messages
get downloaded, transcribed via `stt.transcribe`, echoed back to the
user (so misrecognitions are obvious), and finally piped through the
existing `bot.send_to_agent_zero` text path.

`send_to_agent_zero` is imported lazily inside the handler to avoid a
circular import — bot.py imports this module to register the handler.
"""
from __future__ import annotations

import logging
import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from telegram_handlers.stt import STTUnavailable, transcribe

logger = logging.getLogger(__name__)

_chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
CHAT_ID: int | None = int(_chat_id_raw) if _chat_id_raw else None

# Telegram bot API hard limit for getFile downloads.
MAX_AUDIO_BYTES = 20 * 1024 * 1024


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("권한이 없습니다.")
        return

    media = update.message.voice or update.message.audio
    if media is None:
        return

    if media.file_size and media.file_size > MAX_AUDIO_BYTES:
        await update.message.reply_text(
            f"음성 파일이 너무 큽니다 ({media.file_size / 1024 / 1024:.1f}MB > 20MB). "
            "더 짧게 녹음해주세요."
        )
        return

    suffix = ".ogg" if update.message.voice else ".bin"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.close()
    try:
        tg_file = await media.get_file()
        await tg_file.download_to_drive(tmp.name)

        try:
            text = await transcribe(tmp.name)
        except STTUnavailable:
            await update.message.reply_text(
                "음성 인식이 비활성화되어 있습니다 (OPENAI_API_KEY 미설정)."
            )
            return
        except Exception as e:
            logger.exception("STT failed")
            await update.message.reply_text(f"음성 인식 실패: {e}")
            return

        if not text:
            await update.message.reply_text("들리지 않습니다. 다시 시도해주세요.")
            return

        await update.message.reply_text(f"🎤 들은 내용: {text}")

        from bot import send_to_agent_zero

        logger.info("Voice → Agent Zero: %s", text[:100])
        response = await send_to_agent_zero(text)
        await update.message.reply_text(response)

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
