"""LiteLLM price-table daily snapshot + drift detection (M5-C · issue #21).

Phase G carve from bot.py (issue #79). Daily snapshot of the LiteLLM
price table for models we've actually called in the past 7 days.
Diffs against the previous snapshot and surfaces real $-rate changes
to Telegram. Stored as one JSON per day under `/app/data/pricing/`,
rotated at 30 days.

Dependency injection for the telegram alert:
`take_pricing_snapshot()` accepts a `send_alert` callable instead of
reaching for `send_telegram` directly. Keeps the module free of
telegram / aiohttp-session imports beyond the LiteLLM HTTP fetch and
makes the orchestrator unit-testable. bot.py's caller passes
`send_alert=send_telegram` to wire it back up.

What stays in bot.py:
- `daily_pricing_snapshot()` — the 24h scheduler loop. Lives with the
  other background loops (hourly_budget_sweep, daily_usage_reporter)
  in bot.py since it owns the send_telegram wiring.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

import aiohttp
from budget.core import BUDGET_DIR
from task_agg.agg import _filter_date_range, _kst_now, _load_task_jsons

from pricing.cost import _model_cost_map

logger = logging.getLogger(__name__)


PRICING_DIR = os.path.join(BUDGET_DIR, "pricing")
PRICING_RETENTION_DAYS = 30
LITELLM_PRICE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
# Fields whose change we surface in the alert. context_window etc. change
# silently — we only care about $ that flows through `compute_cost`.
PRICING_DIFF_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
)


def _resolve_litellm_key(model: str) -> str | None:
    """Resolve an AZ-side model name to its canonical LiteLLM key.

    Mirrors `pricing.cost._model_info`'s lookup order but returns the
    KEY (not the rates) so we can store snapshots under one stable
    identifier even when AZ sends multiple aliases (e.g. with vs
    without the `anthropic/` prefix).
    """
    if not isinstance(model, str) or not model:
        return None
    if model in _model_cost_map:
        return model
    aliases = {
        "anthropic/claude-sonnet-4-6": "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6": "claude-sonnet-4-5-20250929",
        "anthropic/claude-haiku-4-5": "claude-haiku-4-5-20251001",
        "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    }
    if model in aliases and aliases[model] in _model_cost_map:
        return aliases[model]
    if model.startswith("anthropic/"):
        tail = model.split("/", 1)[1]
        if tail in _model_cost_map:
            return tail
    return None


def _interested_models(window_days: int = 7) -> dict[str, list[str]]:
    """Models actually called in the last `window_days` days.

    Returns `{litellm_key: [az_aliases_seen]}` so the drift alert can show
    the user-recognizable AZ name alongside the canonical LiteLLM key.
    Models that don't resolve to a LiteLLM key are skipped — they
    wouldn't be priced anyway.
    """
    now = _kst_now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=window_days - 1)
    end = today + timedelta(days=1)
    tasks = _filter_date_range(_load_task_jsons(), start, end)
    seen: dict[str, set[str]] = {}
    for t in tasks:
        for c in t.get("llm_calls") or []:
            az_name = c.get("model")
            key = _resolve_litellm_key(az_name) if az_name else None
            if not key:
                continue
            seen.setdefault(key, set()).add(az_name)
    return {k: sorted(v) for k, v in seen.items()}


async def _fetch_litellm_table() -> dict | None:
    """Async fetch of the live LiteLLM price table. None on failure —
    caller falls back to the in-memory `_model_cost_map` (loaded at
    startup) so a transient HTTP failure still produces a snapshot per
    #21's "원격 HTTP 실패 시 지난 스냅샷으로 fallback" criterion."""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(LITELLM_PRICE_URL) as resp:
                if resp.status != 200:
                    logger.warning(f"[pricing] fetch HTTP {resp.status}")
                    return None
                return await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"[pricing] fetch failed: {e}")
        return None


def _select_for_snapshot(table: dict, interested: dict[str, list[str]]) -> dict:
    """Filter the full LiteLLM table down to interested models + the
    cost fields we track. Drops irrelevant metadata so each daily
    snapshot is small (~1KB for typical 4-model usage)."""
    out = {}
    for key, aliases in interested.items():
        info = table.get(key)
        if not info:
            continue
        rates = {f: info.get(f) for f in PRICING_DIFF_FIELDS if info.get(f) is not None}
        if not rates:
            continue
        rates["az_aliases"] = aliases
        out[key] = rates
    return out


def _snapshot_path(date_str: str) -> str:
    return os.path.join(PRICING_DIR, f"{date_str}.json")


def _save_snapshot(date_str: str, models: dict) -> None:
    """Atomic write — never leave a half-flushed file behind."""
    try:
        os.makedirs(PRICING_DIR, exist_ok=True)
        path = _snapshot_path(date_str)
        payload = {
            "snapshot_date": date_str,
            "fetched_at": _kst_now().isoformat(),
            "source_url": LITELLM_PRICE_URL,
            "models": models,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        logger.info(f"[pricing] saved snapshot {date_str} ({len(models)} models)")
    except Exception as e:
        logger.warning(f"[pricing] save failed: {e}")


def _load_snapshot(date_str: str) -> dict | None:
    path = _snapshot_path(date_str)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[pricing] load {date_str} failed: {e}")
        return None


def _list_snapshots() -> list[str]:
    """Return snapshot date strings (YYYY-MM-DD), most recent first."""
    if not os.path.isdir(PRICING_DIR):
        return []
    out = []
    for name in os.listdir(PRICING_DIR):
        if name.endswith(".json") and not name.endswith(".tmp"):
            out.append(name[:-5])  # strip ".json"
    out.sort(reverse=True)
    return out


def _previous_snapshot(before: str) -> tuple[str, dict] | None:
    """Find the most recent snapshot strictly before `before`. Used as
    the diff baseline — we want yesterday's data, not today's."""
    for date_str in _list_snapshots():
        if date_str < before:
            data = _load_snapshot(date_str)
            if data:
                return date_str, data
    return None


def _diff_snapshots(prev: dict, curr: dict) -> list[dict]:
    """Compare model rates between two snapshots.

    Returns change records `{model, alias, field, before, after, pct_change}`.

    A field counts as changed only when both sides have a value AND
    they differ — newly-added or newly-removed fields are reported via
    `before=None` / `after=None` so the user notices schema additions.
    """
    prev_models = (prev or {}).get("models") or {}
    curr_models = (curr or {}).get("models") or {}
    keys = set(prev_models) | set(curr_models)
    changes = []
    for key in sorted(keys):
        p = prev_models.get(key) or {}
        c = curr_models.get(key) or {}
        alias = (c.get("az_aliases") or p.get("az_aliases") or [key])[0]
        for field in PRICING_DIFF_FIELDS:
            pv = p.get(field)
            cv = c.get(field)
            if pv == cv:
                continue
            pct = None
            if isinstance(pv, int | float) and pv > 0 and isinstance(cv, int | float):
                pct = (cv - pv) / pv * 100.0
            changes.append({
                "model": key,
                "alias": alias,
                "field": field,
                "before": pv,
                "after": cv,
                "pct_change": pct,
            })
    return changes


def _format_pricing_diff(changes: list[dict], prev_date: str, curr_date: str) -> str:
    """One alert message covering all detected changes.

    Shape:
      💱 가격 변동 감지 (2026-04-27 → 2026-04-28)
        claude-sonnet-4-5-20250929 (claude-sonnet-4-6)
          input_cost_per_token: $3.00 → $2.50 / 1M (-16.7%)
    """
    def fmt_rate(v) -> str:
        if v is None:
            return "—"
        # All cost-per-token rates are in the e-7 to e-5 range; per-1M is
        # easier to eyeball. Two decimals catch sub-cent changes.
        return f"${v * 1_000_000:.2f}/1M"

    by_model: dict[str, list[dict]] = {}
    for ch in changes:
        by_model.setdefault(ch["model"], []).append(ch)

    lines = [f"💱 가격 변동 감지 ({prev_date} → {curr_date})"]
    for model in sorted(by_model.keys()):
        rows = by_model[model]
        alias = rows[0].get("alias") or model
        header = f"  {model}" + (f"  ({alias})" if alias != model else "")
        lines.append(header)
        for ch in rows:
            arrow = f"{fmt_rate(ch['before'])} → {fmt_rate(ch['after'])}"
            if ch["pct_change"] is not None:
                sign = "+" if ch["pct_change"] >= 0 else ""
                arrow += f"  ({sign}{ch['pct_change']:.1f}%)"
            lines.append(f"    {ch['field']}: {arrow}")
    return "\n".join(lines)


def _rotate_pricing_snapshots(keep_days: int = PRICING_RETENTION_DAYS) -> int:
    """Delete snapshot files older than `keep_days`. Returns count removed.

    Old snapshots are useful for archeology but we don't need 6 months
    of them locally — the GitHub source is authoritative for historical
    queries. 30 days covers "did the Sonnet rate change last week?"
    """
    if not os.path.isdir(PRICING_DIR):
        return 0
    today = _kst_now().date()
    removed = 0
    for date_str in _list_snapshots():
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue  # un-dated file, leave alone
        if (today - d).days > keep_days:
            try:
                os.remove(_snapshot_path(date_str))
                removed += 1
            except OSError as e:
                logger.warning(f"[pricing] rotate {date_str} failed: {e}")
    if removed:
        logger.info(f"[pricing] rotated {removed} snapshot(s) older than {keep_days}d")
    return removed


async def take_pricing_snapshot(
    *,
    force: bool = False,
    alert: bool = True,
    send_alert=None,
) -> dict | None:
    """Fetch fresh prices, save today's snapshot, diff vs previous, alert.

    Args:
        force: overwrite today's snapshot if it already exists. Used by
            `/pricing snapshot` when the user wants to re-poll right now.
        alert: send a telegram message when changes are detected.
        send_alert: async callable `(msg: str) -> None`. bot.py passes
            `send_telegram` here so this module stays import-clean of
            the telegram client. None disables alerts (smoke tests).

    Returns the saved snapshot dict on success, None on failure.
    """
    today = _kst_now().strftime("%Y-%m-%d")
    if not force and _load_snapshot(today) is not None:
        logger.info(f"[pricing] today's snapshot {today} already exists, skipping")
        return _load_snapshot(today)

    table = await _fetch_litellm_table()
    if table is None:
        # Fallback: in-memory table loaded at startup. Better an old
        # snapshot than no snapshot — matches issue #21's resilience
        # criterion.
        if _model_cost_map:
            logger.info("[pricing] using in-memory startup table as fallback")
            table = _model_cost_map
        else:
            logger.warning("[pricing] no table available — skipping snapshot")
            return None

    interested = _interested_models()
    if not interested:
        logger.info("[pricing] no interested models in window — skipping")
        return None

    selected = _select_for_snapshot(table, interested)
    _save_snapshot(today, selected)
    payload = _load_snapshot(today)

    if alert and send_alert is not None:
        prev = _previous_snapshot(before=today)
        if prev:
            prev_date, prev_data = prev
            changes = _diff_snapshots(prev_data, payload)
            if changes:
                msg = _format_pricing_diff(changes, prev_date, today)
                try:
                    await send_alert(msg)
                    logger.info(f"[pricing] sent drift alert: {len(changes)} changes")
                except Exception as e:
                    logger.warning(f"[pricing] alert send failed: {e}")
            else:
                logger.info(f"[pricing] no changes vs {prev_date}")
        else:
            logger.info("[pricing] first snapshot — no baseline for diff")

    _rotate_pricing_snapshots()
    return payload
