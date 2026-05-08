"""Pin telegram-bridge/telegram_handlers/today.py — Phase I carve.

Async cmd handlers (`cmd_today`, `cmd_week`, `cmd_tasks`) need a real
python-telegram-bot mock harness to test meaningfully — out of scope.
The pure helper `_parse_by_flag` is testable on its own and that's
the regression risk worth pinning (it's the user-visible argument
surface for /today and /week).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent / "telegram-bridge"


def _load_today_module():
    """Spec-load `telegram_handlers.today` after wiring task_agg + a
    fake `telegram` namespace package. The real `telegram` library
    isn't installed on the test host."""
    # Set CHAT_ID for the import-time `int(os.environ[...])` read.
    os.environ.setdefault("TELEGRAM_CHAT_ID", "999")

    # Fake `telegram` + `telegram.ext` so the imports succeed.
    if "telegram" not in sys.modules:
        telegram_pkg = types.ModuleType("telegram")
        telegram_pkg.Update = object  # type: ignore[attr-defined]
        sys.modules["telegram"] = telegram_pkg
        ext = types.ModuleType("telegram.ext")

        class _CTX:
            DEFAULT_TYPE = object

        ext.ContextTypes = _CTX  # type: ignore[attr-defined]
        sys.modules["telegram.ext"] = ext

    # Wire pricing.cost (transitive dep of task_agg.agg).
    pricing_pkg = types.ModuleType("pricing")
    pricing_pkg.__path__ = [str(_ROOT / "pricing")]  # type: ignore[attr-defined]
    sys.modules["pricing"] = pricing_pkg
    cost_spec = importlib.util.spec_from_file_location(
        "pricing.cost", _ROOT / "pricing" / "cost.py"
    )
    assert cost_spec and cost_spec.loader
    cost_mod = importlib.util.module_from_spec(cost_spec)
    sys.modules["pricing.cost"] = cost_mod
    cost_spec.loader.exec_module(cost_mod)

    # Wire task_agg.agg.
    tasks_pkg = types.ModuleType("task_agg")
    tasks_pkg.__path__ = [str(_ROOT / "task_agg")]  # type: ignore[attr-defined]
    sys.modules["task_agg"] = tasks_pkg
    agg_spec = importlib.util.spec_from_file_location(
        "task_agg.agg", _ROOT / "task_agg" / "agg.py"
    )
    assert agg_spec and agg_spec.loader
    agg_mod = importlib.util.module_from_spec(agg_spec)
    sys.modules["task_agg.agg"] = agg_mod
    agg_spec.loader.exec_module(agg_mod)

    # Now the handler module.
    handlers_pkg = types.ModuleType("telegram_handlers")
    handlers_pkg.__path__ = [str(_ROOT / "telegram_handlers")]  # type: ignore[attr-defined]
    sys.modules["telegram_handlers"] = handlers_pkg

    spec = importlib.util.spec_from_file_location(
        "telegram_handlers.today", _ROOT / "telegram_handlers" / "today.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["telegram_handlers.today"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def today():
    return _load_today_module()


def test_parse_by_flag_none_when_no_args(today):
    assert today._parse_by_flag([]) is None
    assert today._parse_by_flag(None) is None


def test_parse_by_flag_model_variants(today):
    assert today._parse_by_flag(["by:model"]) == "model"
    assert today._parse_by_flag(["model"]) == "model"
    assert today._parse_by_flag(["by:models"]) == "model"
    assert today._parse_by_flag(["models"]) == "model"


def test_parse_by_flag_profile_variants(today):
    assert today._parse_by_flag(["by:profile"]) == "profile"
    assert today._parse_by_flag(["profile"]) == "profile"
    assert today._parse_by_flag(["by:profiles"]) == "profile"
    assert today._parse_by_flag(["profiles"]) == "profile"


def test_parse_by_flag_case_insensitive(today):
    assert today._parse_by_flag(["BY:MODEL"]) == "model"
    assert today._parse_by_flag(["By:Profile"]) == "profile"
    assert today._parse_by_flag(["MODEL"]) == "model"


def test_parse_by_flag_unknown_returns_none(today):
    """Unknown keys must NOT raise — graceful fallback to default view."""
    assert today._parse_by_flag(["something_random"]) is None
    assert today._parse_by_flag(["by:something"]) is None
    assert today._parse_by_flag(["task"]) is None


def test_chat_id_read_from_env(today):
    """CHAT_ID is read from TELEGRAM_CHAT_ID at module import; the
    test fixture sets it to 999 before importing."""
    assert today.CHAT_ID == 999
