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
poetry install --quiet

echo "== generate protobuf"
sh scripts/gen_proto.sh

echo "== ruff check"
poetry run ruff check .

echo "== ruff format --check"
poetry run ruff format --check .

echo "== mypy"
poetry run mypy app

echo "== pytest"
poetry run pytest -q

echo "OK"
