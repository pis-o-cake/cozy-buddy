"""날씨: 클라이언트, 캐시·푸시, 음성 질문. 실제 네트워크는 쓰지 않는다."""

import asyncio
import json
import time
from collections.abc import Callable, Iterator

import httpx
import pytest
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import TextQuery
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.core.connections import ConnectionManager
from app.core.i18n import Translator
from app.domain.agent.matcher import match
from app.domain.agent.model import NoMatch, UtteranceContext, WeatherIntent
from app.domain.weather.answers import build_answer
from app.domain.weather.client import OpenMeteoClient, WeatherUnavailableError
from app.domain.weather.mapping import condition_key
from app.domain.weather.schemas import DailyForecast, Weather, WeatherWhen
from app.domain.weather.service import WeatherService
from app.main import create_app
from tests.agent_fixtures import HOME, LIVING, NOW
from tests.conftest import connect_mobile, eventually, migrate, recv, send
from tests.voice_support import (
    FakeOpenAI,
    is_final_text,
    recv_until,
    spoken_text,
    text_turn,
    tool_turn,
)

translate = Translator("ko")


def forecast_payload(
    temperature: float = 12.4, code: int = 0, observed: float | None = None, high: float = 15.2
) -> dict[str, object]:
    return {
        "current": {
            "time": int(observed if observed is not None else time.time()),
            "temperature_2m": temperature,
            "weather_code": code,
            "is_day": 1,
        },
        "daily": {
            "weather_code": [code, 61],
            "temperature_2m_max": [high, 9.0],
            "temperature_2m_min": [3.0, -1.2],
            "precipitation_probability_max": [10, None],
        },
    }


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], attempts: int = 3
) -> tuple[OpenMeteoClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = OpenMeteoClient(
        "https://weather.test/v1/forecast",
        37.5,
        127.0,
        "Asia/Seoul",
        timeout_seconds=1,
        max_attempts=attempts,
        backoff_seconds=1.0,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )
    return client, sleeps


def ok_handler(**kwargs: float) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=forecast_payload(**kwargs))

    return handler


async def test_client_parses_current_and_daily() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=forecast_payload(observed=1_700_000_000))

    client, _ = make_client(handler)
    weather = await client.fetch()
    await client.aclose()

    assert weather.observed_epoch_ms == 1_700_000_000_000
    assert weather.temperature_c == 12.4
    assert weather.is_day is True
    assert weather.today == DailyForecast(3.0, 15.2, 10, 0)
    assert weather.tomorrow == DailyForecast(-1.2, 9.0, 0, 61)
    params = seen[0].url.params
    assert params["latitude"] == "37.5" and params["timezone"] == "Asia/Seoul"
    assert params["forecast_days"] == "2"


async def test_client_retries_transient_failures_with_backoff() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        if calls == 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=forecast_payload())

    client, sleeps = make_client(handler)
    await client.fetch()
    await client.aclose()
    assert calls == 3
    assert sleeps == [1.0, 2.0]


async def test_client_gives_up_after_max_attempts() -> None:
    client, sleeps = make_client(lambda r: httpx.Response(500), attempts=2)
    with pytest.raises(WeatherUnavailableError):
        await client.fetch()
    await client.aclose()
    assert sleeps == [1.0]


async def test_client_does_not_retry_client_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": True})

    client, _ = make_client(handler)
    with pytest.raises(WeatherUnavailableError):
        await client.fetch()
    await client.aclose()
    assert calls == 1


async def test_client_rejects_malformed_payload() -> None:
    client, _ = make_client(lambda r: httpx.Response(200, json={"current": {}}))
    with pytest.raises(WeatherUnavailableError):
        await client.fetch()
    await client.aclose()


def test_condition_keys_cover_known_codes_and_fall_back() -> None:
    assert condition_key(0) == "weather.condition.clear"
    assert condition_key(63) == "weather.condition.rain"
    assert condition_key(95) == "weather.condition.thunder"
    assert condition_key(12345) == "weather.condition.unknown"
    for code in (0, 1, 2, 3, 45, 48, 51, 61, 66, 71, 80, 85, 95, 99):
        assert translate(condition_key(code)) != condition_key(code)


def sample_weather(temperature: float = 12.4) -> Weather:
    return Weather(
        observed_epoch_ms=1_700_000_000_000,
        temperature_c=temperature,
        weather_code=61,
        is_day=True,
        today=DailyForecast(-3.4, 2.6, 80, 61),
        tomorrow=DailyForecast(1.0, 9.0, 10, 3),
    )


def test_answers_use_korean_templates() -> None:
    weather = sample_weather()
    assert build_answer(weather, WeatherWhen.NOW, translate) == (
        "지금 바깥은 비가 오고 기온은 12도예요."
    )
    assert build_answer(weather, WeatherWhen.TODAY, translate) == (
        "오늘은 비가 오고 최저 영하 3도에서 최고 3도예요. 강수 확률은 80%예요."
    )
    assert build_answer(weather, WeatherWhen.TOMORROW, translate) == (
        "내일은 흐리고 최저 1도에서 최고 9도예요. 강수 확률은 10%예요."
    )


@pytest.mark.parametrize(
    ("text", "when"),
    [
        ("오늘 날씨 어때", WeatherWhen.TODAY),
        ("날씨 알려줘", WeatherWhen.TODAY),
        ("지금 날씨 어때", WeatherWhen.NOW),
        ("내일 날씨 어때", WeatherWhen.TOMORROW),
        ("내일 비 와?", WeatherWhen.TOMORROW),
        ("비 올까", WeatherWhen.TODAY),
        ("오늘 눈 와?", WeatherWhen.TODAY),
        ("우산 챙겨야 돼", WeatherWhen.TODAY),
        ("내일 우산 필요해", WeatherWhen.TOMORROW),
        ("지금 몇 도야", WeatherWhen.NOW),
        ("몇 도야", WeatherWhen.NOW),
        ("밖에 몇 도야", WeatherWhen.NOW),
        ("바깥 온도 알려줘", WeatherWhen.NOW),
        ("오늘 기온 어때", WeatherWhen.TODAY),
        ("내일 몇 도야", WeatherWhen.TOMORROW),
    ],
)
def test_weather_questions_match(text: str, when: WeatherWhen) -> None:
    assert match(UtteranceContext(text, LIVING, NOW), HOME) == WeatherIntent(when)


@pytest.mark.parametrize(
    "text",
    [
        "거실 온도 몇 도야",
        "온도 알려줘",
        "침실 온도 몇 도야",
        "에어컨 몇 도로 맞춰줘",
        "모레 날씨 어때",
        "주말 날씨 어때",
        "비 오는 날 듣기 좋은 노래 추천해줘 제발 부탁이야",
        "비행기 표 알아봐줘",
        "준비 됐어",
        "눈이 아파",
    ],
)
def test_non_weather_utterances_do_not_match_weather(text: str) -> None:
    assert not isinstance(match(UtteranceContext(text, LIVING, NOW), HOME), WeatherIntent)


def test_indoor_sensor_question_is_still_a_state_query() -> None:
    from app.domain.agent.model import StateQuery

    result = match(UtteranceContext("거실 온도 몇 도야", None, NOW), HOME)
    assert isinstance(result, StateQuery)
    assert match(UtteranceContext("안녕", LIVING, NOW), HOME) == NoMatch()


async def test_service_disabled_without_coordinates() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    service = WeatherService(settings, ConnectionManager())
    await service.start()
    assert not service.enabled
    assert service.snapshot() is None
    assert service.answer(WeatherWhen.NOW, translate) == translate("weather.disabled")
    await service.stop()


def test_blank_coordinates_in_env_mean_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COZY_HOME_LATITUDE", "")
    monkeypatch.setenv("COZY_HOME_LONGITUDE", "")
    assert not Settings(_env_file=None).weather_enabled  # type: ignore[call-arg]


async def test_refresh_broadcasts_only_when_values_change() -> None:
    payloads = iter(
        [
            forecast_payload(12.4, observed=1_700_000_000),
            forecast_payload(12.4, observed=1_700_000_900),
            forecast_payload(13.0, observed=1_700_001_800),
        ]
    )
    client, _ = make_client(lambda r: httpx.Response(200, json=next(payloads)))
    connections = ConnectionManager()
    sent: list[Envelope] = []

    async def capture(envelope: Envelope) -> None:
        sent.append(envelope)

    connections.broadcast = capture  # type: ignore[method-assign]
    settings = Settings(_env_file=None, home_latitude=37.5, home_longitude=127.0)  # type: ignore[call-arg]
    service = WeatherService(settings, connections, client, clock=lambda: 1_700_002_000)

    assert await service.refresh()
    assert await service.refresh()
    assert await service.refresh()
    await client.aclose()

    assert [e.weather_snapshot.temperature_c for e in sent] == [12.4, 13.0]


async def test_failed_refresh_keeps_cache_and_stale_cache_is_hidden() -> None:
    responses = [httpx.Response(200, json=forecast_payload(observed=1_700_000_000))]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0) if responses else httpx.Response(500)

    client, _ = make_client(handler, attempts=1)
    now = [1_700_000_100.0]
    settings = Settings(_env_file=None, home_latitude=37.5, home_longitude=127.0)  # type: ignore[call-arg]
    service = WeatherService(settings, ConnectionManager(), client, clock=lambda: now[0])

    assert await service.refresh()
    assert not await service.refresh()
    assert service.current() is not None
    now[0] += settings.weather_stale_seconds + 1000
    assert service.current() is None
    assert service.answer(WeatherWhen.NOW, translate) == translate("weather.unavailable")
    await client.aclose()


# --- 앱 통합 -------------------------------------------------------------------------------


@pytest.fixture
def weather_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"home_latitude": 37.5665, "home_longitude": 126.978})


@pytest.fixture
def weather_app(weather_settings: Settings) -> Iterator[tuple[TestClient, FakeOpenAI]]:
    migrate(weather_settings.database_url)
    gateway = FakeOpenAI()
    client, _ = make_client(ok_handler(temperature=-2.6, code=61))
    app = create_app(weather_settings, openai=gateway, clock=lambda: NOW, weather_client=client)
    with TestClient(app) as test_client:
        eventually(lambda: test_client.app.state.weather.current())  # type: ignore[attr-defined]
        yield test_client, gateway


def _query(ws: WebSocketTestSession, text: str) -> str:
    send(ws, Envelope(text_query=TextQuery(request_id="q1", text=text)))
    return spoken_text(recv_until(ws, is_final_text))


def test_snapshot_is_sent_right_after_home_snapshot(
    weather_app: tuple[TestClient, FakeOpenAI],
) -> None:
    client, _ = weather_app
    with connect_mobile(client) as ws:
        envelope = recv(ws)
    assert envelope.WhichOneof("body") == "weather_snapshot"
    snapshot = envelope.weather_snapshot
    assert snapshot.temperature_c == -2.6
    assert snapshot.weather_code == 61
    assert snapshot.today.max_c == 15.2
    assert snapshot.tomorrow.min_c == -1.2


def test_weather_question_is_answered_from_cache_without_llm(
    weather_app: tuple[TestClient, FakeOpenAI],
) -> None:
    client, gateway = weather_app
    with connect_mobile(client) as ws:
        recv(ws)
        answer = _query(ws, "지금 몇 도야")
    assert answer == "지금 바깥은 비가 오고 기온은 영하 3도예요."
    assert gateway.chat_calls == []


def test_get_weather_tool_returns_cached_values(
    weather_app: tuple[TestClient, FakeOpenAI],
) -> None:
    client, gateway = weather_app
    gateway.chat_turns = [
        tool_turn("get_weather", {"when": "tomorrow"}),
        text_turn("내일은 쌀쌀해요"),
    ]
    with connect_mobile(client) as ws:
        recv(ws)
        _query(ws, "다음 날 옷차림 추천해줘")
    tool_names = [t["function"]["name"] for t in gateway.chat_calls[0]["tools"]]
    assert "get_weather" in tool_names
    result = json.loads(gateway.chat_calls[1]["messages"][-1]["content"])
    assert result["ok"] is True
    assert result["when"] == "tomorrow"
    assert result["min_c"] == -1.2
    assert result["precipitation_probability_pct"] == 0


def test_no_snapshot_and_polite_answer_when_disabled(
    settings: Settings,
) -> None:
    migrate(settings.database_url)
    app = create_app(settings, openai=FakeOpenAI(), clock=lambda: NOW)
    with TestClient(app) as client, connect_mobile(client) as ws:
        answer = _query(ws, "오늘 날씨 어때")
    assert answer == translate("weather.disabled")


async def test_poller_stops_cleanly() -> None:
    client, _ = make_client(ok_handler())
    settings = Settings(_env_file=None, home_latitude=37.5, home_longitude=127.0)  # type: ignore[call-arg]
    service = WeatherService(settings, ConnectionManager(), client)
    await service.start()
    await asyncio.sleep(0.05)
    await service.stop()
    assert service.current() is not None
