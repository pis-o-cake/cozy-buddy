"""유료 API 남용 방어: 하루 사용량 예산과 토큰 버킷 속도 제한.

IMPORTANT: 메모리에만 두며 재시작하면 초기화된다. 재시작으로 한도가 풀리는 것은
가족용 서버의 비용 상한(안전망)이라는 목적에서 허용한 trade-off 다.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from loguru import logger

from app.config import Settings


class UsageKind(StrEnum):
    """예산을 세는 자원 종류."""

    STT_SECONDS = "stt_seconds"
    LLM_CALLS = "llm_calls"
    TTS_CHARS = "tts_chars"


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """오늘의 사용량과 한도(0 은 무제한)."""

    day: date
    used: dict[UsageKind, float]
    limits: dict[UsageKind, float]


class UsageGuard:
    """서버 전체의 하루 사용량을 세고 한도를 넘는 요청을 거절한다. 날짜는 로컬 자정에 바뀐다."""

    def __init__(self, settings: Settings, clock: Callable[[], datetime] | None = None) -> None:
        tz = ZoneInfo(settings.timezone)
        self._clock = clock or (lambda: datetime.now(tz))
        self._limits: dict[UsageKind, float] = {
            UsageKind.STT_SECONDS: settings.daily_stt_seconds,
            UsageKind.LLM_CALLS: settings.daily_llm_calls,
            UsageKind.TTS_CHARS: settings.daily_tts_chars,
        }
        self._day = self._clock().date()
        self._used: dict[UsageKind, float] = dict.fromkeys(UsageKind, 0.0)
        self._tripped: set[UsageKind] = set()

    def consume(self, kind: UsageKind, amount: float) -> bool:
        """사용량을 더한다. 한도를 넘게 되면 더하지 않고 False 를 돌려준다.

        한도를 처음 넘는 날에는 종류마다 ERROR 로그를 한 번만 남긴다.
        """
        self._roll_day()
        limit = self._limits[kind]
        if limit > 0 and self._used[kind] + amount > limit:
            if kind not in self._tripped:
                self._tripped.add(kind)
                logger.error(
                    "Daily budget exceeded: kind={} used={:.0f} limit={:.0f} day={}",
                    kind.value,
                    self._used[kind],
                    limit,
                    self._day,
                )
            return False
        self._used[kind] += amount
        return True

    def snapshot(self) -> UsageSnapshot:
        """오늘의 사용량 사본."""
        self._roll_day()
        return UsageSnapshot(self._day, dict(self._used), dict(self._limits))

    def _roll_day(self) -> None:
        today = self._clock().date()
        if today == self._day:
            return
        logger.info(
            "Daily usage rolled over: day={} stt_seconds={:.0f} llm_calls={:.0f} tts_chars={:.0f}",
            self._day,
            self._used[UsageKind.STT_SECONDS],
            self._used[UsageKind.LLM_CALLS],
            self._used[UsageKind.TTS_CHARS],
        )
        self._day = today
        self._used = dict.fromkeys(UsageKind, 0.0)
        self._tripped.clear()


class TokenBucket:
    """분당 허용 횟수로 채워지는 토큰 버킷. 버스트는 분당 횟수만큼 허용한다."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0
        self._clock = clock
        self._tokens = self._capacity
        self._updated = clock()

    def try_take(self) -> bool:
        """토큰 하나를 쓴다. 없으면 False."""
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


class BucketRegistry:
    """클라이언트 ID 별 토큰 버킷."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._per_minute = per_minute
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def try_take(self, key: str) -> bool:
        """key 의 버킷에서 토큰 하나를 쓴다."""
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = TokenBucket(self._per_minute, self._clock)
        return bucket.try_take()
