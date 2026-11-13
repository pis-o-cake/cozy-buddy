"""loguru 설정."""

import re
import sys
from typing import Any

from loguru import logger

# IMPORTANT: 공개 저장소·로그 수집기로 키가 새지 않도록 OpenAI 키 모양 토큰은 항상 가린다.
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
MASK = "sk-***"


def mask_secrets(text: str) -> str:
    """`sk-` 로 시작하는 API 키 모양 토큰을 가린다."""
    return _SECRET_RE.sub(MASK, text)


def _patch_record(record: Any) -> None:
    record["message"] = mask_secrets(record["message"])


def configure_logging(level: str) -> None:
    """기본 핸들러를 교체해 stderr 로 출력하고 키 마스킹 패처를 건다."""
    logger.remove()
    logger.configure(patcher=_patch_record)
    logger.add(sys.stderr, level=level.upper())
