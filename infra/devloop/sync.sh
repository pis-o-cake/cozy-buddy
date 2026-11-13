#!/usr/bin/env bash
# 맥 → 윈도우 WSL2 코드 동기화 (mutagen one-way-replica, ssh).
# Docker 는 맥이 아니라 윈도우 WSL2 에서만 돈다. 사용법:
#
#   ./sync.sh start    # 세션 생성 (있으면 resume)
#   ./sync.sh stop     # 일시정지
#   ./sync.sh resume   # 재개
#   ./sync.sh status   # 상태 (Watching for changes 면 정상)
#   ./sync.sh flush    # 동기화 완료까지 대기
#
# one-way-replica: 윈도우 쪽 수정이 맥으로 역류하지 않는다. 코드는 맥에서만 고친다.
# 로컬 원본은 LOCAL_PATH(메인 체크아웃)다 — 워크트리가 아니다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    echo "ERROR: $SCRIPT_DIR/config.env 가 없습니다." >&2
    echo "  cp $SCRIPT_DIR/config.env.example $SCRIPT_DIR/config.env" >&2
    exit 1
fi
# shellcheck source=config.env.example
source "$SCRIPT_DIR/config.env"

if [[ -z "${WIN_HOST:-}" || -z "${WIN_USER:-}" || "$WIN_USER" == "changeme" ]]; then
    echo "ERROR: $SCRIPT_DIR/config.env 의 WIN_HOST · WIN_USER 를 채우세요." >&2
    exit 1
fi

REMOTE_URL="$WIN_USER@$WIN_HOST:$WIN_SSH_PORT:$REMOTE_PATH"

cmd_start() {
    if mutagen sync list "$SESSION_NAME" &>/dev/null; then
        mutagen sync resume "$SESSION_NAME"
        echo "session '$SESSION_NAME' resumed"
        return
    fi
    # 루트 .env(OpenAI 키)는 gitignore 지만 일부러 같이 보낸다 — 서버가 ../.env 를 읽는다.
    # 0644/0755 필수 — mutagen 기본값(0600/0700)이면 컨테이너 비 root 사용자가
    # 바인드 마운트 소스를 못 읽는다.
    mutagen sync create \
        --name="$SESSION_NAME" \
        --sync-mode=one-way-replica \
        --default-file-mode=0644 \
        --default-directory-mode=0755 \
        --ignore-vcs \
        --ignore=".claude/worktrees" \
        --ignore="**/.venv" \
        --ignore="**/__pycache__" \
        --ignore="*.pyc" \
        --ignore="**/.pytest_cache" \
        --ignore="**/.ruff_cache" \
        --ignore="**/.mypy_cache" \
        --ignore="server/gen" \
        --ignore="server/data" \
        --ignore="hub/**/build" \
        --ignore="hub/.gradle" \
        --ignore="hub/local.properties" \
        --ignore="**/.idea" \
        --ignore=".DS_Store" \
        --ignore="spikes/**/audio" \
        --ignore="spikes/**/.venv" \
        "$LOCAL_PATH" "$REMOTE_URL"
    echo "session '$SESSION_NAME' created -> $REMOTE_URL"
}

case "${1:-}" in
start) cmd_start ;;
stop) mutagen sync pause "$SESSION_NAME" ;;
resume) mutagen sync resume "$SESSION_NAME" ;;
status) mutagen sync list "$SESSION_NAME" ;;
flush) mutagen sync flush "$SESSION_NAME" ;;
*)
    echo "usage: $0 {start|stop|resume|status|flush}" >&2
    exit 1
    ;;
esac
