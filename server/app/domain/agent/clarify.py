"""허브별로 보류 중인 확인 질문을 잠깐 기억한다."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.domain.agent.model import Clarify


@dataclass(slots=True)
class _Pending:
    clarify: Clarify
    expires_at: float


class ClarifyStore:
    """허브당 하나의 보류 질문. 프로세스 메모리에만 있으므로 인스턴스를 공유해야 한다."""

    def __init__(self, ttl_seconds: float, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._monotonic = monotonic
        self._pending: dict[str, _Pending] = {}

    def remember(self, hub_id: str, clarify: Clarify) -> None:
        """허브의 보류 질문을 새로 잡는다. 이미 있으면 덮어쓴다."""
        self._pending[hub_id] = _Pending(clarify, self._monotonic() + self._ttl)

    def take(self, hub_id: str) -> Clarify | None:
        """보류 질문을 꺼내고 지운다. 없거나 만료됐으면 None."""
        pending = self._pending.pop(hub_id, None)
        if pending is None or pending.expires_at < self._monotonic():
            return None
        return pending.clarify
