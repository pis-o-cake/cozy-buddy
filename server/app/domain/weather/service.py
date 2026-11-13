"""날씨 캐시와 주기 갱신. 값이 바뀌면 연결된 모든 클라이언트에 보낸다."""

import asyncio
import contextlib
import time
from collections.abc import Callable

from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.weather_pb2 import WeatherSnapshot
from loguru import logger

from app.config import Settings
from app.core.connections import ConnectionManager
from app.domain.weather.answers import KEY_DISABLED, KEY_UNAVAILABLE, Translate, build_answer
from app.domain.weather.client import OpenMeteoClient, WeatherUnavailableError
from app.domain.weather.mapping import condition_key, to_proto
from app.domain.weather.schemas import Weather, WeatherWhen


class WeatherService:
    """날씨 캐시 하나를 소유한다. 앱 수명 동안 하나만 존재한다.

    집 좌표가 없으면 비활성이며 어떤 요청도 보내지 않는다.
    """

    def __init__(
        self,
        settings: Settings,
        connections: ConnectionManager,
        client: OpenMeteoClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._connections = connections
        self._poll_seconds = settings.weather_poll_seconds
        self._stale_seconds = settings.weather_stale_seconds
        self._clock = clock
        self._cached: Weather | None = None
        self._task: asyncio.Task[None] | None = None
        self._client = client
        if self._client is None and settings.weather_enabled:
            self._client = OpenMeteoClient(
                settings.weather_base_url,
                settings.home_latitude or 0.0,
                settings.home_longitude or 0.0,
                settings.timezone,
                timeout_seconds=settings.weather_timeout_seconds,
                max_attempts=settings.weather_max_attempts,
                backoff_seconds=settings.weather_backoff_seconds,
            )

    @property
    def enabled(self) -> bool:
        """집 좌표가 설정돼 날씨 조회가 켜져 있는지."""
        return self._client is not None

    def current(self) -> Weather | None:
        """캐시된 날씨. 없거나 너무 오래됐으면 None."""
        weather = self._cached
        if weather is None:
            return None
        age = self._clock() - weather.observed_epoch_ms / 1000
        return weather if age <= self._stale_seconds else None

    def snapshot(self) -> WeatherSnapshot | None:
        """클라이언트에 보낼 스냅샷. 보낼 값이 없으면 None."""
        weather = self.current()
        return to_proto(weather) if weather else None

    def answer(self, when: WeatherWhen, t: Translate) -> str:
        """음성 응답 문장. 비활성이거나 값이 없으면 그 사정을 알리는 문장이다."""
        if not self.enabled:
            return t(KEY_DISABLED)
        weather = self.current()
        if weather is None:
            return t(KEY_UNAVAILABLE)
        return build_answer(weather, when, t)

    def tool_payload(self, when: WeatherWhen, t: Translate) -> dict[str, object]:
        """LLM 툴 결과. 수치와 한국어 상태 표현을 함께 준다."""
        weather = self.current()
        if weather is None:
            return {"ok": False, "error": "unavailable" if self.enabled else "disabled"}
        if when is WeatherWhen.NOW:
            return {
                "ok": True,
                "when": when.value,
                "temperature_c": round(weather.temperature_c, 1),
                "condition": t(condition_key(weather.weather_code)),
            }
        day = weather.today if when is WeatherWhen.TODAY else weather.tomorrow
        return {
            "ok": True,
            "when": when.value,
            "min_c": round(day.min_c, 1),
            "max_c": round(day.max_c, 1),
            "precipitation_probability_pct": day.precipitation_probability_pct,
            "condition": t(condition_key(day.weather_code)),
        }

    async def start(self) -> None:
        """주기 갱신 태스크를 띄운다. 첫 조회를 기다리지 않는다."""
        if self._client is None:
            logger.info("Weather disabled: COZY_HOME_LATITUDE or COZY_HOME_LONGITUDE not set")
            return
        self._task = asyncio.create_task(self._run(), name="weather-poller")

    async def stop(self) -> None:
        """갱신 태스크와 HTTP 클라이언트를 정리한다."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._client is not None:
            await self._client.aclose()

    async def refresh(self) -> bool:
        """한 번 조회해 캐시를 갱신한다.

        Returns:
            조회에 성공했으면 True. 실패하면 이전 캐시를 유지하고 False.
        """
        if self._client is None:
            return False
        try:
            fresh = await self._client.fetch()
        except WeatherUnavailableError as exc:
            logger.warning("Weather refresh failed: {}", exc)
            return False
        previous, self._cached = self._cached, fresh
        if previous is not None and previous.same_conditions(fresh):
            return True
        logger.info("Weather updated: {}C code={}", fresh.temperature_c, fresh.weather_code)
        await self._connections.broadcast(Envelope(weather_snapshot=to_proto(fresh)))
        return True

    async def _run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                logger.exception("Unexpected weather poll error")
            await asyncio.sleep(self._poll_seconds)
