#!/usr/bin/env bash
# 윈도우 WSL2 에서 server/ compose 를 실행한다 (먼저 sync.sh flush).
#
#   ./dev-deploy.sh              # 기본 서비스(cozy-server, nginx) up -d --build
#   ./dev-deploy.sh logs <svc>   # 로그 팔로우
#   ./dev-deploy.sh ps           # 상태
#   ./dev-deploy.sh down         # 중지·삭제
#   ./dev-deploy.sh <svc>...     # 지정 서비스만 up -d --build
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

remote() {
    ssh -p "$WIN_SSH_PORT" "$WIN_USER@$WIN_HOST" "cd $REMOTE_PATH/server && COZY_HTTP_PORT=${COZY_HTTP_PORT:-8120} $*"
}

"$SCRIPT_DIR/sync.sh" flush

case "${1:-}" in
"") remote "docker compose up -d --build" ;;
logs) shift; remote "docker compose logs -f --tail=100 $*" ;;
ps) remote "docker compose ps" ;;
down) remote "docker compose down" ;;
*) remote "docker compose up -d --build $*" ;;
esac
