"""`/start`, `/help` Telegram commands — Phase O carve from bot.py (issue #79).

Pure text-reply commands with no bot.py-internal deps beyond CHAT_ID.
`/status` stays in bot.py for now since it calls
`check_agent_zero_status()` which still depends on monitor state
globals; carving it needs the monitor state to move first.
"""
from __future__ import annotations

import os

from telegram import Update
from telegram.ext import ContextTypes

# Read CHAT_ID directly from env — same source bot.py uses.
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])


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
