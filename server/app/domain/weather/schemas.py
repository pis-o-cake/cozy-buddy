"""날씨 도메인 값 객체. 제공자 응답과 프로토콜 사이의 중립 표현이다."""

from dataclasses import dataclass, replace
from enum import StrEnum


class WeatherWhen(StrEnum):
    """질문이 가리키는 시점."""

    NOW = "now"
    TODAY = "today"
    TOMORROW = "tomorrow"


@dataclass(frozen=True, slots=True)
class DailyForecast:
    """하루치 예보. weather_code 는 WMO 코드다."""

    min_c: float
    max_c: float
    precipitation_probability_pct: int
    weather_code: int


@dataclass(frozen=True, slots=True)
class Weather:
    """현재 날씨와 오늘·내일 예보."""

    observed_epoch_ms: int
    temperature_c: float
    weather_code: int
    is_day: bool
    today: DailyForecast
    tomorrow: DailyForecast

    def same_conditions(self, other: "Weather") -> bool:
        """관측 시각을 제외한 값이 같은지. 푸시 여부 판단에 쓴다."""
        return replace(self, observed_epoch_ms=0) == replace(other, observed_epoch_ms=0)
