"""
Telegram ↔ Agent Zero Bridge Bot
- Agent Zero 응답을 Telegram으로 전달 (알림)
- Telegram 메시지를 Agent Zero에 전달 (양방향 지시)
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


async def get_az_session() -> aiohttp.ClientSession:
    """Agent Zero 세션 가져오기 (CSRF 토큰 포함)"""
    global az_session, csrf_token

    if az_session and not az_session.closed:
        return az_session

    # 쿠키 유지하는 세션 생성
    jar = CookieJar(unsafe=True)
    az_session = aiohttp.ClientSession(cookie_jar=jar)

    # CSRF 토큰 획득
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


# ── Agent Zero API helpers ──
async def send_to_agent_zero(message: str) -> str:
    """Agent Zero /message_async API로 메시지 전송 후 /poll로 응답 수집"""
    global az_context, csrf_token

    payload = {
        "text": message,
        "context": az_context,
    }

    headers = {}
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    try:
        session = await get_az_session()

        # 1. 메시지 전송 (async - 즉시 반환)
        async with session.post(
            f"{AZ_API_URL}/message_async",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 403:
                # CSRF 토큰 만료 → 재발급 후 재시도
                await close_az_session()
                session = await get_az_session()
                headers["X-CSRF-Token"] = csrf_token
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

        # 2. 폴링으로 응답 수집
        collected = []
        log_from = 0
        max_polls = 120  # 최대 10분 (5초 * 120)
        idle_count = 0

        for _ in range(max_polls):
            await asyncio.sleep(5)

            poll_payload = {
                "log_from": log_from,
                "context": az_context,
                "timezone": "Asia/Seoul",
            }

            try:
                async with session.post(
                    f"{AZ_API_URL}/poll",
                    json=poll_payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as poll_resp:
                    if poll_resp.status != 200:
                        continue
                    poll_data = await poll_resp.json()
            except Exception:
                continue

            log_version = poll_data.get("log_version", 0)
            logs = poll_data.get("logs", [])

            if logs:
                idle_count = 0
                for log in logs:
                    log_type = log.get("type", "")
                    heading = log.get("heading", "")
                    content = log.get("content", "")
                    temp = log.get("temp", False)

                    # 임시 메시지(thinking 등) 건너뛰기
                    if temp:
                        continue

                    # 에이전트 최종 응답 수집
                    if log_type in ("response", "ai", "agent"):
                        collected.append(content)
                    elif log_type == "info" and content:
                        collected.append(f"[{heading}] {content}")

                log_from = log_version
            else:
                idle_count += 1
                # 응답 수집 후 10초(2회) 이상 새 로그 없으면 완료로 판단
                if collected and idle_count >= 2:
                    break

        if collected:
            return "\n\n".join(collected)
        return "Agent Zero가 아직 처리 중이거나 응답이 없습니다."

    except asyncio.TimeoutError:
        return "Agent Zero 응답 시간 초과"
    except Exception as e:
        return f"Agent Zero 연결 실패: {str(e)}"


async def check_agent_zero_status() -> str:
    """Agent Zero 상태 확인"""
    try:
        session = await get_az_session()
        async with session.get(
            f"{AZ_API_URL}/",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                return f"Agent Zero: 정상 동작 중\nCSRF Token: {'있음' if csrf_token else '없음'}\nContext: {az_context or '없음 (새 대화)'}"
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
        "• /help → 도움말"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    status = await check_agent_zero_status()
    await update.message.reply_text(status)


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """새 대화 컨텍스트 시작"""
    global az_context
    if update.effective_chat.id != CHAT_ID:
        return
    az_context = ""
    await close_az_session()  # 세션 리셋 (새 CSRF 토큰)
    await update.message.reply_text("새 대화를 시작합니다.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    await update.message.reply_text(
        "명령어:\n"
        "/start - 봇 시작\n"
        "/status - Agent Zero 상태 확인\n"
        "/new - 새 대화 시작\n"
        "/help - 도움말\n\n"
        "일반 메시지를 보내면 Agent Zero에 전달됩니다."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("권한이 없습니다.")
        return

    user_msg = update.message.text
    logger.info(f"User → Agent Zero: {user_msg[:100]}")

    await update.message.reply_text("Agent Zero에 전달 중... 응답을 기다립니다.")

    response = await send_to_agent_zero(user_msg)
    logger.info(f"Agent Zero → User: {response[:100]}")

    # 텔레그램 메시지 길이 제한 (4096자)
    if len(response) > 4000:
        for i in range(0, len(response), 4000):
            await update.message.reply_text(response[i : i + 4000])
    else:
        await update.message.reply_text(response)


# ── Notification webhook ──
async def send_notification(text: str):
    bot = Bot(token=BOT_TOKEN)
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"
    await bot.send_message(chat_id=CHAT_ID, text=text)


async def webhook_handler(request):
    """HTTP POST로 알림 수신 → Telegram 전달
    curl -X POST http://telegram-bridge:8443/notify \
         -H 'Content-Type: application/json' \
         -d '{"text": "작업 완료!"}'
    """
    try:
        data = await request.json()
        text = data.get("text", data.get("message", str(data)))
        await send_notification(text)
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


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_webhook_server())

    logger.info("Telegram Bridge Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
