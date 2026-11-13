"""loguru 설정."""

import sys

from loguru import logger


def configure_logging(level: str) -> None:
    """기본 핸들러를 교체해 stderr 로 출력한다."""
    logger.remove()
    logger.add(sys.stderr, level=level.upper())
