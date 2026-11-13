#!/bin/sh
# 저장소 전체 검증. 문서 계약은 항상, 컴포넌트 검사는 변경된 컴포넌트만 돌린다.
# VERIFY_ALL=1 이면 변경 여부와 무관하게 모든 컴포넌트를 검사한다.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$root"

failed=0

engsys docs check --project . || failed=1
engsys review check --project . || failed=1

# 기밀 검사는 변경 여부와 무관하게 항상 돈다. base 가 있으면 그 이후 추가분도 아래에서 검사한다.
sh scripts/tests/test-check-secrets.sh || failed=1
sh scripts/check-secrets.sh --tree || failed=1

# origin/main 이 없거나 HEAD 가 같은 커밋이면 비교 기준이 없으므로 전부 변경으로 본다.
base=
if git rev-parse --verify --quiet origin/main >/dev/null; then
  base=$(git merge-base HEAD origin/main 2>/dev/null || true)
  [ "$base" != "$(git rev-parse HEAD)" ] || base=
fi

if [ -n "$base" ]; then
  sh scripts/check-secrets.sh --range "$base" HEAD || failed=1
fi

# 커밋된 변경(base..HEAD), 작업 트리 변경, 추적되지 않는 파일을 모두 센다.
is_changed() {
  [ -n "$base" ] || return 0
  [ -z "$(git diff --name-only "$base" HEAD -- "$1/")" ] || return 0
  [ -z "$(git diff --name-only HEAD -- "$1/")" ] || return 0
  [ -z "$(git ls-files --others --exclude-standard -- "$1/")" ] || return 0
  return 1
}

# server·hub·mobile 은 protocol 에서 코드를 생성하므로 protocol 변경 시 함께 검사한다(CI paths-filter 와 동일).
protocol_changed=0
if is_changed protocol; then protocol_changed=1; fi

for dir in protocol server hub mobile; do
  if [ ! -d "$dir" ]; then
    echo "skip   $dir: directory does not exist"
    continue
  fi
  if [ ! -f "$dir/scripts/check.sh" ]; then
    echo "skip   $dir: no scripts/check.sh"
    continue
  fi
  reason=
  if [ "${VERIFY_ALL:-0}" != 1 ] && ! is_changed "$dir"; then
    if [ "$dir" = protocol ] || [ "$protocol_changed" != 1 ]; then
      echo "skip   $dir: unchanged"
      continue
    fi
    reason=" (protocol changed)"
  fi
  echo "run    $dir$reason"
  sh "$dir/scripts/check.sh" || { echo "FAIL   $dir" >&2; failed=1; }
done

exit "$failed"
