"""WMO 날씨 코드 해석과 프로토콜 변환."""

from cozy.v1.weather_pb2 import WeatherDaily, WeatherSnapshot

from app.domain.weather.schemas import DailyForecast, Weather

_CODE_KEYS: dict[int, str] = {
    0: "clear",
    1: "mostly_clear",
    2: "partly_cloudy",
    3: "overcast",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "freezing_rain",
    57: "freezing_rain",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "freezing_rain",
    67: "freezing_rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    80: "shower",
    81: "shower",
    82: "shower",
    85: "snow_shower",
    86: "snow_shower",
    95: "thunder",
    96: "thunder",
    99: "thunder",
}
UNKNOWN_KEY = "unknown"


def condition_key(weather_code: int) -> str:
    """WMO 코드를 언어팩 키 `weather.condition.<이름>` 으로 바꾼다. 모르는 코드는 unknown."""
    return f"weather.condition.{_CODE_KEYS.get(weather_code, UNKNOWN_KEY)}"


def _daily_to_proto(day: DailyForecast) -> WeatherDaily:
    return WeatherDaily(
        min_c=day.min_c,
        max_c=day.max_c,
        precipitation_probability_pct=day.precipitation_probability_pct,
        weather_code=day.weather_code,
    )


def to_proto(weather: Weather) -> WeatherSnapshot:
    """도메인 값을 WeatherSnapshot 으로 바꾼다."""
    return WeatherSnapshot(
        observed_epoch_ms=weather.observed_epoch_ms,
        temperature_c=weather.temperature_c,
        weather_code=weather.weather_code,
        is_day=weather.is_day,
        today=_daily_to_proto(weather.today),
        tomorrow=_daily_to_proto(weather.tomorrow),
    )
