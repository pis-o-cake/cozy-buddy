"""WebSocket 접속 한도. 전체 연결 수, IP 별 미인증 연결 수, IP 별 hello 실패 횟수를 센다."""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from enum import StrEnum

_PRUNE_THRESHOLD = 4096


class Admission(StrEnum):
    """접속 허용 판정."""

    OK = "ok"
    BLOCKED = "blocked"
    FULL = "full"
    TOO_MANY_UNAUTHENTICATED = "too_many_unauthenticated"


class WsGuard:
    """프로세스 메모리 안의 슬라이딩 윈도우와 카운터. 워커가 1개라는 전제다."""

    def __init__(
        self,
        max_connections: int,
        max_unauthenticated_per_ip: int,
        failure_limit: int,
        failure_window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_connections = max_connections
        self._max_unauth = max_unauthenticated_per_ip
        self._failure_limit = failure_limit
        self._window = failure_window_seconds
        self._clock = clock
        self._total = 0
        self._unauth: defaultdict[str, int] = defaultdict(int)
        self._failures: dict[str, deque[float]] = {}

    @property
    def total(self) -> int:
        """현재 열려 있는 연결 수."""
        return self._total

    def admit(self, ip: str) -> Admission:
        """새 연결을 받을 수 있는지 판단하고, 받으면 미인증 연결로 센다."""
        if self._recent_failures(ip) >= self._failure_limit:
            return Admission.BLOCKED
        if self._total >= self._max_connections:
            return Admission.FULL
        if self._unauth[ip] >= self._max_unauth:
            return Admission.TOO_MANY_UNAUTHENTICATED
        self._total += 1
        self._unauth[ip] += 1
        return Admission.OK

    def authenticated(self, ip: str) -> None:
        """hello 를 통과한 연결을 미인증 집계에서 뺀다."""
        self._drop_unauth(ip)

    def release(self, ip: str, was_authenticated: bool) -> None:
        """연결이 끝났을 때 호출한다. `admit` 이 OK 였던 연결만 넘긴다."""
        self._total = max(0, self._total - 1)
        if not was_authenticated:
            self._drop_unauth(ip)

    def record_failure(self, ip: str) -> None:
        """hello 실패를 기록한다."""
        now = self._clock()
        if len(self._failures) > _PRUNE_THRESHOLD:
            self._prune(now)
        self._failures.setdefault(ip, deque()).append(now)

    def _recent_failures(self, ip: str) -> int:
        events = self._failures.get(ip)
        if events is None:
            return 0
        cutoff = self._clock() - self._window
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            del self._failures[ip]
        return len(events)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        for ip in [k for k, v in self._failures.items() if not v or v[-1] <= cutoff]:
            del self._failures[ip]

    def _drop_unauth(self, ip: str) -> None:
        count = self._unauth.get(ip, 0) - 1
        if count > 0:
            self._unauth[ip] = count
            return
        self._unauth.pop(ip, None)
