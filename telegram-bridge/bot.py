"""
Telegram ↔ Agent Zero Bridge Bot
- Agent Zero 응답을 Telegram으로 전달 (알림)
- Telegram 메시지를 Agent Zero에 전달 (양방향 지시)
- Agent Zero 웹 채팅 모니터링 → Telegram 실시간 알림
- 멀티채팅 지원: 채팅 목록 조회, 전환, 자동 추적
- 토큰 사용량 추적 및 일일 리포트
"""

import os
import asyncio
import logging
import json
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import aiohttp
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

# ── Timezone ──
# 모든 /today /week /tasks 날짜 경계와 daily-usage reset 은 KST 로 정규화한다.
# 컨테이너 OS 타임존(Docker 기본=UTC)과 무관하게 일관된 일자 경계를 보장.
KST = ZoneInfo("Asia/Seoul")


def _kst_now() -> datetime:
    """Current time as a naive datetime in KST wall-clock.

    Naive 로 반환하는 이유: 기존 `_filter_date_range` 비교 로직이 naive 기준이라
    호환성을 유지하기 위함. 내부적으로는 모두 KST 기준이다.
    """
    return datetime.now(KST).replace(tzinfo=None)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ──
# Telegram credentials are optional (issue #106). When either is missing,
# bot polling is skipped and only the aiohttp webhook/dashboard server runs.
# Empty strings (e.g. `TELEGRAM_BOT_TOKEN=` in .env) are treated as unset.
BOT_TOKEN: str | None = os.environ.get("TELEGRAM_BOT_TOKEN") or None
_chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
CHAT_ID: int | None = int(_chat_id_raw) if _chat_id_raw else None
TELEGRAM_ENABLED: bool = bool(BOT_TOKEN and CHAT_ID is not None)

# AZ HTTP session + URL constants moved to `az_client/session.py`
# (issue #79 Phase H). Re-exported below so existing call sites and
# direct constant references keep working.

# AZ session + monitor-loop globals (`az_context`, `monitor_enabled`,
# `monitor_log_version`, `monitor_context`, `monitor_log_guid`,
# `monitor_auto_follow`, `monitor_verbose`) moved to `monitor/state.py`
# (issue #79 Phase P). Access them as attributes on the imported
# module so reads + writes stay shared with every other module that
# imports `monitor.state` (the cmd handlers we'll carve in subsequent
# phases). `monitor.state.monitor_enabled = True` from any module
# reaches the same slot as a read here.
from monitor import state  # noqa: E402

# Telegram Bot 인스턴스 (모니터에서 사용)
tg_bot: Bot | None = None

# AZ HTTP client surface — re-exported from az_client.session.
# `cached_contexts` is the same list object, mutated in place by
# `fetch_chat_list`, so direct reads in bot.py (cmd_chats, monitor)
# stay live without extra plumbing.
from az_client.session import (  # noqa: E402
    AZ_API_PREFIX,
    AZ_API_URL,
    cached_contexts,
    close_az_session,
    fetch_chat_list,
    get_az_session,
    get_headers,
    sync_log_version,
)


# `_short_id` and `format_monitor_message` moved to `render/monitor.py`
# (issue #79 Phase J). Re-exported for the call sites still in bot.py
# (cmd_chats, cmd_switch, cmd_new, monitor_agent_zero, etc.). The new
# `format_monitor_message` is pure — callers pass `verbose=monitor_verbose`
# explicitly instead of the function reaching for the global.
from render.monitor import format_monitor_message  # noqa: E402, F401
from render.monitor import short_id as _short_id  # noqa: E402


# Markdown → Telegram-HTML rendering moved to `render/markdown.py`
# (issue #79 Phase F). Re-exported here so the streaming-edit + monitor
# paths keep the existing import-free name reference.
from render import md_to_telegram_html  # noqa: F401


# Per-AZ-context streaming-edit Telegram message machinery
# (`streaming_msg_id`, `streaming_text`, `STREAM_MAX_CHARS`,
# `stream_reset`, `stream_extend`) moved to `streaming/edit.py`
# (issue #79 Phase R). The Bot instance + CHAT_ID get wired in
# `post_init` via `streaming.configure(bot=..., chat_id=...)`.
#
# Underscore prefixes dropped in the move: `_stream_reset` →
# `stream_reset`, `_stream_extend` → `stream_extend`. They WERE the
# public surface of bot.py's streaming block, just unexported by
# convention; in their own module they're properly public.
from streaming import stream_extend as _stream_extend  # noqa: E402, F401
from streaming import stream_reset as _stream_reset  # noqa: E402, F401


# ── 토큰 사용량 추적 ──
#
# Pricing + per-day accumulation moved to `pricing/` (issue #79 Phase A
# carved out cost; Phase B carved usage). Both modules use clear+update
# instead of reassignment so importers can hold stable bindings — see
# the docstrings there for the rationale.
#
# Names re-exported here so existing call sites (cmd_today, daily_usage_reporter,
# /usage handler, _resolve_litellm_key, take_pricing_snapshot, _aggregate, …)
# keep working without prefix changes during the multi-phase split.
from pricing.cost import (
    _load_model_cost_map,
    _model_cost_map,
    _model_info,
    _normalize_model,
    calc_cost,
)
from pricing.usage import (
    build_daily_report_lines,
    track_usage,
    usage_history,
    usage_today,
)


# `send_telegram` moved to `notify/telegram.py` (issue #79 Phase L).
# Re-exported here for the ~30 call sites still in bot.py (cmd handlers,
# monitor loop, streaming-edit path, webhook handlers). The Bot instance
# + CHAT_ID get wired into the carved-out module from `post_init` once
# Application.bot is available — search for `notify.telegram.configure`.
from notify.telegram import send_telegram  # noqa: E402, F401


# `fetch_chat_list` and `sync_log_version` moved to
# `az_client/session.py` (issue #79 Phase H). Re-exported at the top
# of this file alongside the session helpers.


# ── Agent Zero 웹 채팅 모니터 ──
async def monitor_agent_zero():
    """Agent Zero의 모든 대화를 백그라운드로 모니터링하여 Telegram에 전달"""
    logger.info("Agent Zero monitor started")
    await asyncio.sleep(10)

    # 최초 시작 시 현재 시점으로 스킵 (기존 히스토리 전송 방지)
    state.monitor_log_version = await sync_log_version(state.monitor_context)
    logger.info(f"Monitor synced to log_version: {state.monitor_log_version}")

    while True:
        if not state.monitor_enabled:
            await asyncio.sleep(5)
            continue

        try:
            session = await get_az_session()
            headers = get_headers()

            poll_payload = {
                "log_from": state.monitor_log_version,
                "context": state.monitor_context or None,
                "timezone": "Asia/Seoul",
            }

            async with session.post(
                f"{AZ_API_URL}{AZ_API_PREFIX}/poll",
                json=poll_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 403:
                    await close_az_session()
                    await asyncio.sleep(3)
                    continue
                if resp.status != 200:
                    await asyncio.sleep(5)
                    continue
                poll_data = await resp.json()

            # 채팅 목록 캐시 업데이트 — clear+extend so the binding shared
            # with cmd_chats and az_client.session stays the same list object.
            contexts = poll_data.get("contexts", [])
            if contexts:
                cached_contexts.clear()
                cached_contexts.extend(contexts)

            # 컨텍스트 동기화
            new_context = poll_data.get("context", "")
            new_log_guid = poll_data.get("log_guid", "")
            new_log_version = poll_data.get("log_version", 0)

            # 자동 추적: 웹에서 다른 채팅으로 전환된 경우
            if state.monitor_auto_follow and new_context and new_context != state.monitor_context:
                old_ctx = _short_id(state.monitor_context)
                new_ctx = _short_id(new_context)
                # Drop the in-progress stream tied to the old chat — the
                # 채팅 전환 알림 is itself a fresh standalone message and
                # the new chat's logs should start their own stream.
                _stream_reset(state.monitor_context or "_default")
                await send_telegram(f"🔄 채팅 전환 감지: {old_ctx} → {new_ctx}")
                state.monitor_context = new_context
                state.monitor_log_guid = new_log_guid
                # 현재 시점으로 스킵 (이전 히스토리 전송 방지)
                state.monitor_log_version = await sync_log_version(new_context)
                await asyncio.sleep(2)
                continue

            # 대화가 리셋된 경우
            if new_log_guid and new_log_guid != state.monitor_log_guid:
                state.monitor_log_guid = new_log_guid
                state.monitor_log_version = new_log_version  # 현재 시점 유지
                await asyncio.sleep(2)
                continue

            logs = poll_data.get("logs", [])

            if logs:
                # Stream logs into ONE Telegram message per AZ "turn" — see
                # streaming_msg_id docstring above. We batch this poll's logs
                # and either edit the active message or open a new one.
                # A `user`-type log mid-batch marks a new turn boundary.
                stream_key = state.monitor_context or "_default"
                pending: list[str] = []

                async def flush_pending():
                    if pending:
                        await _stream_extend(stream_key, "\n\n".join(pending))
                        pending.clear()

                for log in logs:
                    log_type = log.get("type", "")
                    heading = log.get("heading", "")
                    content = log.get("content", "")
                    temp = log.get("temp", False)

                    if temp:
                        continue

                    if log_type == "user":
                        # Push prior pieces to the previous turn's message,
                        # then close that stream so the user line opens fresh.
                        await flush_pending()
                        _stream_reset(stream_key)

                    formatted = format_monitor_message(
                        log_type, heading, content, verbose=state.monitor_verbose,
                    )
                    if formatted:
                        pending.append(formatted)

                await flush_pending()
                state.monitor_log_version = new_log_version

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            await asyncio.sleep(10)
            continue

        await asyncio.sleep(3)


# ── Agent Zero API: Telegram에서 직접 메시지 전송 ──
async def send_to_agent_zero(message: str) -> str:
    """Agent Zero /message_async API로 메시지 전송"""
    payload = {
        "text": message,
        "context": state.az_context,
    }
    headers = get_headers()

    try:
        session = await get_az_session()

        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/message_async",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 403:
                await close_az_session()
                session = await get_az_session()
                headers = get_headers()
                async with session.post(
                    f"{AZ_API_URL}{AZ_API_PREFIX}/message_async",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as retry_resp:
                    if retry_resp.status != 200:
                        text = await retry_resp.text()
                        return f"Agent Zero 전송 실패 ({retry_resp.status}): {text}"
                    data = await retry_resp.json()
            elif resp.status != 200:
                text = await resp.text()
                return f"Agent Zero 전송 실패 ({resp.status}): {text}"
            else:
                data = await resp.json()

            state.az_context = data.get("context", state.az_context)
            # 모니터도 같은 컨텍스트 추적하도록 동기화
            if state.monitor_context != state.az_context:
                state.monitor_context = state.az_context
                state.monitor_log_version = await sync_log_version(state.az_context)

        return "✅ Agent Zero에 전달 완료. 응답은 자동으로 전송됩니다."

    except TimeoutError:
        return "Agent Zero 응답 시간 초과"
    except Exception as e:
        return f"Agent Zero 연결 실패: {str(e)}"


async def check_agent_zero_status() -> str:
    try:
        session = await get_az_session()
        headers = get_headers()

        # 기본 연결 확인
        async with session.get(
            f"{AZ_API_URL}/",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return f"Agent Zero: 응답 코드 {resp.status}"

        # 설정 조회 — AZ v1.9 의 /api/settings_get 은
        #   {"settings": {...}, "additional": {...}}
        # 형태로 한 단계 wrapping 되어 응답합니다. 이전 코드는 최상위에서
        # 키를 읽어 늘 "알 수 없음" 으로 나왔던 버그를 unwrap 으로 수정.
        profile = "알 수 없음"
        try:
            async with session.post(
                f"{AZ_API_URL}{AZ_API_PREFIX}/settings_get",
                json={},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as settings_resp:
                if settings_resp.status == 200:
                    payload = await settings_resp.json()
                    inner = payload.get("settings") or payload  # tolerate older shape
                    profile = inner.get("agent_profile") or "알 수 없음"
        except Exception as e:
            logger.debug(f"settings_get failed: {e}")

        # 모델은 v1.9 에서 _model_config 플러그인으로 옮겨졌고 자체 API 가
        # 있습니다 (/api/plugins/_model_config/model_config_get). 사용자 override
        # 가 없으면 default_config.yaml 의 값이 돌아와서, AZ 내부 라우팅이
        # 실제로 보고 있는 설정과 정확히 일치합니다.
        chat_model, util_model = "알 수 없음", "알 수 없음"
        try:
            async with session.post(
                f"{AZ_API_URL}{AZ_API_PREFIX}/plugins/_model_config/model_config_get",
                json={"agent_profile": profile if profile != "알 수 없음" else ""},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as mc_resp:
                if mc_resp.status == 200:
                    mc = (await mc_resp.json()).get("config") or {}
                    cm = mc.get("chat_model") or {}
                    um = mc.get("utility_model") or {}
                    if cm.get("name"):
                        chat_model = cm["name"]
                    if um.get("name"):
                        util_model = um["name"]
        except Exception as e:
            logger.debug(f"model_config_get failed: {e}")

        ctx_short = _short_id(state.monitor_context)
        return (
            f"Agent Zero: 정상 동작 중\n\n"
            f"📋 프로필: {profile}\n"
            f"🤖 메인 모델: {chat_model}\n"
            f"⚡ 유틸 모델: {util_model}\n\n"
            f"모니터링: {'켜짐' if state.monitor_enabled else '꺼짐'}\n"
            f"자동 추적: {'켜짐' if state.monitor_auto_follow else '꺼짐'}\n"
            f"현재 채팅: {ctx_short}"
        )
    except Exception as e:
        return f"Agent Zero: 연결 불가 - {str(e)}"


# ── Telegram handlers ──
# `/start` and `/help` moved to telegram_handlers/system.py (issue #79
# Phase O); `/docs` moved to telegram_handlers/files.py (uses
# notify.send_document). `/status` stays here for now since it calls
# `check_agent_zero_status()` which still reaches monitor state globals.
from telegram_handlers.files import cmd_docs as _cmd_docs_external  # noqa: E402
from telegram_handlers.system import cmd_help as _cmd_help_external  # noqa: E402
from telegram_handlers.system import cmd_start as _cmd_start_external  # noqa: E402
cmd_start = _cmd_start_external
cmd_help = _cmd_help_external
cmd_docs = _cmd_docs_external


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    status = await check_agent_zero_status()
    await update.message.reply_text(status)


# /chats, /switch, /new + the six monitor toggles all live in
# telegram_handlers/chat.py — Phase Q carved /chats + toggles, Phase R
# completed the trio with /switch + /new (which needed the streaming
# carve to land first since they call stream_reset).
from telegram_handlers.chat import cmd_chats, cmd_new, cmd_switch  # noqa: E402, F401


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """현재 채팅의 전체 로그를 파일로 전송"""
    if update.effective_chat.id != CHAT_ID:
        return

    if not state.monitor_context:
        await update.message.reply_text("추적 중인 채팅이 없습니다. /chats 로 확인하세요.")
        return

    await update.message.reply_text("📄 로그 파일 생성 중...")

    try:
        session = await get_az_session()
        headers = get_headers()

        # chat export API로 전체 대화 JSON 가져오기
        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/chat_export",
            json={"ctxid": state.monitor_context},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                await update.message.reply_text(f"로그 조회 실패: {resp.status}")
                return
            data = await resp.json()

        content = data.get("content", {})
        import json
        import io

        # JSON 파일로 전송
        json_str = json.dumps(content, ensure_ascii=False, indent=2)
        json_file = io.BytesIO(json_str.encode("utf-8"))
        json_file.name = f"chat_log_{state.monitor_context[:8]}.json"

        await tg_bot.send_document(
            chat_id=CHAT_ID,
            document=json_file,
            caption=f"📋 채팅 로그 (Context: {_short_id(state.monitor_context)})",
        )

        # 텍스트 요약도 함께 생성
        txt_lines = []
        if isinstance(content, list):
            messages = content
        elif isinstance(content, dict):
            messages = content.get("messages", content.get("history", []))
        else:
            messages = []

        for msg in messages:
            role = msg.get("role", "unknown")
            text = msg.get("content", "")
            if isinstance(text, list):
                text = " ".join(
                    item.get("text", "") for item in text if isinstance(item, dict)
                )
            if text:
                preview = text[:200] + "..." if len(text) > 200 else text
                txt_lines.append(f"[{role}] {preview}")

        if txt_lines:
            txt_str = "\n\n".join(txt_lines)
            txt_file = io.BytesIO(txt_str.encode("utf-8"))
            txt_file.name = f"chat_log_{state.monitor_context[:8]}.txt"
            await tg_bot.send_document(
                chat_id=CHAT_ID,
                document=txt_file,
                caption="📝 텍스트 요약 버전",
            )

    except Exception as e:
        await update.message.reply_text(f"로그 조회 실패: {str(e)}")


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """설정 경량 백업 → Telegram 파일 전송"""
    import zipfile
    import io as _io

    if update.effective_chat.id != CHAT_ID:
        return

    await update.message.reply_text("📦 백업 생성 중...")

    # 백업 대상 (경량: 설정 + 프롬프트 + 프로필)
    backup_targets = {
        # 호스트 마운트된 파일들 (telegram-bridge 컨테이너에서 접근 가능한 것)
        "GUIDE.md": "/app/GUIDE.md",
        "README.md": "/app/README.md",
    }

    # docs 디렉토리
    docs_dir = "/app/docs"

    # Agent Zero 설정은 API로 가져오기
    settings_data = None
    try:
        session = await get_az_session()
        headers = get_headers()
        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/settings_get",
            json={},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                settings_data = await resp.json()
    except Exception:
        pass

    try:
        # ZIP 생성
        zip_buffer = _io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # 문서 파일
            for name, path in backup_targets.items():
                if os.path.exists(path):
                    zf.write(path, name)

            # docs 디렉토리
            if os.path.isdir(docs_dir):
                for f in os.listdir(docs_dir):
                    fpath = os.path.join(docs_dir, f)
                    if os.path.isfile(fpath):
                        zf.write(fpath, f"docs/{f}")

            # settings.json (API에서 가져온 것)
            if settings_data:
                zf.writestr(
                    "agent-zero/settings.json",
                    json.dumps(settings_data, ensure_ascii=False, indent=2),
                )

            # 사용량 데이터
            usage_data = {
                "today": usage_today,
                "history": usage_history,
            }
            zf.writestr(
                "usage_data.json",
                json.dumps(usage_data, ensure_ascii=False, indent=2),
            )

            # 백업 메타데이터
            meta = {
                "timestamp": datetime.now().isoformat(),
                "type": "telegram-light-backup",
                "monitor_context": state.monitor_context,
            }
            zf.writestr(
                "backup_meta.json",
                json.dumps(meta, ensure_ascii=False, indent=2),
            )

        zip_buffer.seek(0)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        await tg_bot.send_document(
            chat_id=CHAT_ID,
            document=zip_buffer,
            filename=f"az_backup_{ts}.zip",
            caption=f"📦 경량 백업 완료 ({ts})\n설정 + 문서 + 사용량 데이터",
        )

    except Exception as e:
        await update.message.reply_text(f"백업 실패: {str(e)}")


# Task aggregation primitives (TASKS_DIR, _load_task_jsons, _aggregate, …)
# moved to `task_agg/agg.py` (issue #79 Phase D). Note the package name —
# we can't call it `tasks/` because docker-compose mounts the AZ task-JSON
# read-only volume at `/app/tasks`, which would shadow the Python package.
# Re-export of the names lives below; the constant TASKS_DIR is reachable
# as `task_agg.agg.TASKS_DIR` for the few places that referenced it directly.

# ── Budget alerts (M5-A · issue #19) ──
# Persists across restarts via a docker volume mount (/app/data → ./telegram-bridge/data).
# Budget state, persistence, threshold ladder, pure formatters — moved
# to `budget/core.py` (issue #79 Phase C). Re-exported below so the
# async engine (_budget_check_window, hourly_budget_sweep) and the
# /budget command handler keep the existing names. A future phase can
# rename callers to use the public surface (alert_key, format_alert).
from budget.core import (
    BUDGET_DIR,
    BUDGET_PATH,
    BUDGET_THRESHOLDS,
    _budget,
    _budget_default,
    _load_budget,
    _save_budget,
)
from budget.core import alert_key as _alert_key  # noqa: F401  # legacy name
from budget.core import format_alert as _format_budget_alert  # noqa: F401


# Async budget engine moved to `budget/engine.py` (issue #79 Phase K).
# Telegram alert callback wired in main() via `budget.engine.configure(
# send_alert=send_telegram)` so the engine module stays import-clean of
# the telegram bot. Re-exported below for the existing call sites
# (`usage_track_handler` → `budget_check_all`, post_init scheduler →
# `hourly_budget_sweep`, `cmd_budget` → `_budget_check_window`,
# `_compute_window_cost`).
from budget.engine import (  # noqa: E402, F401
    _budget_check_window,
    _compute_window_cost,
    budget_check_all,
    hourly_budget_sweep,
)


# ── Pricing drift detection (M5-C · issue #21) ──
# Constants + helpers + `take_pricing_snapshot` moved to
# `pricing/snapshot.py` (issue #79 Phase G). Re-exported below so
# `cmd_pricing` and `daily_pricing_snapshot` keep their existing names.
# `take_pricing_snapshot` now requires an explicit `send_alert` callback
# (decoupled from `send_telegram`) — bot.py wires it in below.
from pricing.snapshot import (  # noqa: F401, E402  (re-export)
    LITELLM_PRICE_URL,
    PRICING_DIFF_FIELDS,
    PRICING_DIR,
    PRICING_RETENTION_DAYS,
    _diff_snapshots,
    _fetch_litellm_table,
    _format_pricing_diff,
    _interested_models,
    _list_snapshots,
    _load_snapshot,
    _previous_snapshot,
    _resolve_litellm_key,
    _rotate_pricing_snapshots,
    _save_snapshot,
    _select_for_snapshot,
    _snapshot_path,
)
from pricing.snapshot import take_pricing_snapshot  # noqa: F401  # for legacy import


async def daily_pricing_snapshot() -> None:
    """Background task. Wakes once a day at 00:30 KST and takes a snapshot.

    Why 00:30 (not 00:00): the daily usage reporter fires at 00:01, leaving
    a 29-minute buffer so we don't pile concurrent Telegrams on the user.
    Also gives LiteLLM's GitHub source a moment to settle if they happen
    to release on midnight UTC.

    Wires `send_telegram` into the pricing.snapshot module's drift-alert
    hook directly here — no more bot.py-side wrapper needed since
    `cmd_pricing` (the only other take_pricing_snapshot caller) moved
    to telegram_handlers/cost.py and does the same wiring inline.
    """
    logger.info("Daily pricing snapshot scheduler started")
    while True:
        try:
            now = _kst_now()
            target = now.replace(hour=0, minute=30, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            await asyncio.sleep(wait)
            await take_pricing_snapshot(alert=True, send_alert=send_telegram)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[pricing] scheduler error: {e}")
            await asyncio.sleep(300)  # back off 5 min on unexpected errors


# ── Web dashboard (M5-E · issue #23) ──
# Auth + JSON stats + HTML page moved to `dashboard/` (issue #79 Phase E).
# Re-exported below so the route registration in `run_webhook_server()` and
# the routes_msg startup log keep working unchanged.
from dashboard import (  # noqa: F401, E402
    DASHBOARD_HTML,
    DASHBOARD_TOKEN,
    _build_stats,
    _check_dashboard_auth,
    dashboard_handler,
    stats_api_handler,
)


# Task-JSON load + aggregate + format primitives moved to `task_agg/agg.py`
# (issue #79 Phase D — pivoted ahead of dashboard so dashboard's
# `_build_stats` and budget's `_compute_window_cost` can both consume
# from a single source). Re-exported here so existing callers (cmd_today,
# cmd_week, cmd_tasks, daily_usage_reporter, _build_stats,
# _compute_window_cost) keep working without prefix changes.
from task_agg.agg import (  # noqa: F401  (re-export)
    _aggregate,
    _cache_efficiency,
    _data_quality_summary,
    _filter_date_range,
    _format_agg_block,
    _format_cache_line,
    _format_model_breakdown,
    _format_profile_breakdown,
    _is_anthropic_model,
    _load_task_jsons,
    _quality_banner,
)


# /today, /week, /tasks moved to `telegram_handlers/today.py` (issue
# #79 Phase I). Re-exported for the dispatcher in `main()` to keep
# referencing the same names.
from telegram_handlers.today import (  # noqa: F401, E402
    _parse_by_flag,
    cmd_tasks,
    cmd_today,
    cmd_week,
)
# `/usage`, `/budget`, `/pricing` moved to `telegram_handlers/cost.py`
# (issue #79 Phase N) — all three are cost-reporting commands and their
# deps (pricing.usage, budget.core, budget.engine, pricing.snapshot,
# notify.telegram) all live in already-carved modules.
from telegram_handlers.cost import (  # noqa: E402, F401
    cmd_budget,
    cmd_pricing,
    cmd_usage,
)


# /monitor_on/off, /track_chat_on/off, /verbose_on/off moved to
# telegram_handlers/chat.py (issue #79 Phase Q) — all six are pure
# state.X = bool flips that became carve-able once monitor state moved
# out of bot.py (Phase P). Re-exported for the dispatcher in main().
from telegram_handlers.chat import (  # noqa: E402, F401
    cmd_monitor_off,
    cmd_monitor_on,
    cmd_track_chat_off,
    cmd_track_chat_on,
    cmd_verbose_off,
    cmd_verbose_on,
)



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("권한이 없습니다.")
        return

    user_msg = update.message.text
    logger.info(f"User → Agent Zero: {user_msg[:100]}")

    response = await send_to_agent_zero(user_msg)
    await update.message.reply_text(response)


# ── Notification & Usage webhook ──
# 4 aiohttp handlers + run_webhook_server moved to `webhooks/handlers.py`
# (issue #79 Phase M). Re-exported below so `post_init`'s
# `asyncio.create_task(run_webhook_server())` keeps working unchanged.
from webhooks.handlers import (  # noqa: E402, F401
    run_webhook_server,
    usage_get_handler,
    usage_track_handler,
    webhook_handler,
)


# ── 일일 사용량 리포트 스케줄러 ──
async def daily_usage_reporter():
    """매일 KST 00:01 에 어제 사용량 리포트를 Telegram 으로 전송.

    `usage_today` 가 \"어제 일자\" 데이터를 들고 있을 때만 발송한다.
    `/track` 이벤트만 bucket 을 회전시키므로 idle 인 날에는 stale 한
    이전 활성일 데이터가 남아있는데, 이걸 매일 같은 라벨로 반복 발송하지
    않도록 발송 단계에서 일자 매칭 가드를 둔다 (issue #131).
    """
    logger.info("Daily usage reporter started")

    while True:
        now = datetime.now()
        # 다음 자정(00:01)까지 대기
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        wait_seconds = (tomorrow - now).total_seconds()
        if wait_seconds < 60:
            # 자정 직전이면 다음날로
            wait_seconds += 86400
        await asyncio.sleep(wait_seconds)

        # KST 기준 어제 일자 — usage_today.date 와 일치할 때만 발송
        yesterday_str = (_kst_now() - timedelta(days=1)).strftime("%Y-%m-%d")
        lines = build_daily_report_lines(usage_today, yesterday_str)
        if lines:
            await send_telegram("\n".join(lines))


# ── Post-init: 모니터 시작 ──
async def post_init(application: Application):
    global tg_bot
    tg_bot = application.bot

    # Wire the Bot instance into the carved-out send_telegram module
    # (issue #79 Phase L). bot.py keeps its own `tg_bot` global pointing
    # at the SAME Bot object for cmd_logs/cmd_backup paths that still
    # reach for `tg_bot.send_document` etc.
    import notify.telegram
    notify.telegram.configure(bot=application.bot, chat_id=CHAT_ID)

    # Same injection for the streaming-edit module (Phase R) — its
    # stream_extend needs the Bot to call edit_message_text /
    # send_message and return the new message_id.
    import streaming
    streaming.configure(bot=application.bot, chat_id=CHAT_ID)

    asyncio.create_task(monitor_agent_zero())
    asyncio.create_task(daily_usage_reporter())
    # M5-A: hourly budget sweep — defensive double-check on top of the
    # per-/track call (issue #19).
    asyncio.create_task(hourly_budget_sweep())
    # M5-C: daily 00:30 KST pricing snapshot + drift detection (issue #21).
    asyncio.create_task(daily_pricing_snapshot())
    logger.info("Monitor + daily reporter + budget sweep + pricing snapshot tasks created")


def main():
    # LiteLLM 모델 가격표 로드 (GitHub에서 최신 다운로드)
    _load_model_cost_map()

    # M5-A: load persisted budget settings before the webhook server starts
    # accepting /track requests (which trigger budget_check_all). Otherwise
    # the first /track after restart would use empty defaults and skip alerts.
    _load_budget()

    # Wire the telegram alert callback into the carved-out budget engine
    # (issue #79 Phase K). Without this, `_budget_check_window` would log
    # but skip the actual alert send. Done here so it's set before the
    # webhook server accepts its first /track.
    import budget.engine
    # send_telegram is a no-op when notify.telegram isn't configure()d
    # (notify/telegram.py:54), so wiring it unconditionally is safe even
    # when the bot polling branch is skipped below.
    budget.engine.configure(send_alert=send_telegram)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_webhook_server())

    # Issue #106: Telegram polling is optional. When credentials are missing,
    # keep the aiohttp webhook/dashboard server alive but skip the bot
    # Application entirely (which would crash on a None token).
    if not TELEGRAM_ENABLED:
        logger.warning(
            "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 미설정 — Telegram polling 비활성화. "
            "Webhook/Dashboard 만 기동합니다."
        )
        loop.run_forever()
        return

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("chats", cmd_chats))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("docs", cmd_docs))
    app.add_handler(CommandHandler("usage", cmd_usage))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("pricing", cmd_pricing))
    app.add_handler(CommandHandler("backup", cmd_backup))
    # /eval — golden-set 실행 + LLM-as-judge 채점 (#114). 마운트된 eval/
    # 가 없으면 핸들러가 친절한 에러로 거절.
    from telegram_handlers.eval import cmd_eval  # noqa: E402

    app.add_handler(CommandHandler("eval", cmd_eval))
    app.add_handler(CommandHandler("monitor_on", cmd_monitor_on))
    app.add_handler(CommandHandler("monitor_off", cmd_monitor_off))
    # Primary names (clearer about direction — both /monitor and /track_chat
    # are AZ→Telegram concerns, /track_chat picks WHICH chat to watch).
    app.add_handler(CommandHandler("track_chat_on", cmd_track_chat_on))
    app.add_handler(CommandHandler("track_chat_off", cmd_track_chat_off))
    # Verbose mode — show every AZ activity log instead of just user echoes.
    app.add_handler(CommandHandler("verbose_on", cmd_verbose_on))
    app.add_handler(CommandHandler("verbose_off", cmd_verbose_off))
    # Legacy aliases — old names still work for muscle memory / saved scripts.
    # Drop these after a transition window if desired.
    app.add_handler(CommandHandler("follow_on", cmd_track_chat_on))
    app.add_handler(CommandHandler("follow_off", cmd_track_chat_off))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Voice / audio messages → STT → send_to_agent_zero (text path).
    from telegram_handlers.voice import handle_voice  # noqa: E402
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    logger.info("Telegram Bridge Bot started (with monitor + multi-chat)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
