#!/usr/bin/env bash
# 윈도우 WSL2 에서 server/ compose 를 실행한다 (먼저 sync.sh flush).
#
#   ./dev-deploy.sh [--smoke]    # 기본 서비스 up -d --build 후 /health 대기 (--smoke: 가상 허브 스모크)
#   ./dev-deploy.sh health       # 배포 없이 /health 만 대기 (동기화·빌드 안 함)
#   ./dev-deploy.sh logs <svc>   # 로그 팔로우
#   ./dev-deploy.sh ps           # 상태
#   ./dev-deploy.sh down         # 중지·삭제
#   ./dev-deploy.sh pair NAME [AREA]   # 등록 코드 발급 (컨테이너 안 python -m app.cli)
#   ./dev-deploy.sh clients [--all]    # 발급된 클라이언트 목록
#   ./dev-deploy.sh revoke ID          # 클라이언트 폐기
#   ./dev-deploy.sh <svc>...     # 지정 서비스만 up -d --build
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"
RUN_SMOKE=0

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    echo "환경변수: HEALTH_TIMEOUT(초, 기본 60), COZY_HTTP_PORT, WIN_HOST (config.env 값을 덮어씀)"
}

args=()
for arg in "$@"; do
    case "$arg" in
    -h | --help) usage; exit 0 ;;
    --smoke) RUN_SMOKE=1 ;;
    *) args+=("$arg") ;;
    esac
done
set -- ${args[@]+"${args[@]}"}

if [[ ! -f "$SCRIPT_DIR/config.env" ]]; then
    echo "ERROR: $SCRIPT_DIR/config.env 가 없습니다." >&2
    echo "  cp $SCRIPT_DIR/config.env.example $SCRIPT_DIR/config.env" >&2
    exit 1
fi
# shellcheck source=config.env.example
# 호출 시 지정한 환경변수가 config.env 보다 우선한다.
_host_override="${WIN_HOST:-}"
_port_override="${COZY_HTTP_PORT:-}"
source "$SCRIPT_DIR/config.env"
[[ -n "$_host_override" ]] && WIN_HOST="$_host_override"
[[ -n "$_port_override" ]] && COZY_HTTP_PORT="$_port_override"

if [[ -z "${WIN_HOST:-}" || -z "${WIN_USER:-}" || "$WIN_USER" == "changeme" ]]; then
    echo "ERROR: $SCRIPT_DIR/config.env 의 WIN_HOST · WIN_USER 를 채우세요." >&2
    exit 1
fi

remote() {
    if [[ "${DEV_DEPLOY_DRY_RUN:-0}" == "1" ]]; then
        echo "DRY-RUN ssh -p $WIN_SSH_PORT $WIN_USER@$WIN_HOST: cd $REMOTE_PATH/server && $*"
        return 0
    fi
    ssh -p "$WIN_SSH_PORT" "$WIN_USER@$WIN_HOST" "cd $REMOTE_PATH/server && COZY_HTTP_PORT=${COZY_HTTP_PORT:-8120} $*"
}

# nginx 포트의 /health 가 200 이 될 때까지 기다린다. 실패하면 cozy-server 로그 끝 50줄을 출력한다.
wait_healthy() {
    local url="http://$WIN_HOST:${COZY_HTTP_PORT:-8120}/health"
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    echo "== /health 대기 ($url, 최대 ${HEALTH_TIMEOUT}s)"
    while ((SECONDS < deadline)); do
        if curl -fsS --max-time 3 -o /dev/null "$url" 2>/dev/null; then
            echo "OK: 서버 정상 ($((HEALTH_TIMEOUT - (deadline - SECONDS)))s)"
            return 0
        fi
        sleep 2
    done
    echo "ERROR: ${HEALTH_TIMEOUT}s 안에 /health 가 응답하지 않았습니다. cozy-server 로그 마지막 50줄:" >&2
    remote "docker compose logs --tail=50 cozy-server" >&2 || echo "(로그를 가져오지 못했습니다)" >&2
    return 1
}

run_smoke() {
    local e2e_dir="$SCRIPT_DIR/../../scripts/e2e"
    echo "== 스모크 시나리오 (scenarios/smoke.yaml)"
    if (cd "$e2e_dir" && uv run python voice_smoke.py --scenario scenarios/smoke.yaml \
        --url "ws://$WIN_HOST:${COZY_HTTP_PORT:-8120}/ws"); then
        echo "SMOKE PASS"
        return 0
    fi
    echo "SMOKE FAIL" >&2
    return 1
}

if [[ "${1:-}" == "health" ]]; then
    wait_healthy
    exit $?
fi

# 설정 검증 후 reload. 검증 실패 시 nginx -t 출력을 보이고 실패한다.
reload_nginx() {
    echo "== nginx 설정 검증·reload"
    local out
    if ! out="$(remote "docker compose exec -T nginx nginx -t -c /etc/cozy-nginx/nginx.conf" 2>&1)"; then
        echo "ERROR: nginx -t 실패:" >&2
        echo "$out" >&2
        return 1
    fi
    echo "$out"
    remote "docker compose exec -T nginx nginx -c /etc/cozy-nginx/nginx.conf -s reload"
}

# 운영자 CLI: 동기화·빌드 없이 실행 중인 컨테이너에 exec 한다. 인자는 원격 셸에 안전하게 인용한다.
run_cli() {
    local quoted
    quoted="$(printf '%q ' "$@")"
    remote "docker compose exec -T cozy-server python -m app.cli $quoted"
}

case "${1:-}" in
pair)
    [[ -n "${2:-}" ]] || { echo "사용법: $0 pair NAME [AREA]" >&2; exit 1; }
    if [[ -n "${3:-}" ]]; then run_cli pairing-code --name "$2" --area "$3"; else run_cli pairing-code --name "$2"; fi
    exit $?
    ;;
clients) shift; run_cli clients "$@"; exit $? ;;
revoke)
    [[ -n "${2:-}" ]] || { echo "사용법: $0 revoke ID" >&2; exit 1; }
    run_cli revoke "$2"
    exit $?
    ;;
esac

"$SCRIPT_DIR/sync.sh" flush

case "${1:-}" in
"")
    remote "docker compose up -d --build"
    reload_nginx
    wait_healthy
    if ((RUN_SMOKE)); then run_smoke; fi
    ;;
logs) shift; remote "docker compose logs -f --tail=100 $*" ;;
ps) remote "docker compose ps" ;;
down) remote "docker compose down" ;;
*) remote "docker compose up -d --build $*"; wait_healthy ;;
esac
