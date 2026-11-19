"""WebSocket 연결 레지스트리."""

import asyncio

from cozy.v1.envelope_pb2 import Envelope
from fastapi import WebSocket
from loguru import logger
from starlette.requests import HTTPConnection

from app.config import ClientRole
from app.core.hub_control import HubControl

CLOSE_REPLACED = 4000
CLOSE_SLOW_CONSUMER = 4001
SERVING_DRAIN_SECONDS = 5.0
DEFAULT_SEND_TIMEOUT_SECONDS = 5.0


class ClientConnection:
    """WebSocket 하나와 송신 순번을 묶는다.

    인증 전에는 role/client_id 가 없고, `identify` 후에 레지스트리에 등록된다.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.role: ClientRole | None = None
        self.client_id = ""
        self.client_ip = ""
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

    def __init__(self, send_timeout_seconds: float = DEFAULT_SEND_TIMEOUT_SECONDS) -> None:
        self._send_timeout = send_timeout_seconds
        self._hubs: dict[str, ClientConnection] = {}
        self._mobiles: dict[str, ClientConnection] = {}
        self.hub_control = HubControl(self)
        self._serving: set[asyncio.Future[None]] = set()

    def track_serving(self, task: asyncio.Future[None]) -> None:
        """연결 처리 태스크를 추적한다. 종료 시 `drain_serving` 이 끝나기를 기다린다."""
        self._serving.add(task)
        task.add_done_callback(self._serving.discard)

    async def drain_serving(self, grace_seconds: float) -> None:
        """진행 중인 연결 처리 태스크가 끝나길 기다리고, 남은 것은 취소한다.

        IMPORTANT: DB 세션을 쥔 채 엔진이 먼저 dispose 되면 그 커넥션이 닫히지 않고 새어
        aiosqlite 워커 스레드가 닫힌 이벤트 루프에 접근한다. 반드시 dispose 전에 호출한다.
        """
        pending = {t for t in self._serving if not t.done()}
        if not pending:
            return
        await asyncio.wait(pending, timeout=grace_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def _pool(self, role: ClientRole) -> dict[str, ClientConnection]:
        return self._hubs if role == ClientRole.HUB else self._mobiles

    async def register(self, conn: ClientConnection) -> None:
        """연결을 등록한다. 같은 client_id 의 기존 연결은 닫고 대체한다."""
        if conn.role is None:
            raise ValueError("Connection is not identified")
        pool = self._pool(conn.role)
        old = pool.get(conn.client_id)
        pool[conn.client_id] = conn
        if conn.role == ClientRole.HUB:
            self.hub_control.forget(conn.client_id)
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
        if conn.role == ClientRole.HUB:
            self.hub_control.forget(conn.client_id)
        return True

    async def disconnect_client(self, client_id: str, code: int) -> int:
        """client_id 의 연결을 허브·앱 구분 없이 닫는다.

        Returns:
            닫은 연결 수.
        """
        conns = [pool[client_id] for pool in (self._hubs, self._mobiles) if client_id in pool]
        for conn in conns:
            await conn.close(code)
        return len(conns)

    def is_hub_online(self, hub_id: str) -> bool:
        """허브가 연결돼 있는지."""
        return hub_id in self._hubs

    def online_hub_ids(self) -> set[str]:
        """연결된 허브 ID 집합."""
        return set(self._hubs)

    def online_client_ids(self) -> set[str]:
        """연결된 허브·앱의 client_id 집합."""
        return set(self._hubs) | set(self._mobiles)

    async def _deliver(self, conn: ClientConnection, envelope: Envelope) -> bool:
        """제한 시간 안에 보낸다. 실패는 예외 대신 False 로 돌려주고 연결 하나로 격리한다.

        WARNING: 시간 초과한 연결은 프레임이 반쯤 나갔을 수 있어 재사용하지 않고 닫는다.
        """
        try:
            async with asyncio.timeout(self._send_timeout):
                await conn.send(envelope)
        except TimeoutError:
            logger.warning("Send timed out, closing slow client: {} {}", conn.role, conn.client_id)
            await self._close_quietly(conn)
            return False
        except Exception:
            logger.exception("Failed to send to client: {} {}", conn.role, conn.client_id)
            return False
        return True

    async def _close_quietly(self, conn: ClientConnection) -> None:
        try:
            async with asyncio.timeout(self._send_timeout):
                await conn.close(CLOSE_SLOW_CONSUMER)
        except Exception:
            logger.debug("Slow client close failed: {}", conn.client_id)

    async def _fan_out(self, conns: list[ClientConnection], envelope: Envelope) -> None:
        async def one(conn: ClientConnection) -> None:
            copy = Envelope()
            copy.CopyFrom(envelope)
            await self._deliver(conn, copy)

        await asyncio.gather(*(one(conn) for conn in conns))

    async def send_to_hub(self, hub_id: str, envelope: Envelope) -> bool:
        """허브에 보낸다. 오프라인이거나 전송에 실패하거나 시간이 초과되면 False."""
        conn = self._hubs.get(hub_id)
        if conn is None:
            return False
        return await self._deliver(conn, envelope)

    async def broadcast(self, envelope: Envelope) -> None:
        """연결된 모든 허브와 앱에 동시에 보낸다. 느리거나 실패한 연결은 그 연결만 건너뛴다."""
        await self._fan_out([*self._hubs.values(), *self._mobiles.values()], envelope)

    async def broadcast_to_mobiles(self, envelope: Envelope) -> None:
        """연결된 모든 앱에 동시에 보낸다. 느리거나 실패한 연결은 그 연결만 건너뛴다."""
        await self._fan_out(list(self._mobiles.values()), envelope)


def get_connections(conn: HTTPConnection) -> ConnectionManager:
    """HTTP/WebSocket 공용 ConnectionManager 의존성."""
    manager: ConnectionManager = conn.app.state.connections
    return manager
