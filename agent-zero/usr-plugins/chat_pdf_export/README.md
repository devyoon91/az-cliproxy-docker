# chat_pdf_export

Sidebar dropdown button that exports the active Agent Zero chat to a PDF.
Reads the live `AgentContext` log (same source the WebUI renders), maps each
`LogItem` to a normalized message dict, then renders via markdown-it-py +
Jinja2 + WeasyPrint with embedded Korean font (Noto Sans CJK KR).

## Layout

```
chat_pdf_export/
├── plugin.yaml                                    # 4-field manifest
├── api/
│   └── export_pdf.py                              # ApiHandler — POST /plugins/chat_pdf_export/export_pdf
├── render/                                        # NOT named `helpers/` — would shadow /a0/helpers
│   ├── render.py                                  # render_chat_to_pdf(chat) -> bytes
│   └── templates/
│       └── chat.html.j2                           # CSS + per-role styling, A4 page
└── extensions/
    └── webui/
        ├── chat-input-bottom-actions-start/
        │   └── export-pdf-button.html             # full-chat button (always visible)
        └── set_messages_after_loop/
            └── inject-pdf-buttons.js              # per-message action button injector
```

## Pipeline

```
AgentContext.log.logs        ← live UI source-of-truth
  → _logitem_to_message()    ← drop noise types (progress/info/hint/...)
  → markdown-it-py           ← per-message body to HTML (GFM tables + strikethrough)
  → Jinja chat.html.j2       ← role-colored blocks, code/table/blockquote styles
  → WeasyPrint               ← HTML+CSS to PDF, A4, page-counter footer
  → Flask Response           ← Content-Disposition with RFC 5987 filename* (Korean-safe)
```

## Installation

The plugin needs `weasyprint` (and a copy of Cairo/Pango/fontconfig — but those
are already present in the base image because LibreOffice depends on them).
[`agent-zero/Dockerfile`](../../Dockerfile) extends `agent0ai/agent-zero:v1.13`
with a single `pip install weasyprint`. To rebuild after a base-image bump:

```bash
docker compose build agent-zero
docker compose up -d --force-recreate agent-zero
```

## API

```
POST /api/plugins/chat_pdf_export/export_pdf
Content-Type: application/json
X-CSRF-Token: <token>

{
  "context": "<active-chat-context-id>",
  "log_no":  42                            // optional — single-message export
}
```

When `log_no` is omitted, the entire visible chat is rendered. When set, only
the LogItem with that `.no` is rendered, and the title becomes
`<chat name> — message #<log_no>`. Returns `200 application/pdf` with
`Content-Disposition: attachment` and a download filename derived from the
title. Errors:

| Status | Meaning |
|--------|---------|
| 400    | Missing context id, `log_no` not an integer, or chat has no exportable messages |
| 404    | Context not found, or `log_no` out of range / message has no exportable content |
| 500    | WeasyPrint missing (rebuild image), or render error |

## UI

**Full chat** — `chat-input-bottom-actions-start` slot. Always-visible row
under the chat input (same row as Browser, Compact, Pause Agent, Nudge,
Terminal, Stop). Mirrors `_browser/.../browser-button.html`.

**Per-message** — `set_messages_after_loop` extension injects a
`picture_as_pdf` action button into every message's `.step-action-buttons`
bar. Same hook + helper (`createActionButton`) as
`_chat_branching/inject-branch-buttons.js` — appears next to copy / branch
on each message.

Both flows use `fetchApi` (auto-attaches CSRF) → blob → `<a download>`.
Korean filenames use RFC 5987 `filename*=UTF-8''...` and decode client-side.

(An earlier revision used `sidebar-quick-actions-dropdown-start` for the
full-chat button, which hid it inside a collapsed dropdown — moved out for
discoverability.)

## What's filtered out

LogItem types that aren't surfaced in the PDF:
`progress`, `info`, `hint`, `warning`, `error`, `util` — too noisy for a
shareable artifact. To include them, edit `_TYPE_MAP` in `api/export_pdf.py`.

## Limitations / known gaps

- WeasyPrint syntax-highlights nothing — code blocks are monospace + box only.
  Add Pygments or shiki output in `render.py` if you want colors.
- No range filter (entire chat exported). Future: `since` / `until` body fields.
- No cost/usage footer. Easy add via `pricing.py` (the repo already imports it).
- Subagent labels collapse into the parent agent role; expand if needed.

## Why `render/` not `helpers/`

agent-zero exports `/a0/helpers/` as a top-level package. A plugin-local
`helpers/` package on `sys.path` shadows it (subtle: `import helpers.api`
suddenly resolves to the plugin's empty `helpers/`, and the api handler
crashes at import). Naming the package `render/` removes the conflict
with zero ambiguity.
