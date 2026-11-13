#!/bin/sh
# verify.sh 의 컴포넌트 의존(protocol -> server/hub/mobile) 선택 로직 테스트.
# 임시 git 저장소에 더미 check.sh 를 만들어 어떤 컴포넌트가 실행되는지 확인한다. 네트워크 불필요.
set -eu

src=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM

fails=0

setup_repo() {
  rm -rf "$tmp/repo"
  mkdir -p "$tmp/repo" "$tmp/bin"
  cd "$tmp/repo"
  git init -q .
  git config user.email t@example.com
  git config user.name t
  mkdir -p scripts/tests
  cp "$src/verify.sh" scripts/verify.sh
  for f in scripts/check-secrets.sh scripts/tests/test-check-secrets.sh scripts/tests/test-verify-deps.sh; do
    echo 'exit 0' > "$f"
  done
  printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/engsys"
  chmod +x "$tmp/bin/engsys"
  for d in protocol server hub mobile; do
    mkdir -p "$d/scripts"
    echo 'exit 0' > "$d/scripts/check.sh"
    echo init > "$d/file.txt"
  done
  git add -A
  git commit -q -m init
  # 비교 기준(origin/main)을 만들고 별도 브랜치로 이동해 base 가 계산되게 한다.
  git update-ref refs/remotes/origin/main HEAD
  git checkout -q -b work
  git commit -q --allow-empty -m work
}

run_verify() {
  PATH="$tmp/bin:$PATH" sh scripts/verify.sh 2>&1 || echo "verify exited non-zero"
}

assert_runs() {
  name=$1
  out=$2
  expected=$3
  actual=$(printf '%s\n' "$out" | sed -n 's/^run    \([a-z]*\).*/\1/p' | tr '\n' ' ' | sed 's/ $//')
  if [ "$actual" = "$expected" ]; then
    echo "ok   $name"
    return 0
  fi
  echo "FAIL $name: expected [$expected] got [$actual]" >&2
  printf '%s\n' "$out" >&2
  fails=$((fails + 1))
}

setup_repo
echo x >> protocol/file.txt
out=$(run_verify)
assert_runs "protocol-only runs dependents" "$out" "protocol server hub mobile"
if ! printf '%s\n' "$out" | grep -q '^run    server (protocol changed)$'; then
  echo "FAIL missing '(protocol changed)' label" >&2
  fails=$((fails + 1))
fi

setup_repo
echo x >> server/file.txt
assert_runs "server-only runs server" "$(run_verify)" "server"

setup_repo
assert_runs "no change skips all" "$(run_verify)" ""

setup_repo
out=$(VERIFY_ALL=1 run_verify)
assert_runs "VERIFY_ALL runs all" "$out" "protocol server hub mobile"

if [ "$fails" -ne 0 ]; then
  echo "$fails failure(s)" >&2
  exit 1
fi
echo "all passed"
