"""도메인 패키지 자동 탐색."""

import importlib
from pathlib import Path
from types import ModuleType

DOMAIN_DIR = Path(__file__).resolve().parent.parent / "domain"


def discover_domain_modules(filename: str) -> list[tuple[str, ModuleType]]:
    """app/domain/*/<filename>.py 를 import 해 (도메인명, 모듈) 목록으로 돌려준다."""
    found: list[tuple[str, ModuleType]] = []
    for path in sorted(DOMAIN_DIR.glob(f"*/{filename}.py")):
        name = path.parent.name
        found.append((name, importlib.import_module(f"app.domain.{name}.{filename}")))
    return found
