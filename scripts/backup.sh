#!/bin/bash
# ── Agent Zero Harness Kit 백업 스크립트 ──
# 사용법: ./scripts/backup.sh [full|config|light]
#   full   - 전체 백업 (설정 + 메모리 + 프롬프트 + 프로필 + 로그 + 작업물)
#   config - 설정만 백업 (설정 + 프롬프트 + 프로필)
#   light  - 경량 백업 (설정 파일만: .env·settings.json 등 민감 데이터)

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
    "agent-zero/settings.json"
    "docker-compose.yml"
)

# config: 프롬프트 + 프로필 + 사용자 플러그인 상태 + 브리지 영구 상태
#
# usr-plugins/ 의 하위에는:
#   - _model_config/config.json — 활성 chat/utility model 선택 (사용자 상태,
#     `--force-recreate` 시 휘발 위험, 1KB 미만)
#   - _browser/, _office/, docker_terminal/, stop_process/ — 업스트림 이미지가
#     첫 실행 때 복사하는 vendored 자산 (수백 MB의 playwright 바이너리 포함,
#     이미지 재생성으로 복구 가능 → 백업 불필요)
#   - chat_pdf_export/, dashboard_link/ — 이 repo 의 git 추적 코드 (백업 불필요)
# 따라서 _model_config/ 만 명시적으로 포함. 향후 다른 플러그인이 영구 상태를
# 추가하면 그 플러그인의 state 디렉토리를 여기 명시적으로 추가한다.
#
# telegram-bridge/data/ 는 /budget 한도 + alert cooldown 같은 영구 데이터를
# 들고 있고, docker-compose 상에서 :ro 가 아니라 read-write 로 마운트된다.
# `--force-recreate` 한 번이면 의도와 무관하게 휘발될 수 있다.
BACKUP_DIRS_CONFIG=(
    "agent-zero/prompts"
    "agent-zero/agents"
    "agent-zero/git-init.sh"
    "agent-zero/usr-plugins/_model_config"
    "telegram-bridge/data"
)

# full: 메모리 + 로그 + 작업물
BACKUP_DIRS_FULL=(
    "agent-zero/memory"
    "agent-zero/logs"
    "agent-zero/work_dir"
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
