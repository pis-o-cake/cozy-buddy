from typing import Any
from unittest.mock import AsyncMock

import pytest
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.session_pb2 import Ping

from app.config import ClientRole, Settings, parse_static_tokens
from app.core.connections import ClientConnection, ConnectionManager
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
