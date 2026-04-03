"""
Telegram ↔ Agent Zero Bridge Bot
- Agent Zero 응답을 Telegram으로 전달 (알림)
- Telegram 메시지를 Agent Zero에 전달 (양방향 지시)
- Agent Zero 웹 채팅 모니터링 → Telegram 실시간 알림
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

# Telegram Bot 인스턴스 (모니터에서 사용)
tg_bot: Bot | None = None


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


# ── Agent Zero 웹 채팅 모니터 ──
async def monitor_agent_zero():
    """Agent Zero의 모든 대화를 백그라운드로 모니터링하여 Telegram에 전달"""
    global monitor_log_version, monitor_context, monitor_log_guid, monitor_enabled

    logger.info("Agent Zero monitor started")
    await asyncio.sleep(10)  # 초기 대기 (Agent Zero 부팅 대기)

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
                    # CSRF 만료 → 세션 리셋
                    await close_az_session()
                    await asyncio.sleep(3)
                    continue
                if resp.status != 200:
                    await asyncio.sleep(5)
                    continue
                poll_data = await resp.json()

            # 컨텍스트 동기화
            new_context = poll_data.get("context", "")
            new_log_guid = poll_data.get("log_guid", "")
            new_log_version = poll_data.get("log_version", 0)

            # 새 대화가 시작된 경우
            if new_context and new_context != monitor_context:
                monitor_context = new_context
                monitor_log_version = 0
                monitor_log_guid = new_log_guid
                await asyncio.sleep(2)
                continue

            # 대화가 리셋된 경우
            if new_log_guid and new_log_guid != monitor_log_guid:
                monitor_log_guid = new_log_guid
                monitor_log_version = 0
                await asyncio.sleep(2)
                continue

            logs = poll_data.get("logs", [])

            if logs:
                for log in logs:
                    log_type = log.get("type", "")
                    heading = log.get("heading", "")
                    content = log.get("content", "")
                    temp = log.get("temp", False)

                    # 임시 메시지 건너뛰기
                    if temp:
                        continue

                    # 로그 타입별 Telegram 알림
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

        await asyncio.sleep(3)  # 폴링 간격


def format_monitor_message(log_type: str, heading: str, content: str) -> str | None:
    """로그 타입에 따라 Telegram 메시지 포맷"""
    if not content and not heading:
        return None

    if log_type == "user":
        return f"👤 사용자: {content}"
    elif log_type in ("response", "ai", "agent"):
        # 너무 긴 응답은 요약
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

    # 기타 타입은 무시 (너무 많은 알림 방지)
    return None


# ── Agent Zero API: Telegram에서 직접 메시지 전송 ──
async def send_to_agent_zero(message: str) -> str:
    """Agent Zero /message_async API로 메시지 전송 후 /poll로 응답 수집"""
    global az_context, csrf_token

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

        # Telegram에서 보낸 메시지의 응답은 모니터가 알아서 전달하므로
        # 여기서는 전송 확인만 반환
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
            if resp.status == 200:
                return (
                    f"Agent Zero: 정상 동작 중\n"
                    f"CSRF Token: {'있음' if csrf_token else '없음'}\n"
                    f"모니터링: {'켜짐' if monitor_enabled else '꺼짐'}\n"
                    f"Context: {monitor_context or '없음'}"
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
        "• /status → Agent Zero 상태 확인\n"
        "• /new → 새 대화 시작\n"
        "• /monitor_on → 웹 채팅 모니터링 켜기\n"
        "• /monitor_off → 웹 채팅 모니터링 끄기\n"
        "• /help → 도움말"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    status = await check_agent_zero_status()
    await update.message.reply_text(status)


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
    await update.message.reply_text("✅ 웹 채팅 모니터링이 켜졌습니다.\nAgent Zero 웹 UI의 대화가 Telegram으로 전달됩니다.")


async def cmd_monitor_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitor_enabled
    if update.effective_chat.id != CHAT_ID:
        return
    monitor_enabled = False
    await update.message.reply_text("🔇 웹 채팅 모니터링이 꺼졌습니다.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    await update.message.reply_text(
        "명령어:\n"
        "/start - 봇 시작\n"
        "/status - Agent Zero 상태 확인\n"
        "/new - 새 대화 시작\n"
        "/monitor_on - 웹 채팅 모니터링 켜기\n"
        "/monitor_off - 웹 채팅 모니터링 끄기\n"
        "/help - 도움말\n\n"
        "일반 메시지를 보내면 Agent Zero에 전달됩니다.\n"
        "웹에서 채팅하면 자동으로 Telegram에 알림이 옵니다."
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
    """Telegram Bot 시작 후 모니터링 태스크 실행"""
    global tg_bot
    tg_bot = application.bot
    asyncio.create_task(monitor_agent_zero())
    logger.info("Monitor task created")


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("monitor_on", cmd_monitor_on))
    app.add_handler(CommandHandler("monitor_off", cmd_monitor_off))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 알림 webhook 서버 동시 실행
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_webhook_server())

    logger.info("Telegram Bridge Bot started (with monitor)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
