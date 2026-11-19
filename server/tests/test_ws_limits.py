"""WebSocket 한도: 첫 프레임 크기, hello 실패 제한, 연결 수 상한, 시작 시 토큰 검사."""

from collections.abc import Iterator

import pytest
from cozy.v1.common_pb2 import ERROR_CODE_UNAUTHENTICATED
from fastapi.testclient import TestClient
from loguru import logger
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from app.config import Settings, weak_token_ids
from app.core.ws_guard import Admission, WsGuard
from app.domain.gateway.service import (
    CLOSE_POLICY_VIOLATION,
    CLOSE_TRY_AGAIN_LATER,
    MAX_HELLO_BYTES,
)
from app.main import create_app
from app.middleware.request_log import sanitize_request_id
from tests.conftest import (
    HUB_ID,
    HUB_TOKEN,
    connect_hub,
    hello_envelope,
    migrate,
    recv,
    send,
)


def make_client(settings: Settings, **update: object) -> Iterator[TestClient]:
    migrate(settings.database_url)
    with TestClient(create_app(settings.model_copy(update=update))) as c:
        yield c


def close_code(ws: object) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        while True:
            ws.receive_bytes()  # type: ignore[attr-defined]
    return closed.value.code


def test_oversized_first_frame_is_rejected_before_parsing(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(token="x" * (MAX_HELLO_BYTES + 1)))
        assert recv(ws).error.code == ERROR_CODE_UNAUTHENTICATED
        assert close_code(ws) == CLOSE_POLICY_VIOLATION


def test_garbage_first_frame_is_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_bytes(b"\xff" * (MAX_HELLO_BYTES * 4))
        assert recv(ws).error.code == ERROR_CODE_UNAUTHENTICATED
        assert close_code(ws) == CLOSE_POLICY_VIOLATION


def test_normal_hello_still_works(client: TestClient) -> None:
    with connect_hub(client, HUB_ID, HUB_TOKEN):
        pass


@pytest.fixture
def throttled(settings: Settings) -> Iterator[TestClient]:
    yield from make_client(settings, ws_hello_failure_limit=3)


def test_failed_hello_throttle_closes_before_reading(throttled: TestClient) -> None:
    for _ in range(3):
        with throttled.websocket_connect("/ws") as ws:
            send(ws, hello_envelope(token="wrong"))
            assert recv(ws).error.code == ERROR_CODE_UNAUTHENTICATED
    with throttled.websocket_connect("/ws") as ws:
        # 올바른 hello 를 보내도 이미 막혔으므로 읽지 않고 닫는다.
        send(ws, hello_envelope())
        assert close_code(ws) == CLOSE_POLICY_VIOLATION


def test_successful_hello_does_not_count_as_failure(throttled: TestClient) -> None:
    for _ in range(6):
        with connect_hub(throttled):
            pass


@pytest.fixture
def two_slots(settings: Settings) -> Iterator[TestClient]:
    yield from make_client(settings, ws_max_connections=2)


def test_total_connection_cap(two_slots: TestClient) -> None:
    with (
        connect_hub(two_slots),
        connect_hub(two_slots, "hub-2", "hub2-secret"),
        two_slots.websocket_connect("/ws") as ws,
    ):
        assert close_code(ws) == CLOSE_TRY_AGAIN_LATER
    with connect_hub(two_slots):
        pass


@pytest.fixture
def one_unauth(settings: Settings) -> Iterator[TestClient]:
    yield from make_client(settings, ws_max_unauthenticated_per_ip=1, hello_timeout_seconds=5.0)


def test_unauthenticated_per_ip_cap(one_unauth: TestClient) -> None:
    with one_unauth.websocket_connect("/ws") as idle:
        with one_unauth.websocket_connect("/ws") as second:
            assert close_code(second) == CLOSE_POLICY_VIOLATION
        # 인증을 마친 연결은 미인증 한도를 쓰지 않는다.
        send(idle, hello_envelope())
        assert recv(idle).WhichOneof("body") == "server_hello"
    with one_unauth.websocket_connect("/ws") as ws:
        send(ws, hello_envelope())
        assert recv(ws).WhichOneof("body") == "server_hello"


def test_guard_window_slides() -> None:
    now = [0.0]
    guard = WsGuard(10, 10, 2, 60.0, clock=lambda: now[0])
    guard.record_failure("a")
    guard.record_failure("a")
    assert guard.admit("a") is Admission.BLOCKED
    assert guard.admit("b") is Admission.OK
    now[0] = 61.0
    assert guard.admit("a") is Admission.OK


def test_guard_release_restores_capacity() -> None:
    guard = WsGuard(1, 1, 5, 60.0)
    assert guard.admit("a") is Admission.OK
    assert guard.admit("b") is Admission.FULL
    guard.release("a", was_authenticated=False)
    assert guard.total == 0
    assert guard.admit("b") is Admission.OK


@pytest.mark.parametrize(
    "tokens",
    [
        "hub:hub-1:change-me-hub-token",
        "hub:hub-1:ok-token-ok-token-ok-token-ok,mobile:p:CHANGE-ME-mobile",
    ],
)
def test_placeholder_static_token_refuses_to_start(tokens: str) -> None:
    with pytest.raises(ValidationError, match="Placeholder"):
        Settings(_env_file=None, static_tokens=tokens)  # type: ignore[call-arg, arg-type]


def test_placeholder_admin_token_refuses_to_start() -> None:
    with pytest.raises(ValidationError, match="Placeholder"):
        Settings(_env_file=None, admin_token="change-me-admin")  # type: ignore[call-arg, arg-type]


def test_short_static_token_warns_but_starts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.main.configure_logging", lambda _level: None)
    assert weak_token_ids(settings.token_entries) == [HUB_ID, "hub-2", "phone-1"]
    messages: list[str] = []
    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        migrate(settings.database_url)
        with TestClient(create_app(settings)):
            pass
    finally:
        logger.remove(sink)
    assert any("shorter than 24" in m and HUB_ID in m for m in messages)
    assert not any(HUB_TOKEN in m for m in messages)


def test_long_static_token_is_not_weak() -> None:
    parsed = Settings(_env_file=None, static_tokens="hub:h:" + "a" * 24)  # type: ignore[call-arg, arg-type]
    assert weak_token_ids(parsed.token_entries) == []


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("req-123.abc_DEF", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("has space", False),
        ("line\nbreak", False),
        ("trailing\n", False),
        ("", False),
        ("한글", False),
        (None, False),
    ],
)
def test_request_id_sanitising(raw: str | None, kept: bool) -> None:
    result = sanitize_request_id(raw)
    assert (result == raw) is kept
    assert 1 <= len(result) <= 64


def test_request_id_header_roundtrip(client: TestClient) -> None:
    echoed = client.get("/health", headers={"x-request-id": "abc-1"})
    replaced = client.get("/health", headers={"x-request-id": "bad id!"})
    assert echoed.headers["x-request-id"] == "abc-1"
    assert replaced.headers["x-request-id"] != "bad id!"
    assert len(replaced.headers["x-request-id"]) == 32
