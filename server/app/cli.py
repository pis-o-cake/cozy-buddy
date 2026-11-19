"""운영자 CLI 진입점. 컨테이너 안에서 `python -m app.cli <command>` 로 실행한다."""

import argparse
import sys

from app.domain.auth import cli as auth_cli


def build_parser() -> argparse.ArgumentParser:
    """도메인별 하위 명령을 모은 파서를 만든다."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Cozy Buddy operator CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    auth_cli.add_subcommands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """명령을 실행하고 종료 코드를 돌려준다."""
    args = build_parser().parse_args(argv)
    return auth_cli.run(args)


if __name__ == "__main__":
    sys.exit(main())
