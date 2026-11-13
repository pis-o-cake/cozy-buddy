"""Home Assistant WebSocket API 클라이언트: 인증, 요청 상관, 이벤트 수신, 재연결."""

import asyncio
import contextlib
import itertools
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from loguru import logger
from pydantic import SecretStr
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

# get_states 응답이 기본 한도(1 MiB)를 넘을 수 있다.
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
BACKOFF_MIN_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
ConnectedHandler = Callable[["HomeAssistantClient"], Awaitable[None]]
DisconnectedHandler = Callable[[], Awaitable[None]]


class HomeAssistantError(Exception):
    """HA 연동 오류의 공통 부모."""


class HaUnavailableError(HomeAssistantError):
    """연결이 없거나 끊겼거나 응답 시간이 초과됐다."""


class HaAuthError(HomeAssistantError):
    """HA 가 토큰을 거부했다."""


class HaCommandError(HomeAssistantError):
    """HA 가 요청을 `success: false` 로 거절했다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def to_websocket_url(base_url: str) -> str:
    """HA 기본 URL(http/https)을 `/api/websocket` WebSocket URL 로 바꾼다.

    Raises:
        ValueError: 스킴이 http/https/ws/wss 가 아니거나 호스트가 없을 때.
    """
    parts = urlsplit(base_url.strip())
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(parts.scheme)
    if scheme is None or not parts.netloc:
        raise ValueError("Home Assistant URL must be http(s)://host[:port]")
    path = parts.path.rstrip("/") + "/api/websocket"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


class HomeAssistantClient:
    """HA WebSocket 연결 하나를 유지하며 요청/이벤트를 중계한다.

    `run()` 이 연결-인증-`on_connected`-수신 루프를 돌리고, 끊기면 지수 백오프(1s→30s)로
    다시 붙는다. `on_connected` 는 수신 루프가 도는 중에 호출되므로 `request()` 를 쓸 수 있다.
    """

    def __init__(
        self,
        url: str,
        token: SecretStr,
        *,
        on_connected: ConnectedHandler,
        on_disconnected: DisconnectedHandler,
        on_event: EventHandler,
        request_timeout: float = 5.0,
        backoff_min: float = BACKOFF_MIN_SECONDS,
        backoff_max: float = BACKOFF_MAX_SECONDS,
    ) -> None:
        self._ws_url = to_websocket_url(url)
        self._token = token
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_event = on_event
        self._request_timeout = request_timeout
        self._backoff_min = backoff_min
        self._backoff_max = backoff_max
        self._ws: ClientConnection | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}

    @property
    def connected(self) -> bool:
        """인증을 마치고 요청을 보낼 수 있는지."""
        return self._ws is not None

    async def run(self) -> None:
        """취소될 때까지 연결을 유지한다. 실패는 로그만 남기고 백오프 후 재시도한다."""
        delay = self._backoff_min
        while True:
            established = False
            try:
                established = await self._session()
            except asyncio.CancelledError:
                raise
            except HaAuthError:
                logger.error("Home Assistant rejected the access token")
            except (OSError, WebSocketException, HomeAssistantError, TimeoutError) as exc:
                logger.warning("Home Assistant connection failed: {}", type(exc).__name__)
            except Exception:
                logger.exception("Unexpected Home Assistant client error")
            if established:
                delay = self._backoff_min
            logger.info("Reconnecting to Home Assistant in {:.1f}s", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._backoff_max)

    async def request(self, message_type: str, payload: dict[str, Any] | None = None) -> Any:
        """명령 하나를 보내고 `result` 를 기다린다.

        Raises:
            HaUnavailableError: 연결이 없거나 끊겼거나 시간이 초과됐을 때.
            HaCommandError: HA 가 요청을 거절했을 때.
        """
        ws = self._ws
        if ws is None:
            raise HaUnavailableError("Not connected")
        message_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        frame = json.dumps({"id": message_id, "type": message_type, **(payload or {})})
        try:
            async with asyncio.timeout(self._request_timeout):
                await ws.send(frame)
                return await future
        except TimeoutError as exc:
            raise HaUnavailableError(f"Timed out: {message_type}") from exc
        except WebSocketException as exc:
            raise HaUnavailableError(f"Send failed: {message_type}") from exc
        finally:
            self._pending.pop(message_id, None)

    async def call_service(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any] | None = None
    ) -> None:
        """엔티티 하나를 대상으로 서비스를 호출한다."""
        await self.request(
            "call_service",
            {
                "domain": domain,
                "service": service,
                "service_data": data or {},
                "target": {"entity_id": entity_id},
            },
        )

    async def _session(self) -> bool:
        """연결 한 번의 수명. on_connected 까지 끝났으면 True."""
        async with connect(self._ws_url, max_size=MAX_MESSAGE_BYTES) as ws:
            await self._authenticate(ws)
            self._ws = ws
            reader = asyncio.create_task(self._read_loop(ws))
            established = False
            try:
                await self._on_connected(self)
                established = True
                logger.info("Home Assistant connected")
                await reader
            finally:
                self._ws = None
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
                self._fail_pending()
                if established:
                    await self._notify_disconnected()
        return established

    async def _authenticate(self, ws: ClientConnection) -> None:
        async with asyncio.timeout(self._request_timeout):
            first = json.loads(await ws.recv())
            if first.get("type") != "auth_required":
                raise HaUnavailableError("Unexpected greeting")
            await ws.send(
                json.dumps({"type": "auth", "access_token": self._token.get_secret_value()})
            )
            reply = json.loads(await ws.recv())
        if reply.get("type") == "auth_invalid":
            raise HaAuthError("Invalid access token")
        if reply.get("type") != "auth_ok":
            raise HaUnavailableError("Unexpected auth reply")

    async def _read_loop(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                await self._handle_frame(raw)
        finally:
            self._fail_pending()

    async def _handle_frame(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            logger.warning("Undecodable Home Assistant frame dropped")
            return
        kind = message.get("type")
        if kind == "result":
            self._resolve(message)
            return
        if kind != "event":
            return
        try:
            await self._on_event(message.get("event") or {})
        except Exception:
            logger.exception("Home Assistant event handler failed")

    def _resolve(self, message: dict[str, Any]) -> None:
        future = self._pending.get(message.get("id", -1))
        if future is None or future.done():
            return
        if message.get("success"):
            future.set_result(message.get("result"))
            return
        error = message.get("error") or {}
        future.set_exception(HaCommandError(error.get("code", "unknown"), error.get("message", "")))

    def _fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(HaUnavailableError("Connection lost"))

    async def _notify_disconnected(self) -> None:
        try:
            await self._on_disconnected()
        except Exception:
            logger.exception("Home Assistant disconnect handler failed")
