#!/bin/sh
# 저장소 루트에서 실행: sh protocol/scripts/check.sh
set -eu

GRPCIO_TOOLS_VERSION="1.85.1"
PROTOBUF_VERSION="7.36.2"

if ! command -v buf >/dev/null 2>&1; then
  echo "error: buf not found. Install: brew install bufbuild/buf/buf" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv not found. Install: brew install uv" >&2
  exit 1
fi

echo "== buf lint"
buf lint protocol

echo "== buf breaking"
# main에 protocol 모듈이 아직 없으면 비교 대상이 없으므로 건너뛴다.
# IMPORTANT: worktree에서는 .git이 파일이므로 공용 git 디렉터리를 비교 대상으로 쓴다.
if git cat-file -e main:protocol/buf.yaml 2>/dev/null; then
  buf breaking protocol --against "$(git rev-parse --git-common-dir)#branch=main,subdir=protocol"
else
  echo "skipped: main has no protocol/buf.yaml"
fi

GEN_DIR="$(mktemp -d)"
trap 'rm -rf "$GEN_DIR"' EXIT

py() {
  uv run --no-project \
    --with "grpcio-tools==${GRPCIO_TOOLS_VERSION}" \
    --with "protobuf==${PROTOBUF_VERSION}" \
    python "$@"
}

echo "== generate python"
py -m grpc_tools.protoc -I protocol/proto --python_out="$GEN_DIR" \
  protocol/proto/cozy/v1/*.proto

echo "== roundtrip test"
py protocol/tests/roundtrip_test.py --gen-dir "$GEN_DIR"

echo "OK"
