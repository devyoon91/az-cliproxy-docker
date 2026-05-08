"""Pin telegram-bridge/render/markdown.py — Phase F carve.

Each rule in `md_to_telegram_html` exists for a real production
incident — fence-protection from header chasing, table separator
stripping, italic guard against bold-bursts, etc. Pinning each one
catches regressions before they show up as Telegram "can't parse
entities" 400s and the bridge falling back to plain text.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MD_PATH = (
    Path(__file__).resolve().parent.parent / "telegram-bridge" / "render" / "markdown.py"
)


@pytest.fixture(scope="module")
def md():
    spec = importlib.util.spec_from_file_location("bridge_render_md", _MD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── escape ──────────────────────────────────────────────────────────


def test_empty_string(md):
    assert md.md_to_telegram_html("") == ""


def test_html_chars_escaped(md):
    assert md.md_to_telegram_html("<script>") == "&lt;script&gt;"
    assert md.md_to_telegram_html("a & b") == "a &amp; b"
    assert md.md_to_telegram_html("3 < 5 > 2") == "3 &lt; 5 &gt; 2"


def test_user_cant_inject_telegram_tags(md):
    """Defensive — even if user puts <b>fake</b> in their text it must
    show as literal &lt;b&gt;fake&lt;/b&gt;, not become real bold."""
    out = md.md_to_telegram_html("user types <b>boom</b>")
    assert "<b>" not in out  # no real tags injected
    assert "&lt;b&gt;boom&lt;/b&gt;" in out


# ── inline code ─────────────────────────────────────────────────────


def test_inline_code(md):
    assert md.md_to_telegram_html("`x = 1`") == "<code>x = 1</code>"


def test_inline_code_inside_text(md):
    out = md.md_to_telegram_html("call `foo()` then `bar()`")
    assert out == "call <code>foo()</code> then <code>bar()</code>"


# ── fenced code blocks ──────────────────────────────────────────────


def test_fence_no_lang(md):
    out = md.md_to_telegram_html("```\nprint(1)\n```")
    assert out == "<pre>print(1)</pre>"


def test_fence_with_lang(md):
    out = md.md_to_telegram_html("```python\nprint(1)\n```")
    assert out == '<pre><code class="language-python">print(1)</code></pre>'


def test_fence_protects_inner_content_from_other_rules(md):
    """The whole point of the stash-restore mechanism — a `# comment`
    inside a code fence must NOT be wrapped as <b>...</b> by the header
    rule, and `**stars**` inside must NOT become <b>stars</b>."""
    src = "```bash\n# a comment\necho **stars**\n```"
    out = md.md_to_telegram_html(src)
    # The fence body should be present LITERALLY (after HTML escape only).
    assert "# a comment" in out
    assert "**stars**" in out
    # And NOT wrapped in extra tags.
    assert "<b># a comment</b>" not in out
    assert "<b>stars</b>" not in out
    # Outer wrap is <pre><code class="language-bash">...
    assert out.startswith('<pre><code class="language-bash">')


def test_multiple_fences(md):
    src = "```\nA\n```\nbetween\n```\nB\n```"
    out = md.md_to_telegram_html(src)
    assert out.count("<pre>") == 2
    assert "between" in out


# ── tables ──────────────────────────────────────────────────────────


def test_table_separator_row_stripped(md):
    src = "| col1 | col2 |\n|------|------|\n| a | b |"
    out = md.md_to_telegram_html(src)
    assert "<pre>" in out
    # Header + data rows kept; separator row dropped.
    assert "col1" in out and "col2" in out
    assert "a" in out and "b" in out
    assert "------" not in out


def test_table_aligned_separator_stripped(md):
    """GitHub-style aligned separator like `| :-: | -: |`."""
    src = "| L | R |\n| :-: | -: |\n| 1 | 2 |"
    out = md.md_to_telegram_html(src)
    assert ":-:" not in out
    assert "<pre>" in out


# ── bold / italic ───────────────────────────────────────────────────


def test_bold(md):
    assert md.md_to_telegram_html("**important**") == "<b>important</b>"


def test_italic(md):
    assert md.md_to_telegram_html("*emphasis*") == "<i>emphasis</i>"


def test_bold_runs_before_italic(md):
    """`**x**` must produce <b>x</b>, NOT <i><i>x</i></i> from naive italic-first."""
    out = md.md_to_telegram_html("**bold here**")
    assert out == "<b>bold here</b>"
    assert "<i>" not in out


def test_italic_doesnt_eat_word_internal_star(md):
    """`a*b*c` is NOT italic — guard with negative lookarounds. Common in
    file paths or filename patterns."""
    out = md.md_to_telegram_html("file*pattern*ext")
    assert "<i>" not in out


# ── links ───────────────────────────────────────────────────────────


def test_link(md):
    out = md.md_to_telegram_html("see [docs](https://example.com)")
    assert out == 'see <a href="https://example.com">docs</a>'


def test_link_only_http(md):
    """The regex requires http(s) — javascript: URLs shouldn't match."""
    out = md.md_to_telegram_html("[bad](javascript:alert(1))")
    assert "<a" not in out


# ── headers ─────────────────────────────────────────────────────────


def test_h1(md):
    out = md.md_to_telegram_html("# Title")
    assert out == "<b>Title</b>"


def test_h6(md):
    out = md.md_to_telegram_html("###### Sub")
    assert out == "<b>Sub</b>"


def test_header_strips_inner_bold(md):
    """`## **important**` could become `<b><b>important</b></b>` if the
    header rule didn't strip pre-applied bold. Pin the dedup."""
    out = md.md_to_telegram_html("## **important**")
    assert out == "<b>important</b>"


def test_header_only_at_line_start(md):
    """A `#` mid-line shouldn't trigger the header rule."""
    out = md.md_to_telegram_html("issue #42 was fixed")
    assert "<b>" not in out


# ── interaction / regression ────────────────────────────────────────


def test_realistic_az_response(md):
    """Sanity check against a realistic Agent Zero answer — multiple
    rules interact in one input. Not pinning the exact output (would
    be brittle); just smoke-checking that the result is well-formed."""
    src = (
        "## 결과\n\n"
        "**현대차** 가격: $616,000 — *상승세* (출처: [Investing](https://example.com))\n\n"
        "```python\nprice = 616000\n```\n\n"
        "| 항목 | 값 |\n|------|-----|\n| 가격 | 616k |"
    )
    out = md.md_to_telegram_html(src)
    # Each rule fired:
    assert "<b>결과</b>" in out
    assert "<b>현대차</b>" in out
    assert "<i>상승세</i>" in out
    assert '<a href="https://example.com">' in out
    assert '<pre><code class="language-python">' in out
    # Table converted to <pre>, separator row dropped
    assert "<pre>| 항목" in out or "<pre>\n| 항목" in out or "<pre>| 항목 | 값 |" in out
    assert "------" not in out
