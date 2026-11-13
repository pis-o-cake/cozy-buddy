"""HA 별칭: 영어 이름 엔티티를 별칭만으로 LLM 없이 결정론적으로 제어한다."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.agent_fixtures import NOW
from tests.conftest import connect_mobile, eventually, migrate
from tests.fake_ha import FakeHomeAssistant, _state
from tests.test_voice import _text_query
from tests.voice_support import FakeOpenAI, spoken_text


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    fake.states = [
        _state("light.ceiling", "on", {"friendly_name": "Ceiling Lights", "brightness": 200}),
        _state("light.reading", "on", {"friendly_name": "Reading Lamp", "brightness": 100}),
    ]
    fake.areas = [
        {"area_id": "living_room", "name": "거실", "aliases": []},
        {"area_id": "bedroom", "name": "침실", "aliases": ["안방"]},
    ]
    fake.devices = []
    fake.entity_registry = [
        {"entity_id": "light.ceiling", "area_id": "living_room", "device_id": None},
        {"entity_id": "light.reading", "area_id": "bedroom", "device_id": None},
    ]
    fake.entity_aliases = {"light.ceiling": ["거실 조명"]}
    fake.exposed = {"light.ceiling", "light.reading"}
    fake.start()
    yield fake
    fake.close()


@pytest.fixture
def gateway() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def alias_client(ha_settings: Settings, gateway: FakeOpenAI) -> Iterator[TestClient]:
    migrate(ha_settings.database_url)
    with TestClient(create_app(ha_settings, openai=gateway, clock=lambda: NOW)) as client:
        eventually(lambda: client.app.state.home.ready)  # type: ignore[attr-defined]
        yield client


def _service_calls(fake_ha: FakeHomeAssistant) -> list[tuple[str, str]]:
    return [
        (c["service"], c["target"]["entity_id"])
        for c in fake_ha.service_calls
        if c["type"] == "call_service"
    ]


def test_entity_alias_controls_english_named_entity_without_llm(
    alias_client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    with connect_mobile(alias_client) as mobile:
        frames = _text_query(mobile, "거실 조명 꺼줘")
    assert _service_calls(fake_ha) == [("turn_off", "light.ceiling")]
    assert spoken_text(frames)
    assert gateway.chat_calls == []


def test_area_alias_resolves_room_without_llm(
    alias_client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    with connect_mobile(alias_client) as mobile:
        _text_query(mobile, "안방 불 꺼줘")
    assert _service_calls(fake_ha) == [("turn_off", "light.reading")]
    assert gateway.chat_calls == []


def test_alias_added_later_is_usable_after_snapshot_refresh(
    alias_client: TestClient, gateway: FakeOpenAI, fake_ha: FakeHomeAssistant
) -> None:
    fake_ha.set_entity_aliases("light.reading", ["독서등"])
    with connect_mobile(alias_client) as mobile:
        eventually(
            lambda: any(
                list(e.aliases) == ["독서등"]
                for e in alias_client.app.state.home.snapshot().entities  # type: ignore[attr-defined]
            )
        )
        _text_query(mobile, "독서등 꺼줘")
    assert _service_calls(fake_ha) == [("turn_off", "light.reading")]
    assert gateway.chat_calls == []
