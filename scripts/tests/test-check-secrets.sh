#!/bin/sh
# check-secrets.sh 동작 검증. 가짜 키는 런타임에 이어 붙여 만들어 소스 자체가 스캐너에 걸리지 않게 한다.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
script="$here/../check-secrets.sh"

tmp=$(mktemp -d "${TMPDIR:-/tmp}/check-secrets-test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT INT TERM

pad=$(printf 'A%.0s' $(seq 1 40))
fake="sk-proj-""$pad"
fails=0

repo="$tmp/repo"
mkdir -p "$repo"
cd "$repo"
git init -q .
git config user.email t@example.com
git config user.name t
cp "$script" ./check-secrets.sh
echo base > README.md
git add README.md check-secrets.sh
git commit -q -m base --no-verify

run() { sh ./check-secrets.sh "$@" >"$tmp/out" 2>&1; }

expect_fail() {
  if run "$@"; then
    echo "FAIL: $label (expected failure)"; fails=$((fails + 1))
  else
    echo "ok:   $label"
  fi
}
expect_pass() {
  if run "$@"; then
    echo "ok:   $label"
  else
    echo "FAIL: $label (expected pass)"; cat "$tmp/out"; fails=$((fails + 1))
  fi
}
reset() { git reset -q --hard HEAD; git clean -fdq; }

label="staged fake OpenAI key fails"
printf 'KEY=%s\n' "$fake" > app.txt
git add app.txt
expect_fail --staged
if grep -F -q "$fake" "$tmp/out"; then
  echo "FAIL: output leaked full value"; fails=$((fails + 1))
else
  echo "ok:   masked output hides full value"
fi
reset

label="staged .env fails"
echo 'X=1' > .env
git add -f .env
expect_fail --staged
reset

label="staged nested .env.local fails"
mkdir -p sub
echo 'X=1' > sub/.env.local
git add -f sub/.env.local
expect_fail --staged
reset

label="staged .env.example passes"
echo 'OPENAI_API_KEY=' > .env.example
git add .env.example
expect_pass --staged
reset

label="allow marker passes"
printf 'KEY=%s # secret-scan: allow\n' "$fake" > app.txt
git add app.txt
expect_pass --staged
reset

label="clean change passes"
echo 'hello' > app.txt
git add app.txt
expect_pass --staged
reset

label="generic assignment fails"
printf 'api_key = "%s"\n' "$(printf 'b%.0s' $(seq 1 20))" > cfg.py
git add cfg.py
expect_fail --staged
reset

label="--range finds added key"
base=$(git rev-parse HEAD)
printf 'KEY=%s\n' "$fake" > app.txt
git add app.txt
git commit -q -m leak --no-verify
expect_fail --range "$base" HEAD

label="--tree finds committed key"
expect_fail --tree

label="--tree passes on clean tree"
git rm -q -f app.txt
git commit -q -m clean --no-verify
expect_pass --tree

[ "$fails" -eq 0 ] || { echo "$fails test(s) failed" >&2; exit 1; }
echo "all tests passed"
