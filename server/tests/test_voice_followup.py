"""후속 지시 전체 흐름: 허브 연결에서 연속 발화가 맥락 경로로 풀리고 LLM 에는 최근 대화가 간다."""

from collections.abc import Iterator

import pytest
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import TextQuery
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.domain.telemetry.turn import TurnPath
from app.main import create_app
from tests.agent_fixtures import NOW
from tests.conftest import connect_hub, eventually, migrate, send
from tests.fake_ha import FakeHomeAssistant
from tests.test_turn_flow import Captured
from tests.voice_support import (
    FakeOpenAI,
    is_final_text,
    make_korean_home,
    recv_until,
    spoken_text,
    text_turn,
)


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    make_korean_home(fake)
    fake.start()
    yield fake
    fake.close()


@pytest.fixture
def gateway() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def client(ha_settings: Settings, gateway: FakeOpenAI) -> Iterator[TestClient]:
    migrate(ha_settings.database_url)
    with TestClient(create_app(ha_settings, openai=gateway, clock=lambda: NOW)) as test_client:
        eventually(lambda: test_client.app.state.home.ready)  # type: ignore[attr-defined]
        yield test_client


def say(ws: WebSocketTestSession, text: str, request_id: str) -> str:
    send(ws, Envelope(text_query=TextQuery(request_id=request_id, text=text)))
    return spoken_text(recv_until(ws, is_final_text))


def service_calls(fake: FakeHomeAssistant) -> list[tuple[str, str, str]]:
    return [
        (c["service"], c["target"]["entity_id"], str(c.get("service_data", {})))
        for c in fake.service_calls
        if c["type"] == "call_service"
    ]


def test_again_turns_the_same_light_back_on_without_llm(
    client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    captured = Captured(client.app.state.turns)  # type: ignore[attr-defined]
    with connect_hub(client) as hub:
        assert say(hub, "거실 조명 꺼줘", "q1") == "거실 조명을 껐어요"
        assert say(hub, "다시 켜줘", "q2") == "거실 조명을 켰어요"

    eventually(lambda: len(captured.turns) == 2)
    assert [t.path for t in captured.turns] == [TurnPath.MATCHER, TurnPath.CONTEXT]
    assert gateway.chat_calls == []
    assert [(s, e) for s, e, _ in service_calls(fake_ha)] == [
        ("turn_off", "light.living"),
        ("turn_on", "light.living"),
    ]


def test_brighter_adjusts_remembered_light(
    client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    with connect_hub(client) as hub:
        say(hub, "거실 조명 켜줘", "q1")
        say(hub, "더 밝게", "q2")

    calls = service_calls(fake_ha)
    assert calls[-1][1] == "light.living" and "brightness_pct" in calls[-1][2]
    assert gateway.chat_calls == []


def test_not_that_one_retargets_to_other_room(
    client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    with connect_hub(client) as hub:
        say(hub, "거실 조명 꺼줘", "q1")
        reply = say(hub, "아니 침실 거", "q2")

    assert reply == "침실 조명을 껐어요"
    assert [(s, e) for s, e, _ in service_calls(fake_ha)] == [
        ("turn_off", "light.living"),
        ("turn_off", "light.bed"),
    ]


def test_expired_memory_falls_back_to_llm(
    client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    agent = client.app.state.agent  # type: ignore[attr-defined]
    with connect_hub(client) as hub:
        say(hub, "거실 조명 꺼줘", "q1")
        base = agent._monotonic()
        agent._monotonic = lambda: base + 61.0
        gateway.chat_turns = [text_turn("어떤 걸 켤까요?")]
        reply = say(hub, "그거 켜줘", "q2")

    assert reply == "어떤 걸 켤까요?"
    assert len(gateway.chat_calls) == 1
    assert [s for s, _, _ in service_calls(fake_ha)] == ["turn_off"]


def test_llm_receives_recent_turns_as_prior_messages(
    client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    gateway.chat_turns = [text_turn("저는 코지예요.")]
    with connect_hub(client) as hub:
        say(hub, "거실 조명 꺼줘", "q1")
        say(hub, "다시 켜줘", "q2")
        say(hub, "너 이름이 뭐야", "q3")

    (call,) = gateway.chat_calls
    messages = call["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    assert [m["content"] for m in messages[1:]] == [
        "거실 조명 꺼줘",
        "거실 조명을 껐어요",
        "다시 켜줘",
        "거실 조명을 켰어요",
        "너 이름이 뭐야",
    ]
