"""Pin chat_pdf_export.api.export_pdf._build_chat_dict (PR #73).

The per-message export feature relies on `_build_chat_dict(ctx, log_no=N)`
returning EXACTLY one message — the one with `LogItem.no == N` — and
augmenting the title to make the source obvious.

Pinning:
1. log_no=None (default) → all visible messages, original title.
2. log_no=N → exactly the message with `.no == N`.
3. log_no=N tags the title `<chat name> — message #N`.
4. log_no out of range → empty `messages` list (handler returns 404).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_EXPORT_PATH = (
    Path(__file__).resolve().parent.parent
    / "agent-zero" / "usr-plugins" / "chat_pdf_export" / "api" / "export_pdf.py"
)


def _load_export_module():
    sys.modules.pop("export_pdf", None)
    spec = importlib.util.spec_from_file_location("export_pdf", _EXPORT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── fakes ────────────────────────────────────────────────────────────


class _FakeLogItem:
    """Minimal LogItem stand-in. Real LogItem has an `output()` method that
    returns a dict-shaped LogOutput; the export code's `_logitem_to_message`
    handles both that and the dict directly. We feed dicts straight in."""

    def __init__(self, no, type_, heading, content):
        self.no = no
        self._payload = {"type": type_, "heading": heading, "content": content}

    def output(self):
        return self._payload


class _FakeLog:
    def __init__(self, items):
        self.logs = list(items)


class _FakeContext:
    def __init__(self, name, items):
        self.name = name
        self.created_at = None
        self.log = _FakeLog(items)


# ── tests ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def export_pdf():
    return _load_export_module()


def test_full_export_returns_all_messages(export_pdf):
    ctx = _FakeContext("현대차 시세", [
        _FakeLogItem(0, "user", "사용자", "현대차 오늘 시세 알려줘"),
        _FakeLogItem(1, "agent", "에이전트", "조회중입니다."),
        _FakeLogItem(2, "response", "응답", "**현대차** 616,000원 (+7.69%)"),
    ])
    chat = export_pdf._build_chat_dict(ctx)
    assert chat["title"] == "현대차 시세"
    assert len(chat["messages"]) == 3


def test_log_no_filters_to_single_message(export_pdf):
    ctx = _FakeContext("Hyundai", [
        _FakeLogItem(0, "user", "사용자", "Q"),
        _FakeLogItem(1, "agent", "에이전트", "thinking..."),
        _FakeLogItem(2, "response", "응답", "ANSWER-616K"),
    ])
    chat = export_pdf._build_chat_dict(ctx, log_no=2)
    assert len(chat["messages"]) == 1
    body = chat["messages"][0].get("text") or ""
    assert "ANSWER-616K" in body


def test_log_no_tags_title(export_pdf):
    ctx = _FakeContext("Hyundai", [
        _FakeLogItem(0, "user", "u", "Q"),
        _FakeLogItem(1, "response", "r", "A"),
    ])
    chat = export_pdf._build_chat_dict(ctx, log_no=1)
    assert chat["title"] == "Hyundai — message #1"


def test_log_no_out_of_range_yields_empty_messages(export_pdf):
    ctx = _FakeContext("Hyundai", [
        _FakeLogItem(0, "user", "u", "Q"),
        _FakeLogItem(1, "response", "r", "A"),
    ])
    chat = export_pdf._build_chat_dict(ctx, log_no=999)
    assert chat["messages"] == []


def test_noise_types_filtered_out(export_pdf):
    """LogItem types like progress/info/hint/warning shouldn't appear in
    a shareable PDF — the export's _logitem_to_message drops them."""
    ctx = _FakeContext("Mixed", [
        _FakeLogItem(0, "user", "u", "Q"),
        _FakeLogItem(1, "info", "i", "thinking..."),
        _FakeLogItem(2, "progress", "p", "loading..."),
        _FakeLogItem(3, "response", "r", "A"),
    ])
    chat = export_pdf._build_chat_dict(ctx)
    types = [m.get("role") for m in chat["messages"]]
    # Whatever role the response maps to, info/progress should be gone.
    assert len(chat["messages"]) == 2
    assert all(t not in {"info", "progress"} for t in types)


# ── progress notifications (issue #133) ──────────────────────────────
#
# The slow WeasyPrint render is bracketed by a progress → success/error
# toast so the user knows a PDF is being generated. The handler is async
# and returns a Flask Response; we drive it with a fresh event loop the
# same way test_bridge_webhooks.py exercises aiohttp handlers.

import agent as _agent  # noqa: E402  (stub installed by conftest)
import helpers.notification as _notif  # noqa: E402  (stub installed by conftest)


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def notif_sink():
    _notif.NotificationManager.sent.clear()
    yield _notif.NotificationManager.sent
    _notif.NotificationManager.sent.clear()


def test_export_emits_progress_then_success(export_pdf, notif_sink):
    ctx = _FakeContext("현대차 시세", [
        _FakeLogItem(0, "user", "사용자", "현대차 오늘 시세 알려줘"),
        _FakeLogItem(1, "response", "응답", "**현대차** 616,000원"),
    ])
    _agent.AgentContext._store["ctx-ok"] = ctx
    resp = _run(export_pdf.ExportPdf().process({"context": "ctx-ok"}, None))

    kinds = [n["type"] for n in notif_sink]
    assert kinds == ["progress", "success"]
    # Same id on both → the toast updates in place rather than stacking.
    assert notif_sink[0]["id"] == notif_sink[1]["id"]
    assert notif_sink[0]["group"] == "chat-pdf-export"
    # Success carries the generated filename.
    assert notif_sink[1]["message"].endswith(".pdf")
    # The export itself still succeeded (PDF bytes returned).
    assert 200 in resp.kwargs.values() or 200 in getattr(resp, "args", ())


def test_export_per_message_progress_mentions_log_no(export_pdf, notif_sink):
    ctx = _FakeContext("Hyundai", [
        _FakeLogItem(0, "user", "u", "Q"),
        _FakeLogItem(1, "response", "r", "ANSWER"),
    ])
    _agent.AgentContext._store["ctx-msg"] = ctx
    _run(export_pdf.ExportPdf().process({"context": "ctx-msg", "log_no": 1}, None))

    assert notif_sink[0]["type"] == "progress"
    assert "#1" in notif_sink[0]["message"]
    # Per-message id is distinct from the full-chat id for the same context.
    assert notif_sink[0]["id"].endswith("-1")


def test_export_emits_error_on_render_failure(export_pdf, notif_sink, monkeypatch):
    ctx = _FakeContext("boom", [
        _FakeLogItem(0, "user", "u", "Q"),
        _FakeLogItem(1, "response", "r", "A"),
    ])
    _agent.AgentContext._store["ctx-err"] = ctx

    def _boom(_chat):
        raise RuntimeError("weasyprint exploded")

    monkeypatch.setattr(export_pdf, "render_chat_to_pdf", _boom)
    with pytest.raises(RuntimeError, match="exploded"):
        _run(export_pdf.ExportPdf().process({"context": "ctx-err"}, None))

    kinds = [n["type"] for n in notif_sink]
    assert kinds == ["progress", "error"]
    assert notif_sink[1]["id"] == notif_sink[0]["id"]


def test_export_importerror_returns_500_and_error_toast(export_pdf, notif_sink, monkeypatch):
    ctx = _FakeContext("noweasy", [
        _FakeLogItem(0, "user", "u", "Q"),
        _FakeLogItem(1, "response", "r", "A"),
    ])
    _agent.AgentContext._store["ctx-noweasy"] = ctx

    def _no_weasy(_chat):
        raise ImportError("No module named 'weasyprint'")

    monkeypatch.setattr(export_pdf, "render_chat_to_pdf", _no_weasy)
    resp = _run(export_pdf.ExportPdf().process({"context": "ctx-noweasy"}, None))

    # Graceful 500 (not a raise) so the frontend shows a useful message.
    assert 500 in getattr(resp, "args", ())
    assert [n["type"] for n in notif_sink] == ["progress", "error"]


def test_export_no_toast_when_chat_empty(export_pdf, notif_sink):
    """Empty-chat validation returns before the render bracket, so no
    progress toast — the frontend surfaces the 4xx immediately."""
    ctx = _FakeContext("empty", [])
    _agent.AgentContext._store["ctx-empty"] = ctx
    _run(export_pdf.ExportPdf().process({"context": "ctx-empty"}, None))
    assert notif_sink == []
