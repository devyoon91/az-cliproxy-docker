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
from aiohttp import web, CookieJar

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
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
AZ_API_URL = os.environ.get("AZ_API_URL", "http://agent-zero:80")
AZ_API_PREFIX = os.environ.get("AZ_API_PREFIX", "/api")  # v1.8+: /api prefix

# Agent Zero context ID (세션 유지)
az_context = ""

# 세션 유지 (CSRF 토큰 + 쿠키)
az_session: aiohttp.ClientSession | None = None
csrf_token: str = ""

# 모니터링 상태
monitor_enabled = True
monitor_log_version = 0
monitor_context = ""
monitor_log_guid = ""
monitor_auto_follow = True  # 웹에서 활성 채팅 변경 시 자동 추적
# When False (the default), only `user` log types echo through to Telegram
# during a task — AZ's intermediate activity (info/tool/code_exe/response/
# error/warning) is suppressed. The user instead gets two clean messages
# at task completion: AZ's answer + the metrics card (driven from
# task_report.py). When True, every log type passes through unchanged for
# debugging. Toggled via /verbose_on, /verbose_off.
monitor_verbose = False

# Telegram Bot 인스턴스 (모니터에서 사용)
tg_bot: Bot | None = None

# 채팅 목록 캐시
cached_contexts: list = []


def _short_id(ctx_id: str | None, max_len: int = 12) -> str:
    """Render a context ID for display.

    AZ's `helpers.guids.generate_id` defaults to length=8, so today's IDs
    are already 8 chars total. The previous code did `id[:8] + "..."`
    which was a UX lie — the ellipsis suggested truncation when nothing
    had been truncated, and users couldn't tell where the ID ended.

    Behavior:
      - empty / None → "없음"
      - len(id) <= max_len → return as-is, no ellipsis
      - longer → truncate to max_len + "..." (real truncation, real marker)
    """
    if not ctx_id:
        return "없음"
    if len(ctx_id) <= max_len:
        return ctx_id
    return ctx_id[:max_len] + "..."


# Markdown → Telegram-HTML rendering moved to `render/markdown.py`
# (issue #79 Phase F). Re-exported here so the streaming-edit + monitor
# paths keep the existing import-free name reference.
from render import md_to_telegram_html  # noqa: F401


# ── Monitor message streaming state ──
# Per AZ context, track the last Telegram message we sent for the active
# AZ "turn" so subsequent log entries from the same turn extend that message
# (via editMessageText) instead of arriving as separate Telegram messages.
# An AZ turn typically emits 5–7 logs (info → tool → code_exe → tool result
# → response); without this, every poll cycle's logs went out as their own
# message, producing visual fragmentation.
#
# Boundaries (when to stop editing and start a new message):
#   • A `user`-type log appears in the poll batch — that's a new turn
#   • Telegram's 4096-char limit hit — close current, open next
#   • Active chat switches (/switch, /new, monitor auto-follow)
#   • Edit fails for any non-recoverable reason — fall back to send-new
streaming_msg_id: dict[str, int] = {}   # ctx_key → Telegram message_id
streaming_text: dict[str, str] = {}     # ctx_key → text we last sent/edited

# Telegram's hard cap is 4096 chars; leave headroom for the next chunk join
# and any sneaky widening from formatter changes.
STREAM_MAX_CHARS = 3800


def _stream_reset(ctx_key: str) -> None:
    """Forget the active streamed message for `ctx_key`. Next chunk will
    send a brand-new Telegram message. Safe to call when no state exists."""
    streaming_msg_id.pop(ctx_key, None)
    streaming_text.pop(ctx_key, None)


async def _stream_extend(ctx_key: str, new_chunk: str) -> None:
    """Append `new_chunk` to the active streamed message — editing it
    in place. Falls back to a brand-new send when:
      • There's no active message yet
      • The edit would push past STREAM_MAX_CHARS (so we close + reopen)
      • Telegram refuses the edit (message too old, deleted, etc.)
    """
    if not new_chunk or tg_bot is None:
        return

    cur_text = streaming_text.get(ctx_key, "")
    cur_msg_id = streaming_msg_id.get(ctx_key)
    sep = "\n\n" if cur_text else ""
    extended = cur_text + sep + new_chunk

    # Length cap — finalize current message, start a fresh one with new_chunk.
    if cur_msg_id and len(extended) > STREAM_MAX_CHARS:
        cur_msg_id = None
        cur_text = ""
        extended = new_chunk

    if cur_msg_id:
        try:
            await tg_bot.edit_message_text(
                chat_id=CHAT_ID,
                message_id=cur_msg_id,
                text=extended,
            )
            streaming_text[ctx_key] = extended
            return
        except Exception as e:
            # "Message is not modified" is a no-op success; everything else
            # means the message can't be edited (too old / deleted / rate
            # limit). Drop state and fall through to send-new — better the
            # user gets a fresh message than no update at all.
            err = str(e).lower()
            if "not modified" in err:
                return
            logger.debug(f"[stream] edit failed for {ctx_key!r}: {e}; sending new")
            _stream_reset(ctx_key)

    # Send-new path (also taken when length cap forced a fresh message).
    try:
        msg = await tg_bot.send_message(chat_id=CHAT_ID, text=new_chunk)
        streaming_msg_id[ctx_key] = msg.message_id
        streaming_text[ctx_key] = new_chunk
    except Exception as e:
        logger.error(f"[stream] send failed: {e}")


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
    track_usage,
    usage_history,
    usage_today,
)


async def get_az_session() -> aiohttp.ClientSession:
    """Agent Zero 세션 가져오기 (CSRF 토큰 포함)"""
    global az_session, csrf_token

    if az_session and not az_session.closed:
        return az_session

    jar = CookieJar(unsafe=True)
    az_session = aiohttp.ClientSession(cookie_jar=jar)

    try:
        async with az_session.get(
            f"{AZ_API_URL}{AZ_API_PREFIX}/csrf_token",
            headers={"Origin": "http://localhost:50001"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                csrf_token = data.get("token", "")
                logger.info(f"CSRF token acquired: {csrf_token[:10]}...")
            else:
                logger.warning(f"CSRF token request failed: {resp.status}")
    except Exception as e:
        logger.error(f"Failed to get CSRF token: {e}")

    return az_session


async def close_az_session():
    """세션 닫기"""
    global az_session
    if az_session and not az_session.closed:
        await az_session.close()
        az_session = None


def get_headers() -> dict:
    headers = {
        "Origin": "http://localhost:50001",  # v1.8 origin check bypass
    }
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    return headers


# ── Telegram 메시지 전송 헬퍼 ──
async def send_telegram(
    text: str,
    parse_mode: str | None = None,
    fallback_text: str | None = None,
):
    """Send `text` to Telegram with optional `parse_mode` (HTML / Markdown).

    `fallback_text` (recommended when parse_mode is set): a plain-text
    version sent if Telegram rejects the formatted payload with a parse
    error. Useful when AZ output produces malformed HTML/markdown despite
    our converter — better the user gets the raw text than nothing.

    Long messages (>4000 chars) are split. The fallback is only retried
    for the chunk that actually failed parsing; other chunks aren't
    re-sent so the user doesn't get duplicates.
    """
    if not tg_bot or not text.strip():
        return

    chunks = (
        [text[i : i + 4000] for i in range(0, len(text), 4000)]
        if len(text) > 4000 else [text]
    )
    fallback_chunks = None
    if fallback_text and len(text) > 4000:
        fallback_chunks = [
            fallback_text[i : i + 4000] for i in range(0, len(fallback_text), 4000)
        ]

    for idx, chunk in enumerate(chunks):
        try:
            await tg_bot.send_message(
                chat_id=CHAT_ID, text=chunk, parse_mode=parse_mode
            )
        except Exception as e:
            err = str(e).lower()
            # Telegram returns "Bad Request: can't parse entities" on bad
            # HTML/Markdown. Retry the SAME chunk in plain mode.
            if parse_mode and ("can't parse" in err or "parse entities" in err):
                fb = (
                    fallback_chunks[idx] if fallback_chunks
                    else (fallback_text if fallback_text and len(chunks) == 1 else chunk)
                )
                logger.warning(
                    f"[telegram] parse_mode={parse_mode} rejected, "
                    f"retrying chunk {idx} as plain"
                )
                try:
                    await tg_bot.send_message(chat_id=CHAT_ID, text=fb)
                except Exception as e2:
                    logger.error(f"[telegram] plain fallback also failed: {e2}")
            else:
                logger.error(f"[telegram] send failed: {e}")


# ── 채팅 목록 조회 ──
async def fetch_chat_list() -> list:
    """Agent Zero의 활성 채팅(컨텍스트) 목록 조회"""
    global cached_contexts
    try:
        session = await get_az_session()
        headers = get_headers()

        poll_payload = {
            "log_from": 0,
            "context": None,
            "timezone": "Asia/Seoul",
        }

        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/poll",
            json=poll_payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            poll_data = await resp.json()

        contexts = poll_data.get("contexts", [])
        cached_contexts = contexts
        return contexts

    except Exception as e:
        logger.error(f"Failed to fetch chat list: {e}")
        return []


# ── 현재 시점 log_version 가져오기 (히스토리 스킵용) ──
async def sync_log_version(ctx: str) -> int:
    """특정 컨텍스트의 현재 log_version만 조용히 가져옴 (알림 없이 스킵)"""
    try:
        session = await get_az_session()
        headers = get_headers()
        poll_payload = {
            "log_from": 999999999,  # 큰 수 → 로그 0건 반환, version만 획득
            "context": ctx or None,
            "timezone": "Asia/Seoul",
        }
        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/poll",
            json=poll_payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("log_version", 0)
    except Exception as e:
        logger.error(f"sync_log_version error: {e}")
    return 0


# ── Agent Zero 웹 채팅 모니터 ──
async def monitor_agent_zero():
    """Agent Zero의 모든 대화를 백그라운드로 모니터링하여 Telegram에 전달"""
    global monitor_log_version, monitor_context, monitor_log_guid
    global monitor_enabled, monitor_auto_follow, cached_contexts

    logger.info("Agent Zero monitor started")
    await asyncio.sleep(10)

    # 최초 시작 시 현재 시점으로 스킵 (기존 히스토리 전송 방지)
    monitor_log_version = await sync_log_version(monitor_context)
    logger.info(f"Monitor synced to log_version: {monitor_log_version}")

    while True:
        if not monitor_enabled:
            await asyncio.sleep(5)
            continue

        try:
            session = await get_az_session()
            headers = get_headers()

            poll_payload = {
                "log_from": monitor_log_version,
                "context": monitor_context or None,
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

            # 채팅 목록 캐시 업데이트
            contexts = poll_data.get("contexts", [])
            if contexts:
                cached_contexts = contexts

            # 컨텍스트 동기화
            new_context = poll_data.get("context", "")
            new_log_guid = poll_data.get("log_guid", "")
            new_log_version = poll_data.get("log_version", 0)

            # 자동 추적: 웹에서 다른 채팅으로 전환된 경우
            if monitor_auto_follow and new_context and new_context != monitor_context:
                old_ctx = _short_id(monitor_context)
                new_ctx = _short_id(new_context)
                # Drop the in-progress stream tied to the old chat — the
                # 채팅 전환 알림 is itself a fresh standalone message and
                # the new chat's logs should start their own stream.
                _stream_reset(monitor_context or "_default")
                await send_telegram(f"🔄 채팅 전환 감지: {old_ctx} → {new_ctx}")
                monitor_context = new_context
                monitor_log_guid = new_log_guid
                # 현재 시점으로 스킵 (이전 히스토리 전송 방지)
                monitor_log_version = await sync_log_version(new_context)
                await asyncio.sleep(2)
                continue

            # 대화가 리셋된 경우
            if new_log_guid and new_log_guid != monitor_log_guid:
                monitor_log_guid = new_log_guid
                monitor_log_version = new_log_version  # 현재 시점 유지
                await asyncio.sleep(2)
                continue

            logs = poll_data.get("logs", [])

            if logs:
                # Stream logs into ONE Telegram message per AZ "turn" — see
                # streaming_msg_id docstring above. We batch this poll's logs
                # and either edit the active message or open a new one.
                # A `user`-type log mid-batch marks a new turn boundary.
                stream_key = monitor_context or "_default"
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

                    formatted = format_monitor_message(log_type, heading, content)
                    if formatted:
                        pending.append(formatted)

                await flush_pending()
                monitor_log_version = new_log_version

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            await asyncio.sleep(10)
            continue

        await asyncio.sleep(3)


def format_monitor_message(log_type: str, heading: str, content: str) -> str | None:
    """로그 타입에 따라 Telegram 메시지 포맷.

    Quiet mode (default, `monitor_verbose=False`): only `user` log types
    echo through. The user-facing answer + metrics card are sent at task
    completion via `task_report.py`'s `_post_task_response` /
    `_post_task_summary`, so the in-progress monitor doesn't need to
    repeat what's coming anyway.

    Verbose mode (`monitor_verbose=True`, toggled via /verbose_on): every
    log type formats and forwards as before — useful when debugging an
    AZ profile or a stuck task.
    """
    if not content and not heading:
        return None

    if log_type == "user":
        return f"👤 사용자: {content}"

    if not monitor_verbose:
        # Quiet path — let task completion drive the actual answer + metrics.
        return None

    if log_type in ("response", "ai", "agent"):
        if len(content) > 2000:
            content = content[:2000] + "\n...(생략)"
        return f"🤖 Agent Zero:\n{content}"
    elif log_type == "code_exe":
        code_preview = content[:500] if content else ""
        return f"⚙️ 코드 실행: {heading}\n```\n{code_preview}\n```"
    elif log_type == "tool":
        return f"🔧 도구: {heading}\n{content[:500] if content else ''}"
    elif log_type == "info":
        return f"ℹ️ {heading}: {content[:500] if content else ''}"
    elif log_type == "error":
        return f"❌ 오류: {heading}\n{content[:500] if content else ''}"
    elif log_type == "warning":
        return f"⚠️ 경고: {heading}\n{content[:500] if content else ''}"

    return None


# ── Agent Zero API: Telegram에서 직접 메시지 전송 ──
async def send_to_agent_zero(message: str) -> str:
    """Agent Zero /message_async API로 메시지 전송"""
    global az_context, csrf_token, monitor_context, monitor_log_version

    payload = {
        "text": message,
        "context": az_context,
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

            az_context = data.get("context", az_context)
            # 모니터도 같은 컨텍스트 추적하도록 동기화
            if monitor_context != az_context:
                monitor_context = az_context
                monitor_log_version = await sync_log_version(az_context)

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

        ctx_short = _short_id(monitor_context)
        return (
            f"Agent Zero: 정상 동작 중\n\n"
            f"📋 프로필: {profile}\n"
            f"🤖 메인 모델: {chat_model}\n"
            f"⚡ 유틸 모델: {util_model}\n\n"
            f"모니터링: {'켜짐' if monitor_enabled else '꺼짐'}\n"
            f"자동 추적: {'켜짐' if monitor_auto_follow else '꺼짐'}\n"
            f"현재 채팅: {ctx_short}"
        )
    except Exception as e:
        return f"Agent Zero: 연결 불가 - {str(e)}"


# ── Telegram handlers ──
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("권한이 없습니다.")
        return
    await update.message.reply_text(
        "Agent Zero Telegram Bridge\n\n"
        "사용법:\n"
        "• 메시지 전송 → Agent Zero에 지시\n"
        "• /status → 상태 확인\n"
        "• /chats → 채팅 목록\n"
        "• /switch [번호] → 채팅 전환\n"
        "• /new → 새 대화 시작\n"
        "• /logs → 전체 로그 파일 전송\n"
        "• /docs → 문서 목록/열람\n"
        "• /usage → 세션 내 토큰/비용 (휘발성)\n"
        "• /today → 오늘의 태스크 집계 (task JSON 기반)\n"
        "• /week → 최근 7일 집계\n"
        "• /tasks [N] → 최근 N개 태스크 목록\n"
        "• /backup → 설정 백업 파일 전송\n"
        "• /monitor_on → 모니터링 켜기\n"
        "• /monitor_off → 모니터링 끄기\n"
        "• /track_chat_on → 채팅 자동 추적 켜기 (웹 UI 채팅 전환 따라감)\n"
        "• /track_chat_off → 채팅 자동 추적 끄기 (현재 채팅 고정)\n"
        "• /help → 도움말"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    status = await check_agent_zero_status()
    await update.message.reply_text(status)


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """채팅 목록 조회"""
    if update.effective_chat.id != CHAT_ID:
        return

    contexts = await fetch_chat_list()
    if not contexts:
        await update.message.reply_text("활성 채팅이 없습니다.")
        return

    lines = ["📋 채팅 목록:\n"]
    for i, ctx in enumerate(contexts):
        ctx_id = ctx.get("id", "")
        name = ctx.get("name", "이름 없음")
        is_current = "→ " if ctx_id == monitor_context else "  "
        lines.append(f"{is_current}{i+1}. {name}\n   ID: {_short_id(ctx_id)}")

    lines.append(f"\n현재 추적 중: {_short_id(monitor_context)}")
    lines.append("\n채팅 전환: /switch [번호]")

    await update.message.reply_text("\n".join(lines))


async def cmd_switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """채팅 전환"""
    global az_context, monitor_context, monitor_log_version, monitor_log_guid

    if update.effective_chat.id != CHAT_ID:
        return

    args = context.args
    if not args:
        await update.message.reply_text("사용법: /switch [번호]\n/chats 로 목록을 먼저 확인하세요.")
        return

    try:
        idx = int(args[0]) - 1
    except ValueError:
        await update.message.reply_text("숫자를 입력하세요. 예: /switch 1")
        return

    # 캐시된 목록 사용, 없으면 새로 조회
    contexts = cached_contexts if cached_contexts else await fetch_chat_list()

    if idx < 0 or idx >= len(contexts):
        await update.message.reply_text(f"1~{len(contexts)} 범위에서 선택하세요.")
        return

    target = contexts[idx]
    target_id = target.get("id", "")
    target_name = target.get("name", "이름 없음")

    # Close any in-progress stream tied to the previous chat before swapping.
    _stream_reset(monitor_context or "_default")

    az_context = target_id
    monitor_context = target_id
    monitor_log_guid = ""
    # 현재 시점으로 스킵 (이전 히스토리 전송 방지)
    monitor_log_version = await sync_log_version(target_id)

    await update.message.reply_text(f"✅ 채팅 전환: {target_name}\nID: {_short_id(target_id)}")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a brand-new Agent Zero chat context and switch to it.

    Previous behavior: just zeroed the local az_context / monitor_context and
    relied on the next user message to lazily create a context. That meant:
      - The reply "새 대화를 시작합니다" was misleading (no chat created yet)
      - /chats wouldn't show the new chat until something was sent
      - With monitor_auto_follow ON, the next poll would latch back onto
        AZ's previously-active context — defeating the reset entirely

    Fixed behavior: calls AZ's /api/chat_create (the same endpoint the web
    UI's "New Chat" button uses), gets the real ctxid back, then pins both
    az_context and monitor_context to it before replying.
    """
    global az_context, monitor_context, monitor_log_version, monitor_log_guid
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return

    new_ctxid = ""
    try:
        session = await get_az_session()
        headers = get_headers()
        # Pass current_context so AZ can carry over project / model-override
        # data per its chat_inherit_project setting (matches web UI behavior).
        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/chat_create",
            json={"current_context": az_context or ""},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 403:
                await close_az_session()
                session = await get_az_session()
                headers = get_headers()
                async with session.post(
                    f"{AZ_API_URL}{AZ_API_PREFIX}/chat_create",
                    json={"current_context": az_context or ""},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as retry:
                    if retry.status == 200:
                        new_ctxid = (await retry.json()).get("ctxid", "")
            elif resp.status == 200:
                new_ctxid = (await resp.json()).get("ctxid", "")
    except Exception as e:
        logger.error(f"chat_create failed: {e}")

    if not new_ctxid:
        await msg.reply_text("⚠️ 새 대화 생성 실패 — Agent Zero 응답 확인 필요.")
        return

    # Close any in-progress stream from the previous chat — new chat's logs
    # should open their own message, not extend the old one.
    _stream_reset(monitor_context or "_default")

    az_context = new_ctxid
    monitor_context = new_ctxid
    monitor_log_guid = ""
    # Skip past whatever log_version the new context starts at so we don't
    # re-stream the (empty) initial state. This mirrors /switch behavior.
    monitor_log_version = await sync_log_version(new_ctxid)

    await msg.reply_text(
        f"✅ 새 대화 시작됨\nID: {_short_id(new_ctxid)}"
    )


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """현재 채팅의 전체 로그를 파일로 전송"""
    if update.effective_chat.id != CHAT_ID:
        return

    if not monitor_context:
        await update.message.reply_text("추적 중인 채팅이 없습니다. /chats 로 확인하세요.")
        return

    await update.message.reply_text("📄 로그 파일 생성 중...")

    try:
        session = await get_az_session()
        headers = get_headers()

        # chat export API로 전체 대화 JSON 가져오기
        async with session.post(
            f"{AZ_API_URL}{AZ_API_PREFIX}/chat_export",
            json={"ctxid": monitor_context},
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
        json_file.name = f"chat_log_{monitor_context[:8]}.json"

        await tg_bot.send_document(
            chat_id=CHAT_ID,
            document=json_file,
            caption=f"📋 채팅 로그 (Context: {_short_id(monitor_context)})",
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
            txt_file.name = f"chat_log_{monitor_context[:8]}.txt"
            await tg_bot.send_document(
                chat_id=CHAT_ID,
                document=txt_file,
                caption="📝 텍스트 요약 버전",
            )

    except Exception as e:
        await update.message.reply_text(f"로그 조회 실패: {str(e)}")


async def cmd_docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """문서 목록 조회 및 파일 전송"""
    import os
    import io

    if update.effective_chat.id != CHAT_ID:
        return

    # 문서 파일 목록
    doc_files = {}
    for f in ["GUIDE.md", "README.md"]:
        path = f"/app/{f}"
        if os.path.exists(path):
            doc_files[f] = path

    docs_dir = "/app/docs"
    if os.path.isdir(docs_dir):
        for f in sorted(os.listdir(docs_dir)):
            if f.endswith(".md"):
                doc_files[f"docs/{f}"] = os.path.join(docs_dir, f)

    if not doc_files:
        await update.message.reply_text("문서를 찾을 수 없습니다.")
        return

    args = context.args
    if not args:
        # 목록 표시
        lines = ["📚 문서 목록:\n"]
        for i, name in enumerate(doc_files.keys(), 1):
            lines.append(f"  {i}. {name}")
        lines.append("\n문서 보기: /docs [번호]")
        lines.append("전체 다운로드: /docs all")
        await update.message.reply_text("\n".join(lines))
        return

    if args[0].lower() == "all":
        # 전체 파일 전송
        for name, path in doc_files.items():
            with open(path, "rb") as f:
                await tg_bot.send_document(
                    chat_id=CHAT_ID,
                    document=f,
                    filename=name.replace("/", "_"),
                    caption=f"📄 {name}",
                )
        return

    try:
        idx = int(args[0]) - 1
        keys = list(doc_files.keys())
        if idx < 0 or idx >= len(keys):
            await update.message.reply_text(f"1~{len(keys)} 범위에서 선택하세요.")
            return

        name = keys[idx]
        path = doc_files[name]

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # 텔레그램 메시지로 보내기 (4000자 이하면 텍스트, 초과면 파일)
        if len(content) <= 4000:
            await update.message.reply_text(f"📄 **{name}**\n\n{content}")
        else:
            # 파일로 전송
            doc_file = io.BytesIO(content.encode("utf-8"))
            doc_file.name = name.replace("/", "_")
            await tg_bot.send_document(
                chat_id=CHAT_ID,
                document=doc_file,
                caption=f"📄 {name} ({len(content)} 글자)",
            )

    except ValueError:
        await update.message.reply_text("숫자 또는 'all'을 입력하세요. 예: /docs 1")


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
                "monitor_context": monitor_context,
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


def _compute_window_cost(window: str) -> dict:
    """Sum cost over a window from on-disk task JSONs.

    Authoritative source — uses the same `_aggregate` pipeline as /today
    /week, so budget alerts and dashboards never disagree. Going through
    `usage_today` (RAM-only) would miss any in-progress task and wouldn't
    survive a bridge restart.

    Returns:
        {
          "cost_usd":  float,
          "tasks":     int,
          "top_model": (name, cost) | None,
          "period_id": str,        # for cooldown key
          "label":     str,        # human-readable for the alert text
        }
    """
    now = _kst_now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "day":
        start = today
        end = today + timedelta(days=1)
        period_id = today.strftime("%Y-%m-%d")
        label = f"오늘 ({period_id} KST)"
    elif window == "week":
        start = today - timedelta(days=6)
        end = today + timedelta(days=1)
        # ISO week — naturally rotates Mon→Mon, but we just want a stable
        # bucket for cooldown so 7-day rolling window's date string is fine.
        period_id = today.strftime("%G-W%V")
        label = f"최근 7일 ({start.strftime('%m-%d')}~{today.strftime('%m-%d')} KST)"
    else:
        raise ValueError(f"unknown window: {window!r}")

    tasks = _filter_date_range(_load_task_jsons(), start, end)
    agg = _aggregate(tasks)
    by_model = agg.get("by_model") or {}
    top = None
    if by_model:
        m, b = max(by_model.items(), key=lambda kv: kv[1]["cost"])
        top = (m, b["cost"])
    return {
        "cost_usd": float(agg["cost_usd"]),
        "tasks": agg["tasks"],
        "top_model": top,
        "period_id": period_id,
        "label": label,
    }


async def _budget_check_window(window: str) -> bool:
    """Check one window's spend vs its limit. Fires at most ONE alert per
    call (the highest crossed threshold). Cooldown: per (period_id, window,
    threshold) — once today's 80% fires, won't fire again today even if
    spend keeps climbing.

    Returns True if any alert was sent (useful for tests / hourly logging).
    """
    limit_key = f"{window}_limit_usd"
    limit = _budget.get(limit_key)
    if not limit or limit <= 0:
        return False
    info = _compute_window_cost(window)
    cost = info["cost_usd"]
    if cost <= 0:
        return False
    ratio = cost / float(limit)
    period_id = info["period_id"]

    fired = _budget.setdefault("alerts_fired", {})
    sent_any = False
    # Walk thresholds high-to-low; fire the highest one crossed that hasn't
    # been fired yet for this period. Mark all lower not-yet-fired thresholds
    # as fired too so we don't trigger a cascade on the next call.
    for thresh, level_label, pct_label in BUDGET_THRESHOLDS:
        key = _alert_key(window, pct_label, period_id)
        if ratio >= thresh and key not in fired:
            if not sent_any:
                msg = _format_budget_alert(window, info, float(limit), level_label, ratio)
                try:
                    await send_telegram(msg)
                    sent_any = True
                except Exception as e:
                    logger.warning(f"[budget] alert send failed: {e}")
                    return False
            fired[key] = True
    if sent_any:
        _save_budget()
    return sent_any


async def budget_check_all() -> None:
    """Public entrypoint: check both day + week windows. Wired into
    /track (per-call) and the hourly sweep (catches missed alerts and
    week-rollover edge cases)."""
    try:
        await _budget_check_window("day")
        await _budget_check_window("week")
    except Exception as e:
        logger.warning(f"[budget] sweep error: {e}")


async def hourly_budget_sweep() -> None:
    """Hourly background task. Defensive — `_budget_check_window` is also
    called from /track, but that path can be skipped if AZ batches /track
    or fails-quiet. Hourly cadence is plenty for a 24h-budget signal."""
    logger.info("Hourly budget sweep started")
    while True:
        try:
            await asyncio.sleep(3600)  # 1 hour
            await budget_check_all()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[budget] hourly sweep error: {e}")


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
from pricing.snapshot import (
    take_pricing_snapshot as _take_pricing_snapshot_pure,
)


async def take_pricing_snapshot(
    *, force: bool = False, alert: bool = True
) -> dict | None:
    """bot.py wrapper that wires `send_telegram` into the pure
    `pricing.snapshot.take_pricing_snapshot`. Keeps existing callers
    (`/pricing snapshot`, `daily_pricing_snapshot`) free of the extra
    `send_alert=` argument."""
    return await _take_pricing_snapshot_pure(
        force=force,
        alert=alert,
        send_alert=send_telegram if alert else None,
    )


async def daily_pricing_snapshot() -> None:
    """Background task. Wakes once a day at 00:30 KST and takes a snapshot.

    Why 00:30 (not 00:00): the daily usage reporter fires at 00:01, leaving
    a 29-minute buffer so we don't pile concurrent Telegrams on the user.
    Also gives LiteLLM's GitHub source a moment to settle if they happen
    to release on midnight UTC.
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
            await take_pricing_snapshot(alert=True)
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


def _parse_by_flag(args) -> str | None:
    """Parse `by:model` / `by:profile` from Telegram command args.

    Supports:
      /today                → None        (default compact view)
      /today by:model       → "model"
      /today by:profile     → "profile"
      /today model          → "model"     (shorthand, same as by:model)
      /today profile        → "profile"

    Unknown keys return None so the command gracefully falls back to the
    default view instead of erroring.
    """
    if not args:
        return None
    a = args[0].strip().lower()
    if a in ("by:model", "model", "by:models", "models"):
        return "model"
    if a in ("by:profile", "profile", "by:profiles", "profiles"):
        return "profile"
    return None


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Today's task aggregate from on-disk JSONs (KST boundary).

    Supports an optional breakdown flag (issue #20):
      /today              — default summary + compact model list
      /today by:model     — detailed per-model table (replaces compact list)
      /today by:profile   — per-profile table

    NOTE on `effective_message`: python-telegram-bot's `CommandHandler` fires
    on BOTH new messages and edits (edits go through `update.edited_message`,
    not `update.message`). Reaching for `update.message.reply_text` directly
    crashes with NoneType when the user edits a previous /today (e.g. "/today"
    → "/today by:model" to test variants). `effective_message` resolves to
    whichever variant actually carries the command text.
    """
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return
    tasks = _load_task_jsons()
    today_start = _kst_now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)
    todays = _filter_date_range(tasks, today_start, tomorrow)
    agg = _aggregate(todays)

    lines = _format_agg_block(f"오늘 ({today_start.strftime('%Y-%m-%d')} KST)", agg)

    # Data quality caveat (issue #24)
    banner = _quality_banner(_data_quality_summary(todays))
    if banner:
        lines.insert(1, banner)

    # Cache efficiency line — Anthropic-only, suppressed when no anthropic
    # traffic in window (issue #22).
    ce = _cache_efficiency(agg)
    if ce:
        lines.append(_format_cache_line(ce))

    # Breakdown mode — `/today by:model` or `by:profile` replaces the default
    # compact model list. Default stays as today's pre-Wave-3 format so the
    # unflagged command remains familiar.
    mode = _parse_by_flag(context.args)
    if mode == "model":
        lines.append("")
        lines.extend(_format_model_breakdown(agg, title="모델별 (상세)"))
    elif mode == "profile":
        lines.append("")
        lines.extend(_format_profile_breakdown(agg))
    else:
        by_model = agg.get("by_model", {})
        if by_model:
            lines.append("\n🤖 모델별:")
            for model, b in sorted(
                by_model.items(),
                key=lambda kv: (kv[1]["cost"], kv[1]["calls"]),
                reverse=True,
            ):
                cache = (
                    f" | cache r:{b['cache_read']:,} c:{b['cache_create']:,}"
                    if (b["cache_read"] or b["cache_create"]) else ""
                )
                lines.append(
                    f"  • {model}: {b['calls']}× "
                    f"in {b['input']:,} out {b['output']:,}{cache} → ${b['cost']:.4f}"
                )

    await msg.reply_text("\n".join(lines))


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Last 7 days: daily breakdown + grand totals (KST boundary).

    Supports an optional breakdown flag (issue #20):
      /week              — default daily rows + compact weekly model list
      /week by:model     — detailed per-model table for the week
      /week by:profile   — per-profile table for the week

    See cmd_today for the `effective_message` rationale (edit-aware).
    """
    if update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return
    all_tasks = _load_task_jsons()
    today_start = _kst_now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)  # last 7 days including today
    week_end = today_start + timedelta(days=1)
    window = _filter_date_range(all_tasks, week_start, week_end)

    lines = [f"📈 최근 7일 ({week_start.strftime('%m-%d')} ~ {today_start.strftime('%m-%d')} KST)"]

    # Data quality caveat (issue #24)
    banner = _quality_banner(_data_quality_summary(window))
    if banner:
        lines.append(banner)
    lines.append("")

    # Per-day rows
    days_with_data = 0
    for i in range(7):
        day_start = week_start + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_tasks = _filter_date_range(window, day_start, day_end)
        if not day_tasks:
            continue
        days_with_data += 1
        agg = _aggregate(day_tasks)
        lines.append(
            f"  {day_start.strftime('%m-%d')}: "
            f"{agg['tasks']}건 · {agg['llm_calls']} LLM · ${agg['cost_usd']:.4f}"
        )

    if days_with_data == 0:
        lines.append("  (데이터 없음)")

    # Grand total
    grand = _aggregate(window)
    lines.append("")
    lines.extend(_format_agg_block("주간 합계", grand))

    # Cache efficiency for the full week (Anthropic only).
    ce = _cache_efficiency(grand)
    if ce:
        lines.append(_format_cache_line(ce))

    mode = _parse_by_flag(context.args)
    if mode == "model":
        lines.append("")
        lines.extend(_format_model_breakdown(grand, title="모델별 (주간 상세)"))
    elif mode == "profile":
        lines.append("")
        lines.extend(_format_profile_breakdown(grand, title="프로파일별 (주간)"))
    else:
        by_model = grand.get("by_model", {})
        if by_model:
            lines.append("\n🤖 모델별 (주간):")
            for model, b in sorted(
                by_model.items(),
                key=lambda kv: (kv[1]["cost"], kv[1]["calls"]),
                reverse=True,
            ):
                lines.append(f"  • {model}: {b['calls']}× → ${b['cost']:.4f}")

    await msg.reply_text("\n".join(lines))


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the most recent N tasks with individual summaries (default 10)."""
    if update.effective_chat.id != CHAT_ID:
        return
    n = 10
    args = context.args
    if args:
        try:
            n = max(1, min(50, int(args[0])))
        except ValueError:
            pass
    tasks = _load_task_jsons()
    recent = list(reversed(tasks[-n:]))
    if not recent:
        await update.message.reply_text("최근 태스크가 없습니다.")
        return

    lines = [f"🗂 최근 {len(recent)}개 태스크\n"]
    status_icon = {"completed": "✅", "orphaned": "⚠️", "pending": "⏳"}
    for t in recent:
        tid = t.get("task_id", "?")
        reason = t.get("ended_reason", "pending")
        icon = status_icon.get(reason, "•")
        elapsed = t.get("elapsed_sec", 0) or 0
        totals = t.get("totals") or {}
        cost = totals.get("cost_usd", 0.0) or 0.0
        # Short HH:MM:SS from task_id (task-YYYYMMDD-HHMMSS-xxxxxx)
        parts = tid.split("-")
        when = "?"
        if len(parts) >= 3 and len(parts[2]) == 6:
            when = f"{parts[2][:2]}:{parts[2][2:4]}:{parts[2][4:6]}"
        lines.append(
            f"{icon} {when} {elapsed:.0f}s · "
            f"LLM {totals.get('llm_calls', 0)} · "
            f"도구 {totals.get('tool_calls', 0)} · "
            f"${cost:.4f}"
        )
    await update.message.reply_text("\n".join(lines))


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
                marker = "🚨" if ratio >= 1.5 else ("❌" if ratio >= 1.0 else ("⚠️" if ratio >= 0.8 else "✅"))
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
            await msg.reply_text(f"사용법: /budget {sub} <USD>  · 예: /budget {sub} 5  ·  /budget {sub} off")
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
            await msg.reply_text("📂 저장된 가격 스냅샷이 없습니다. /pricing snapshot 으로 강제 생성 가능.")
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
        result = await take_pricing_snapshot(force=True, alert=True)
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
        lines.append(
            f"  {alias}\n"
            f"    in ${in_rate * 1e6:.2f}/1M · out ${out_rate * 1e6:.2f}/1M"
            f"{' · cache_read $' + format(cr_rate * 1e6, '.2f') + '/1M' if cr_rate else ''}"
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
        for model, stats in sorted(by_model.items(), key=lambda x: x[1]["cost_usd"], reverse=True):
            cr = stats.get("cache_read_tokens", 0)
            cc = stats.get("cache_creation_tokens", 0)
            rt = stats.get("reasoning_tokens", 0)
            cache_part = f" | cache r:{cr:,} c:{cc:,}" if (cr or cc) else ""
            reason_part = f" | reasoning:{rt:,}" if rt else ""
            lines.append(
                f"  {model}\n"
                f"    {stats['requests']}건 | "
                f"in:{stats['input_tokens']:,} out:{stats['output_tokens']:,}{cache_part}{reason_part}\n"
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


async def cmd_monitor_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitor_enabled, monitor_log_version
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_enabled = True
    # 현재 시점부터 모니터링 (이전 히스토리 전송 방지)
    monitor_log_version = await sync_log_version(monitor_context)
    await update.message.reply_text("✅ 웹 채팅 모니터링이 켜졌습니다. (현재 시점부터)")


async def cmd_monitor_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitor_enabled
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_enabled = False
    await update.message.reply_text("🔇 웹 채팅 모니터링이 꺼졌습니다.")


async def cmd_track_chat_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-track which AZ chat the monitor watches.

    Renamed from /follow_on for clarity — old name was ambiguous about
    direction (does \"follow\" mean Telegram→AZ or AZ→Telegram?). Both
    /monitor and /track_chat are AZ→Telegram concerns; /track_chat
    specifically controls whether the monitor switches its target when
    the AZ web UI activates a different chat.

    /follow_on is kept registered as an alias for muscle memory.
    """
    global monitor_auto_follow
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_auto_follow = True
    await update.message.reply_text(
        "✅ 채팅 자동 추적 켜짐: 웹에서 채팅 전환 시 모니터가 따라갑니다."
    )


async def cmd_track_chat_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pin the monitor to its current chat regardless of AZ web's active chat.
    See cmd_track_chat_on for the rename rationale."""
    global monitor_auto_follow
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_auto_follow = False
    await update.message.reply_text(
        "📌 채팅 자동 추적 꺼짐: 현재 채팅만 고정 추적합니다."
    )


async def cmd_verbose_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show every AZ log (info/tool/code_exe/response/error/warning) during
    a task — useful when debugging a stuck profile. Default-quiet behavior
    (only user echoes + task-completion summary) is the normal mode."""
    global monitor_verbose
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_verbose = True
    await update.message.reply_text(
        "🔊 상세 모니터 켜짐: AZ 활동 로그(도구/코드/info 등) 모두 텔레그램으로 전송."
    )


async def cmd_verbose_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to quiet mode — only user echoes during task, completion-time
    answer + metrics card from task_report."""
    global monitor_verbose
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_verbose = False
    await update.message.reply_text(
        "🔇 상세 모니터 꺼짐: 진행 중엔 조용, 완료 시 답변+메트릭만."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    await update.message.reply_text(
        "📖 명령어 목록:\n\n"
        "대화:\n"
        "  /new → 새 대화 시작\n"
        "  /chats → 채팅 목록 조회\n"
        "  /switch [번호] → 채팅 전환\n"
        "  /logs → 전체 로그 파일 전송\n"
        "  /docs → 문서 목록 조회\n"
        "  /docs [번호] → 문서 열람\n"
        "  /docs all → 전체 문서 다운로드\n"
        "  일반 메시지 → Agent Zero에 지시\n\n"
        "모니터링:\n"
        "  /monitor_on → 웹 채팅 알림 켜기 (현재 시점부터)\n"
        "  /monitor_off → 웹 채팅 알림 끄기\n"
        "  /track_chat_on → 채팅 자동 추적 켜기 (웹 UI 채팅 전환 따라감)\n"
        "  /track_chat_off → 채팅 자동 추적 끄기 (현재 채팅 고정)\n"
        "  /verbose_on → 진행 중 AZ 활동 로그도 보기 (디버그용)\n"
        "  /verbose_off → 진행 중엔 조용히, 완료 시만 알림 (기본)\n\n"
        "상태/비용:\n"
        "  /status → Agent Zero 상태 확인\n"
        "  /usage → 세션 내 토큰/비용 (bridge 재시작 시 초기화)\n"
        "  /today → 오늘의 태스크 집계 (영구 데이터)\n"
        "  /week → 최근 7일 일별 + 합계\n"
        "  /tasks [N] → 최근 N개 태스크 목록 (기본 10)\n"
        "  /budget [day|week] [USD] → 예산 한도 + 자동 알림\n"
        "  /pricing [list|diff|snapshot] → LiteLLM 가격 스냅샷 + drift\n"
        "  /backup → 설정 경량 백업 (ZIP 파일 전송)\n"
        "  /help → 도움말"
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
async def webhook_handler(request):
    """HTTP POST → Telegram forwarding.

    Payload:
      {
        "text": "...",
        "markdown": true,        # optional — convert text from markdown to
                                 # Telegram-safe HTML before sending
        "parse_mode": "HTML",    # optional — sent verbatim if you've
                                 # already-formatted HTML/MarkdownV2
        "kind": "task_response"  # optional — adds a UI prefix emoji
                                 # AFTER markdown conversion so leading
                                 # `## header` / `| table |` aren't
                                 # displaced from line-start
      }

    `markdown: true` is the easy path: senders write plain markdown
    (```code```, **bold**, etc.) and the bridge handles conversion +
    fallback to plain text on parse failure. AZ's task_report uses this
    with kind="task_response" so the 🤖 emoji lands AFTER conversion.
    """
    # Map of `kind` → UI prefix emoji + space. Keep tiny; this is purely
    # presentation. Empty/unknown kind → no prefix.
    KIND_PREFIX = {
        "task_response": "🤖 ",
    }
    try:
        data = await request.json()
        raw_text = data.get("text", data.get("message", str(data)))
        parse_mode = data.get("parse_mode")
        kind = data.get("kind")
        prefix = KIND_PREFIX.get(kind, "")

        if data.get("markdown"):
            # Convert THEN prefix — order matters. If we prefixed first,
            # leading "## header" would no longer be at line-start, breaking
            # the converter's `^#` regex. Same goes for table rows
            # starting with `|`.
            converted = md_to_telegram_html(raw_text)
            if prefix:
                converted = f"{prefix}{converted}"
                fallback = f"{prefix}{raw_text}"
            else:
                fallback = raw_text
            await send_telegram(
                converted,
                parse_mode="HTML",
                fallback_text=fallback,
            )
        else:
            text = f"{prefix}{raw_text}" if prefix else raw_text
            await send_telegram(text, parse_mode=parse_mode)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def usage_track_handler(request):
    """HTTP POST로 토큰 사용량 기록 (cache + reasoning 토큰 포함).

    Payload: {
        "model": "anthropic/claude-sonnet-4-6",
        "input_tokens": 1500,
        "output_tokens": 500,
        "cache_read_tokens": 0,        # optional (Anthropic prompt caching)
        "cache_creation_tokens": 0,    # optional
        "reasoning_tokens": 0          # optional (Claude 4.x extended thinking,
                                       #          OpenAI o-series; billed at output rate)
    }
    """
    try:
        data = await request.json()
        model = data.get("model", "unknown")
        input_tokens = int(data.get("input_tokens", 0))
        output_tokens = int(data.get("output_tokens", 0))
        cache_read = int(data.get("cache_read_tokens", 0))
        cache_creation = int(data.get("cache_creation_tokens", 0))
        reasoning = int(data.get("reasoning_tokens", 0))
        track_usage(
            model, input_tokens, output_tokens,
            cache_read, cache_creation, reasoning,
        )
        # Budget check fires AFTER track_usage so the cumulative cost in the
        # on-disk task JSONs has caught up. Fire-and-forget — never let a
        # failed alert reject the /track request itself.
        try:
            await budget_check_all()
        except Exception as e:
            logger.warning(f"[budget] post-track check failed: {e}")
        return web.json_response({
            "ok": True,
            "today": usage_today,
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def usage_get_handler(request):
    """GET 현재 사용량 조회"""
    return web.json_response({
        "today": usage_today,
        "history": usage_history[-7:],
    })


async def run_webhook_server():
    app = web.Application()
    app.router.add_post("/notify", webhook_handler)
    app.router.add_post("/track", usage_track_handler)
    app.router.add_get("/usage", usage_get_handler)
    # M5-E: web dashboard (issue #23). Both routes 404 when DASHBOARD_TOKEN
    # is unset, so registering them is harmless when disabled.
    app.router.add_get("/api/stats", stats_api_handler)
    app.router.add_get("/dashboard", dashboard_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8443)
    await site.start()
    routes_msg = "/notify, /track, /usage"
    if DASHBOARD_TOKEN:
        routes_msg += ", /dashboard, /api/stats (token-protected)"
    else:
        routes_msg += " (dashboard disabled — set DASHBOARD_TOKEN to enable)"
    logger.info(f"Webhook server started on :8443 ({routes_msg})")


# ── 일일 사용량 리포트 스케줄러 ──
async def daily_usage_reporter():
    """매일 자정에 일일 사용량 리포트를 Telegram으로 전송"""
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

        # 어제 사용량 리포트
        if usage_today["requests"] > 0:
            lines = [
                f"📊 일일 사용량 리포트 ({usage_today['date']})\n",
                f"총 요청: {usage_today['requests']}건",
                f"총 입력: {usage_today['input_tokens']:,} 토큰",
                f"총 출력: {usage_today['output_tokens']:,} 토큰",
                f"총 비용: ${usage_today['cost_usd']:.4f}",
            ]
            by_model = usage_today.get("by_model", {})
            if by_model:
                lines.append("\n🤖 모델별 내역:")
                for model, stats in sorted(by_model.items(), key=lambda x: x[1]["cost_usd"], reverse=True):
                    lines.append(
                        f"  {model}\n"
                        f"    {stats['requests']}건 | "
                        f"in:{stats['input_tokens']:,} out:{stats['output_tokens']:,} | "
                        f"${stats['cost_usd']:.4f}"
                    )
            await send_telegram("\n".join(lines))


# ── Post-init: 모니터 시작 ──
async def post_init(application: Application):
    global tg_bot
    tg_bot = application.bot
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

    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_webhook_server())

    logger.info("Telegram Bridge Bot started (with monitor + multi-chat)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
