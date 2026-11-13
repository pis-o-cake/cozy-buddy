"""HA 브리지: 엔티티 캐시, 상태 변경 중계, 제어 명령 실행."""

import asyncio
import contextlib
from typing import Any

from cozy.v1.common_pb2 import (
    ERROR_CODE_BAD_REQUEST,
    ERROR_CODE_NOT_FOUND,
    ERROR_CODE_UPSTREAM_UNAVAILABLE,
    ErrorCode,
)
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.home_pb2 import CommandResult, EntityCommand, EntityStateChanged, HomeSnapshot
from loguru import logger

from app.config import Settings
from app.core.connections import ConnectionManager
from app.domain.home.commands import UnsupportedCommandError, build_service_call
from app.domain.home.ha_client import (
    HaCommandError,
    HaUnavailableError,
    HomeAssistantClient,
)
from app.domain.home.mapping import (
    build_catalog,
    catalog_fingerprint,
    changed_to_proto,
    parse_exposed,
    snapshot_to_proto,
    to_entity,
)
from app.domain.home.schemas import HomeCatalog

KEY_UNAVAILABLE = "home.unavailable"
KEY_UNSUPPORTED = "home.unsupported_action"
KEY_NOT_FOUND = "home.entity_not_found"

EXPOSE_LIST = "homeassistant/expose_entity/list"

# 구조 변경 이벤트. Assist 노출 변경은 전용 이벤트가 없고, 등록된 엔티티는
# entity_registry_updated(options 변경)로 온다. 레지스트리에 없는 엔티티는 폴링으로 잡는다.
REGISTRY_EVENTS: tuple[str, ...] = (
    "entity_registry_updated",
    "area_registry_updated",
    "device_registry_updated",
)
SUBSCRIBED_EVENTS: tuple[str, ...] = ("state_changed", *REGISTRY_EVENTS)


class HomeService:
    """HA 와의 연결 하나와 그 위의 엔티티 캐시를 소유한다. 앱 수명 동안 하나만 존재한다.

    HA 설정이 없으면 비활성이며, 이때 스냅샷은 비어 있고 제어는 UPSTREAM_UNAVAILABLE 이다.
    """

    def __init__(self, settings: Settings, connections: ConnectionManager) -> None:
        self._connections = connections
        self._catalog = HomeCatalog()
        self._client: HomeAssistantClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = False
        self._buffer: list[dict[str, Any]] | None = None
        self._expose_fallback_logged = False
        self._exposed: set[str] | None = None
        self._debounce = settings.ha_registry_debounce_seconds
        self._expose_poll = settings.ha_expose_poll_seconds
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_dirty = False
        self._poll_task: asyncio.Task[None] | None = None
        if settings.ha_enabled:
            self._client = HomeAssistantClient(
                settings.ha_url,
                settings.ha_token,
                on_connected=self._on_connected,
                on_disconnected=self._on_disconnected,
                on_event=self._on_event,
                request_timeout=settings.ha_request_timeout_seconds,
                backoff_min=settings.ha_backoff_min_seconds,
                backoff_max=settings.ha_backoff_max_seconds,
            )

    @property
    def enabled(self) -> bool:
        """HA 설정이 있어 브리지가 켜져 있는지."""
        return self._client is not None

    @property
    def ready(self) -> bool:
        """HA 에 연결되고 캐시가 만들어졌는지."""
        return self._ready

    async def start(self) -> None:
        """HA 연결 태스크를 띄운다. HA 가 꺼져 있어도 기다리지 않는다."""
        if self._client is None:
            logger.info("Home Assistant bridge disabled: COZY_HA_URL or COZY_HA_TOKEN not set")
            return
        self._task = asyncio.create_task(self._client.run(), name="home-assistant-client")

    async def stop(self) -> None:
        """HA 연결 태스크를 멈춘다."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def snapshot(self) -> HomeSnapshot:
        """현재 캐시의 스냅샷. 비활성이거나 연결 전이면 비어 있다."""
        return snapshot_to_proto(self._catalog)

    async def execute(self, command: EntityCommand) -> CommandResult:
        """EntityCommand 를 HA 서비스 호출로 실행한다. 예상 가능한 실패는 결과 코드로 돌려준다."""
        entity = self._catalog.entities.get(command.entity_id)
        client = self._client
        if client is None or not self._ready:
            return _failure(command, ERROR_CODE_UPSTREAM_UNAVAILABLE, KEY_UNAVAILABLE)
        if entity is None:
            return _failure(command, ERROR_CODE_NOT_FOUND, KEY_NOT_FOUND)
        try:
            call = build_service_call(entity.domain, command.action, command.params)
        except UnsupportedCommandError as exc:
            logger.warning("Entity command rejected: {}", exc)
            return _failure(command, ERROR_CODE_BAD_REQUEST, KEY_UNSUPPORTED)
        try:
            await client.call_service(call.domain, call.service, entity.entity_id, call.data)
        except (HaUnavailableError, HaCommandError) as exc:
            logger.warning("Home Assistant service call failed: {}", exc)
            return _failure(command, ERROR_CODE_UPSTREAM_UNAVAILABLE, KEY_UNAVAILABLE)
        return CommandResult(request_id=command.request_id, ok=True)

    async def _on_connected(self, client: HomeAssistantClient) -> None:
        self._buffer = []
        try:
            # IMPORTANT: 구독을 먼저 건다. 상태 조회 사이에 생긴 변경을 놓치지 않기 위해서다.
            for event_type in SUBSCRIBED_EVENTS:
                await client.request("subscribe_events", {"event_type": event_type})
            states = await client.request("get_states")
            areas = await client.request("config/area_registry/list")
            devices = await client.request("config/device_registry/list")
            registry = await client.request("config/entity_registry/list")
            self._exposed = await self._load_exposed(client)
            self._catalog = build_catalog(states, areas, devices, registry, self._exposed)
            buffered, self._buffer = self._buffer, None
        except BaseException:
            self._buffer = None
            raise
        for event in buffered:
            self._apply_state_changed(event)
        self._ready = True
        logger.info(
            "Home catalog built: areas={} entities={}",
            len(self._catalog.areas),
            len(self._catalog.entities),
        )
        if self._exposed is not None and self._expose_poll > 0:
            self._poll_task = asyncio.create_task(
                self._poll_exposed(client), name="home-expose-poll"
            )
        await self._connections.broadcast(Envelope(home_snapshot=self.snapshot()))

    async def _on_disconnected(self) -> None:
        self._ready = False
        await self._cancel(self._poll_task)
        await self._cancel(self._refresh_task)
        self._poll_task = self._refresh_task = None
        self._refresh_dirty = False
        self._catalog = HomeCatalog()
        logger.warning("Home Assistant disconnected, catalog cleared")
        await self._connections.broadcast(Envelope(home_snapshot=self.snapshot()))

    async def _on_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("event_type")
        if event_type in REGISTRY_EVENTS:
            self._schedule_refresh()
            return
        if event_type != "state_changed":
            return
        if self._buffer is not None:
            self._buffer.append(event)
            return
        changed = self._apply_state_changed(event)
        if changed is None:
            return
        await self._connections.broadcast(Envelope(entity_state_changed=changed))

    def _schedule_refresh(self) -> None:
        """캐시 재구성을 예약한다. 연결 직후 구성 중이면 새 데이터가 이미 반영되므로 무시한다."""
        if not self._ready or self._client is None:
            return
        self._refresh_dirty = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="home-refresh")

    async def _refresh_loop(self) -> None:
        # 대기 중에 온 이벤트는 대기 뒤 재구성이 반영하므로 dirty 를 대기 뒤에 내린다.
        # 재구성 중에 온 이벤트만 한 번 더 돈다.
        while self._refresh_dirty:
            await asyncio.sleep(self._debounce)
            self._refresh_dirty = False
            try:
                await self._refresh_catalog()
            except (HaUnavailableError, HaCommandError) as exc:
                logger.warning("Home catalog refresh failed: {}", exc)
            except Exception:
                logger.exception("Unexpected home catalog refresh error")

    async def _refresh_catalog(self) -> None:
        """레지스트리를 다시 읽어 구조가 바뀐 경우에만 새 스냅샷을 push 한다."""
        client = self._client
        if client is None or not self._ready or self._buffer is not None:
            return
        self._buffer = []
        try:
            states = await client.request("get_states")
            areas = await client.request("config/area_registry/list")
            devices = await client.request("config/device_registry/list")
            registry = await client.request("config/entity_registry/list")
            exposed = await self._load_exposed(client)
            fresh = build_catalog(states, areas, devices, registry, exposed)
        except BaseException:
            self._buffer = None
            raise
        buffered, self._buffer = self._buffer, None
        self._exposed = exposed
        if catalog_fingerprint(fresh) == catalog_fingerprint(self._catalog):
            logger.debug("Home registry changed but nothing visible, snapshot skipped")
            for event in buffered:
                changed = self._apply_state_changed(event)
                if changed is not None:
                    await self._connections.broadcast(Envelope(entity_state_changed=changed))
            return
        self._catalog = fresh
        for event in buffered:
            self._apply_state_changed(event)
        logger.info(
            "Home catalog refreshed: areas={} entities={}",
            len(self._catalog.areas),
            len(self._catalog.entities),
        )
        # 삭제된 엔티티도 클라이언트가 홈 상태 전체를 이 스냅샷으로 교체해 반영한다.
        await self._connections.broadcast(Envelope(home_snapshot=self.snapshot()))

    async def _poll_exposed(self, client: HomeAssistantClient) -> None:
        """전용 이벤트가 없는 노출 변경을 주기적으로 확인한다."""
        while True:
            await asyncio.sleep(self._expose_poll)
            try:
                current = parse_exposed(await client.request(EXPOSE_LIST))
            except HaUnavailableError:
                continue
            except HaCommandError:
                logger.warning("Exposure polling stopped: command unavailable")
                return
            if current != self._exposed:
                self._schedule_refresh()

    @staticmethod
    async def _cancel(task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _apply_state_changed(self, event: dict[str, Any]) -> EntityStateChanged | None:
        """캐시에 반영하고 클라이언트에 보낼 변경 메시지를 돌려준다. 대상이 아니면 None."""
        data = event.get("data") or {}
        entity_id = str(data.get("entity_id", ""))
        current = self._catalog.entities.get(entity_id)
        if current is None:
            return None
        new_state = data.get("new_state")
        if not new_state:
            del self._catalog.entities[entity_id]
            logger.debug("Entity removed from Home Assistant: {}", entity_id)
            return None
        updated = to_entity(new_state, current.area_id, current.name)
        if updated is None:
            return None
        self._catalog.entities[entity_id] = updated
        return changed_to_proto(updated)

    async def _load_exposed(self, client: HomeAssistantClient) -> set[str] | None:
        """Assist 노출 목록. 명령을 쓸 수 없으면 None(도메인 허용 목록으로 대체)."""
        try:
            return parse_exposed(await client.request(EXPOSE_LIST))
        except HaCommandError as exc:
            if not self._expose_fallback_logged:
                self._expose_fallback_logged = True
                logger.warning(
                    "Exposure list unavailable ({}), falling back to domain allow-list", exc.code
                )
            return None


def _failure(command: EntityCommand, code: ErrorCode, message_key: str) -> CommandResult:
    return CommandResult(
        request_id=command.request_id, ok=False, code=code, message_key=message_key
    )
