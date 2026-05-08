"""Pin telegram-bridge/budget/core.py — Phase C carve.

What's pinned here:

1. Default shape (`_budget_default()`).
2. Threshold ladder is high-to-low (the iteration order is load-bearing
   in the engine's "fire highest crossed level only" logic).
3. `alert_key` cooldown formatter.
4. `format_alert` text rendering with each branch covered.
5. Round-trip: save → load preserves user limits + alerts_fired.
6. Load tolerates missing file, corrupt file, partial schema.
7. `_budget` dict identity survives `_load_budget()` (clear+update,
   not reassignment — the binding-stability invariant from Phase A/B).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_CORE_PATH = (
    Path(__file__).resolve().parent.parent / "telegram-bridge" / "budget" / "core.py"
)


def _load_budget_module(tmp_path):
    """Load `budget.core` with BUDGET_DIR pointed at a tmp dir so the
    on-disk JSON tests don't touch /app/data."""
    # Same trick as test_bridge_usage: put the package on sys.modules.
    pkg = types.ModuleType("bridge_budget")
    pkg.__path__ = [str(_CORE_PATH.parent)]  # type: ignore[attr-defined]
    sys.modules["bridge_budget"] = pkg

    spec = importlib.util.spec_from_file_location("bridge_budget.core", _CORE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_budget.core"] = mod
    spec.loader.exec_module(mod)

    # Redirect persistence to tmp.
    mod.BUDGET_DIR = str(tmp_path)
    mod.BUDGET_PATH = str(tmp_path / "budget.json")
    return mod


@pytest.fixture
def budget(tmp_path):
    mod = _load_budget_module(tmp_path)
    # Reset state between tests.
    mod._budget.clear()
    mod._budget.update(mod._budget_default())
    return mod


# ── pure helpers ────────────────────────────────────────────────────


def test_default_shape(budget):
    d = budget._budget_default()
    assert d == {"day_limit_usd": None, "week_limit_usd": None, "alerts_fired": {}}


def test_thresholds_high_to_low(budget):
    """Ladder iteration order is load-bearing: highest first so only
    the strongest crossed level fires. If someone sorts ascending by
    accident, a 90% spend would fire 80% before 100%, 100% before 150%."""
    thresholds = [t[0] for t in budget.BUDGET_THRESHOLDS]
    assert thresholds == sorted(thresholds, reverse=True)
    # And the human labels match the percentages.
    for thresh, _label, pct_label in budget.BUDGET_THRESHOLDS:
        assert pct_label == f"{int(thresh * 100)}%"


def test_alert_key_format(budget):
    assert budget.alert_key("day", "80%", "2026-05-08") == "2026-05-08:day:80%"
    assert budget.alert_key("week", "150%", "2026-W19") == "2026-W19:week:150%"


def test_format_alert_under_budget(budget):
    info = {
        "cost_usd": 4.50,
        "label": "오늘 (2026-05-08 KST)",
        "top_model": ("claude-sonnet-4-5", 4.20),
    }
    text = budget.format_alert("day", info, limit=5.0, level_label="⚠️ 주의", ratio=0.90)
    assert "⚠️ 주의" in text
    assert "일간 예산 90% 도달" in text
    assert "$4.5000 / $5.00" in text
    assert "남은 예산: $0.5000" in text
    assert "claude-sonnet-4-5" in text
    assert "초과:" not in text


def test_format_alert_over_budget(budget):
    info = {"cost_usd": 7.50, "label": "오늘", "top_model": None}
    text = budget.format_alert("day", info, limit=5.0, level_label="🚨 심각", ratio=1.50)
    assert "초과: $2.5000" in text
    assert "남은 예산:" not in text
    # No top_model → no "주요 소비 모델" line.
    assert "주요 소비 모델" not in text


def test_format_alert_week_label(budget):
    info = {"cost_usd": 25.0, "label": "최근 7일", "top_model": None}
    text = budget.format_alert("week", info, limit=30.0, level_label="❌ 초과", ratio=0.83)
    assert "주간" in text
    assert "일간" not in text


# ── persistence ──────────────────────────────────────────────────────


def test_save_then_load_round_trip(budget):
    budget._budget["day_limit_usd"] = 5.0
    budget._budget["week_limit_usd"] = 30.0
    budget._budget["alerts_fired"]["2026-05-08:day:80%"] = True

    budget._save_budget()
    # Confirm file written.
    raw = json.loads(Path(budget.BUDGET_PATH).read_text(encoding="utf-8"))
    assert raw["day_limit_usd"] == 5.0
    assert raw["alerts_fired"]["2026-05-08:day:80%"] is True

    # Wipe in-memory and reload.
    budget._budget.clear()
    budget._budget.update(budget._budget_default())
    budget._load_budget()
    assert budget._budget["day_limit_usd"] == 5.0
    assert budget._budget["week_limit_usd"] == 30.0
    assert budget._budget["alerts_fired"]["2026-05-08:day:80%"] is True


def test_load_missing_file_uses_defaults(budget):
    # No file written; load is a no-op except for state guarantee.
    budget._load_budget()
    assert budget._budget == budget._budget_default()


def test_load_corrupt_file_resets(budget):
    Path(budget.BUDGET_PATH).write_text("not-valid-json{", encoding="utf-8")
    # Pollute state to verify the reset.
    budget._budget["day_limit_usd"] = 999.0
    budget._load_budget()
    assert budget._budget == budget._budget_default()


def test_load_partial_schema_tolerated(budget):
    """Old/partial JSON missing a key shouldn't crash. Defaults fill in."""
    Path(budget.BUDGET_PATH).write_text(
        json.dumps({"day_limit_usd": 5.0}),  # missing week + alerts_fired
        encoding="utf-8",
    )
    budget._load_budget()
    assert budget._budget["day_limit_usd"] == 5.0
    assert budget._budget["week_limit_usd"] is None
    assert budget._budget["alerts_fired"] == {}


def test_load_alerts_fired_wrong_type_normalized(budget):
    """A pre-existing JSON with `alerts_fired: null` shouldn't leave the
    field non-dict — engine assumes dict semantics later."""
    Path(budget.BUDGET_PATH).write_text(
        json.dumps({"day_limit_usd": 5.0, "alerts_fired": None}),
        encoding="utf-8",
    )
    budget._load_budget()
    assert isinstance(budget._budget["alerts_fired"], dict)


def test_budget_dict_identity_preserved_through_load(budget):
    """The whole point of clear+update over reassignment — bot.py's
    `from budget.core import _budget` binding must reflect post-load
    contents, not the empty pre-load dict."""
    captured = budget._budget  # simulates the import binding
    budget._budget["day_limit_usd"] = 999.0  # would be wiped by reassignment
    budget._save_budget()
    budget._load_budget()
    # `captured` is still the same dict object, with the loaded values.
    assert captured is budget._budget
    assert captured["day_limit_usd"] == 999.0


def test_save_creates_dir(budget, tmp_path):
    """First save must create BUDGET_DIR if it doesn't exist."""
    nested = tmp_path / "nested" / "data"
    budget.BUDGET_DIR = str(nested)
    budget.BUDGET_PATH = str(nested / "budget.json")
    assert not nested.exists()
    budget._budget["day_limit_usd"] = 1.0
    budget._save_budget()
    assert (nested / "budget.json").exists()
