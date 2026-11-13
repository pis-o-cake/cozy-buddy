"""날씨 질문에 대한 한국어 응답. 문구는 locales/ko.json 에만 둔다."""

from collections.abc import Callable

from app.domain.weather.mapping import condition_key
from app.domain.weather.schemas import DailyForecast, Weather, WeatherWhen

Translate = Callable[[str], str]

KEY_UNAVAILABLE = "weather.unavailable"
KEY_DISABLED = "weather.disabled"


def _degrees(value: float, t: Translate) -> str:
    rounded = round(value)
    if rounded < 0:
        return t("weather.temp.below_zero").format(n=-rounded)
    return t("weather.temp.degrees").format(n=rounded)


def _daily(day: DailyForecast, key: str, t: Translate) -> str:
    text = t(key).format(
        condition=t(condition_key(day.weather_code)),
        min=_degrees(day.min_c, t),
        max=_degrees(day.max_c, t),
    )
    chance = t("weather.answer.precipitation").format(pct=day.precipitation_probability_pct)
    return f"{text} {chance}"


def build_answer(weather: Weather, when: WeatherWhen, t: Translate) -> str:
    """캐시된 날씨로 질문 시점에 맞는 응답 문장을 만든다.

    Args:
        weather: 최신 날씨.
        when: 지금, 오늘, 내일 중 질문이 가리킨 시점.
        t: 언어팩 조회 함수.

    Returns:
        음성으로 읽을 문장.
    """
    if when is WeatherWhen.NOW:
        return t("weather.answer.now").format(
            condition=t(condition_key(weather.weather_code)),
            temp=_degrees(weather.temperature_c, t),
        )
    if when is WeatherWhen.TOMORROW:
        return _daily(weather.tomorrow, "weather.answer.tomorrow", t)
    return _daily(weather.today, "weather.answer.today", t)
