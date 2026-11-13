#!/bin/sh
# 어느 위치에서 실행해도 server/gen/ 에 생성한다.
# 버전은 protocol/scripts/check.sh 와 동일하게 고정한다.
set -eu

server_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
proto_dir="${PROTO_DIR:-$server_dir/../protocol/proto}"
out_dir="$server_dir/gen"

rm -rf "$out_dir"
mkdir -p "$out_dir"

uv run --no-project \
  --with "grpcio-tools==1.85.1" \
  --with "protobuf==7.36.2" \
  python -m grpc_tools.protoc -I "$proto_dir" \
  --python_out="$out_dir" --pyi_out="$out_dir" \
  "$proto_dir"/cozy/v1/*.proto

echo "generated: $out_dir"
