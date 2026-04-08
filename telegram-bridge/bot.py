"""
Telegram ↔ Agent Zero Bridge Bot
- Agent Zero 응답을 Telegram으로 전달 (알림)
- Telegram 메시지를 Agent Zero에 전달 (양방향 지시)
- Agent Zero 웹 채팅 모니터링 → Telegram 실시간 알림
- 멀티채팅 지원: 채팅 목록 조회, 전환, 자동 추적
"""

import os
import asyncio
import logging
import aiohttp
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import web, CookieJar

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ──
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
AZ_API_URL = os.environ.get("AZ_API_URL", "http://agent-zero:80")

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


async def get_az_session() -> aiohttp.ClientSession:
    """Agent Zero 세션 가져오기 (CSRF 토큰 포함)"""
    global az_session, csrf_token

    if az_session and not az_session.closed:
        return az_session

    jar = CookieJar(unsafe=True)
    az_session = aiohttp.ClientSession(cookie_jar=jar)

    try:
        async with az_session.get(
            f"{AZ_API_URL}/csrf_token",
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
    headers = {}
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
            f"{AZ_API_URL}/poll",
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
            f"{AZ_API_URL}/poll",
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
                f"{AZ_API_URL}/poll",
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
            f"{AZ_API_URL}/message_async",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 403:
                await close_az_session()
                session = await get_az_session()
                headers = get_headers()
                async with session.post(
                    f"{AZ_API_URL}/message_async",
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
            monitor_context = az_context
            monitor_log_version = 0

        return "✅ Agent Zero에 전달 완료. 응답은 자동으로 전송됩니다."

    except asyncio.TimeoutError:
        return "Agent Zero 응답 시간 초과"
    except Exception as e:
        return f"Agent Zero 연결 실패: {str(e)}"


async def check_agent_zero_status() -> str:
    try:
        session = await get_az_session()
        async with session.get(
            f"{AZ_API_URL}/",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            ctx_short = monitor_context[:8] + "..." if monitor_context else "없음"
            if resp.status == 200:
                return (
                    f"Agent Zero: 정상 동작 중\n"
                    f"CSRF Token: {'있음' if csrf_token else '없음'}\n"
                    f"모니터링: {'켜짐' if monitor_enabled else '꺼짐'}\n"
                    f"자동 추적: {'켜짐' if monitor_auto_follow else '꺼짐'}\n"
                    f"현재 채팅: {ctx_short}"
                )
            return f"Agent Zero: 응답 코드 {resp.status}"
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
    monitor_log_version = 0
    await close_az_session()
    await update.message.reply_text("새 대화를 시작합니다.")


async def cmd_monitor_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitor_enabled
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_enabled = True
    await update.message.reply_text("✅ 웹 채팅 모니터링이 켜졌습니다.")


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
        "  일반 메시지 → Agent Zero에 지시\n\n"
        "모니터링:\n"
        "  /monitor_on → 웹 채팅 알림 켜기\n"
        "  /monitor_off → 웹 채팅 알림 끄기\n"
        "  /follow_on → 채팅 자동 추적 켜기\n"
        "  /follow_off → 채팅 자동 추적 끄기\n\n"
        "상태:\n"
        "  /status → Agent Zero 상태 확인\n"
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


# ── Notification webhook ──
async def webhook_handler(request):
    """HTTP POST로 알림 수신 → Telegram 전달"""
    try:
        data = await request.json()
        text = data.get("text", data.get("message", str(data)))
        await send_telegram(text)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def run_webhook_server():
    app = web.Application()
    app.router.add_post("/notify", webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8443)
    await site.start()
    logger.info("Notification webhook server started on :8443")


# ── Post-init: 모니터 시작 ──
async def post_init(application: Application):
    global tg_bot
    tg_bot = application.bot
    asyncio.create_task(monitor_agent_zero())
    logger.info("Monitor task created")


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("chats", cmd_chats))
    app.add_handler(CommandHandler("switch", cmd_switch))
    app.add_handler(CommandHandler("new", cmd_new))
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
