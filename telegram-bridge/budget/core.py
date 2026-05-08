"""Budget state + persistence + pure formatters.

Phase C carve-out from bot.py (issue #79). The async budget engine
(`_budget_check_window`, `hourly_budget_sweep`) stays in bot.py for now
because it depends on `send_telegram()` and `_compute_window_cost()`
(task-JSON aggregation, Phase D's territory).

Binding stability:
The `_budget` state dict is mutated in place (`clear()` + `update()`)
in `_load_budget()` instead of being reassigned, so a `from budget.core
import _budget` binding in bot.py keeps pointing at the right object
across reloads. Same pattern as `pricing.cost._model_cost_map` and
`pricing.usage.usage_today`.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


BUDGET_DIR = "/app/data"
BUDGET_PATH = os.path.join(BUDGET_DIR, "budget.json")

# Threshold ladder. Order matters — `_budget_check_window` iterates
# highest first so the strongest crossed level fires (and the lower ones
# are recorded as already-fired so they don't follow-up next call).
BUDGET_THRESHOLDS = [
    (1.50, "🚨 심각", "150%"),
    (1.00, "❌ 초과", "100%"),
    (0.80, "⚠️ 주의", "80%"),
]


def _budget_default() -> dict:
    """Empty budget shape. `alerts_fired` keys self-rotate by date —
    yesterday's keys are simply never queried again."""
    return {
        "day_limit_usd": None,
        "week_limit_usd": None,
        "alerts_fired": {},  # "YYYY-MM-DD:day:80" -> True (presence = fired)
    }


# Module-level state. Mutated in place (clear+update) — see file docstring.
_budget: dict = _budget_default()


def _load_budget() -> None:
    """Read budget.json once at startup. Missing file is fine — defaults stand.
    Corrupt file logs once and resets to defaults so the bot keeps running.
    """
    try:
        if os.path.isfile(BUDGET_PATH):
            with open(BUDGET_PATH, encoding="utf-8") as f:
                data = json.load(f)
            # Merge into defaults to tolerate older/partial schemas.
            base = _budget_default()
            base.update({k: v for k, v in data.items() if k in base})
            if not isinstance(base.get("alerts_fired"), dict):
                base["alerts_fired"] = {}
            _budget.clear()
            _budget.update(base)
            logger.info(
                f"[budget] loaded day=${_budget['day_limit_usd']} "
                f"week=${_budget['week_limit_usd']}"
            )
        else:
            logger.info("[budget] no saved budget — using defaults (no limits)")
    except Exception as e:
        logger.warning(f"[budget] load failed, using defaults: {e}")
        _budget.clear()
        _budget.update(_budget_default())


def _save_budget() -> None:
    """Best-effort write. Never propagate exceptions — a budget save failure
    must not crash a /budget command or the hourly sweep.
    """
    try:
        os.makedirs(BUDGET_DIR, exist_ok=True)
        tmp = BUDGET_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_budget, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BUDGET_PATH)
    except Exception as e:
        logger.warning(f"[budget] save failed: {e}")


def alert_key(window: str, threshold_pct: str, period_id: str) -> str:
    """Build the cooldown key for one (window, threshold, period).

    `period_id` is "YYYY-MM-DD" for day, "YYYY-Www" for week — naturally
    self-rotating, so old entries never fire again. Renamed from the
    private `_alert_key` to mark it part of the public module API.
    """
    return f"{period_id}:{window}:{threshold_pct}"


def format_alert(
    window: str,
    info: dict,
    limit: float,
    level_label: str,
    ratio: float,
) -> str:
    """Render the threshold-crossed alert text. Spec from issue #19."""
    cost = info["cost_usd"]
    remaining = limit - cost
    pct = ratio * 100
    period_word = "일간" if window == "day" else "주간"
    lines = [
        f"{level_label} {period_word} 예산 {pct:.0f}% 도달",
        f"{info['label']}",
        f"비용: ${cost:.4f} / ${limit:.2f} 한도",
    ]
    if remaining >= 0:
        lines.append(f"남은 예산: ${remaining:.4f}")
    else:
        lines.append(f"초과: ${abs(remaining):.4f}")
    if info.get("top_model"):
        m, mc = info["top_model"]
        lines.append(f"주요 소비 모델: {m} (${mc:.4f})")
    return "\n".join(lines)
