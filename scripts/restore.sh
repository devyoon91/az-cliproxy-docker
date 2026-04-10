#!/bin/bash
# ── Agent Zero Harness Kit 복원 스크립트 ──
# 사용법: ./scripts/restore.sh <백업파일.tar.gz>

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -z "$1" ]; then
    echo "사용법: ./scripts/restore.sh <백업파일.tar.gz>"
    echo ""
    echo "사용 가능한 백업:"
    ls -lh "${PROJECT_DIR}/backups/"*.tar.gz 2>/dev/null || echo "  백업 파일이 없습니다."
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    # 상대 경로 시도
    BACKUP_FILE="${PROJECT_DIR}/backups/$1"
    if [ ! -f "$BACKUP_FILE" ]; then
        echo "파일을 찾을 수 없습니다: $1"
        exit 1
    fi
fi

echo "========================================="
echo " Agent Zero Harness Kit 복원"
echo " 파일: $BACKUP_FILE"
echo "========================================="

echo ""
echo "백업 내용:"
tar -tzf "$BACKUP_FILE" | head -30
echo "..."

echo ""
read -p "복원하시겠습니까? 기존 파일이 덮어씌워집니다. (y/N): " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "취소되었습니다."
    exit 0
fi

cd "$PROJECT_DIR"

echo ""
echo "복원 중..."
tar -xzf "$BACKUP_FILE"

echo ""
echo "========================================="
echo " 복원 완료!"
echo "========================================="
echo ""
echo "다음 단계:"
echo "  1. docker compose down"
echo "  2. docker compose up -d --build"
echo "  3. 필요시 CLIProxy OAuth 재인증"
