#!/bin/sh
# API 키·토큰이 git 으로 새어 나가는 것을 로컬에서 막는다.
# 사용: check-secrets.sh --staged | --range <base> <head> | --tree
set -eu

ALLOW_MARKER='secret-scan: allow'

usage() {
  echo "usage: check-secrets.sh --staged | --range <base> <head> | --tree" >&2
  exit 2
}

[ $# -ge 1 ] || usage
mode=$1

work=$(mktemp -d "${TMPDIR:-/tmp}/check-secrets.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
stream="$work/stream"

# 추가된 줄만 path:line:content 형태로 뽑는다. +++ 는 파일 헤더 구간에서만 헤더로 본다.
added_lines() {
  awk '
    /^diff --git /      { hdr = 1; f = ""; next }
    hdr && /^\+\+\+ /   { f = ($0 == "+++ /dev/null") ? "" : substr($0, 7); next }
    /^@@/               { hdr = 0; s = $0; sub(/^@@ -[^+]*\+/, "", s); sub(/[ ,].*/, "", s); n = s + 0; next }
    !hdr && /^\+/       { if (f != "") print f ":" n ":" substr($0, 2); n++ }
  '
}

findings=0

# 기밀을 로그에 다시 남기지 않도록 앞 6자만 보여 준다.
mask() {
  printf '%s' "$1" | cut -c1-6 | tr -d '\n'
  printf '…'
}

# scan_pattern <이름> <ERE> [-i]
scan_pattern() {
  name=$1
  regex=$2
  icase=${3:-}
  [ -s "$stream" ] || return 0
  # shellcheck disable=SC2086
  grep -E $icase -e "$regex" "$stream" > "$work/hits" 2>/dev/null || return 0
  while IFS= read -r hit; do
    # path 에 ':' 가 없다는 가정 하에 path:line:content 로 쪼갠다.
    loc=${hit%%:*}
    rest=${hit#*:}
    lineno=${rest%%:*}
    content=${rest#*:}
    # shellcheck disable=SC2086
    value=$(printf '%s\n' "$content" | grep -E -o $icase -e "$regex" | head -n 1) || continue
    [ -n "$value" ] || continue
    printf 'secret-scan: %s:%s: %s [%s]\n' "$loc" "$lineno" "$name" "$(mask "$value")" >&2
    findings=$((findings + 1))
  done < "$work/hits"
}

scan_stream() {
  # 허용 마커 줄과 리뷰 기록 경로는 제외한다.
  grep -v -F -e "$ALLOW_MARKER" "$stream" | grep -v -e '^\.engsys/reviews/' > "$stream.f" || true
  mv "$stream.f" "$stream"
  # IMPORTANT: 'task-…' 같은 단어 속 'sk-'를 잡지 않도록 앞 글자가 영숫자가 아닐 때만 본다.
  scan_pattern 'OpenAI API key' '(^|[^A-Za-z0-9])sk-(proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}'
  scan_pattern 'Anthropic API key' 'sk-ant-[A-Za-z0-9_-]{20,}'
  scan_pattern 'AWS access key id' 'AKIA[0-9A-Z]{16}'
  scan_pattern 'GitHub token' 'gh[pousr]_[A-Za-z0-9]{36,}'
  scan_pattern 'Google API key' 'AIza[0-9A-Za-z_-]{35}'
  scan_pattern 'Private key header' '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  scan_pattern 'Generic secret assignment' '(api[_-]?key|secret|token|password)[A-Za-z_]*[[:space:]]*[:=][[:space:]]*['"'"'"][^'"'"'"[:space:]]{16,}['"'"'"]' -i
}

case "$mode" in
  --staged)
    # 환경 파일은 내용과 무관하게 커밋 자체를 막는다.
    git diff --cached --name-only --diff-filter=ACMR > "$work/names"
    while IFS= read -r path; do
      base=${path##*/}
      case "$base" in
        .env.example) ;;
        .env|.env.*)
          printf 'secret-scan: %s: env file must not be committed\n' "$path" >&2
          findings=$((findings + 1))
          ;;
      esac
    done < "$work/names"
    git diff --cached -U0 --no-color --no-ext-diff | added_lines > "$stream"
    ;;
  --range)
    [ $# -eq 3 ] || usage
    git diff -U0 --no-color --no-ext-diff "$2" "$3" | added_lines > "$stream"
    ;;
  --tree)
    git grep -I -n -e '' HEAD | sed 's/^HEAD://' > "$stream"
    ;;
  *) usage ;;
esac

scan_stream

if [ "$findings" -gt 0 ]; then
  cat >&2 <<MSG

ERROR: $findings potential secret(s) found.
Fix: remove the secret from the change and load it from an untracked .env
or the environment instead. If it was ever pushed, treat it as leaked and
rotate it at the provider. For a verified false positive, append the marker
"$ALLOW_MARKER" to that line.
MSG
  exit 1
fi
echo "secret-scan: ok ($mode)"
