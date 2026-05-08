"""Render a chat (intermediate dict) to PDF bytes.

Pipeline: chat dict → markdown-it-py per message → Jinja template → WeasyPrint.

Intermediate chat shape (the API handler layer is responsible for
adapting from `AgentContext` to this):

    {
        "title": str,
        "created_at": "2026-05-08T12:34:56+09:00",  # optional ISO8601
        "messages": [
            {
                "role": "user" | "agent" | "tool_call" | "tool_result",
                "text": str,                 # raw markdown (or plain)
                "ts": "...",                 # optional ISO8601
                "tool_name": str,            # optional, only for tool_*
                "tool_args_json": str,       # optional, only for tool_call
            },
            ...
        ],
    }

The renderer is pure: no I/O beyond reading its own template + CSS,
no network. Test it standalone before wiring the API/UI.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt

_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE / "templates"

_ROLE_LABELS = {
    "user": "사용자",
    "agent": "에이전트",
    "tool_call": "도구 호출",
    "tool_result": "도구 결과",
}


def _build_md() -> MarkdownIt:
    # commonmark + GFM-ish features that markdown-it-py supports natively.
    return MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(
        ["table", "strikethrough"]
    )


def _build_jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _format_message(m: dict[str, Any], md: MarkdownIt) -> dict[str, Any]:
    role = m.get("role", "user")
    text = m.get("text", "") or ""
    label = _ROLE_LABELS.get(role, role)

    # tool_call: render the tool name + args as a fenced code block; ignore `text`.
    if role == "tool_call":
        # tool_name is folded into label downstream; the body is just the JSON args.
        args = m.get("tool_args_json") or m.get("text") or ""
        if isinstance(args, dict | list):
            args = json.dumps(args, ensure_ascii=False, indent=2)
        body_md = f"```json\n{args}\n```"
    else:
        body_md = text

    body_html = md.render(body_md) if body_md else ""

    return {
        "role": role,
        "role_label": label,
        "ts": m.get("ts"),
        "tool_name": m.get("tool_name"),
        "body_html": body_html,
    }


def render_chat_to_pdf(chat: dict[str, Any]) -> bytes:
    """Render a normalized chat dict to PDF bytes.

    Imports WeasyPrint lazily so importing this module doesn't fail when
    the package isn't installed (lets the API endpoint return a clean
    error instead of 500ing on import).
    """
    from weasyprint import HTML  # type: ignore

    md = _build_md()
    env = _build_jinja()
    template = env.get_template("chat.html.j2")

    formatted = [_format_message(m, md) for m in chat.get("messages", [])]
    html_str = template.render(
        title=chat.get("title") or "Chat Export",
        created_at=chat.get("created_at"),
        messages=formatted,
    )
    return HTML(string=html_str, base_url=str(_HERE)).write_pdf()
