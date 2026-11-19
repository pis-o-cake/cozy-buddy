import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.hub import crud as hub_crud
from app.domain.hub.models import Hub
from app.domain.hub.service import HubService
from tests.conftest import HUB_ID, connect_hub, eventually


def test_hub_auto_registered_on_hello(client: TestClient) -> None:
    assert client.get("/api/hub").json() == []
    with connect_hub(client):
        hubs = client.get("/api/hub").json()
        assert [h["hub_id"] for h in hubs] == [HUB_ID]
        assert hubs[0]["online"] is True
        assert hubs[0]["last_seen_at"] is not None
    hubs = eventually(lambda: [h for h in client.get("/api/hub").json() if not h["online"]])
    assert hubs[0]["hub_id"] == HUB_ID


def test_patch_hub_and_server_hello_hub_config(client: TestClient) -> None:
    with connect_hub(client):
        pass
    response = client.patch(f"/api/hub/{HUB_ID}", json={"name": "거실", "area_id": "living_room"})
    assert response.status_code == 200
    assert response.json()["name"] == "거실"

    cm = connect_hub(client)
    with cm:
        assert cm.server_hello is not None
        config = cm.server_hello.server_hello.hub_config
        assert (config.name, config.area_id) == ("거실", "living_room")


def test_patch_unknown_hub_returns_localized_404(client: TestClient) -> None:
    response = client.patch("/api/hub/nope", json={"name": "x"})
    assert response.status_code == 404
    assert response.json()["message_key"] == "hub.not_found"
    assert response.json()["message"] != "hub.not_found"


def test_patch_hub_rejects_oversized_name_and_area(client: TestClient) -> None:
    with connect_hub(client):
        pass
    assert client.patch(f"/api/hub/{HUB_ID}", json={"name": "가" * 101}).status_code == 422
    assert client.patch(f"/api/hub/{HUB_ID}", json={"area_id": "a" * 101}).status_code == 422
    assert client.patch(f"/api/hub/{HUB_ID}", json={"name": "가" * 100}).status_code == 200


def test_ensure_registered_survives_a_concurrent_first_hello(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    connections = client.app.state.connections  # type: ignore[attr-defined]
    original = hub_crud.get
    calls = 0

    async def stale_get(session: AsyncSession, hub_id: str) -> Hub | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await original(session, hub_id)

    async def scenario() -> Hub:
        async with db.session() as winner:
            await HubService(winner, connections).ensure_registered("race-hub")
        monkeypatch.setattr(hub_crud, "get", stale_get)
        async with db.session() as loser:
            return await HubService(loser, connections).ensure_registered("race-hub")

    hub = client.portal.call(scenario)  # type: ignore[union-attr]

    assert hub.hub_id == "race-hub" and hub.last_seen_at is not None
