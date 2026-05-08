"""Pin telegram-bridge/render/monitor.py — Phase J carve.

Two pure helpers, both worth pinning per branch:

- `short_id` — UX-relevant truncation that has been wrong before
  (`id[:8] + "..."` wrongly suggesting truncation that hadn't happened).
- `format_monitor_message` — every log_type branch matters. Quiet
  mode hides everything except `user`; verbose mode passes 6 distinct
  log types through with different prefixes/wrapping. A regression
  here changes what users see during a task.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MON_PATH = (
    Path(__file__).resolve().parent.parent / "telegram-bridge" / "render" / "monitor.py"
)


@pytest.fixture(scope="module")
def mon():
    spec = importlib.util.spec_from_file_location("bridge_render_monitor", _MON_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── short_id ────────────────────────────────────────────────────────


def test_short_id_empty(mon):
    assert mon.short_id("") == "없음"
    assert mon.short_id(None) == "없음"


def test_short_id_already_short(mon):
    """If ID is already <= max_len, no ellipsis is appended (the
    original UX-bug fix)."""
    assert mon.short_id("abc12345") == "abc12345"  # 8 chars at default max_len=12


def test_short_id_truncates_with_marker(mon):
    long_id = "abcdefghijklmnopqrstuvwxyz"
    out = mon.short_id(long_id, max_len=10)
    assert out == "abcdefghij..."
    assert out.endswith("...")


# ── format_monitor_message: quiet (default) ─────────────────────────


def test_quiet_user_passes_through(mon):
    assert mon.format_monitor_message("user", "X", "안녕") == "👤 사용자: 안녕"


def test_quiet_response_suppressed(mon):
    """Quiet mode: anything other than `user` returns None — task
    completion handler is the source of truth for the actual answer."""
    assert mon.format_monitor_message("response", "X", "answer") is None


def test_quiet_tool_suppressed(mon):
    assert mon.format_monitor_message("tool", "code_execution", "...") is None


def test_quiet_info_suppressed(mon):
    assert mon.format_monitor_message("info", "loading", "...") is None


# ── format_monitor_message: empty payload ───────────────────────────


def test_empty_payload_returns_none(mon):
    """No content AND no heading → skip entirely. Otherwise we'd send
    naked '👤 사용자: ' lines on stub events."""
    assert mon.format_monitor_message("user", "", "") is None
    assert mon.format_monitor_message("info", "", "", verbose=True) is None


# ── format_monitor_message: verbose ─────────────────────────────────


def test_verbose_response_with_robot_prefix(mon):
    out = mon.format_monitor_message("response", "h", "the answer", verbose=True)
    assert out is not None
    assert out.startswith("🤖 Agent Zero:")
    assert "the answer" in out


def test_verbose_response_truncates_at_2000(mon):
    big = "x" * 5000
    out = mon.format_monitor_message("response", "h", big, verbose=True)
    assert out is not None
    assert "...(생략)" in out
    # Original 5000 + truncation marker, but body itself capped at 2000.
    assert out.count("x") == 2000


def test_verbose_code_exe(mon):
    out = mon.format_monitor_message("code_exe", "exec", "print(1)", verbose=True)
    assert out is not None
    assert out.startswith("⚙️ 코드 실행: exec")
    assert "```" in out
    assert "print(1)" in out


def test_verbose_tool(mon):
    out = mon.format_monitor_message("tool", "browser", "url=...", verbose=True)
    assert out is not None
    assert out.startswith("🔧 도구: browser")
    assert "url=..." in out


def test_verbose_info(mon):
    out = mon.format_monitor_message("info", "ready", "starting", verbose=True)
    assert out is not None
    assert out.startswith("ℹ️ ready:")


def test_verbose_error(mon):
    out = mon.format_monitor_message("error", "fail", "stack", verbose=True)
    assert out is not None
    assert out.startswith("❌ 오류: fail")


def test_verbose_warning(mon):
    out = mon.format_monitor_message("warning", "slow", "took 30s", verbose=True)
    assert out is not None
    assert out.startswith("⚠️ 경고: slow")


def test_verbose_unknown_log_type(mon):
    """Unknown log_type → None (not a wildcard fall-through to noise)."""
    assert mon.format_monitor_message("weird_type", "h", "c", verbose=True) is None


def test_verbose_truncates_long_secondary_content(mon):
    """All the verbose branches except `response` cap secondary content at 500
    chars to keep the monitor feed tight."""
    big = "Q" * 1000  # non-overlapping with the heading word ("status")
    out = mon.format_monitor_message("info", "status", big, verbose=True)
    assert out is not None
    # The function slices content to first 500 chars.
    assert out.count("Q") == 500
    # And the total length is roughly heading + 500, not 1000.
    assert len(out) < 600
