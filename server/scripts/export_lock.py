"""poetry.lock 에서 런타임(main 그룹) 의존성만 뽑아 해시 고정 requirements 를 만든다.

poetry-plugin-export 에 의존하지 않으려고 lock 파일(TOML)을 직접 읽는다.

Usage:
    python scripts/export_lock.py          # requirements.lock.txt 를 다시 쓴다
    python scripts/export_lock.py --check  # 파일이 poetry.lock 과 어긋나면 종료 코드 1
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent.parent
LOCK_PATH = SERVER_DIR / "poetry.lock"
OUTPUT_PATH = SERVER_DIR / "requirements.lock.txt"
RUNTIME_GROUP = "main"
HEADER = (
    "# poetry.lock 에서 생성한 런타임 의존성(해시 고정). 직접 수정하지 않는다.\n"
    "# 갱신: poetry run python scripts/export_lock.py\n"
)


def render(lock: dict[str, Any]) -> str:
    """lock 내용을 requirements 텍스트로 만든다.

    Args:
        lock: tomllib 로 읽은 poetry.lock.

    Returns:
        이름순으로 정렬된 `name==version --hash=...` 항목들.

    Raises:
        ValueError: 해시가 없는 패키지(git·path 의존성 등)가 런타임에 섞여 있을 때.
    """
    entries: list[str] = []
    packages = sorted(lock["package"], key=lambda p: p["name"].lower())
    for pkg in packages:
        if RUNTIME_GROUP not in pkg.get("groups", []):
            continue
        hashes = sorted({f["hash"] for f in pkg.get("files", [])})
        if not hashes:
            raise ValueError(f"No hashes for runtime package: {pkg['name']}")
        spec = f"{pkg['name']}=={pkg['version']}"
        if pkg.get("markers"):
            spec += f" ; {pkg['markers']}"
        parts = [spec, *(f"--hash={h}" for h in hashes)]
        entries.append(" \\\n    ".join(parts))
    return HEADER + "\n".join(entries) + "\n"


def main(argv: list[str]) -> int:
    """생성 또는 최신 여부 검사를 수행한다."""
    with LOCK_PATH.open("rb") as fh:
        expected = render(tomllib.load(fh))
    if "--check" not in argv:
        OUTPUT_PATH.write_text(expected)
        return 0
    current = OUTPUT_PATH.read_text() if OUTPUT_PATH.exists() else ""
    if current == expected:
        return 0
    print(
        "error: requirements.lock.txt is stale; run: poetry run python scripts/export_lock.py",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
