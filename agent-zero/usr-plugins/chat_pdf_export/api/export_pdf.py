"""POST /api/plugins/chat_pdf_export/export_pdf

Body: { "context": "<chat-context-id>" }
Returns: PDF binary blob with Content-Disposition: attachment.

The handler reads the live `AgentContext` log (same source the WebUI
renders), maps each LogItem to a normalized message dict, then hands
off to `helpers.render.render_chat_to_pdf`. Errors return JSON 4xx/5xx
so the frontend can surface a useful message.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agent import AgentContext
from flask import Response
from helpers.api import ApiHandler, Input, Output, Request

# Make the plugin's `render/` package importable regardless of cwd.
# Note: deliberately NOT named `helpers` — that would shadow agent-zero's
# top-level `helpers/` package once the plugin root is on sys.path.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from render.render import render_chat_to_pdf  # noqa: E402

# LogItem.type → our normalized role. Types we don't surface in the PDF
# (progress/info/hint/etc.) map to None and get skipped.
_TYPE_MAP: dict[str, str | None] = {
    "user": "user",
    "response": "agent",
    "agent": "agent",       # agent's reasoning/thoughts
    "tool": "tool_call",
    "code_exe": "tool_call",
    "browser": "tool_call",
    "subagent": "agent",
    "mcp": "tool_call",
    "input": "user",
    # noise / meta
    "progress": None,
    "info": None,
    "hint": None,
    "warning": None,
    "error": None,
    "util": None,
}


def _logitem_to_message(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map a LogItem.output() dict (from `_serialize_log`) into our chat
    message dict. Returns None for items that should be hidden."""
    role = _TYPE_MAP.get(item.get("type", ""))
    if role is None:
        return None

    heading = (item.get("heading") or "").strip()
    content = (item.get("content") or "").strip()
    kvps = item.get("kvps") or {}

    text_parts: list[str] = []
    if heading and role != "tool_call":
        text_parts.append(f"**{heading}**")
    if content:
        text_parts.append(content)

    msg: dict[str, Any] = {
        "role": role,
        "ts": _ts_from_log_item(item),
    }

    if role == "tool_call":
        msg["tool_name"] = heading or item.get("type", "tool")
        # kvps usually carries the tool args; fall back to content if not.
        msg["tool_args_json"] = kvps if kvps else (content or {})
    else:
        msg["text"] = "\n\n".join(text_parts) if text_parts else (heading or "")

    return msg


def _ts_from_log_item(item: dict[str, Any]) -> str | None:
    ts = item.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
    except Exception:
        return None


def _build_chat_dict(
    context: AgentContext,
    log_no: int | None = None,
) -> dict[str, Any]:
    """Build a normalized chat dict.

    When `log_no` is None, every visible LogItem is included. When set,
    only the LogItem with that `.no` is included — used for the
    per-message export button. Title is augmented to make the source
    obvious in the resulting PDF and download filename.
    """
    name = (getattr(context, "name", None) or "Chat").strip() or "Chat"
    created_at_dt = getattr(context, "created_at", None)
    created_at = created_at_dt.isoformat(timespec="seconds") if created_at_dt else None

    # context.log.logs is the canonical list of LogItem objects. (Log.output()
    # returns a LogOutput wrapper for the UI, not a plain list — easy footgun.)
    log_items = list(getattr(context.log, "logs", []) or [])
    if log_no is not None:
        log_items = [it for it in log_items if getattr(it, "no", None) == log_no]

    messages: list[dict[str, Any]] = []
    for item in log_items:
        raw = item.output() if hasattr(item, "output") else item
        m = _logitem_to_message(raw)
        if m is not None:
            messages.append(m)

    title = name if log_no is None else f"{name} — message #{log_no}"

    return {
        "title": title,
        "created_at": created_at,
        "messages": messages,
    }


def _safe_filename(name: str) -> str:
    """Produce a filesystem-friendly base for the download. Strips
    path separators and trims length; UTF-8 friendly."""
    bad = '<>:"/\\|?*\n\r\t'
    cleaned = "".join(ch for ch in name if ch not in bad).strip() or "chat"
    return cleaned[:80]


class ExportPdf(ApiHandler):
    """Render the active chat context to a PDF and stream it back."""

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    async def process(self, input: Input, request: Request) -> Output:
        ctxid = (input or {}).get("context", "")
        if not ctxid:
            return Response("Missing context id", 400)

        # Optional: when the per-message button is clicked, the frontend sends
        # the LogItem.no for that message. We render only that one log entry.
        raw_log_no = (input or {}).get("log_no")
        log_no: int | None = None
        if raw_log_no is not None and raw_log_no != "":
            try:
                log_no = int(raw_log_no)
            except (TypeError, ValueError):
                return Response("log_no must be an integer", 400)

        context = AgentContext.get(ctxid)
        if not context:
            return Response("Context not found", 404)

        chat = _build_chat_dict(context, log_no=log_no)
        if not chat["messages"]:
            if log_no is not None:
                return Response(
                    f"Message #{log_no} not found or has no exportable content",
                    404,
                )
            return Response("Chat has no exportable messages", 400)

        try:
            pdf_bytes = render_chat_to_pdf(chat)
        except ImportError as e:
            return Response(
                f"PDF rendering dependency missing: {e}. "
                f"Install via `pip install weasyprint` inside the agent-zero container.",
                500,
            )

        # Korean-safe filename (RFC 5987). Browsers prefer the filename* form
        # for non-ASCII, but we also send a sanitized ASCII fallback for
        # compatibility with older clients.
        base = _safe_filename(chat["title"])
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        utf8_name = f"{base}-{ts}.pdf"
        ascii_fallback = f"chat-export-{ts}.pdf"
        disp = (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{quote(utf8_name)}"
        )

        return Response(
            response=pdf_bytes,
            status=200,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": disp,
                "Content-Length": str(len(pdf_bytes)),
                "Cache-Control": "no-store",
            },
        )
