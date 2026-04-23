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

# Telegram Bot 인스턴스 (모니터에서 사용)
tg_bot: Bot | None = None

# 채팅 목록 캐시
cached_contexts: list = []

# ── 토큰 사용량 추적 ──
usage_today: dict = {
    "date": _kst_now().strftime("%Y-%m-%d"),
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "requests": 0,
    "cost_usd": 0.0,
    "by_model": {},  # 모델별 집계
}
usage_history: list = []  # 최근 7일

# LiteLLM 모델 가격표 (원격 자동 업데이트)
_model_cost_map: dict = {}


def _load_model_cost_map():
    """LiteLLM의 최신 모델 가격표를 로드 (GitHub에서 자동 다운로드)"""
    global _model_cost_map
    try:
        import httpx
        resp = httpx.get(
            "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
            timeout=10,
        )
        if resp.status_code == 200:
            _model_cost_map = resp.json()
            logger.info(f"[Cost] Loaded {len(_model_cost_map)} model prices from LiteLLM")
            return
    except Exception as e:
        logger.warning(f"[Cost] Failed to fetch remote prices: {e}")

    # fallback: 기본값
    _model_cost_map = {}
    logger.warning("[Cost] Using fallback cost estimation")


def _model_info(model: str) -> dict:
    """Resolve a model name against the LiteLLM price map with AZ aliasing.

    AZ tags streaming calls as "anthropic/claude-sonnet-4-6" but LiteLLM
    keys them as "claude-sonnet-4-5-20250929". Try the exact key first,
    then a few known aliases, then strip the `anthropic/` prefix.
    """
    if model in _model_cost_map:
        return _model_cost_map[model]
    aliases = {
        "anthropic/claude-sonnet-4-6": "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6": "claude-sonnet-4-5-20250929",
        "anthropic/claude-haiku-4-5": "claude-haiku-4-5-20251001",
    }
    if model in aliases and aliases[model] in _model_cost_map:
        return _model_cost_map[aliases[model]]
    if model.startswith("anthropic/"):
        tail = model.split("/", 1)[1]
        if tail in _model_cost_map:
            return _model_cost_map[tail]
    return {}


def calc_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Cache-aware cost calc. On Anthropic the provider-reported
    `input_tokens` is already the regular-input-only count (cache_read /
    cache_creation are billed separately), so we don't subtract — we just
    price each bucket at its own rate.
    """
    info = _model_info(model)
    in_rate = info.get("input_cost_per_token", 0.000003)      # $3 / 1M fallback
    out_rate = info.get("output_cost_per_token", 0.000015)    # $15 / 1M
    read_rate = info.get("cache_read_input_token_cost", in_rate * 0.10)
    create_rate = info.get("cache_creation_input_token_cost", in_rate * 1.25)
    return (
        max(0, int(input_tokens)) * in_rate
        + max(0, int(output_tokens)) * out_rate
        + max(0, int(cache_read_tokens)) * read_rate
        + max(0, int(cache_creation_tokens)) * create_rate
    )


def _normalize_model(model: str) -> str:
    """Canonicalize model name before aggregating.

    LiteLLM's kwargs["model"] and the stream-probe POST sometimes carry the
    provider prefix (`anthropic/claude-sonnet-4-6`) and sometimes don't
    (`claude-sonnet-4-6`). Without this the `by_model` dict splits one model
    into two rows with mismatched cache/cost stats. Mirrors the same helper
    now living in agent-zero/lib/task_report.py (issue #24 Wave 2).
    """
    if not isinstance(model, str) or not model:
        return model
    if model.startswith("anthropic/"):
        return model.split("/", 1)[1]
    return model


def track_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
):
    """사용량 누적 (cache tokens 포함)"""
    global usage_today
    # Collapse provider-prefixed and bare forms so /today by_model stays unified.
    model = _normalize_model(model)
    today = _kst_now().strftime("%Y-%m-%d")

    # 날짜가 바뀌면 리셋
    if usage_today["date"] != today:
        if usage_today["requests"] > 0:
            usage_history.append(usage_today.copy())
            while len(usage_history) > 7:
                usage_history.pop(0)
        usage_today = {
            "date": today,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "requests": 0,
            "cost_usd": 0.0,
            "by_model": {},
        }

    cost = calc_cost(model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens)

    usage_today["input_tokens"] += input_tokens
    usage_today["output_tokens"] += output_tokens
    usage_today["cache_read_tokens"] = usage_today.get("cache_read_tokens", 0) + cache_read_tokens
    usage_today["cache_creation_tokens"] = usage_today.get("cache_creation_tokens", 0) + cache_creation_tokens
    usage_today["requests"] += 1
    usage_today["cost_usd"] += cost

    if model not in usage_today["by_model"]:
        usage_today["by_model"][model] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "requests": 0,
            "cost_usd": 0.0,
        }
    m = usage_today["by_model"][model]
    m["input_tokens"] += input_tokens
    m["output_tokens"] += output_tokens
    m["cache_read_tokens"] = m.get("cache_read_tokens", 0) + cache_read_tokens
    m["cache_creation_tokens"] = m.get("cache_creation_tokens", 0) + cache_creation_tokens
    m["requests"] += 1
    m["cost_usd"] += cost


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
async def send_telegram(text: str):
    """Telegram으로 메시지 전송 (길이 제한 처리)"""
    if not tg_bot or not text.strip():
        return
    try:
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                await tg_bot.send_message(chat_id=CHAT_ID, text=text[i : i + 4000])
        else:
            await tg_bot.send_message(chat_id=CHAT_ID, text=text)
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


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
                old_ctx = monitor_context[:8] if monitor_context else "없음"
                new_ctx = new_context[:8]
                await send_telegram(f"🔄 채팅 전환 감지: {old_ctx}... → {new_ctx}...")
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
                for log in logs:
                    log_type = log.get("type", "")
                    heading = log.get("heading", "")
                    content = log.get("content", "")
                    temp = log.get("temp", False)

                    if temp:
                        continue

                    msg = format_monitor_message(log_type, heading, content)
                    if msg:
                        await send_telegram(msg)

                monitor_log_version = new_log_version

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            await asyncio.sleep(10)
            continue

        await asyncio.sleep(3)


def format_monitor_message(log_type: str, heading: str, content: str) -> str | None:
    """로그 타입에 따라 Telegram 메시지 포맷"""
    if not content and not heading:
        return None

    if log_type == "user":
        return f"👤 사용자: {content}"
    elif log_type in ("response", "ai", "agent"):
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

    except asyncio.TimeoutError:
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

        # 설정 조회 (프로필, 모델 정보)
        profile = "알 수 없음"
        chat_model = "알 수 없음"
        util_model = "알 수 없음"
        try:
            async with session.post(
                f"{AZ_API_URL}{AZ_API_PREFIX}/settings_get",
                json={},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as settings_resp:
                if settings_resp.status == 200:
                    settings = await settings_resp.json()
                    profile = settings.get("agent_profile", "알 수 없음")
                    chat_model = settings.get("chat_model_name", "알 수 없음")
                    util_model = settings.get("util_model_name", "알 수 없음")
        except Exception:
            pass

        ctx_short = monitor_context[:8] + "..." if monitor_context else "없음"
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
        "• /follow_on → 자동 추적 켜기\n"
        "• /follow_off → 자동 추적 끄기\n"
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
        lines.append(f"{is_current}{i+1}. {name}\n   ID: {ctx_id[:12]}...")

    lines.append(f"\n현재 추적 중: {monitor_context[:12]}..." if monitor_context else "\n현재 추적 중: 없음")
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

    az_context = target_id
    monitor_context = target_id
    monitor_log_guid = ""
    # 현재 시점으로 스킵 (이전 히스토리 전송 방지)
    monitor_log_version = await sync_log_version(target_id)

    await update.message.reply_text(f"✅ 채팅 전환: {target_name}\nID: {target_id[:12]}...")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global az_context, monitor_context, monitor_log_version
    if update.effective_chat.id != CHAT_ID:
        return
    az_context = ""
    monitor_context = ""
    await close_az_session()
    # 새 세션이므로 다음 poll에서 sync_log_version이 자동 처리
    # 빈 컨텍스트는 poll 시 새 컨텍스트를 받아오며 그때 sync됨
    monitor_log_version = await sync_log_version("")
    await update.message.reply_text("새 대화를 시작합니다.")


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
            caption=f"📋 채팅 로그 (Context: {monitor_context[:12]}...)",
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
        lines.append(f"\n문서 보기: /docs [번호]")
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


# ── Task JSON aggregation (issue #1 M2) ──
TASKS_DIR = "/app/tasks"


def _load_task_jsons() -> list[dict]:
    """Read every task JSON from the mounted AZ logs dir.

    Returns a list of parsed dicts sorted by `started_at` ascending.
    Malformed files are skipped silently; they show up in logs once and
    then get ignored so /today doesn't crash on a single bad file.
    """
    if not os.path.isdir(TASKS_DIR):
        return []
    items: list[dict] = []
    for name in os.listdir(TASKS_DIR):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        path = os.path.join(TASKS_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                items.append(json.load(f))
        except Exception as e:
            logger.debug(f"skip bad task json {name}: {e}")
    items.sort(key=lambda r: r.get("started_at") or "")
    return items


def _aggregate(tasks: list[dict]) -> dict:
    """Sum totals across a list of tasks, also grouping by model and status."""
    agg = {
        "tasks": len(tasks),
        "completed": 0,
        "orphaned": 0,
        "pending": 0,
        "tool_calls": 0,
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.0,
        "by_model": {},
    }
    for t in tasks:
        reason = t.get("ended_reason", "pending")
        if reason in agg:
            agg[reason] += 1
        totals = t.get("totals") or {}
        for k in ("tool_calls", "llm_calls", "input_tokens", "output_tokens",
                  "cache_read_tokens", "cache_creation_tokens"):
            agg[k] += int(totals.get(k, 0) or 0)
        agg["cost_usd"] += float(totals.get("cost_usd", 0.0) or 0.0)
        for c in t.get("llm_calls") or []:
            m = c.get("model") or "unknown"
            bucket = agg["by_model"].setdefault(m, {
                "calls": 0, "input": 0, "output": 0,
                "cache_read": 0, "cache_create": 0, "cost": 0.0,
            })
            bucket["calls"] += 1
            bucket["input"] += int(c.get("input_tokens", 0) or 0)
            bucket["output"] += int(c.get("output_tokens", 0) or 0)
            bucket["cache_read"] += int(c.get("cache_read_tokens", 0) or 0)
            bucket["cache_create"] += int(c.get("cache_creation_tokens", 0) or 0)
            bucket["cost"] += float(c.get("cost_usd", 0.0) or 0.0)
    agg["cost_usd"] = round(agg["cost_usd"], 6)
    return agg


def _filter_date_range(tasks: list[dict], start, end) -> list[dict]:
    """Filter tasks whose `started_at` (ISO, UTC) falls in [start, end).

    `start` and `end` are naive `datetime` objects treated as **KST wall clock**.
    AZ writes started_at in UTC with +00:00 offset; we convert each task's
    UTC instant to KST before comparing, so "today" means "today in KST"
    regardless of the container OS timezone.
    """
    out = []
    for t in tasks:
        started = t.get("started_at")
        if not started:
            continue
        try:
            # fromisoformat handles "+00:00"
            ts = datetime.fromisoformat(started)
        except Exception:
            continue
        # Normalize to KST wall-clock for date-boundary comparison.
        if ts.tzinfo:
            ts_local = ts.astimezone(KST).replace(tzinfo=None)
        else:
            ts_local = ts  # assume caller already passed KST-naive
        if start <= ts_local < end:
            out.append(t)
    return out


def _data_quality_summary(tasks: list[dict]) -> dict:
    """Scan a task list for known observability gaps (M1/M2/M3 evolution).

    These gaps mean the per-task cost / token numbers drift from Anthropic
    Console reality. We surface the counts in /today and /week so the user
    knows when to trust the number.

    Returns:
        {
          "total":       total tasks in window,
          "legacy_cost": tasks without totals.cost_usd (pre-M3 schema),
          "approximate": tasks with any approximate-token LLM call (pre-M2 str
                         response bug),
        }
    """
    legacy_cost = 0
    approximate = 0
    for t in tasks:
        totals = t.get("totals") or {}
        if "cost_usd" not in totals:
            legacy_cost += 1
        for c in t.get("llm_calls") or []:
            if c.get("tokens_approximate"):
                approximate += 1
                break
    return {
        "total": len(tasks),
        "legacy_cost": legacy_cost,
        "approximate": approximate,
    }


def _quality_banner(dq: dict) -> str | None:
    """Return a one-line caveat if any data quality gap is present, else None."""
    if dq["legacy_cost"] == 0 and dq["approximate"] == 0:
        return None
    parts = []
    if dq["legacy_cost"]:
        parts.append(f"M3 이전 {dq['legacy_cost']}건")
    if dq["approximate"]:
        parts.append(f"근사 토큰 {dq['approximate']}건")
    return (
        "⚠️ 신뢰도: " + " / ".join(parts)
        + " — Anthropic 대시보드와 차이 있을 수 있음"
    )


def _format_agg_block(title: str, agg: dict) -> list[str]:
    """Render an aggregate dict as a Telegram-friendly block."""
    lines = [
        f"📊 {title}",
        f"  태스크: {agg['tasks']}건 "
        f"(✅{agg['completed']} ⚠️{agg['orphaned']} ⏳{agg['pending']})",
    ]
    if agg["tasks"] == 0:
        return lines
    lines.append(
        f"  LLM 호출: {agg['llm_calls']}건 · 도구: {agg['tool_calls']}건"
    )
    lines.append(
        f"  토큰: in {agg['input_tokens']:,} · out {agg['output_tokens']:,}"
    )
    if agg["cache_read_tokens"] or agg["cache_creation_tokens"]:
        lines.append(
            f"  캐시: read {agg['cache_read_tokens']:,} · "
            f"create {agg['cache_creation_tokens']:,}"
        )
    lines.append(f"  💰 ${agg['cost_usd']:.4f}")
    return lines


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Today's task aggregate from on-disk JSONs (KST boundary)."""
    if update.effective_chat.id != CHAT_ID:
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

    by_model = agg.get("by_model", {})
    if by_model:
        lines.append("\n🤖 모델별:")
        for model, b in sorted(by_model.items(), key=lambda kv: kv[1]["cost"], reverse=True):
            cache = (
                f" | cache r:{b['cache_read']:,} c:{b['cache_create']:,}"
                if (b["cache_read"] or b["cache_create"]) else ""
            )
            lines.append(
                f"  • {model}: {b['calls']}× "
                f"in {b['input']:,} out {b['output']:,}{cache} → ${b['cost']:.4f}"
            )

    await update.message.reply_text("\n".join(lines))


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Last 7 days: daily breakdown + grand totals (KST boundary)."""
    if update.effective_chat.id != CHAT_ID:
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
    by_model = grand.get("by_model", {})
    if by_model:
        lines.append("\n🤖 모델별 (주간):")
        for model, b in sorted(by_model.items(), key=lambda kv: kv[1]["cost"], reverse=True):
            lines.append(f"  • {model}: {b['calls']}× → ${b['cost']:.4f}")

    await update.message.reply_text("\n".join(lines))


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


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """토큰 사용량 + 비용 조회 (cache 토큰 포함)"""
    if update.effective_chat.id != CHAT_ID:
        return

    today = usage_today
    cache_read = today.get("cache_read_tokens", 0)
    cache_create = today.get("cache_creation_tokens", 0)

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
    lines.append(f"비용: ${today['cost_usd']:.4f}")

    by_model = today.get("by_model", {})
    if by_model:
        lines.append("\n🤖 모델별:")
        for model, stats in sorted(by_model.items(), key=lambda x: x[1]["cost_usd"], reverse=True):
            cr = stats.get("cache_read_tokens", 0)
            cc = stats.get("cache_creation_tokens", 0)
            cache_part = f" | cache r:{cr:,} c:{cc:,}" if (cr or cc) else ""
            lines.append(
                f"  {model}\n"
                f"    {stats['requests']}건 | "
                f"in:{stats['input_tokens']:,} out:{stats['output_tokens']:,}{cache_part}\n"
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


async def cmd_follow_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitor_auto_follow
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_auto_follow = True
    await update.message.reply_text("✅ 자동 추적 켜짐: 웹에서 채팅 전환 시 모니터가 자동으로 따라갑니다.")


async def cmd_follow_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitor_auto_follow
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_auto_follow = False
    await update.message.reply_text("📌 자동 추적 꺼짐: 현재 채팅만 고정 추적합니다.")


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
        "  /follow_on → 채팅 자동 추적 켜기\n"
        "  /follow_off → 채팅 자동 추적 끄기\n\n"
        "상태/비용:\n"
        "  /status → Agent Zero 상태 확인\n"
        "  /usage → 세션 내 토큰/비용 (bridge 재시작 시 초기화)\n"
        "  /today → 오늘의 태스크 집계 (영구 데이터)\n"
        "  /week → 최근 7일 일별 + 합계\n"
        "  /tasks [N] → 최근 N개 태스크 목록 (기본 10)\n"
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
    """HTTP POST로 알림 수신 → Telegram 전달"""
    try:
        data = await request.json()
        text = data.get("text", data.get("message", str(data)))
        await send_telegram(text)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def usage_track_handler(request):
    """HTTP POST로 토큰 사용량 기록 (cache 토큰 포함).

    Payload: {
        "model": "anthropic/claude-sonnet-4-6",
        "input_tokens": 1500,
        "output_tokens": 500,
        "cache_read_tokens": 0,     # optional (Anthropic prompt caching)
        "cache_creation_tokens": 0  # optional
    }
    """
    try:
        data = await request.json()
        model = data.get("model", "unknown")
        input_tokens = int(data.get("input_tokens", 0))
        output_tokens = int(data.get("output_tokens", 0))
        cache_read = int(data.get("cache_read_tokens", 0))
        cache_creation = int(data.get("cache_creation_tokens", 0))
        track_usage(model, input_tokens, output_tokens, cache_read, cache_creation)
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
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8443)
    await site.start()
    logger.info("Webhook server started on :8443 (/notify, /track, /usage)")


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
    logger.info("Monitor + daily reporter tasks created")


def main():
    # LiteLLM 모델 가격표 로드 (GitHub에서 최신 다운로드)
    _load_model_cost_map()

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
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("monitor_on", cmd_monitor_on))
    app.add_handler(CommandHandler("monitor_off", cmd_monitor_off))
    app.add_handler(CommandHandler("follow_on", cmd_follow_on))
    app.add_handler(CommandHandler("follow_off", cmd_follow_off))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_webhook_server())

    logger.info("Telegram Bridge Bot started (with monitor + multi-chat)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
