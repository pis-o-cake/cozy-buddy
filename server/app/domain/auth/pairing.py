"""등록 코드 저장소와 시도 제한. 프로세스 메모리에만 둔다."""

import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

PAIRING_CODE_DIGITS = 6
PAIRING_CODE_TTL_SECONDS = 300
PAIR_ATTEMPT_LIMIT = 5
PAIR_ATTEMPT_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class PairingGrant:
    """코드에 묶인 등록 정보."""

    name: str
    area_id: str


class PairingStore:
    """1회용 등록 코드와 IP 별 시도 횟수를 보관한다.

    WARNING: 서버 워커는 1개만 쓰므로(연결 레지스트리와 같은 전제) 메모리 상태로 충분하다.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._codes: dict[str, tuple[float, PairingGrant]] = {}
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def issue(self, grant: PairingGrant) -> str:
        """새 코드를 만든다. 유효한 다른 코드와 겹치지 않는다."""
        self._purge()
        while True:
            code = f"{secrets.randbelow(10**PAIRING_CODE_DIGITS):0{PAIRING_CODE_DIGITS}d}"
            if code not in self._codes:
                break
        self._codes[code] = (self._clock() + PAIRING_CODE_TTL_SECONDS, grant)
        return code

    def consume(self, code: str) -> PairingGrant | None:
        """코드를 소비한다. 없거나 만료됐으면 None. 성공하면 같은 코드는 다시 쓸 수 없다."""
        self._purge()
        entry = self._codes.pop(code, None)
        return entry[1] if entry is not None else None

    def allow_attempt(self, ip: str) -> bool:
        """해당 IP 의 시도를 기록하고, 창 안의 횟수가 한도 이내인지 돌려준다."""
        self._purge()
        now = self._clock()
        window = self._attempts[ip]
        if len(window) >= PAIR_ATTEMPT_LIMIT:
            return False
        window.append(now)
        return True

    def _purge(self) -> None:
        now = self._clock()
        for code in [c for c, (expires, _) in self._codes.items() if expires <= now]:
            del self._codes[code]
        for ip, window in list(self._attempts.items()):
            while window and now - window[0] >= PAIR_ATTEMPT_WINDOW_SECONDS:
                window.popleft()
            if not window:
                del self._attempts[ip]
