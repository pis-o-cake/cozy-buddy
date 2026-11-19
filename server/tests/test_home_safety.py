"""HA 제어 안전: 노출 목록 폴백 제한, 차고류 cover 음성 차단, script 허용 목록, lock 비제어."""

from collections.abc import Iterator

import pytest
from cozy.v1.common_pb2 import ERROR_CODE_BAD_REQUEST
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.voice_pb2 import TextQuery
from fastapi.testclient import TestClient
from loguru import logger
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.domain.home.commands import UnsupportedCommandError, build_service_call
from app.domain.home.policy import can_control
from app.domain.home.schemas import ALLOWED_DOMAINS, HomeEntity
from tests.conftest import connect_mobile, eventually
from tests.fake_ha import FakeHomeAssistant, _state
from tests.test_home import command, home_of, recv_until, running_app, send
from tests.test_voice import _build_client
from tests.voice_support import FakeOpenAI, is_final_text, make_korean_home, tool_turn
from tests.voice_support import recv_until as until


def entity(entity_id: str, **attributes: str) -> HomeEntity:
    return HomeEntity(
        entity_id=entity_id,
        name=entity_id,
        domain=entity_id.partition(".")[0],
        state="off",
        attributes=attributes,
    )


def allowed(
    e: HomeEntity,
    *,
    voice: bool = True,
    known: bool = True,
    scripts: frozenset[str] = frozenset(),
    scenes: frozenset[str] = frozenset(),
    blocked: frozenset[str] = frozenset(),
    action: str | None = None,
) -> bool:
    return can_control(
        e,
        exposure_known=known,
        allowed_scripts=scripts,
        voice=voice,
        allowed_scenes=scenes,
        voice_blocked_entities=blocked,
        action=action,
    )


# --- 정책 단위 ----------------------------------------------------------------------------


@pytest.mark.parametrize("device_class", ["garage", "gate", "door"])
def test_risky_cover_is_blocked_for_voice_but_allowed_by_touch(device_class: str) -> None:
    cover = entity("cover.x", device_class=device_class)
    assert not allowed(cover, voice=True)
    assert allowed(cover, voice=False)


@pytest.mark.parametrize("attributes", [{}, {"device_class": ""}])
@pytest.mark.parametrize("action", ["turn_on", "toggle"])
def test_cover_without_device_class_cannot_be_opened_by_voice(
    attributes: dict[str, str], action: str
) -> None:
    cover = entity("cover.unknown", **attributes)
    assert not allowed(cover, voice=True, action=action)
    assert allowed(cover, voice=False, action=action)


def test_cover_without_device_class_can_still_close_or_be_listed_by_voice() -> None:
    cover = entity("cover.unknown")
    assert allowed(cover, voice=True, action="turn_off")
    assert allowed(cover, voice=True)


@pytest.mark.parametrize("action", ["turn_on", "toggle"])
def test_classified_cover_opens_by_voice(action: str) -> None:
    assert allowed(entity("cover.blind", device_class="blind"), voice=True, action=action)


@pytest.mark.parametrize("attributes", [{}, {"device_class": "curtain"}, {"device_class": "blind"}])
def test_ordinary_cover_stays_voice_controllable(attributes: dict[str, str]) -> None:
    assert allowed(entity("cover.living", **attributes), voice=True)


def test_script_requires_allow_list_for_every_path() -> None:
    script = entity("script.goodnight")
    assert not allowed(script, voice=True)
    assert not allowed(script, voice=False)
    listed = frozenset({"script.goodnight"})
    assert allowed(script, voice=True, scripts=listed)
    assert not allowed(entity("script.other"), voice=True, scripts=listed)


def test_scene_requires_allow_list_for_voice_only() -> None:
    scene = entity("scene.movie")
    assert not allowed(scene, voice=True)
    assert allowed(scene, voice=False)
    listed = frozenset({"scene.movie"})
    assert allowed(scene, voice=True, scenes=listed)
    assert not allowed(entity("scene.other"), voice=True, scenes=listed)


@pytest.mark.parametrize("entity_id", ["switch.front_door", "light.hall", "cover.shade"])
def test_voice_blocked_entities_apply_to_any_domain_on_voice_only(entity_id: str) -> None:
    blocked = frozenset({entity_id})
    target = entity(entity_id)
    assert not allowed(target, voice=True, blocked=blocked)
    assert allowed(target, voice=False, blocked=blocked)
    assert allowed(entity("switch.other"), voice=True, blocked=blocked)


@pytest.mark.parametrize("domain", ["lock", "alarm_control_panel"])
@pytest.mark.parametrize("voice", [True, False])
@pytest.mark.parametrize("known", [True, False])
def test_lock_and_alarm_panel_are_never_controllable(domain: str, voice: bool, known: bool) -> None:
    assert not allowed(entity(f"{domain}.front"), voice=voice, known=known)
    with pytest.raises(UnsupportedCommandError):
        build_service_call(domain, "turn_on", {})
    with pytest.raises(UnsupportedCommandError):
        build_service_call(domain, "turn_off", {})


def test_alarm_control_panel_is_not_even_bridged() -> None:
    assert "alarm_control_panel" not in ALLOWED_DOMAINS


@pytest.mark.parametrize("domain", ["light", "switch", "fan"])
def test_unknown_exposure_still_allows_lights(domain: str) -> None:
    assert allowed(entity(f"{domain}.x"), known=False)


@pytest.mark.parametrize("domain", ["cover", "climate", "media_player", "scene", "script", "lock"])
def test_unknown_exposure_blocks_everything_else(domain: str) -> None:
    scripts = frozenset({"script.x"})
    assert not allowed(entity(f"{domain}.x"), known=False, scripts=scripts, voice=False)


# --- WebSocket: 노출 목록 폴백 -----------------------------------------------------------


def add_risky_entities(fake: FakeHomeAssistant) -> None:
    fake.states += [
        _state("cover.garage", "closed", {"friendly_name": "차고 문", "device_class": "garage"}),
        _state("cover.shade", "closed", {"friendly_name": "거실 커튼"}),
        _state("script.goodnight", "off", {"friendly_name": "굿나잇"}),
        _state("lock.front", "locked", {"friendly_name": "현관 도어락"}),
        _state("climate.living", "off", {"friendly_name": "거실 에어컨"}),
    ]
    fake.exposed |= {
        "cover.garage",
        "cover.shade",
        "script.goodnight",
        "lock.front",
        "climate.living",
    }


@pytest.fixture
def fake_ha() -> Iterator[FakeHomeAssistant]:
    fake = FakeHomeAssistant()
    make_korean_home(fake)
    add_risky_entities(fake)
    fake.start()
    yield fake
    fake.close()


def result_of(ws: WebSocketTestSession, entity_id: str, action: str) -> tuple[bool, int, str]:
    send(ws, command(entity_id, action))
    result = recv_until(ws, "command_result").command_result
    return result.ok, result.code, result.message_key


def test_unknown_exposure_keeps_reads_but_limits_control_and_warns_each_refresh(
    ha_settings: Settings, fake_ha: FakeHomeAssistant
) -> None:
    fake_ha.expose_supported = False
    messages: list[str] = []
    with running_app(ha_settings) as client:
        eventually(lambda: home_of(client).ready)
        # 앱이 시작하면서 로깅을 다시 설정하므로 시작한 뒤에 싱크를 붙인다.
        sink = logger.add(lambda m: messages.append(str(m)), level="WARNING", format="{message}")
        try:
            home = home_of(client)
            snapshot_ids = {e.entity_id for e in home.snapshot().entities}
            assert {"climate.living", "lock.front", "cover.shade"} <= snapshot_ids  # 읽기는 유지
            assert home.entity_controllable("light.living", voice=False)
            assert not home.entity_controllable("climate.living", voice=False)
            assert not home.entity_controllable("cover.shade", voice=True)
            with connect_mobile(client) as ws:
                assert result_of(ws, "light.living", "turn_off")[0]
                ok, code, key = result_of(ws, "climate.living", "turn_on")
                assert (ok, code, key) == (False, ERROR_CODE_BAD_REQUEST, "home.control_forbidden")
            fake_ha.emit("entity_registry_updated", {})
            eventually(lambda: any("Exposure list unavailable" in m for m in messages))
            assert sum("Exposure list unavailable" in m for m in messages) == 1
        finally:
            logger.remove(sink)
    assert [c for c in fake_ha.service_calls if c["type"] == "call_service"][-1][
        "domain"
    ] == "light"
    assert all(
        c["domain"] != "climate" for c in fake_ha.service_calls if c["type"] == "call_service"
    )


def test_known_exposure_allows_touch_control_of_climate_and_garage(
    ha_settings: Settings, fake_ha: FakeHomeAssistant
) -> None:
    with running_app(ha_settings) as client:
        eventually(lambda: home_of(client).ready)
        with connect_mobile(client) as ws:
            assert result_of(ws, "climate.living", "turn_on")[0]
            assert result_of(ws, "cover.garage", "turn_on")[0]  # 터치는 허용(음성만 차단)
            assert result_of(ws, "lock.front", "turn_on")[1] == ERROR_CODE_BAD_REQUEST
            assert result_of(ws, "script.goodnight", "run")[2] == "home.control_forbidden"


# --- WebSocket: 음성/LLM 경로 -----------------------------------------------------------


def voice_client(
    ha_settings: Settings, gateway: FakeOpenAI, scripts: str = ""
) -> Iterator[TestClient]:
    settings = ha_settings.model_copy(update={"allowed_scripts": scripts})
    yield from _build_client(settings, gateway)


def ask(ws: WebSocketTestSession, text: str) -> None:
    send(ws, Envelope(text_query=TextQuery(request_id="q", text=text)))
    until(ws, is_final_text)


def test_llm_cannot_open_garage_or_run_unlisted_script(
    ha_settings: Settings, fake_ha: FakeHomeAssistant
) -> None:
    gateway = FakeOpenAI()
    for client in voice_client(ha_settings, gateway):
        with connect_mobile(client) as ws:
            for entity_id, action in (
                ("cover.garage", "turn_on"),
                ("script.goodnight", "activate"),
            ):
                gateway.chat_turns += [
                    tool_turn("control_device", {"entity_id": entity_id, "action": action}),
                    [],
                ]
                ask(ws, "아무거나 해줘")
    enum = gateway.chat_calls[0]["tools"][0]["function"]["parameters"]["properties"]["entity_id"][
        "enum"
    ]
    assert "cover.garage" not in enum
    assert "script.goodnight" not in enum
    assert {"cover.shade", "light.living", "climate.living"} <= set(enum)
    assert [c for c in fake_ha.service_calls if c["type"] == "call_service"] == []


def test_llm_runs_script_only_when_listed(
    ha_settings: Settings, fake_ha: FakeHomeAssistant
) -> None:
    gateway = FakeOpenAI()
    for client in voice_client(ha_settings, gateway, scripts="script.goodnight"):
        gateway.chat_turns += [
            tool_turn("control_device", {"entity_id": "script.goodnight", "action": "activate"}),
            [],
        ]
        with connect_mobile(client) as ws:
            ask(ws, "굿나잇 해줘")
    enum = gateway.chat_calls[0]["tools"][0]["function"]["parameters"]["properties"]["entity_id"][
        "enum"
    ]
    assert "script.goodnight" in enum
    calls = [c for c in fake_ha.service_calls if c["type"] == "call_service"]
    assert [(c["domain"], c["service"]) for c in calls] == [("script", "turn_on")]
