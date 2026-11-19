"""허브 페어링: 코드 발급, 등록, 토큰 인증, 폐기."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing

import pytest
from cozy.v1.session_pb2 import CLIENT_KIND_HUB
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.core.security import hash_token
from app.domain.auth.pairing import (
    PAIR_ATTEMPT_LIMIT,
    PAIRING_CODE_TTL_SECONDS,
    PairingGrant,
    PairingStore,
)
from app.main import create_app
from tests.conftest import (
    HUB_ID,
    HUB_TOKEN,
    connect_hub,
    hello_envelope,
    migrate,
    recv,
    send,
)

ADMIN_TOKEN = "admin-secret"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def admin_client(settings: Settings, clock: FakeClock) -> Iterator[TestClient]:
    migrate(settings.database_url)
    configured = settings.model_copy(update={"admin_token": SecretStr(ADMIN_TOKEN)})
    app = create_app(configured)
    app.state.pairing = PairingStore(clock)
    with TestClient(app) as c:
        yield c


def issue_code(client: TestClient, **body: str) -> str:
    response = client.post("/api/auth/pairing-codes", json=body, headers=ADMIN)
    assert response.status_code == 200
    code: str = response.json()["code"]
    return code


def pair(client: TestClient, code: str) -> dict[str, str]:
    response = client.post("/api/auth/pair", json={"code": code, "client_kind": "hub"})
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    return body


def test_code_is_six_digits_and_pair_issues_credentials(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/auth/pairing-codes", json={"name": "주방", "area_id": "kitchen"}, headers=ADMIN
    )
    body = response.json()
    assert len(body["code"]) == 6
    assert body["code"].isdigit()
    assert body["expires_in_seconds"] == PAIRING_CODE_TTL_SECONDS

    creds = pair(admin_client, body["code"])
    assert creds["client_id"].startswith("hub-")
    assert len(creds["token"]) >= 43

    hubs = {h["hub_id"]: h for h in admin_client.get("/api/hub").json()}
    assert hubs[creds["client_id"]]["name"] == "주방"
    assert hubs[creds["client_id"]]["area_id"] == "kitchen"


def test_code_expires_after_five_minutes(admin_client: TestClient, clock: FakeClock) -> None:
    code = issue_code(admin_client)
    clock.now += PAIRING_CODE_TTL_SECONDS + 1
    response = admin_client.post("/api/auth/pair", json={"code": code})
    assert response.status_code == 400
    assert response.json()["message_key"] == "auth.pairing_code_invalid"


def test_code_valid_just_before_expiry(admin_client: TestClient, clock: FakeClock) -> None:
    code = issue_code(admin_client)
    clock.now += PAIRING_CODE_TTL_SECONDS - 1
    pair(admin_client, code)


def test_code_is_single_use(admin_client: TestClient) -> None:
    code = issue_code(admin_client)
    pair(admin_client, code)
    again = admin_client.post("/api/auth/pair", json={"code": code})
    assert again.status_code == 400


def test_pair_rate_limited_per_ip(admin_client: TestClient, clock: FakeClock) -> None:
    for _ in range(PAIR_ATTEMPT_LIMIT):
        assert admin_client.post("/api/auth/pair", json={"code": "000000"}).status_code == 400
    limited = admin_client.post("/api/auth/pair", json={"code": "000000"})
    assert limited.status_code == 429
    assert limited.json()["message_key"] == "auth.rate_limited"

    # 한도를 넘으면 유효한 코드도 거절된다.
    code = issue_code(admin_client)
    assert admin_client.post("/api/auth/pair", json={"code": code}).status_code == 429

    clock.now += 61
    pair(admin_client, code)


def test_token_stored_as_hash_only(admin_client: TestClient, settings: Settings) -> None:
    creds = pair(admin_client, issue_code(admin_client))
    db_path = settings.database_url.removeprefix("sqlite+aiosqlite:///")
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM client_token").fetchall()
    assert len(rows) == 1
    assert hash_token(creds["token"]) in rows[0]
    assert creds["token"] not in rows[0]


def test_issued_token_authenticates_websocket(admin_client: TestClient) -> None:
    creds = pair(admin_client, issue_code(admin_client))
    with admin_client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(CLIENT_KIND_HUB, creds["client_id"], creds["token"]))
        assert recv(ws).WhichOneof("body") == "server_hello"


@pytest.mark.parametrize("variant", ["wrong_token", "wrong_id"])
def test_issued_token_mismatch_rejected(admin_client: TestClient, variant: str) -> None:
    creds = pair(admin_client, issue_code(admin_client))
    client_id = "hub-other" if variant == "wrong_id" else creds["client_id"]
    token = "x" * 43 if variant == "wrong_token" else creds["token"]
    with admin_client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(CLIENT_KIND_HUB, client_id, token))
        assert recv(ws).error.message_key == "error.unauthenticated"


def test_static_tokens_still_work(admin_client: TestClient) -> None:
    with connect_hub(admin_client, HUB_ID, HUB_TOKEN):
        pass


def test_revoke_rejects_and_disconnects_live_session(admin_client: TestClient) -> None:
    creds = pair(admin_client, issue_code(admin_client))
    with admin_client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(CLIENT_KIND_HUB, creds["client_id"], creds["token"]))
        assert recv(ws).WhichOneof("body") == "server_hello"
        response = admin_client.delete(f"/api/auth/clients/{creds['client_id']}", headers=ADMIN)
        assert response.status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                ws.receive_bytes()
    assert closed.value.code == 1008

    with admin_client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(CLIENT_KIND_HUB, creds["client_id"], creds["token"]))
        assert recv(ws).error.message_key == "error.unauthenticated"


def test_revoke_unknown_client_is_404(admin_client: TestClient) -> None:
    response = admin_client.delete("/api/auth/clients/hub-nope", headers=ADMIN)
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/api/auth/pairing-codes"), ("delete", "/api/auth/clients/hub-x")],
)
def test_admin_endpoints_require_admin_token(
    admin_client: TestClient, method: str, path: str
) -> None:
    missing = admin_client.request(method, path)
    wrong = admin_client.request(method, path, headers={"Authorization": "Bearer nope"})
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_admin_endpoints_503_when_admin_token_unset(client: TestClient) -> None:
    created = client.post("/api/auth/pairing-codes", json={}, headers=ADMIN)
    revoked = client.delete("/api/auth/clients/hub-x", headers=ADMIN)
    assert created.status_code == 503
    assert revoked.status_code == 503


def test_pair_works_without_admin_token_but_needs_a_code(client: TestClient) -> None:
    assert client.post("/api/auth/pair", json={"code": "123456"}).status_code == 400


def test_store_codes_do_not_collide() -> None:
    store = PairingStore()
    codes = {store.issue(PairingGrant("", "")) for _ in range(200)}
    assert len(codes) == 200


def _bad_pair(client: TestClient, headers: dict[str, str] | None = None) -> int:
    return client.post("/api/auth/pair", json={"code": "000000"}, headers=headers).status_code


def _exhaust(client: TestClient, headers: dict[str, str] | None = None) -> None:
    for _ in range(PAIR_ATTEMPT_LIMIT):
        assert _bad_pair(client, headers) == 400
    assert _bad_pair(client, headers) == 429


def _client_from(settings: Settings, clock: FakeClock, peer: str) -> Iterator[TestClient]:
    migrate(settings.database_url)
    app = create_app(settings)
    app.state.pairing = PairingStore(clock)
    with TestClient(app, client=(peer, 50000)) as c:
        yield c


@pytest.fixture
def proxied_client(settings: Settings, clock: FakeClock) -> Iterator[TestClient]:
    yield from _client_from(settings, clock, "172.18.0.2")


@pytest.fixture
def spoofing_client(settings: Settings, clock: FakeClock) -> Iterator[TestClient]:
    yield from _client_from(settings, clock, "203.0.113.9")


def test_trusted_proxy_limits_per_real_ip(proxied_client: TestClient) -> None:
    first = {"X-Real-IP": "198.51.100.1"}
    second = {"X-Real-IP": "198.51.100.2"}
    _exhaust(proxied_client, first)
    assert _bad_pair(proxied_client, second) == 400
    assert _bad_pair(proxied_client, first) == 429


def test_trusted_proxy_uses_last_forwarded_hop(proxied_client: TestClient) -> None:
    _exhaust(proxied_client, {"X-Forwarded-For": "6.6.6.6, 198.51.100.7"})
    other = {"X-Forwarded-For": "7.7.7.7, 198.51.100.8"}
    assert _bad_pair(proxied_client, other) == 400


def test_trusted_proxy_without_header_uses_peer(proxied_client: TestClient) -> None:
    _exhaust(proxied_client)
    assert _bad_pair(proxied_client, {"X-Real-IP": "198.51.100.3"}) == 400


def test_untrusted_peer_header_is_ignored(spoofing_client: TestClient) -> None:
    _exhaust(spoofing_client, {"X-Real-IP": "198.51.100.1"})
    assert _bad_pair(spoofing_client, {"X-Real-IP": "198.51.100.2"}) == 429


def test_direct_client_limits_by_peer(admin_client: TestClient) -> None:
    _exhaust(admin_client)
