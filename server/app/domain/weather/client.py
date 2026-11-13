"""Open-Meteo 예보 클라이언트. API 키가 필요 없다."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from loguru import logger

from app.domain.weather.schemas import DailyForecast, Weather

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class WeatherUnavailableError(Exception):
    """제공자에서 날씨를 받지 못했거나 응답이 기대한 형식이 아니다."""


class OpenMeteoClient:
    """Open-Meteo forecast 엔드포인트에서 현재 날씨와 오늘·내일 일별 예보를 읽는다."""

    def __init__(
        self,
        base_url: str,
        latitude: float,
        longitude: float,
        timezone: str,
        *,
        timeout_seconds: float,
        max_attempts: int,
        backoff_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._params: dict[str, str | int | float] = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "timeformat": "unixtime",
            "forecast_days": 2,
            "current": "temperature_2m,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max",
        }
        self._base_url = base_url
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._http = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    async def aclose(self) -> None:
        """HTTP 클라이언트를 닫는다."""
        await self._http.aclose()

    async def fetch(self) -> Weather:
        """날씨를 받아 온다. 일시 오류는 지수 백오프로 재시도한다.

        Raises:
            WeatherUnavailableError: 재시도 후에도 받지 못했거나 응답 형식이 틀릴 때.
        """
        payload = await self._get_json()
        try:
            return _parse(payload)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WeatherUnavailableError(f"Unexpected forecast payload: {exc!r}") from exc

    async def _get_json(self) -> Any:
        last: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            retryable = True
            try:
                response = await self._http.get(self._base_url, params=self._params)
                if response.status_code not in _RETRY_STATUS:
                    response.raise_for_status()
                    return response.json()
                last = httpx.HTTPStatusError(
                    f"status {response.status_code}", request=response.request, response=response
                )
            except httpx.HTTPStatusError as exc:
                last = exc
                retryable = False
            except (httpx.TransportError, ValueError) as exc:
                last = exc
            if not retryable or attempt == self._max_attempts:
                break
            delay = self._backoff * 2 ** (attempt - 1)
            logger.debug("Weather fetch retry {} in {}s: {!r}", attempt, delay, last)
            await self._sleep(delay)
        raise WeatherUnavailableError(f"Weather fetch failed: {last!r}") from last


def _day(daily: dict[str, Any], index: int) -> DailyForecast:
    probability = daily["precipitation_probability_max"][index]
    return DailyForecast(
        min_c=float(daily["temperature_2m_min"][index]),
        max_c=float(daily["temperature_2m_max"][index]),
        precipitation_probability_pct=int(probability) if probability is not None else 0,
        weather_code=int(daily["weather_code"][index]),
    )


def _parse(payload: dict[str, Any]) -> Weather:
    current = payload["current"]
    daily = payload["daily"]
    return Weather(
        observed_epoch_ms=int(current["time"]) * 1000,
        temperature_c=float(current["temperature_2m"]),
        weather_code=int(current["weather_code"]),
        is_day=bool(current["is_day"]),
        today=_day(daily, 0),
        tomorrow=_day(daily, 1),
    )
