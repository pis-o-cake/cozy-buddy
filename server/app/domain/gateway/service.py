"""WebSocket 게이트웨이: 핸드셰이크, 인증, 메시지 라우팅."""

import asyncio
import time

from cozy.v1 import session_pb2
from cozy.v1.common_pb2 import (
    ERROR_CODE_BAD_REQUEST,
    ERROR_CODE_INTERNAL,
    ERROR_CODE_NOT_FOUND,
    ERROR_CODE_PROTOCOL_VERSION_UNSUPPORTED,
    ERROR_CODE_UNAUTHENTICATED,
    ErrorCode,
)
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.session_pb2 import CLIENT_KIND_HUB, CLIENT_KIND_MOBILE
from fastapi import WebSocket
from google.protobuf.message import DecodeError
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ClientRole, Settings
from app.core.connections import ClientConnection, ConnectionManager
from app.core.database import Database
from app.core.exceptions import BadRequestError, CozyError, NotFoundError
from app.core.protocol import PROTOCOL_VERSION, error_envelope
from app.core.security import authenticate
from app.core.version import get_server_version
from app.domain.alarm.service import AlarmService
from app.domain.home.service import HomeService
from app.domain.hub.service import HubService

CLOSE_POLICY_VIOLATION = 1008
CANCEL_GRACE_SECONDS = 5.0

KEY_UNAUTHENTICATED = "error.unauthenticated"
KEY_VERSION = "error.protocol_version_unsupported"
KEY_BAD_REQUEST = "error.bad_request"
KEY_INTERNAL = "error.internal"


class _TextFrameError(Exception):
    """텍스트 프레임을 받았다. 프로토콜은 바이너리 프레임만 허용한다."""


class GatewayService:
    """연결 하나의 수명주기를 처리한다. 요청마다 새로 만들어진다."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        connections: ConnectionManager,
        home: HomeService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._connections = connections
        self._home = home

    async def serve(self, websocket: WebSocket) -> None:
        """연결을 받아 hello 검증 후 메시지 루프를 돌린다.

        취소돼도 진행 중인 DB 작업과 정리는 끝낼 시간을 준다.
        """
        # IMPORTANT: DB 호출 도중 취소되면 SQLAlchemy 가 커넥션을 무효화하고 aiosqlite 닫기를
        # shield 태스크로 띄우는데, 그 태스크가 끝나지 않아 이벤트 루프 종료가 멈춘다.
        # TestClient 는 클라이언트가 WebSocket 을 닫는 즉시 엔드포인트를 취소한다.
        task = asyncio.ensure_future(self._serve(websocket))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.wait({task}, timeout=CANCEL_GRACE_SECONDS)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _serve(self, websocket: WebSocket) -> None:
        await websocket.accept()
        conn = ClientConnection(websocket)
        try:
            if not await self._handshake(conn):
                return
            await self._message_loop(conn)
        except _TextFrameError:
            logger.warning("Text frame rejected: {}", conn.client_id or "unauthenticated")
            await conn.close(CLOSE_POLICY_VIOLATION)
        finally:
            await self._cleanup(conn)

    async def _read(self, conn: ClientConnection, limit: float | None = None) -> bytes | None:
        """바이너리 프레임 하나를 읽는다. 연결이 끊기면 None."""
        async with asyncio.timeout(limit):
            message = await conn.websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        if message.get("text") is not None:
            raise _TextFrameError
        data: bytes | None = message.get("bytes")
        return data if data is not None else b""

    async def _reject(self, conn: ClientConnection, code: ErrorCode, key: str, reason: str) -> bool:
        logger.warning("Hello rejected: {}", reason)
        await self._send_quietly(conn, error_envelope(code, key))
        await conn.close(CLOSE_POLICY_VIOLATION)
        return False

    async def _handshake(self, conn: ClientConnection) -> bool:
        try:
            data = await self._read(conn, self._settings.hello_timeout_seconds)
        except TimeoutError:
            return await self._reject(
                conn, ERROR_CODE_UNAUTHENTICATED, KEY_UNAUTHENTICATED, "hello timed out"
            )
        if data is None:
            return False

        envelope = Envelope()
        try:
            envelope.ParseFromString(data)
        except DecodeError:
            return await self._reject(
                conn, ERROR_CODE_UNAUTHENTICATED, KEY_UNAUTHENTICATED, "undecodable first frame"
            )
        if envelope.WhichOneof("body") != "client_hello":
            return await self._reject(
                conn, ERROR_CODE_UNAUTHENTICATED, KEY_UNAUTHENTICATED, "first message not hello"
            )

        hello = envelope.client_hello
        if hello.protocol_version != PROTOCOL_VERSION:
            return await self._reject(
                conn,
                ERROR_CODE_PROTOCOL_VERSION_UNSUPPORTED,
                KEY_VERSION,
                f"unsupported protocol version {hello.protocol_version}",
            )
        role = {CLIENT_KIND_HUB: ClientRole.HUB, CLIENT_KIND_MOBILE: ClientRole.MOBILE}.get(
            hello.kind
        )
        if role is None or not authenticate(
            self._settings.token_entries, role, hello.client_id, hello.token
        ):
            return await self._reject(
                conn, ERROR_CODE_UNAUTHENTICATED, KEY_UNAUTHENTICATED, "invalid credentials"
            )

        conn.identify(role, hello.client_id)
        await self._accept_client(conn)
        return True

    async def _accept_client(self, conn: ClientConnection) -> None:
        server_hello = session_pb2.ServerHello(
            server_version=get_server_version(), server_time_epoch_ms=int(time.time() * 1000)
        )
        is_hub = conn.role == ClientRole.HUB
        async with self._database.session() as session:
            if is_hub:
                hub = await HubService(session, self._connections).ensure_registered(conn.client_id)
                server_hello.hub_config.name = hub.name
                server_hello.hub_config.area_id = hub.area_id
        await conn.send(Envelope(server_hello=server_hello))
        await self._connections.register(conn)
        logger.info("Client connected: {} {}", conn.role, conn.client_id)
        await conn.send(Envelope(home_snapshot=self._home.snapshot()))
        if not is_hub:
            return
        async with self._database.session() as session:
            await self._alarms(session).deliver_pending(conn.client_id)

    async def _message_loop(self, conn: ClientConnection) -> None:
        while True:
            data = await self._read(conn)
            if data is None:
                return
            await self._handle_frame(conn, data)

    async def _handle_frame(self, conn: ClientConnection, data: bytes) -> None:
        envelope = Envelope()
        try:
            envelope.ParseFromString(data)
        except DecodeError:
            await self._send_quietly(conn, error_envelope(ERROR_CODE_BAD_REQUEST, KEY_BAD_REQUEST))
            return
        try:
            await self._dispatch(conn, envelope)
        except CozyError as exc:
            logger.warning("Message rejected: {}", exc)
            await self._send_quietly(
                conn, error_envelope(_error_code(exc), exc.message_key, _request_id(envelope))
            )
        except Exception:
            logger.exception("Unhandled error while handling message")
            await self._send_quietly(
                conn, error_envelope(ERROR_CODE_INTERNAL, KEY_INTERNAL, _request_id(envelope))
            )

    async def _dispatch(self, conn: ClientConnection, envelope: Envelope) -> None:
        body = envelope.WhichOneof("body")
        if body == "ping":
            await conn.send(
                Envelope(pong=session_pb2.Pong(ping_sent_epoch_ms=envelope.ping.sent_epoch_ms))
            )
            return

        if body == "entity_command":
            result = await self._home.execute(envelope.entity_command)
            await conn.send(Envelope(command_result=result))
            return

        is_hub = conn.role == ClientRole.HUB
        if is_hub and body in ("alarm_snapshot", "alarm_ack"):
            async with self._database.session() as session:
                service = self._alarms(session)
                if body == "alarm_snapshot":
                    await service.handle_snapshot(conn.client_id, envelope.alarm_snapshot)
                else:
                    await service.handle_ack(conn.client_id, envelope.alarm_ack)
            return
        if not is_hub and body in ("alarm_upsert", "alarm_delete"):
            async with self._database.session() as session:
                service = self._alarms(session)
                if body == "alarm_upsert":
                    await service.handle_mobile_upsert(envelope.alarm_upsert)
                else:
                    await service.handle_mobile_delete(envelope.alarm_delete)
            return
        raise BadRequestError(f"Unsupported message: {body} from {conn.role}")

    def _alarms(self, session: AsyncSession) -> AlarmService:
        hubs = HubService(session, self._connections)
        return AlarmService(session, self._connections, hubs)

    async def _cleanup(self, conn: ClientConnection) -> None:
        if conn.role is None:
            return
        self._connections.unregister(conn)
        logger.info("Client disconnected: {} {}", conn.role, conn.client_id)
        if conn.role != ClientRole.HUB:
            return
        try:
            async with self._database.session() as session:
                await HubService(session, self._connections).touch(conn.client_id)
        except Exception:
            logger.exception("Failed to update last_seen_at: {}", conn.client_id)

    @staticmethod
    async def _send_quietly(conn: ClientConnection, envelope: Envelope) -> None:
        try:
            await conn.send(envelope)
        except Exception:
            logger.debug("Send failed, connection probably closed")


def _error_code(exc: CozyError) -> ErrorCode:
    if isinstance(exc, BadRequestError):
        return ERROR_CODE_BAD_REQUEST
    if isinstance(exc, NotFoundError):
        return ERROR_CODE_NOT_FOUND
    return ERROR_CODE_INTERNAL


def _request_id(envelope: Envelope) -> str:
    body = envelope.WhichOneof("body")
    if body == "alarm_upsert":
        return str(envelope.alarm_upsert.request_id)
    if body == "alarm_delete":
        return str(envelope.alarm_delete.request_id)
    if body == "entity_command":
        return str(envelope.entity_command.request_id)
    return ""
