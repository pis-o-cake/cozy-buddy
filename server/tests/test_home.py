"""home 브리지: 가짜 HA 서버를 상대로 스냅샷, 상태 중계, 제어, 재연결을 검증한다."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from cozy.v1.common_pb2 import (
    ERROR_CODE_BAD_REQUEST,
    ERROR_CODE_NOT_FOUND,
    ERROR_CODE_UPSTREAM_UNAVAILABLE,
)
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.home_pb2 import EntityCommand, HomeSnapshot
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.domain.home.commands import UnsupportedCommandError, build_service_call
from app.domain.home.ha_client import to_websocket_url
from app.domain.home.service import HomeService
from app.main import create_app
from tests.conftest import (
    connect_hub,
    connect_mobile,
    eventually,
    migrate,
    recv,
    send,
)
from tests.fake_ha import FakeHomeAssistant

EXPECTED_ENTITIES = {
    "light.living",
    "light.bed",
    "switch.plug",
    "scene.movie",
    "script.goodnight",
}


def command(entity_id: str, action: str, request_id: str = "r1", **params: str) -> Envelope:
    return Envelope(
        entity_command=EntityCommand(
            request_id=request_id, entity_id=entity_id, action=action, params=params
        )
    )


def recv_bodies(
    ws: WebSocketTestSession, wanted: set[str], max_frames: int = 10
) -> dict[str, Envelope]:
    """원하는 body 종류가 모두 모일 때까지 읽는다. 그 외 프레임과 도착 순서는 무시한다."""
    found: dict[str, Envelope] = {}
    for _ in range(max_frames):
        envelope = recv(ws)
        found.setdefault(str(envelope.WhichOneof("body")), envelope)
        if wanted <= found.keys():
            return found
    raise AssertionError(f"Expected {sorted(wanted)}, got {sorted(found)} in {max_frames} frames")


def recv_until(ws: WebSocketTestSession, body: str) -> Envelope:
    """지정한 body 종류의 프레임이 올 때까지 읽는다."""
    return recv_bodies(ws, {body})[body]


@contextmanager
def running_app(settings: Settings) -> Iterator[TestClient]:
    migrate(settings.database_url)
    with TestClient(create_app(settings)) as client:
        yield client


def home_of(client: TestClient) -> HomeService:
    home: HomeService = client.app.state.home  # type: ignore[attr-defined]
    return home


def by_id(snapshot: HomeSnapshot) -> dict[str, object]:
    return {e.entity_id: e for e in snapshot.entities}


# --- 순수 함수 ---------------------------------------------------------------


def test_ws_url_from_http_and_https() -> None:
    assert to_websocket_url("http://homeassistant:8123") == "ws://homeassistant:8123/api/websocket"
    assert to_websocket_url("https://ha.example/") == "wss://ha.example/api/websocket"
    with pytest.raises(ValueError):
        to_websocket_url("ftp://x")


@pytest.mark.parametrize(
    ("domain", "action", "params", "service", "data"),
    [
        ("light", "turn_on", {}, "turn_on", {}),
        ("cover", "turn_off", {}, "close_cover", {}),
        ("light", "set_brightness", {"brightness_pct": "40"}, "turn_on", {"brightness_pct": 40}),
        ("light", "set_brightness", {"brightness_pct": "0"}, "turn_off", {}),
        ("scene", "activate", {}, "turn_on", {}),
        ("script", "run", {}, "turn_on", {}),
    ],
)
def test_build_service_call(
    domain: str, action: str, params: dict[str, str], service: str, data: dict[str, int]
) -> None:
    call = build_service_call(domain, action, params)
    assert (call.domain, call.service, call.data) == (domain, service, data)


@pytest.mark.parametrize(
    ("domain", "action", "params"),
    [
        ("light", "explode", {}),
        ("sensor", "turn_on", {}),
        ("light", "set_brightness", {}),
        ("light", "set_brightness", {"brightness_pct": "150"}),
        ("switch", "set_brightness", {"brightness_pct": "10"}),
        ("light", "activate", {}),
        ("switch", "run", {}),
    ],
)
def test_build_service_call_rejects(domain: str, action: str, params: dict[str, str]) -> None:
    with pytest.raises(UnsupportedCommandError):
        build_service_call(domain, action, params)


# --- 비활성 ------------------------------------------------------------------


def test_disabled_bridge_sends_empty_snapshot_and_rejects_commands(client: TestClient) -> None:
    assert not home_of(client).enabled
    ctx = connect_mobile(client)
    with ctx as ws:
        assert ctx.home_snapshot is not None
        assert len(ctx.home_snapshot.entities) == 0
        send(ws, command("light.living", "turn_off"))
        result = recv_until(ws, "command_result").command_result
    assert result.request_id == "r1"
    assert not result.ok
    assert result.code == ERROR_CODE_UPSTREAM_UNAVAILABLE
    assert result.message_key == "home.unavailable"


def test_app_starts_while_ha_is_down(ha_settings: Settings, fake_ha: FakeHomeAssistant) -> None:
    fake_ha.stop()
    with running_app(ha_settings) as client:
        assert home_of(client).enabled
        assert not home_of(client).ready
        ctx = connect_hub(client)
        with ctx:
            assert ctx.home_snapshot is not None
            assert len(ctx.home_snapshot.entities) == 0


# --- 스냅샷 ------------------------------------------------------------------


def test_snapshot_maps_areas_and_filters_exposure(ha_client: TestClient) -> None:
    ctx = connect_mobile(ha_client)
    with ctx:
        snapshot = ctx.home_snapshot
    assert snapshot is not None
    assert {a.area_id: a.name for a in snapshot.areas} == {
        "living_room": "Living Room",
        "bedroom": "Bedroom",
    }
    entities = {e.entity_id: e for e in snapshot.entities}
    # 미노출(light.hidden, sensor.temp)과 비허용 도메인(automation)은 빠진다.
    assert set(entities) == EXPECTED_ENTITIES
    assert entities["light.living"].area_id == "living_room"
    assert entities["light.bed"].area_id == "bedroom"
    assert entities["scene.movie"].area_id == ""
    living = entities["light.living"]
    assert living.domain == "light"
    assert living.name == "Living Light"
    assert living.state.state == "on"
    assert dict(living.state.attributes) == {"friendly_name": "Living Light", "brightness": "200"}
    assert living.state.changed_epoch_ms > 0


def test_snapshot_sent_to_hub_after_hello(ha_client: TestClient) -> None:
    ctx = connect_hub(ha_client)
    with ctx:
        assert ctx.home_snapshot is not None
        assert {e.entity_id for e in ctx.home_snapshot.entities} == EXPECTED_ENTITIES


def test_expose_list_unavailable_falls_back_to_domain_allow_list(
    ha_settings: Settings, fake_ha: FakeHomeAssistant
) -> None:
    fake_ha.expose_supported = False
    with running_app(ha_settings) as client:
        eventually(lambda: home_of(client).ready)
        ctx = connect_mobile(client)
        with ctx:
            assert ctx.home_snapshot is not None
            ids = {e.entity_id for e in ctx.home_snapshot.entities}
    assert {"light.hidden", "sensor.temp"} <= ids
    assert "automation.morning" not in ids


# --- 상태 변경 중계 ------------------------------------------------------------


def test_state_change_is_broadcast_to_all_clients(
    ha_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    with connect_mobile(ha_client) as mobile, connect_hub(ha_client) as hub:
        fake_ha.push_state("light.living", "off", {"brightness": None})
        for ws in (mobile, hub):
            changed = recv(ws)
            assert changed.WhichOneof("body") == "entity_state_changed"
            assert changed.entity_state_changed.entity_id == "light.living"
            assert changed.entity_state_changed.state.state == "off"


def test_state_change_of_unbridged_entity_is_ignored(
    ha_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    with connect_mobile(ha_client) as mobile:
        fake_ha.push_state("light.hidden", "off")
        fake_ha.push_state("switch.plug", "off")
        changed = recv(mobile)
    assert changed.entity_state_changed.entity_id == "switch.plug"


# --- 제어 --------------------------------------------------------------------


def test_command_turn_off_succeeds_and_state_follows(
    ha_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    with connect_mobile(ha_client) as ws:
        send(ws, command("light.living", "turn_off", "req-7"))
        got = recv_bodies(ws, {"command_result", "entity_state_changed"})
    assert got["command_result"].command_result.request_id == "req-7"
    assert got["command_result"].command_result.ok
    assert got["entity_state_changed"].entity_state_changed.state.state == "off"
    call = fake_ha.service_calls[-1]
    assert (call["domain"], call["service"]) == ("light", "turn_off")
    assert call["target"] == {"entity_id": "light.living"}


def test_hub_command_set_brightness(ha_client: TestClient, fake_ha: FakeHomeAssistant) -> None:
    with connect_hub(ha_client) as ws:
        send(ws, command("light.living", "set_brightness", brightness_pct="35"))
        result = recv_until(ws, "command_result").command_result
    assert result.ok
    call = fake_ha.service_calls[-1]
    assert (call["domain"], call["service"]) == ("light", "turn_on")
    assert call["service_data"] == {"brightness_pct": 35}


@pytest.mark.parametrize(
    ("entity_id", "action", "service"),
    [("scene.movie", "activate", "scene"), ("script.goodnight", "run", "script")],
)
def test_command_scene_and_script(
    ha_client: TestClient, fake_ha: FakeHomeAssistant, entity_id: str, action: str, service: str
) -> None:
    with connect_mobile(ha_client) as ws:
        send(ws, command(entity_id, action))
        result = recv_until(ws, "command_result").command_result
    assert result.ok
    call = fake_ha.service_calls[-1]
    assert (call["domain"], call["service"]) == (service, "turn_on")


@pytest.mark.parametrize(
    ("entity_id", "action", "code"),
    [
        ("light.living", "explode", ERROR_CODE_BAD_REQUEST),
        ("sensor.nothing", "turn_on", ERROR_CODE_NOT_FOUND),
        ("light.hidden", "turn_on", ERROR_CODE_NOT_FOUND),
    ],
)
def test_command_rejections(
    ha_client: TestClient, fake_ha: FakeHomeAssistant, entity_id: str, action: str, code: int
) -> None:
    with connect_mobile(ha_client) as ws:
        send(ws, command(entity_id, action, "bad-1"))
        result = recv_until(ws, "command_result").command_result
    assert result.request_id == "bad-1"
    assert not result.ok
    assert result.code == code
    assert fake_ha.service_calls == []


def test_command_times_out_when_ha_is_slow(
    ha_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    fake_ha.service_delay = 2.0
    with connect_mobile(ha_client) as ws:
        send(ws, command("switch.plug", "turn_off"))
        result = recv_until(ws, "command_result").command_result
    assert not result.ok
    assert result.code == ERROR_CODE_UPSTREAM_UNAVAILABLE


def test_command_ha_error_is_upstream_unavailable(
    ha_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    fake_ha.service_error = "boom"
    with connect_mobile(ha_client) as ws:
        send(ws, command("switch.plug", "turn_off"))
        result = recv_until(ws, "command_result").command_result
    assert not result.ok
    assert result.code == ERROR_CODE_UPSTREAM_UNAVAILABLE


# --- 재연결 ------------------------------------------------------------------


def test_reconnect_rebuilds_cache_and_pushes_fresh_snapshot(
    ha_client: TestClient, fake_ha: FakeHomeAssistant
) -> None:
    port = fake_ha.port
    with connect_mobile(ha_client) as ws:
        fake_ha.stop()
        down = recv_until(ws, "home_snapshot")
        assert down.WhichOneof("body") == "home_snapshot"
        assert len(down.home_snapshot.entities) == 0
        assert not home_of(ha_client).ready

        send(ws, command("light.living", "turn_off"))
        assert (
            recv_until(ws, "command_result").command_result.code == ERROR_CODE_UPSTREAM_UNAVAILABLE
        )

        fake_ha.states[0]["state"] = "off"
        fake_ha.start(port)
        fresh = recv_until(ws, "home_snapshot")

    assert fresh.WhichOneof("body") == "home_snapshot"
    entities = {e.entity_id: e for e in fresh.home_snapshot.entities}
    assert set(entities) == EXPECTED_ENTITIES
    assert entities["light.living"].state.state == "off"
    assert home_of(ha_client).ready
