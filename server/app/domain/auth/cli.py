"""운영자용 인증 CLI. 실행 중인 서버의 관리 API 를 localhost HTTP 로 호출한다.

등록 코드는 서버 프로세스 메모리에만 있어 DB 를 직접 열 수 없다. 운영자 도구라 출력은
언어팩 없이 영어로 고정한다(로그와 같은 이유로 검색·복붙 가능해야 한다).
"""

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

import httpx

ENV_ADMIN_TOKEN = "COZY_ADMIN_TOKEN"  # secret-scan: allow (env var name, not a value)
ENV_BASE_URL = "COZY_CLI_BASE_URL"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TIMEOUT_SECONDS = 10.0

EXIT_OK = 0
EXIT_FAILURE = 1

MSG_TOKEN_UNSET = (
    f"{ENV_ADMIN_TOKEN} is not set in this environment.\n"
    'Generate one:  python -c "import secrets; print(secrets.token_urlsafe(32))"\n'
    f"Put it in server/.env as {ENV_ADMIN_TOKEN}=<value>, then redeploy the server."
)
MSG_UNAUTHORIZED = f"Admin token rejected (401). Check that {ENV_ADMIN_TOKEN} matches the server's."
MSG_UNCONFIGURED = (
    f"Server has no admin token (503). Set {ENV_ADMIN_TOKEN} in server/.env and redeploy."
)

Handler = Callable[[httpx.Client, argparse.Namespace], None]


class CliError(Exception):
    """사용자에게 그대로 보여줄 CLI 실패."""


def add_subcommands(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """auth 하위 명령을 파서에 등록한다."""
    code = sub.add_parser("pairing-code", help="issue a one-time pairing code")
    code.add_argument("--name", required=True, help="hub display name")
    code.add_argument("--area", default="", help="Home Assistant area id")
    code.set_defaults(handler=_pairing_code)

    clients = sub.add_parser("clients", help="list issued clients")
    clients.add_argument("--all", action="store_true", help="include revoked clients")
    clients.set_defaults(handler=_clients)

    revoke = sub.add_parser("revoke", help="revoke a client token")
    revoke.add_argument("client_id")
    revoke.set_defaults(handler=_revoke)


def run(
    args: argparse.Namespace,
    env: Mapping[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """파싱된 인자로 명령을 실행하고 종료 코드를 돌려준다."""
    environ = os.environ if env is None else env
    token = environ.get(ENV_ADMIN_TOKEN, "")
    if not token:
        print(MSG_TOKEN_UNSET, file=sys.stderr)
        return EXIT_FAILURE
    base_url = environ.get(ENV_BASE_URL, DEFAULT_BASE_URL)
    handler: Handler = args.handler
    try:
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        ) as client:
            handler(client, args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except httpx.TransportError as exc:
        print(f"error: cannot reach server at {base_url}: {exc!r}", file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


def _check(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise CliError(MSG_UNAUTHORIZED)
    if response.status_code == 503:
        raise CliError(MSG_UNCONFIGURED)
    if response.status_code == 404:
        raise CliError("client not found or already revoked (404)")
    if response.is_error:
        raise CliError(f"unexpected response {response.status_code}: {response.text[:200]}")


def _pairing_code(client: httpx.Client, args: argparse.Namespace) -> None:
    response = client.post(
        "/api/auth/pairing-codes", json={"name": args.name, "area_id": args.area}
    )
    _check(response)
    body: dict[str, Any] = response.json()
    print(f"Pairing code: {body['code']} (valid {body['expires_in_seconds'] // 60} min, one use)")


def _clients(client: httpx.Client, args: argparse.Namespace) -> None:
    response = client.get("/api/auth/clients", params={"include_revoked": str(args.all).lower()})
    _check(response)
    rows: list[dict[str, Any]] = response.json()
    if not rows:
        print("No clients.")
        return
    for row in rows:
        state = "revoked" if row["revoked_at"] else ("online" if row["connected"] else "offline")
        area = f"[{row['area_id']}]" if row["area_id"] else ""
        label = " ".join(part for part in (row["name"], area) if part)
        line = f"{row['client_id']}  {row['kind']}  {state}  created={row['created_at']}  {label}"
        print(line.rstrip())


def _revoke(client: httpx.Client, args: argparse.Namespace) -> None:
    response = client.delete(f"/api/auth/clients/{args.client_id}")
    _check(response)
    print(f"Revoked {args.client_id}.")
