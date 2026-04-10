#!/bin/bash
# ── Agent Zero Harness Kit 백업 스크립트 ──
# 사용법: ./scripts/backup.sh [full|config|light]
#   full   - 전체 백업 (설정 + 인증 + 메모리 + 프롬프트 + 프로필 + 로그 + 작업물)
#   config - 설정만 백업 (설정 + 인증 + 프롬프트 + 프로필)
#   light  - 경량 백업 (설정 + 인증만, 민감 데이터)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${PROJECT_DIR}/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
MODE="${1:-config}"

mkdir -p "$BACKUP_DIR"

echo "========================================="
echo " Agent Zero Harness Kit Backup"
echo " 모드: ${MODE}"
echo " 시간: ${TIMESTAMP}"
echo "========================================="

# 공통: 설정 파일
BACKUP_FILES=(
    ".env"
    "cliproxy/config.yaml"
    "agent-zero/settings.json"
    "docker-compose.yml"
)

# 공통: 인증
BACKUP_DIRS_AUTH=(
    "cliproxy/auth"
)

# config: 프롬프트 + 프로필
BACKUP_DIRS_CONFIG=(
    "agent-zero/prompts"
    "agent-zero/agents"
    "agent-zero/git-init.sh"
)

# full: 메모리 + 로그 + 작업물
BACKUP_DIRS_FULL=(
    "agent-zero/memory"
    "agent-zero/logs"
    "agent-zero/work_dir"
    "cliproxy/logs"
)

cd "$PROJECT_DIR"

# 백업 대상 수집
TARGETS=()

# 파일 추가
for f in "${BACKUP_FILES[@]}"; do
    if [ -e "$f" ]; then
        TARGETS+=("$f")
    fi
done

# 인증 디렉토리
for d in "${BACKUP_DIRS_AUTH[@]}"; do
    if [ -d "$d" ]; then
        TARGETS+=("$d")
    fi
done

# config 이상이면 프롬프트/프로필 추가
if [ "$MODE" = "config" ] || [ "$MODE" = "full" ]; then
    for d in "${BACKUP_DIRS_CONFIG[@]}"; do
        if [ -e "$d" ]; then
            TARGETS+=("$d")
        fi
    done
fi

# full이면 메모리/로그/작업물 추가
if [ "$MODE" = "full" ]; then
    for d in "${BACKUP_DIRS_FULL[@]}"; do
        if [ -d "$d" ]; then
            TARGETS+=("$d")
        fi
    done
fi

# 백업 파일 생성
BACKUP_FILE="${BACKUP_DIR}/backup_${MODE}_${TIMESTAMP}.tar.gz"

echo ""
echo "백업 대상:"
for t in "${TARGETS[@]}"; do
    echo "  ✓ $t"
done

echo ""
echo "압축 중..."
tar -czf "$BACKUP_FILE" "${TARGETS[@]}" 2>/dev/null

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo ""
echo "========================================="
echo " 백업 완료!"
echo " 파일: $BACKUP_FILE"
echo " 크기: $SIZE"
echo "========================================="
echo ""
echo "복원: ./scripts/restore.sh $BACKUP_FILE"
