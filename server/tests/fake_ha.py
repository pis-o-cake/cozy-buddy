"""테스트용 가짜 Home Assistant WebSocket 서버.

별도 스레드의 이벤트 루프에서 돌아, 동기 TestClient 와 비동기 테스트 양쪽에서 쓴다.
"""

import asyncio
import json
import threading
from collections.abc import Awaitable
from concurrent.futures import Future
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

TOKEN = "fake-ha-token"


def default_states() -> list[dict[str, Any]]:
    return [
        _state("light.living", "on", {"friendly_name": "Living Light", "brightness": 200}),
        _state("light.bed", "off", {"friendly_name": "Bed Light"}),
        _state("switch.plug", "on", {"friendly_name": "Plug"}),
        _state("scene.movie", "scening", {"friendly_name": "Movie"}),
        _state("script.goodnight", "off", {"friendly_name": "Goodnight"}),
        _state("sensor.temp", "21.5", {"friendly_name": "Temp", "unit_of_measurement": "C"}),
        # 노출되지 않은 엔티티와 허용되지 않은 도메인
        _state("light.hidden", "on", {"friendly_name": "Hidden"}),
        _state("automation.morning", "on", {"friendly_name": "Morning"}),
    ]


def _state(entity_id: str, state: str, attributes: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes,
        "last_changed": "2026-11-13T09:00:00+00:00",
    }


class FakeHomeAssistant:
    """auth, get_states, 레지스트리, 노출 목록, subscribe_events, call_service 를 흉내 낸다."""

    def __init__(self) -> None:
        self.states = default_states()
        self.areas = [
            {"area_id": "living_room", "name": "Living Room"},
            {"area_id": "bedroom", "name": "Bedroom"},
        ]
        self.devices = [{"id": "dev-bed", "area_id": "bedroom"}]
        self.entity_registry = [
            {"entity_id": "light.living", "area_id": "living_room", "device_id": None},
            # 엔티티 area 가 없으면 디바이스 area 를 쓴다.
            {"entity_id": "light.bed", "area_id": None, "device_id": "dev-bed"},
            {"entity_id": "switch.plug", "area_id": "living_room", "device_id": None},
        ]
        self.exposed = {
            "light.living",
            "light.bed",
            "switch.plug",
            "scene.movie",
            "script.goodnight",
            "sensor.temp",
            "automation.morning",
        }
        self.expose_supported = True
        self.service_delay = 0.0
        self.service_error: str | None = None
        self.service_calls: list[dict[str, Any]] = []
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._server: Server | None = None
        self._subscribers: dict[ServerConnection, dict[str, int]] = {}
        self.registry_list_calls = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, port: int = 0) -> None:
        if not self._thread.is_alive():
            self._thread.start()
        self._wait(self._start(port))

    def stop(self) -> None:
        """서버를 내리고 열린 연결을 모두 끊는다. 스레드는 유지해 다시 start 할 수 있다."""
        self._wait(self._stop())

    def close(self) -> None:
        self.stop()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)

    def push_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        """상태를 바꾸고 구독자에게 state_changed 를 보낸다."""
        self._wait(self._push_state(entity_id, state, attributes))

    def assign_area(self, entity_id: str, area_id: str | None) -> None:
        """엔티티 레지스트리의 area 를 바꾸고 entity_registry_updated 를 보낸다."""
        self._wait(self._assign_area(entity_id, area_id))

    def set_exposed(self, entity_id: str, exposed: bool, *, emit: bool = True) -> None:
        """Assist 노출을 바꾼다. HA 는 등록된 엔티티의 노출 변경을 options 갱신 이벤트로 알린다."""
        self._wait(self._set_exposed(entity_id, exposed, emit))

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """구독자에게 임의의 이벤트를 보낸다."""
        self._wait(self._emit(event_type, data))

    def _wait(self, coro: Awaitable[Any]) -> Any:
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]
        return future.result(timeout=5)

    async def _start(self, port: int) -> None:
        self._server = await serve(self._handle, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _stop(self) -> None:
        if self._server is None:
            return
        self._server.close(close_connections=True)
        await self._server.wait_closed()
        self._server = None
        self._subscribers.clear()

    async def _assign_area(self, entity_id: str, area_id: str | None) -> None:
        for entry in self.entity_registry:
            if entry["entity_id"] == entity_id:
                entry["area_id"] = area_id
        await self._emit(
            "entity_registry_updated",
            {"action": "update", "entity_id": entity_id, "changes": {"area_id": None}},
        )

    async def _set_exposed(self, entity_id: str, exposed: bool, emit: bool) -> None:
        if exposed:
            self.exposed.add(entity_id)
        else:
            self.exposed.discard(entity_id)
        if emit:
            await self._emit(
                "entity_registry_updated",
                {"action": "update", "entity_id": entity_id, "changes": {"options": {}}},
            )

    async def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        event = {"event_type": event_type, "data": data}
        for ws, subs in list(self._subscribers.items()):
            sub_id = subs.get(event_type)
            if sub_id is None:
                continue
            try:
                await ws.send(json.dumps({"id": sub_id, "type": "event", "event": event}))
            except ConnectionClosed:
                self._subscribers.pop(ws, None)

    async def _push_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None
    ) -> None:
        current = next(s for s in self.states if s["entity_id"] == entity_id)
        old = json.loads(json.dumps(current))
        current["state"] = state
        if attributes is not None:
            current["attributes"].update(attributes)
        current["last_changed"] = "2026-11-13T09:30:00+00:00"
        event = {
            "event_type": "state_changed",
            "data": {"entity_id": entity_id, "old_state": old, "new_state": current},
        }
        await self._emit(event["event_type"], event["data"])

    async def _handle(self, ws: ServerConnection) -> None:
        await ws.send(json.dumps({"type": "auth_required", "ha_version": "fake"}))
        auth = json.loads(await ws.recv())
        if auth.get("access_token") != TOKEN:
            await ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid"}))
            return
        await ws.send(json.dumps({"type": "auth_ok", "ha_version": "fake"}))
        try:
            async for raw in ws:
                await self._dispatch(ws, json.loads(raw))
        except ConnectionClosed:
            pass
        finally:
            self._subscribers.pop(ws, None)

    async def _dispatch(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        kind = msg["type"]
        msg_id = msg["id"]
        if kind == "subscribe_events":
            self._subscribers.setdefault(ws, {})[msg["event_type"]] = msg_id
            await self._reply(ws, msg_id, None)
        elif kind == "get_states":
            await self._reply(ws, msg_id, self.states)
        elif kind == "config/area_registry/list":
            await self._reply(ws, msg_id, self.areas)
        elif kind == "config/device_registry/list":
            await self._reply(ws, msg_id, self.devices)
        elif kind == "config/entity_registry/list":
            self.registry_list_calls += 1
            await self._reply(ws, msg_id, self.entity_registry)
        elif kind == "homeassistant/expose_entity/list":
            await self._expose_list(ws, msg_id)
        elif kind == "call_service":
            await self._call_service(ws, msg_id, msg)
        else:
            await self._reply(ws, msg_id, None, error=("unknown_command", kind))

    async def _expose_list(self, ws: ServerConnection, msg_id: int) -> None:
        if not self.expose_supported:
            await self._reply(ws, msg_id, None, error=("unknown_command", "unknown"))
            return
        entities = {e: {"conversation": True} for e in self.exposed}
        entities["sensor.temp"] = {"conversation": False}
        await self._reply(ws, msg_id, {"exposed_entities": entities})

    async def _call_service(self, ws: ServerConnection, msg_id: int, msg: dict[str, Any]) -> None:
        self.service_calls.append(msg)
        if self.service_delay:
            await asyncio.sleep(self.service_delay)
        if self.service_error:
            await self._reply(ws, msg_id, None, error=("service_error", self.service_error))
            return
        await self._reply(ws, msg_id, {"context": {}})
        entity_id = msg["target"]["entity_id"]
        if msg["service"] in ("turn_on", "turn_off"):
            await self._push_state(entity_id, msg["service"].removeprefix("turn_"), None)

    @staticmethod
    async def _reply(
        ws: ServerConnection, msg_id: int, result: Any, error: tuple[str, str] | None = None
    ) -> None:
        body: dict[str, Any] = {"id": msg_id, "type": "result", "success": error is None}
        if error is None:
            body["result"] = result
        else:
            body["error"] = {"code": error[0], "message": error[1]}
        await ws.send(json.dumps(body))
