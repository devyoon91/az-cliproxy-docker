"""`/usage`, `/budget`, `/pricing` Telegram commands.

Phase N carve from bot.py (issue #79). All three are cost-reporting
commands and their dependencies (pricing.usage, budget.core,
budget.engine, pricing.snapshot, notify.telegram) all live in carved
modules already, so this is pure code motion.

The trio shares the same chat-ID guard and `effective_message`
edit-aware reply pattern (`/today`-style), so they live together
under `cost.py`. Future cmd handler groups (system, chats, monitor
toggles, …) get their own files in this package.
"""
from __future__ import annotations

import os

from budget.core import _budget, _save_budget
from budget.engine import _budget_check_window, _compute_window_cost
from notify.telegram import send_telegram
from pricing.snapshot import (
    _diff_snapshots,
    _format_pricing_diff,
    _list_snapshots,
    _load_snapshot,
)
from pricing.snapshot import take_pricing_snapshot as _take_pricing_snapshot_pure
from pricing.usage import usage_history, usage_today
from telegram import Update
from telegram.ext import ContextTypes

# Read CHAT_ID directly from env — same source bot.py uses.
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Budget management for spend alerts (issue #19).

    Usage:
      /budget                        — same as /budget show
      /budget show                   — current limits + today's progress
      /budget day <USD>              — set daily limit, e.g. /budget day 5
      /budget week <USD>             — set weekly limit
      /budget day off / week off     — clear a limit
      /budget reset                  — clear all alert cooldowns (re-arm)
    """
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return

    args = list(context.args or [])
    sub = (args[0].lower() if args else "show")

    # Default / show: render limits + today's spend ratio per window.
    if sub in ("", "show", "status"):
        lines = ["💰 예산 설정"]
        for window, label in (("day", "일간"), ("week", "주간")):
            limit = _budget.get(f"{window}_limit_usd")
            info = _compute_window_cost(window)
            cost = info["cost_usd"]
            if limit and limit > 0:
                ratio = (cost / limit) if limit else 0
                pct = ratio * 100
                bar_full = int(min(ratio, 1.5) * 10)  # cap visual at 150%
                bar = "█" * min(bar_full, 10) + "░" * max(0, 10 - bar_full)
                # Mark over-budget visually past 100%.
                marker = (
                    "🚨" if ratio >= 1.5
                    else ("❌" if ratio >= 1.0
                          else ("⚠️" if ratio >= 0.8 else "✅"))
                )
                lines.append(
                    f"  {marker} {label}: ${cost:.4f} / ${limit:.2f} ({pct:.0f}%)\n"
                    f"     [{bar}]"
                )
            else:
                lines.append(f"  ⚪ {label}: 한도 미설정 (현재 ${cost:.4f})")
        lines.append("")
        lines.append("설정: /budget day 5  ·  /budget week 30  ·  /budget reset")
        await msg.reply_text("\n".join(lines))
        return

    if sub == "reset":
        _budget["alerts_fired"] = {}
        _save_budget()
        await msg.reply_text("✅ 알림 쿨다운 초기화. 다음 임계 도달 시 다시 발송됩니다.")
        return

    if sub in ("day", "week"):
        if len(args) < 2:
            await msg.reply_text(
                f"사용법: /budget {sub} <USD>  · 예: /budget {sub} 5  ·  /budget {sub} off"
            )
            return
        val = args[1].lower()
        key = f"{sub}_limit_usd"
        if val in ("off", "clear", "0", "none"):
            _budget[key] = None
            # Drop fired keys for this window so re-enabling doesn't suppress
            # a legitimate first alert.
            _budget["alerts_fired"] = {
                k: v for k, v in (_budget.get("alerts_fired") or {}).items()
                if f":{sub}:" not in k
            }
            _save_budget()
            await msg.reply_text(f"✅ {sub} 한도 해제됨.")
            return
        try:
            amount = float(val.replace("$", "").replace(",", ""))
            if amount <= 0:
                raise ValueError("must be positive")
        except ValueError:
            await msg.reply_text(f"⚠️ 숫자가 아님: {args[1]!r}. 예: /budget {sub} 5")
            return
        _budget[key] = amount
        # Clear this window's fired keys so the new limit gets evaluated cleanly.
        _budget["alerts_fired"] = {
            k: v for k, v in (_budget.get("alerts_fired") or {}).items()
            if f":{sub}:" not in k
        }
        _save_budget()
        # Immediately evaluate so the user gets feedback if already over.
        await _budget_check_window(sub)
        await msg.reply_text(
            f"✅ {sub} 한도 ${amount:.2f} 설정됨.\n"
            f"   80%/100%/150% 도달 시 알림 (각 단계 1회 / 일)."
        )
        return

    await msg.reply_text(
        "사용법:\n"
        "  /budget                — 현황\n"
        "  /budget day 5          — 일간 $5 한도\n"
        "  /budget week 30        — 주간 $30 한도\n"
        "  /budget day off        — 해제\n"
        "  /budget reset          — 쿨다운 초기화"
    )


async def cmd_pricing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """LiteLLM pricing snapshot inspection / on-demand refresh (issue #21).

    Usage:
      /pricing                — show latest snapshot summary
      /pricing list           — list available snapshot dates
      /pricing snapshot       — force a fresh snapshot now
      /pricing diff           — diff latest two snapshots (no alert)
    """
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return

    args = list(context.args or [])
    sub = args[0].lower() if args else "show"

    if sub == "list":
        snaps = _list_snapshots()
        if not snaps:
            await msg.reply_text(
                "📂 저장된 가격 스냅샷이 없습니다. /pricing snapshot 으로 강제 생성 가능."
            )
            return
        # Show up to 14 most recent so the message stays readable.
        head = snaps[:14]
        lines = [f"📂 가격 스냅샷 ({len(snaps)}개, 최신 {len(head)}개 표시)"]
        for d in head:
            data = _load_snapshot(d) or {}
            n = len(data.get("models") or {})
            lines.append(f"  {d}: {n} 모델")
        await msg.reply_text("\n".join(lines))
        return

    if sub == "snapshot":
        await msg.reply_text("⏳ 가격 스냅샷 생성 중…")
        # `pricing.snapshot.take_pricing_snapshot` accepts an explicit
        # `send_alert` callback (decoupled from telegram) — wire send_telegram
        # in here so /pricing snapshot triggers the same drift-alert path
        # that `daily_pricing_snapshot` does.
        result = await _take_pricing_snapshot_pure(
            force=True, alert=True, send_alert=send_telegram,
        )
        if result is None:
            await msg.reply_text("❌ 스냅샷 실패 — 로그 확인. (HTTP 또는 관심 모델 부재)")
            return
        n = len(result.get("models") or {})
        await msg.reply_text(f"✅ 스냅샷 저장: {result['snapshot_date']} ({n} 모델)")
        return

    if sub == "diff":
        snaps = _list_snapshots()
        if len(snaps) < 2:
            await msg.reply_text("⚠️ 비교할 스냅샷이 부족합니다 (2개 이상 필요).")
            return
        curr_date = snaps[0]
        prev_date = snaps[1]
        curr = _load_snapshot(curr_date) or {}
        prev = _load_snapshot(prev_date) or {}
        changes = _diff_snapshots(prev, curr)
        if not changes:
            await msg.reply_text(f"✅ 변동 없음 ({prev_date} → {curr_date})")
            return
        await msg.reply_text(_format_pricing_diff(changes, prev_date, curr_date))
        return

    # Default: show
    snaps = _list_snapshots()
    if not snaps:
        await msg.reply_text(
            "📂 저장된 스냅샷 없음.\n"
            "  /pricing snapshot — 지금 강제 생성\n"
            "  (자동 일정: 매일 00:30 KST)"
        )
        return
    latest_date = snaps[0]
    data = _load_snapshot(latest_date) or {}
    models = data.get("models") or {}
    lines = [
        f"💱 최신 스냅샷: {latest_date}",
        f"   {len(models)} 모델 · 다음 자동 실행: 00:30 KST",
        "",
    ]
    for key in sorted(models.keys()):
        info = models[key]
        alias = (info.get("az_aliases") or [key])[0]
        in_rate = info.get("input_cost_per_token") or 0
        out_rate = info.get("output_cost_per_token") or 0
        cr_rate = info.get("cache_read_input_token_cost") or 0
        # Per-1M tokens for human readability.
        cr_part = (
            f" · cache_read ${cr_rate * 1e6:.2f}/1M" if cr_rate else ""
        )
        lines.append(
            f"  {alias}\n"
            f"    in ${in_rate * 1e6:.2f}/1M · out ${out_rate * 1e6:.2f}/1M{cr_part}"
        )
    lines.append("")
    lines.append("/pricing list  · /pricing diff  · /pricing snapshot")
    await msg.reply_text("\n".join(lines))


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """토큰 사용량 + 비용 조회 (cache 토큰 포함)"""
    if update.effective_chat.id != CHAT_ID:
        return

    today = usage_today
    cache_read = today.get("cache_read_tokens", 0)
    cache_create = today.get("cache_creation_tokens", 0)
    reasoning = today.get("reasoning_tokens", 0)

    # 캐시 절약 추정치: cache_read 만큼은 90% 할인된다고 가정 (Anthropic)
    # 실절약 = cache_read × (input_rate - cache_read_rate) → 대략 input × 0.9
    # 여기선 단순히 "정가라면 얼마였을지"만 보여준다.
    lines = [
        f"📊 오늘의 사용량 ({today['date']})\n",
        f"요청: {today['requests']}건",
        f"입력: {today['input_tokens']:,}  |  출력: {today['output_tokens']:,}",
    ]
    if cache_read or cache_create:
        lines.append(
            f"캐시: read {cache_read:,}  |  create {cache_create:,}"
        )
    if reasoning:
        # Reasoning / extended-thinking tokens (Claude 4.x, OpenAI o-series).
        # Billed at output rate — already folded into cost_usd; shown
        # separately so users can see how much thinking actually happened.
        lines.append(f"사고 토큰: {reasoning:,} (출력 요율 청구)")
    lines.append(f"비용: ${today['cost_usd']:.4f}")

    by_model = today.get("by_model", {})
    if by_model:
        lines.append("\n🤖 모델별:")
        for model, stats in sorted(
            by_model.items(), key=lambda x: x[1]["cost_usd"], reverse=True
        ):
            cr = stats.get("cache_read_tokens", 0)
            cc = stats.get("cache_creation_tokens", 0)
            rt = stats.get("reasoning_tokens", 0)
            cache_part = f" | cache r:{cr:,} c:{cc:,}" if (cr or cc) else ""
            reason_part = f" | reasoning:{rt:,}" if rt else ""
            lines.append(
                f"  {model}\n"
                f"    {stats['requests']}건 | "
                f"in:{stats['input_tokens']:,} out:{stats['output_tokens']:,}"
                f"{cache_part}{reason_part}\n"
                f"    ${stats['cost_usd']:.4f}"
            )

    if usage_history:
        lines.append("\n📈 최근 7일:")
        total_cost = 0.0
        for day in reversed(usage_history[-7:]):
            lines.append(f"  {day['date']}: {day['requests']}건, ${day['cost_usd']:.4f}")
            total_cost += day["cost_usd"]
        total_cost += today["cost_usd"]
        lines.append(f"\n💰 7일+오늘 누적: ${total_cost:.4f}")

    await update.message.reply_text("\n".join(lines))
