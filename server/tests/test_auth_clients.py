"""클라이언트 목록 API 와 운영자 CLI."""

import argparse
import sqlite3
from collections.abc import Iterator
from contextlib import closing

import httpx
import pytest
from cozy.v1.session_pb2 import CLIENT_KIND_HUB
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.cli import build_parser
from app.config import Settings
from app.domain.auth import cli as auth_cli
from app.main import create_app
from tests.conftest import eventually, hello_envelope, migrate, recv, send

ADMIN_TOKEN = "admin-secret"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
def admin_client(settings: Settings) -> Iterator[TestClient]:
    migrate(settings.database_url)
    configured = settings.model_copy(update={"admin_token": SecretStr(ADMIN_TOKEN)})
    with TestClient(create_app(configured)) as c:
        yield c


def pair_hub(client: TestClient, name: str = "주방", area: str = "kitchen") -> dict[str, str]:
    code = client.post(
        "/api/auth/pairing-codes", json={"name": name, "area_id": area}, headers=ADMIN
    ).json()["code"]
    body: dict[str, str] = client.post("/api/auth/pair", json={"code": code}).json()
    return body


def test_list_requires_admin(admin_client: TestClient) -> None:
    assert admin_client.get("/api/auth/clients").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert admin_client.get("/api/auth/clients", headers=bad).status_code == 401


def test_list_unconfigured_is_503(client: TestClient) -> None:
    assert client.get("/api/auth/clients", headers=ADMIN).status_code == 503


def test_list_fields_and_no_secrets(admin_client: TestClient) -> None:
    creds = pair_hub(admin_client)
    response = admin_client.get("/api/auth/clients", headers=ADMIN)
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "client_id",
        "kind",
        "name",
        "area_id",
        "created_at",
        "revoked_at",
        "connected",
    }
    assert row["client_id"] == creds["client_id"]
    assert row["kind"] == "hub"
    assert row["name"] == "주방"
    assert row["area_id"] == "kitchen"
    assert row["revoked_at"] is None
    assert row["connected"] is False
    assert creds["token"] not in response.text


def test_hashes_never_returned(admin_client: TestClient, settings: Settings) -> None:
    pair_hub(admin_client)
    db_path = settings.database_url.removeprefix("sqlite+aiosqlite:///")
    with closing(sqlite3.connect(db_path)) as conn:
        stored = conn.execute("SELECT token_hash FROM client_token").fetchone()[0]
    assert (
        stored not in admin_client.get("/api/auth/clients?include_revoked=true", headers=ADMIN).text
    )


def test_include_revoked(admin_client: TestClient) -> None:
    keep = pair_hub(admin_client, "거실", "living")
    gone = pair_hub(admin_client, "방", "room")
    assert (
        admin_client.delete(f"/api/auth/clients/{gone['client_id']}", headers=ADMIN).status_code
        == 204
    )

    active = admin_client.get("/api/auth/clients", headers=ADMIN).json()
    assert [r["client_id"] for r in active] == [keep["client_id"]]

    everything = admin_client.get("/api/auth/clients?include_revoked=true", headers=ADMIN).json()
    by_id = {r["client_id"]: r for r in everything}
    assert set(by_id) == {keep["client_id"], gone["client_id"]}
    assert by_id[gone["client_id"]]["revoked_at"] is not None
    assert by_id[keep["client_id"]]["revoked_at"] is None


def test_connected_flag(admin_client: TestClient) -> None:
    creds = pair_hub(admin_client)
    with admin_client.websocket_connect("/ws") as ws:
        send(ws, hello_envelope(CLIENT_KIND_HUB, creds["client_id"], creds["token"]))
        recv(ws)
        rows = admin_client.get("/api/auth/clients", headers=ADMIN).json()
        assert rows[0]["connected"] is True
    # 연결 해제는 서버 쪽에서 비동기로 반영되므로 잠깐 기다린다.
    eventually(
        lambda: (
            admin_client.get("/api/auth/clients", headers=ADMIN).json()[0]["connected"] is False
            or None
        )
    )


# --- CLI ---------------------------------------------------------------------------------


def run_cli(
    argv: list[str],
    handler: httpx.MockTransport,
    env: dict[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environ = {auth_cli.ENV_ADMIN_TOKEN: ADMIN_TOKEN} if env is None else env
    return auth_cli.run(args, env=environ, transport=handler)


def test_cli_pairing_code_success(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"code": "123456", "expires_in_seconds": 300})

    assert (
        run_cli(
            ["pairing-code", "--name", "주방", "--area", "kitchen"], httpx.MockTransport(handle)
        )
        == 0
    )
    assert "123456" in capsys.readouterr().out
    request = seen[0]
    assert request.headers["authorization"] == f"Bearer {ADMIN_TOKEN}"
    assert request.url.path == "/api/auth/pairing-codes"
    assert b'"kitchen"' in request.content


def test_cli_clients_output(capsys: pytest.CaptureFixture[str]) -> None:
    rows = [
        {
            "client_id": "hub-aa",
            "kind": "hub",
            "name": "주방",
            "area_id": "kitchen",
            "created_at": "2026-01-01T00:00:00Z",
            "revoked_at": None,
            "connected": True,
        },
        {
            "client_id": "hub-bb",
            "kind": "hub",
            "name": "",
            "area_id": "",
            "created_at": "2026-01-01T00:00:00Z",
            "revoked_at": "2026-01-02T00:00:00Z",
            "connected": False,
        },
    ]
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=rows)

    assert run_cli(["clients", "--all"], httpx.MockTransport(handle)) == 0
    out = capsys.readouterr().out
    assert "hub-aa  hub  online" in out
    assert "hub-bb  hub  revoked" in out
    assert seen[0].url.params["include_revoked"] == "true"


def test_cli_revoke_success(capsys: pytest.CaptureFixture[str]) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(204))
    assert run_cli(["revoke", "hub-aa"], transport) == 0
    assert "Revoked hub-aa" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("status", "needle"),
    [(401, "401"), (503, "server/.env"), (404, "not found")],
)
def test_cli_error_statuses(status: int, needle: str, capsys: pytest.CaptureFixture[str]) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json={}))
    assert run_cli(["revoke", "hub-aa"], transport) == 1
    assert needle in capsys.readouterr().err


def test_cli_network_error(capsys: pytest.CaptureFixture[str]) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert run_cli(["clients"], httpx.MockTransport(handle)) == 1
    assert "cannot reach server" in capsys.readouterr().err


def test_cli_missing_token_explains_setup(capsys: pytest.CaptureFixture[str]) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call the server")

    assert run_cli(["clients"], httpx.MockTransport(handle), env={}) == 1
    err = capsys.readouterr().err
    assert "secrets.token_urlsafe" in err
    assert "server/.env" in err


def test_parser_requires_name() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["pairing-code"])
    assert isinstance(build_parser().parse_args(["clients"]), argparse.Namespace)
