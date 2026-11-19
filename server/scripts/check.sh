#!/bin/sh
# 저장소 루트에서 실행: sh server/scripts/check.sh
set -eu

server_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$server_dir"

for tool in poetry uv; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "error: $tool not found" >&2
    exit 1
  fi
done

export POETRY_VIRTUALENVS_IN_PROJECT=true

echo "== poetry install"
poetry install --no-interaction --quiet

echo "== generate protobuf"
sh scripts/gen_proto.sh

# requirements.lock.txt 는 poetry.lock 의 파생물이다. 어긋나면 이미지가 테스트와 다른 버전을 깐다.
echo "== requirements.lock.txt freshness"
poetry run python scripts/export_lock.py --check

# 런타임 의존성만 담은 격리 venv. Dockerfile 과 같은 requirements.lock.txt 로 설치하고,
# 파일 해시가 같으면 재사용해 느려지지 않게 한다.
echo "== runtime import smoke"
runtime_venv="$server_dir/.venv-runtime"
dep_hash=$(shasum -a 256 requirements.lock.txt | cut -d' ' -f1)
if [ ! -x "$runtime_venv/bin/python" ] || [ "$(cat "$runtime_venv/.deps-hash" 2>/dev/null)" != "$dep_hash" ]; then
  rm -rf "$runtime_venv"
  uv venv --quiet --python 3.12 "$runtime_venv"
  uv pip install --quiet --require-hashes --no-deps --python "$runtime_venv/bin/python" -r requirements.lock.txt
  echo "$dep_hash" >"$runtime_venv/.deps-hash"
fi
PYTHONPATH="$server_dir/gen:$server_dir" "$runtime_venv/bin/python" scripts/runtime_smoke.py

echo "== ruff check"
poetry run ruff check .

echo "== ruff format --check"
poetry run ruff format --check .

echo "== mypy"
poetry run mypy app

echo "== pytest"
poetry run pytest -q

echo "OK"
