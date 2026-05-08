"""Markdown → Telegram-HTML converter.

Phase F carve from bot.py (issue #79). Pure function, no Telegram /
aiohttp deps — easy to unit-test thoroughly.

Telegram supports a SUBSET of HTML for parse_mode="HTML": <b>, <i>, <u>,
<s>, <code>, <pre>, <a>, <blockquote>, <tg-spoiler>. Notably it rejects
<p>, <ul>, <li>, <h1>, <div>, etc. — so a generic markdown library
(e.g. python-markdown) won't work out of the box. This is a small
purpose-built converter covering what AZ actually emits in answers:
  ```code blocks```, `inline code`, **bold**, *italic*, [text](url)
Anything else falls through as HTML-escaped plain text.
"""
from __future__ import annotations

import re


def md_to_telegram_html(text: str) -> str:
    """Convert simple markdown to Telegram-safe HTML.

    HTML-escapes everything first so untrusted content can't inject
    arbitrary tags, then selectively rewrites known markdown markers
    to the matching Telegram tag. Order matters — code blocks are
    handled before inline code, **bold** before *italic*, so the
    longer pattern always wins.
    """
    if not text:
        return ""
    # Step 1: full HTML escape — protects against tag injection AND lets
    # us safely inject our own tags below since `<`/`>` from user content
    # are already neutralized.
    out = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

    # Step 2: ```lang\n...\n``` fenced code blocks.
    # Stash each fence as a placeholder token and only restore them at the
    # very end. Without this, line-anchored rules like the header regex
    # (`^#{1,6} `) chase Bash comments INSIDE the fence and wrap them in
    # `<b>...</b>`, producing nested `<b><pre><code>...</b></code></pre>`
    # mush that Telegram rejects with "can't parse entities" → bridge
    # falls back to plain text → user sees literal ``` markers. Same shape
    # of problem applies to the table rule and the bold/italic regexes
    # whenever shell/code content contains `|`, `*`, `**`.
    #
    # Sentinel chars `\x00` aren't valid in user input we'd render, so
    # they're a safe placeholder boundary.
    fence_placeholders: list[str] = []

    def _stash_fence(m):
        lang = m.group(1) or ""
        body = m.group(2).rstrip("\n")
        if lang:
            html = f'<pre><code class="language-{lang}">{body}</code></pre>'
        else:
            html = f"<pre>{body}</pre>"
        idx = len(fence_placeholders)
        fence_placeholders.append(html)
        return f"\x00FENCE{idx}\x00"

    out = re.sub(
        r"```(\w*)\n?(.*?)```",
        _stash_fence,
        out,
        flags=re.DOTALL,
    )

    # Step 3: Markdown tables → <pre> blocks.
    # Telegram has no <table> tag, but <pre> renders monospace, so the
    # original `|` separators become a visually aligned ASCII table —
    # which is the closest we can get. Strip the GitHub-style separator
    # row (`|---|---|`) since it adds no value in monospace.
    def _table(m):
        block = m.group(0).strip("\n")
        kept = []
        for line in block.split("\n"):
            stripped = line.strip()
            # Skip the separator-only row, e.g. "|---|---|" or "| --- | :-: |"
            if re.match(r"^\|?\s*[-:|\s]+\|?\s*$", stripped):
                continue
            kept.append(line)
        return "<pre>" + "\n".join(kept) + "</pre>"

    out = re.sub(
        # Two or more consecutive lines starting with optional spaces + `|`.
        r"(?:^[ \t]*\|.+(?:\n|$)){2,}",
        _table,
        out,
        flags=re.MULTILINE,
    )

    # Step 4: `inline code`. Run AFTER fences so triple-backticks aren't
    # eaten as three single-backtick spans.
    out = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", out)

    # Step 5: **bold** (must run before *italic* so ** doesn't match as two *)
    out = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", out)

    # Step 6: *italic* — guard with negative lookarounds to avoid eating
    # asterisks already inside <b>...</b> bursts.
    out = re.sub(
        r"(?<![*\w])\*([^*\n]+?)\*(?!\w)",
        r"<i>\1</i>",
        out,
    )

    # Step 7: [text](url). The `&` in URL would have been HTML-escaped to
    # &amp; in step 1; that's actually correct in href values.
    out = re.sub(
        r"\[([^\]]+?)\]\((https?://[^\s)]+)\)",
        r'<a href="\2">\1</a>',
        out,
    )

    # Step 8: ATX headers `# heading` … `###### heading` → <b>heading</b>.
    # Telegram has no header tag; bold is the closest visual proxy.
    # Run AFTER all inline rules so a header like `## **important**` —
    # which becomes `## <b>important</b>` after step 5 — gets the inner
    # <b>...</b> stripped here before re-wrapping. Otherwise we'd end
    # up with `<b><b>important</b></b>` (Telegram tolerates it but it's noise).
    def _header(m):
        body = m.group(1).strip()
        body = body.replace("<b>", "").replace("</b>", "")
        return f"<b>{body}</b>"

    out = re.sub(
        r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$",
        _header,
        out,
        flags=re.MULTILINE,
    )

    # Step 9: Restore stashed fenced code blocks. Plain string replace —
    # placeholder tokens carry the index, content was already escaped + tag-
    # wrapped at stash time so nothing else needs to happen here.
    for idx, html in enumerate(fence_placeholders):
        out = out.replace(f"\x00FENCE{idx}\x00", html)

    return out
