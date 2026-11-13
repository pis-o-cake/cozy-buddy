"""언어팩 조회."""

import json
from pathlib import Path

from loguru import logger

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"


class Translator:
    """locales/<locale>.json 에서 키를 문구로 푼다. 키가 없으면 키를 그대로 돌려준다."""

    def __init__(self, locale: str) -> None:
        path = LOCALE_DIR / f"{locale}.json"
        self._messages: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))

    def __call__(self, key: str) -> str:
        message = self._messages.get(key)
        if message is None:
            logger.warning("Missing locale key: {}", key)
            return key
        return message
