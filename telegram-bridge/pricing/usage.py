"""Per-day usage accumulator + 7-day history rotation.

Phase B carve-out from bot.py (issue #79). Owns:

- `usage_today` — current day's totals (KST). Cleared + updated in place
  on date rollover, never reassigned, so bot.py and the dashboard can
  hold stable `from pricing.usage import usage_today` bindings.
- `usage_history` — last 7 days. Mutated in place (append + bounded
  pop), same binding-stability story.
- `track_usage(...)` — single entry point for the `/track` webhook.
  Calls `pricing.cost.calc_cost` so cost numbers stay in lockstep with
  agent-zero/lib/pricing.py:compute_cost.

Why the binding-stability dance: agent-zero/lib/pricing's price-table
cache hit the same trap in Phase A — naive `usage_today = {...}`
re-assignment after `from x import usage_today` would leave bot.py
pointing at the pre-rollover bucket forever.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .cost import _normalize_model, calc_cost

KST = timezone(timedelta(hours=9))


def _kst_now() -> datetime:
    return datetime.now(KST)


def _empty_today_bucket(date_str: str) -> dict:
    return {
        "date": date_str,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "requests": 0,
        "cost_usd": 0.0,
        "by_model": {},  # model_name -> _empty_model_bucket()
    }


def _empty_model_bucket() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "requests": 0,
        "cost_usd": 0.0,
    }


# Module-level state. Importers see these bindings; helpers below
# mutate the dict / list in place rather than rebinding.
usage_today: dict = _empty_today_bucket(_kst_now().strftime("%Y-%m-%d"))
usage_history: list = []


def _rotate_if_new_day(today_str: str) -> None:
    """If `usage_today` is for an older date, archive it (when there was
    actual activity) and reset in place to a fresh bucket for `today_str`.
    No-op when already on `today_str`.
    """
    if usage_today["date"] == today_str:
        return
    if usage_today["requests"] > 0:
        # Snapshot — `dict(...)` copies the top-level fields and the
        # `by_model` reference. Good enough for read-only history.
        usage_history.append(dict(usage_today))
        while len(usage_history) > 7:
            usage_history.pop(0)
    usage_today.clear()
    usage_today.update(_empty_today_bucket(today_str))


def build_daily_report_lines(today_bucket: dict, yesterday_str: str) -> list[str] | None:
    """Render the lines for the daily usage report — yesterday's totals only.

    Returns None if there is nothing to send:
    - `today_bucket["date"]` does not match `yesterday_str` (stale bucket
      from an earlier active day — idle days must stay silent so the
      reporter doesn't re-send the same numbers night after night), or
    - `today_bucket["requests"] <= 0` (the bucket is for yesterday but
      nothing was logged).

    Caller passes `yesterday_str` because the time source belongs to the
    reporter — keeping this helper a pure function is what makes it
    testable without async / scheduler machinery. See issue #131.
    """
    if today_bucket["date"] != yesterday_str:
        return None
    if today_bucket["requests"] <= 0:
        return None

    lines = [
        f"📊 일일 사용량 리포트 ({today_bucket['date']})\n",
        f"총 요청: {today_bucket['requests']}건",
        f"총 입력: {today_bucket['input_tokens']:,} 토큰",
        f"총 출력: {today_bucket['output_tokens']:,} 토큰",
        f"총 비용: ${today_bucket['cost_usd']:.4f}",
    ]
    by_model = today_bucket.get("by_model", {})
    if by_model:
        lines.append("\n🤖 모델별 내역:")
        for model, stats in sorted(
            by_model.items(), key=lambda x: x[1]["cost_usd"], reverse=True
        ):
            lines.append(
                f"  {model}\n"
                f"    {stats['requests']}건 | "
                f"in:{stats['input_tokens']:,} out:{stats['output_tokens']:,} | "
                f"${stats['cost_usd']:.4f}"
            )
    return lines


def track_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> None:
    """Accumulate one /track event.

    `reasoning_tokens` (Claude 4.x 확장 사고, OpenAI o-series) are kept
    in their own bucket for `/usage` display, but `calc_cost` bills them
    at the OUTPUT rate per Anthropic's schedule — see #64.
    """
    model = _normalize_model(model)
    _rotate_if_new_day(_kst_now().strftime("%Y-%m-%d"))

    cost = calc_cost(
        model, input_tokens, output_tokens,
        cache_read_tokens, cache_creation_tokens, reasoning_tokens,
    )

    usage_today["input_tokens"] += input_tokens
    usage_today["output_tokens"] += output_tokens
    usage_today["cache_read_tokens"] += cache_read_tokens
    usage_today["cache_creation_tokens"] += cache_creation_tokens
    usage_today["reasoning_tokens"] += reasoning_tokens
    usage_today["requests"] += 1
    usage_today["cost_usd"] += cost

    by_model = usage_today["by_model"]
    if model not in by_model:
        by_model[model] = _empty_model_bucket()
    m = by_model[model]
    m["input_tokens"] += input_tokens
    m["output_tokens"] += output_tokens
    m["cache_read_tokens"] += cache_read_tokens
    m["cache_creation_tokens"] += cache_creation_tokens
    m["reasoning_tokens"] += reasoning_tokens
    m["requests"] += 1
    m["cost_usd"] += cost
