"""WebSocket 연결 레지스트리."""

import asyncio

from cozy.v1.envelope_pb2 import Envelope
from fastapi import WebSocket
from loguru import logger
from starlette.requests import HTTPConnection

from app.config import ClientRole

CLOSE_REPLACED = 4000


class ClientConnection:
    """WebSocket 하나와 송신 순번을 묶는다.

    인증 전에는 role/client_id 가 없고, `identify` 후에 레지스트리에 등록된다.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.role: ClientRole | None = None
        self.client_id = ""
        self._seq = 0
        self._lock = asyncio.Lock()

    def identify(self, role: ClientRole, client_id: str) -> None:
        """인증이 끝난 연결에 종류와 ID 를 부여한다."""
        self.role = role
        self.client_id = client_id

    async def send(self, envelope: Envelope) -> None:
        """seq 를 매겨 바이너리 프레임으로 보낸다. 동시 송신은 직렬화한다."""
        async with self._lock:
            self._seq += 1
            envelope.seq = self._seq
            await self.websocket.send_bytes(envelope.SerializeToString())

    async def close(self, code: int = 1000) -> None:
        """이미 닫혔어도 예외를 내지 않고 닫는다."""
        try:
            await self.websocket.close(code=code)
        except RuntimeError:
            logger.debug("Connection already closed: {}", self.client_id)


class ConnectionManager:
    """연결된 허브·앱을 client_id 별로 추적한다. 프로세스 메모리에만 있다."""

    def __init__(self) -> None:
        self._hubs: dict[str, ClientConnection] = {}
        self._mobiles: dict[str, ClientConnection] = {}

    def _pool(self, role: ClientRole) -> dict[str, ClientConnection]:
        return self._hubs if role == ClientRole.HUB else self._mobiles

    async def register(self, conn: ClientConnection) -> None:
        """연결을 등록한다. 같은 client_id 의 기존 연결은 닫고 대체한다."""
        if conn.role is None:
            raise ValueError("Connection is not identified")
        pool = self._pool(conn.role)
        old = pool.get(conn.client_id)
        pool[conn.client_id] = conn
        if old is not None and old is not conn:
            logger.info("Replacing connection: {} {}", conn.role, conn.client_id)
            await old.close(CLOSE_REPLACED)

    def unregister(self, conn: ClientConnection) -> bool:
        """등록된 연결이 이 인스턴스일 때만 제거한다.

        Returns:
            제거했으면 True. 이미 새 연결로 대체됐으면 False.
        """
        if conn.role is None:
            return False
        pool = self._pool(conn.role)
        if pool.get(conn.client_id) is not conn:
            return False
        del pool[conn.client_id]
        return True

    def is_hub_online(self, hub_id: str) -> bool:
        """허브가 연결돼 있는지."""
        return hub_id in self._hubs

    def online_hub_ids(self) -> set[str]:
        """연결된 허브 ID 집합."""
        return set(self._hubs)

    async def send_to_hub(self, hub_id: str, envelope: Envelope) -> bool:
        """허브에 보낸다. 오프라인이거나 전송에 실패하면 False."""
        conn = self._hubs.get(hub_id)
        if conn is None:
            return False
        try:
            await conn.send(envelope)
        except Exception:
            logger.exception("Failed to send to hub: {}", hub_id)
            return False
        return True

    async def broadcast_to_mobiles(self, envelope: Envelope) -> None:
        """연결된 모든 앱에 보낸다. 개별 실패는 로그만 남기고 계속한다."""
        for conn in list(self._mobiles.values()):
            copy = Envelope()
            copy.CopyFrom(envelope)
            try:
                await conn.send(copy)
            except Exception:
                logger.exception("Failed to send to mobile: {}", conn.client_id)


def get_connections(conn: HTTPConnection) -> ConnectionManager:
    """HTTP/WebSocket 공용 ConnectionManager 의존성."""
    manager: ConnectionManager = conn.app.state.connections
    return manager
