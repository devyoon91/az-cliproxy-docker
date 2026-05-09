"""`/docs` Telegram command — Phase O carve from bot.py (issue #79).

`/docs` lists and serves the bridge's mounted markdown docs (GUIDE,
README, /app/docs/*.md). Uses `notify.send_document` for the file-send
path — same configure() injection that send_telegram already uses.

`/logs` and `/backup` stay in bot.py for now because they read
`monitor_context` (and `/logs` also uses az_client to call AZ's
`/chat_export`). They move once monitor state is carved out.
"""
from __future__ import annotations

import io
import os

from notify.telegram import send_document
from telegram import Update
from telegram.ext import ContextTypes

# Read CHAT_ID directly from env — same source bot.py uses. Optional
# (issue #106): handlers are only registered when telegram is enabled.
_chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
CHAT_ID: int | None = int(_chat_id_raw) if _chat_id_raw else None


async def cmd_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """문서 목록 조회 및 파일 전송"""
    if update.effective_chat.id != CHAT_ID:
        return

    # 문서 파일 목록
    doc_files: dict[str, str] = {}
    for f in ["GUIDE.md", "README.md"]:
        path = f"/app/{f}"
        if os.path.exists(path):
            doc_files[f] = path

    docs_dir = "/app/docs"
    if os.path.isdir(docs_dir):
        for f in sorted(os.listdir(docs_dir)):
            if f.endswith(".md"):
                doc_files[f"docs/{f}"] = os.path.join(docs_dir, f)

    if not doc_files:
        await update.message.reply_text("문서를 찾을 수 없습니다.")
        return

    args = context.args
    if not args:
        # 목록 표시
        lines = ["📚 문서 목록:\n"]
        for i, name in enumerate(doc_files.keys(), 1):
            lines.append(f"  {i}. {name}")
        lines.append("\n문서 보기: /docs [번호]")
        lines.append("전체 다운로드: /docs all")
        await update.message.reply_text("\n".join(lines))
        return

    if args[0].lower() == "all":
        # 전체 파일 전송
        for name, path in doc_files.items():
            with open(path, "rb") as f:
                await send_document(
                    document=f,
                    filename=name.replace("/", "_"),
                    caption=f"📄 {name}",
                )
        return

    try:
        idx = int(args[0]) - 1
        keys = list(doc_files.keys())
        if idx < 0 or idx >= len(keys):
            await update.message.reply_text(f"1~{len(keys)} 범위에서 선택하세요.")
            return

        name = keys[idx]
        path = doc_files[name]

        with open(path, encoding="utf-8") as f:
            content = f.read()

        # 텔레그램 메시지로 보내기 (4000자 이하면 텍스트, 초과면 파일)
        if len(content) <= 4000:
            await update.message.reply_text(f"📄 **{name}**\n\n{content}")
        else:
            # 파일로 전송
            doc_file = io.BytesIO(content.encode("utf-8"))
            doc_file.name = name.replace("/", "_")
            await send_document(
                document=doc_file,
                caption=f"📄 {name} ({len(content)} 글자)",
            )

    except ValueError:
        await update.message.reply_text("숫자 또는 'all'을 입력하세요. 예: /docs 1")
