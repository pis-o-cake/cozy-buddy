"""바이너리 Envelope 프레임 한 개를 protobuf 텍스트 형식으로 출력하는 개발용 디코더.

사용 예::

    uv run --no-project --with grpcio-tools==1.85.1 --with protobuf==7.36.2 \\
        python protocol/tools/decode.py frame.bin
    echo 0a00 | ... python protocol/tools/decode.py --gen-dir /path/to/gen
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("cozy.decode")

PROTO_ROOT = Path(__file__).resolve().parent.parent / "proto"


class DecodeError(Exception):
    """프레임을 읽거나 해석하지 못했을 때 발생한다."""


def generate_python(out_dir: Path) -> None:
    """grpc_tools로 파이썬 코드를 out_dir에 생성한다.

    Args:
        out_dir: 생성 결과를 둘 디렉터리.

    Raises:
        DecodeError: 생성에 실패한 경우.
    """
    protos = sorted(str(p) for p in (PROTO_ROOT / "cozy" / "v1").glob("*.proto"))
    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"-I{PROTO_ROOT}", f"--python_out={out_dir}", *protos,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise DecodeError(f"protoc failed: {result.stderr.strip()}")


def read_frame(path: str | None) -> bytes:
    """프레임 바이트를 파일 또는 stdin(hex)에서 읽는다.

    Args:
        path: 바이너리 프레임 파일 경로. None이면 stdin에서 hex 문자열을 읽는다.

    Returns:
        원본 프레임 바이트.

    Raises:
        DecodeError: 파일을 읽을 수 없거나 hex가 올바르지 않은 경우.
    """
    if path is not None:
        try:
            return Path(path).read_bytes()
        except OSError as exc:
            raise DecodeError(f"Cannot read frame file: {path}") from exc
    text = "".join(sys.stdin.read().split())
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise DecodeError("Invalid hex input on stdin") from exc


def decode(frame: bytes, gen_dir: Path) -> str:
    """프레임을 Envelope로 파싱해 텍스트 형식 문자열로 돌려준다.

    Args:
        frame: 직렬화된 Envelope.
        gen_dir: 생성된 *_pb2 모듈의 루트 디렉터리.

    Returns:
        protobuf 텍스트 형식 문자열.

    Raises:
        DecodeError: 파싱에 실패한 경우.
    """
    from google.protobuf import text_format
    from google.protobuf.message import DecodeError as PbDecodeError

    sys.path.insert(0, str(gen_dir))
    from cozy.v1 import envelope_pb2  # noqa: E402

    envelope = envelope_pb2.Envelope()
    try:
        envelope.ParseFromString(frame)
    except PbDecodeError as exc:
        raise DecodeError("Frame is not a valid Envelope") from exc
    return text_format.MessageToString(envelope)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점.

    Args:
        argv: 인자 목록. None이면 sys.argv를 쓴다.

    Returns:
        프로세스 종료 코드.
    """
    parser = argparse.ArgumentParser(description="Decode one cozy.v1.Envelope frame.")
    parser.add_argument("file", nargs="?", help="binary frame file; omit to read hex from stdin")
    parser.add_argument("--gen-dir", type=Path, help="directory with generated *_pb2 modules")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        frame = read_frame(args.file)
        if args.gen_dir is not None:
            output = decode(frame, args.gen_dir)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                generate_python(Path(tmp))
                output = decode(frame, Path(tmp))
    except DecodeError as exc:
        logger.error("%s", exc)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
