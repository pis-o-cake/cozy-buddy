import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.session_pb2 import Ping

from app.config import ClientRole, Settings, parse_static_tokens
from app.core.connections import CLOSE_SLOW_CONSUMER, ClientConnection, ConnectionManager
from app.core.security import authenticate


def make_conn(role: ClientRole, client_id: str) -> ClientConnection:
    socket: Any = AsyncMock()
    conn = ClientConnection(socket)
    conn.identify(role, client_id)
    return conn


async def test_register_replaces_and_closes_old_connection() -> None:
    manager = ConnectionManager()
    old, new = make_conn(ClientRole.HUB, "h"), make_conn(ClientRole.HUB, "h")
    await manager.register(old)
    await manager.register(new)

    old.websocket.close.assert_awaited_once()  # type: ignore[attr-defined]
    assert manager.unregister(old) is False
    assert manager.is_hub_online("h")
    assert manager.unregister(new) is True
    assert not manager.is_hub_online("h")


async def test_send_and_broadcast_set_increasing_seq() -> None:
    manager = ConnectionManager()
    hub, m1, m2 = (
        make_conn(ClientRole.HUB, "h"),
        make_conn(ClientRole.MOBILE, "m1"),
        make_conn(ClientRole.MOBILE, "m2"),
    )
    for conn in (hub, m1, m2):
        await manager.register(conn)

    envelope = Envelope(ping=Ping(sent_epoch_ms=1))
    assert await manager.send_to_hub("h", envelope)
    assert not await manager.send_to_hub("missing", envelope)
    await manager.broadcast_to_mobiles(envelope)
    await manager.broadcast_to_mobiles(envelope)

    sent = [Envelope.FromString(c.args[0]) for c in m1.websocket.send_bytes.await_args_list]  # type: ignore[attr-defined]
    assert [e.seq for e in sent] == [1, 2]


def test_static_token_parsing_and_auth() -> None:
    entries = parse_static_tokens("hub:h1:a:b, mobile:m1:c")
    assert authenticate(entries, ClientRole.HUB, "h1", "a:b")
    assert not authenticate(entries, ClientRole.MOBILE, "h1", "a:b")
    assert not authenticate(entries, ClientRole.MOBILE, "m1", "x")
    with pytest.raises(ValueError):
        parse_static_tokens("tablet:x:y")
    with pytest.raises(ValueError):
        Settings(_env_file=None, static_tokens="broken")  # type: ignore[call-arg]


class StuckSocket:
    """send_bytes 가 끝나지 않는 느린 클라이언트."""

    def __init__(self) -> None:
        self.closed: int | None = None

    async def send_bytes(self, data: bytes) -> None:
        await asyncio.sleep(3600)

    async def close(self, code: int = 1000) -> None:
        self.closed = code


def stuck_conn(role: ClientRole, client_id: str) -> ClientConnection:
    conn = ClientConnection(StuckSocket())  # type: ignore[arg-type]
    conn.identify(role, client_id)
    return conn


async def test_broadcast_skips_a_stuck_client_without_delaying_others() -> None:
    manager = ConnectionManager(send_timeout_seconds=0.1)
    slow, fast = stuck_conn(ClientRole.MOBILE, "slow"), make_conn(ClientRole.MOBILE, "fast")
    hub = make_conn(ClientRole.HUB, "h")
    for conn in (slow, fast, hub):
        await manager.register(conn)

    started = time.monotonic()
    await manager.broadcast(Envelope(ping=Ping(sent_epoch_ms=1)))

    assert time.monotonic() - started < 1.0
    fast.websocket.send_bytes.assert_awaited_once()  # type: ignore[attr-defined]
    hub.websocket.send_bytes.assert_awaited_once()  # type: ignore[attr-defined]
    assert slow.websocket.closed == CLOSE_SLOW_CONSUMER  # type: ignore[attr-defined]


async def test_broadcast_isolates_exceptions_per_client() -> None:
    manager = ConnectionManager()
    broken, ok = make_conn(ClientRole.MOBILE, "broken"), make_conn(ClientRole.MOBILE, "ok")
    broken.websocket.send_bytes.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
    for conn in (broken, ok):
        await manager.register(conn)

    await manager.broadcast_to_mobiles(Envelope(ping=Ping(sent_epoch_ms=1)))

    ok.websocket.send_bytes.assert_awaited_once()  # type: ignore[attr-defined]


async def test_send_to_hub_times_out_and_closes_the_hub() -> None:
    manager = ConnectionManager(send_timeout_seconds=0.1)
    hub = stuck_conn(ClientRole.HUB, "h")
    await manager.register(hub)

    assert not await manager.send_to_hub("h", Envelope(ping=Ping(sent_epoch_ms=1)))
    assert hub.websocket.closed == CLOSE_SLOW_CONSUMER  # type: ignore[attr-defined]
