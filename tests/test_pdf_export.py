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
