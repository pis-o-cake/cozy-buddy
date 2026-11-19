"""REST 기본 거부: 토큰 종류별 접근 행렬, 역할 제한, 문서 UI 끄기."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import PUBLIC_ROUTERS, create_app
from tests.conftest import (
    ADMIN_HEADERS,
    HUB_ID,
    HUB_TOKEN,
    MOBILE_TOKEN,
    migrate,
)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def anon(client: TestClient) -> TestClient:
    del client.headers["authorization"]
    return client


def issue(client: TestClient, kind: str) -> dict[str, str]:
    code = client.post(
        "/api/auth/pairing-codes", json={"client_kind": kind}, headers=ADMIN_HEADERS
    ).json()["code"]
    response = client.post("/api/auth/pair", json={"code": code})
    assert response.status_code == 200
    body: dict[str, str] = response.json()
    return body


READ_PATHS = ["/api/hub", "/api/alarm"]
ADMIN_PATHS = ["/api/system/turns", "/api/system/turns/summary", "/api/system/backup"]


@pytest.mark.parametrize("path", READ_PATHS + ADMIN_PATHS)
def test_no_token_is_401(anon: TestClient, path: str) -> None:
    assert anon.get(path).status_code == 401


@pytest.mark.parametrize("path", READ_PATHS + ADMIN_PATHS)
def test_wrong_token_is_401(anon: TestClient, path: str) -> None:
    assert anon.get(path, headers=bearer("nope")).status_code == 401
    assert anon.get(path, headers={"Authorization": "Bearer "}).status_code == 401
    assert anon.get(path, headers={"Authorization": "Basic abc"}).status_code == 401


def test_unset_admin_token_does_not_accept_empty_bearer(settings: Settings) -> None:
    migrate(settings.database_url)
    unset = settings.model_copy(update={"admin_token": SecretStr("")})
    with TestClient(create_app(unset), headers={}) as c:
        assert c.get("/api/hub", headers={"Authorization": "Bearer "}).status_code == 401
        assert c.get("/api/hub").status_code == 401


@pytest.mark.parametrize("path", READ_PATHS)
def test_every_valid_token_kind_can_read(anon: TestClient, path: str) -> None:
    issued = issue(anon, "mobile")
    for token in (HUB_TOKEN, MOBILE_TOKEN, issued["token"], ADMIN_HEADERS["Authorization"][7:]):
        assert anon.get(path, headers=bearer(token)).status_code == 200


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_system_is_admin_only(anon: TestClient, path: str) -> None:
    issued_hub = issue(anon, "hub")
    for token in (HUB_TOKEN, MOBILE_TOKEN, issued_hub["token"]):
        assert anon.get(path, headers=bearer(token)).status_code == 403
    assert anon.get(path, headers=ADMIN_HEADERS).status_code == 200


def test_forbidden_body_is_localized(anon: TestClient) -> None:
    body = anon.get("/api/system/turns", headers=bearer(HUB_TOKEN)).json()
    assert body["message_key"] == "error.forbidden"
    assert body["message"] != body["message_key"]


def test_patch_hub_allows_admin_and_mobile_only(anon: TestClient) -> None:
    issued_hub = issue(anon, "hub")
    issued_mobile = issue(anon, "mobile")
    body = {"name": "거실"}
    url = "/api/hub/hub-unknown"
    assert anon.patch(url, json=body).status_code == 401
    assert anon.patch(url, json=body, headers=bearer(HUB_TOKEN)).status_code == 403
    assert anon.patch(url, json=body, headers=bearer(issued_hub["token"])).status_code == 403
    # 허브가 없어 404 이면 인증·권한은 통과한 것이다.
    assert anon.patch(url, json=body, headers=bearer(MOBILE_TOKEN)).status_code == 404
    assert anon.patch(url, json=body, headers=bearer(issued_mobile["token"])).status_code == 404
    assert anon.patch(url, json=body, headers=ADMIN_HEADERS).status_code == 404
    assert HUB_ID


def test_broadcast_accepts_issued_mobile_and_rejects_hub(anon: TestClient) -> None:
    issued_mobile = issue(anon, "mobile")
    body = {"text": "밥"}
    assert anon.post("/api/broadcast", json=body, headers=bearer(HUB_TOKEN)).status_code == 403
    allowed = anon.post("/api/broadcast", json=body, headers=bearer(issued_mobile["token"]))
    assert allowed.status_code == 404  # 대상 허브가 없어 404. 인증은 통과했다.


def test_revoked_issued_token_is_rejected(anon: TestClient) -> None:
    issued = issue(anon, "mobile")
    token = issued["token"]
    assert anon.get("/api/hub", headers=bearer(token)).status_code == 200
    revoked = anon.delete(f"/api/auth/clients/{issued['client_id']}", headers=ADMIN_HEADERS)
    assert revoked.status_code == 204
    assert anon.get("/api/hub", headers=bearer(token)).status_code == 401


def test_public_routes_stay_open(anon: TestClient) -> None:
    assert anon.get("/health").status_code == 200
    assert anon.post("/api/auth/pair", json={"code": "000000"}).status_code == 400


def test_public_router_list_is_exactly_auth_and_gateway() -> None:
    assert {("auth", "router"), ("gateway", "root_router")} == PUBLIC_ROUTERS


@pytest.fixture
def docs_client(settings: Settings, request: pytest.FixtureRequest) -> Iterator[TestClient]:
    migrate(settings.database_url)
    enabled = settings.model_copy(update={"api_docs": request.param})
    with TestClient(create_app(enabled)) as c:
        yield c


@pytest.mark.parametrize("docs_client", [False], indirect=True)
def test_docs_are_off_by_default(docs_client: TestClient) -> None:
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert docs_client.get(path).status_code == 404


@pytest.mark.parametrize("docs_client", [True], indirect=True)
def test_docs_can_be_enabled(docs_client: TestClient) -> None:
    assert docs_client.get("/docs").status_code == 200
    assert docs_client.get("/openapi.json").status_code == 200


def test_docs_flag_defaults_to_false() -> None:
    assert Settings(_env_file=None).api_docs is False  # type: ignore[call-arg]


def test_every_non_public_route_requires_auth(anon: TestClient) -> None:
    """등록된 모든 HTTP 라우트를 토큰 없이 호출해 공개 목록 밖에는 401 만 나오는지 본다."""
    open_prefixes = ("/api/auth/pair", "/health", "/ws")
    for route in anon.app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if not methods or path.startswith(open_prefixes) or path.startswith("/api/auth"):
            continue
        for method in methods:
            response = anon.request(method, path.replace("{hub_id}", "x"))
            assert response.status_code == 401, (method, path)
