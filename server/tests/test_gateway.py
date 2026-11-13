import pytest
from cozy.v1.common_pb2 import (
    ERROR_CODE_BAD_REQUEST,
    ERROR_CODE_PROTOCOL_VERSION_UNSUPPORTED,
    ERROR_CODE_UNAUTHENTICATED,
)
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.session_pb2 import CLIENT_KIND_HUB, CLIENT_KIND_MOBILE, Ping
from cozy.v1.voice_pb2 import ResponseAudioEnd
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import (
    HUB_ID,
    HUB_TOKEN,
    MOBILE_ID,
    MOBILE_TOKEN,
    connect_hub,
    connect_mobile,
    eventually,
    hello_envelope,
    recv,
    send,
)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_hello_success_for_hub(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope())
        reply = recv(ws)
    assert reply.WhichOneof("body") == "server_hello"
    assert reply.seq == 1
    assert reply.server_hello.server_version
    assert reply.server_hello.server_time_epoch_ms > 0


def test_hello_success_for_mobile(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(CLIENT_KIND_MOBILE, MOBILE_ID, MOBILE_TOKEN))
        reply = recv(ws)
    assert reply.WhichOneof("body") == "server_hello"
    assert not reply.server_hello.HasField("hub_config")


@pytest.mark.parametrize(
    ("kind", "client_id", "token"),
    [
        (CLIENT_KIND_HUB, HUB_ID, "wrong"),
        (CLIENT_KIND_HUB, "unknown", HUB_TOKEN),
        (CLIENT_KIND_MOBILE, HUB_ID, HUB_TOKEN),
    ],
)
def test_hello_rejects_bad_credentials(
    client: TestClient, kind: int, client_id: str, token: str
) -> None:
    with client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(kind, client_id, token))
        reply = recv(ws)
        assert reply.error.code == ERROR_CODE_UNAUTHENTICATED
        assert reply.error.message_key == "error.unauthenticated"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == 1008


def test_hello_rejects_unsupported_version(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(version=2))
        reply = recv(ws)
        assert reply.error.code == ERROR_CODE_PROTOCOL_VERSION_UNSUPPORTED
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()


def test_first_message_must_be_hello(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        send(ws, Envelope(ping=Ping(sent_epoch_ms=1)))
        reply = recv(ws)
        assert reply.error.code == ERROR_CODE_UNAUTHENTICATED
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()


def test_hello_timeout(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        reply = recv(ws)
        assert reply.error.code == ERROR_CODE_UNAUTHENTICATED
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == 1008


def test_ping_returns_pong_with_increasing_seq(client: TestClient) -> None:
    with connect_hub(client) as ws:
        send(ws, Envelope(ping=Ping(sent_epoch_ms=1234)))
        pong = recv(ws)
        send(ws, Envelope(ping=Ping(sent_epoch_ms=1235)))
        pong2 = recv(ws)
    assert pong.pong.ping_sent_epoch_ms == 1234
    assert (pong.seq, pong2.seq) == (4, 5)


def test_text_frame_is_rejected(client: TestClient) -> None:
    with connect_hub(client) as ws:
        ws.send_text("hello")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == 1008


def test_unsupported_body_returns_bad_request_and_stays_open(client: TestClient) -> None:
    with connect_hub(client) as ws:
        send(ws, Envelope(response_audio_end=ResponseAudioEnd()))
        assert recv(ws).error.code == ERROR_CODE_BAD_REQUEST
        ws.send_bytes(b"\xff\xff\xff")
        assert recv(ws).error.code == ERROR_CODE_BAD_REQUEST
        send(ws, Envelope(ping=Ping(sent_epoch_ms=1)))
        assert recv(ws).WhichOneof("body") == "pong"


def test_new_connection_replaces_old_one(client: TestClient) -> None:
    with connect_hub(client) as first:
        with connect_hub(client) as second:
            with pytest.raises(WebSocketDisconnect) as closed:
                first.receive_bytes()
            assert closed.value.code == 4000
            send(second, Envelope(ping=Ping(sent_epoch_ms=1)))
            assert recv(second).WhichOneof("body") == "pong"
        eventually(lambda: client.get("/api/hub").json()[0]["online"] is False)


def test_mobile_cannot_send_hub_messages(client: TestClient) -> None:
    from cozy.v1.alarm_pb2 import AlarmSnapshot

    with connect_mobile(client) as ws:
        send(ws, Envelope(alarm_snapshot=AlarmSnapshot(hub_id=HUB_ID)))
        assert recv(ws).error.code == ERROR_CODE_BAD_REQUEST
