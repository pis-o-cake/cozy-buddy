"""음성 알람 설정이 허브 ack 를 기다리는 흐름과 타이머 길이 기억 범위를 검증한다."""

from collections.abc import Iterator
from concurrent.futures import Future
from typing import Any

import pytest
from cozy.v1.alarm_pb2 import ALARM_KIND_ALARM, AlarmAck
from cozy.v1.envelope_pb2 import Envelope
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.agent.skills.alarm_store import (
    KEY_HUB_REJECTED,
    KEY_HUB_UNREACHABLE,
    MAX_REMEMBERED_TIMERS,
    TIMER_MEMORY_GRACE_SECONDS,
    AlarmStore,
    UpsertOutcome,
    new_alarm,
)
from app.main import create_app
from tests.conftest import HUB_ID, connect_hub, migrate, recv, send


@pytest.fixture
def fast_client(settings: Settings) -> Iterator[TestClient]:
    quick = settings.model_copy(update={"alarm_ack_timeout_seconds": 0.3})
    migrate(quick.database_url)
    with TestClient(create_app(quick)) as c:
        yield c


def make_store(client: TestClient) -> AlarmStore:
    app: Any = client.app
    return AlarmStore(lambda: app.state.db, app.state.connections)


def start_upsert(client: TestClient, store: AlarmStore) -> Future[UpsertOutcome]:
    alarm = new_alarm(ALARM_KIND_ALARM, "기상", hour=7, minute=0, days=(1,))
    return client.portal.start_task_soon(store.upsert, HUB_ID, alarm)  # type: ignore[union-attr]


def ack_for(request: Any, *, ok: bool) -> Envelope:
    ack = AlarmAck(request_id=request.request_id, alarm_id=request.alarm.alarm_id, ok=ok)
    return Envelope(alarm_ack=ack)


def test_upsert_is_confirmed_by_the_hub_ack(fast_client: TestClient) -> None:
    store = make_store(fast_client)
    with connect_hub(fast_client) as hub:
        outcome = start_upsert(fast_client, store)
        send(hub, ack_for(recv(hub).alarm_upsert, ok=True))
        assert outcome.result(5) == UpsertOutcome()


def test_upsert_rejected_by_the_hub_reports_rejection(fast_client: TestClient) -> None:
    store = make_store(fast_client)
    with connect_hub(fast_client) as hub:
        outcome = start_upsert(fast_client, store)
        send(hub, ack_for(recv(hub).alarm_upsert, ok=False))
        assert outcome.result(5) == UpsertOutcome(KEY_HUB_REJECTED)


def test_upsert_without_ack_is_pending(fast_client: TestClient) -> None:
    store = make_store(fast_client)
    with connect_hub(fast_client) as hub:
        outcome = start_upsert(fast_client, store)
        recv(hub)
        assert outcome.result(5) == UpsertOutcome(pending=True)


def test_upsert_to_offline_hub_is_unreachable(fast_client: TestClient) -> None:
    with connect_hub(fast_client):
        pass
    store = make_store(fast_client)
    assert start_upsert(fast_client, store).result(5) == UpsertOutcome(KEY_HUB_UNREACHABLE)


def test_live_rows_forgets_timers_missing_from_the_hub(fast_client: TestClient) -> None:
    store = make_store(fast_client)
    with connect_hub(fast_client):
        pass
    store.remember_timer(HUB_ID, "ghost", 60)

    fast_client.portal.call(store.live_rows, HUB_ID)  # type: ignore[union-attr]

    assert store.original_timer_seconds(HUB_ID, "ghost") is None


def test_remembered_timers_expire_and_are_capped() -> None:
    now = [0.0]
    store = AlarmStore(lambda: None, None, clock=lambda: now[0])  # type: ignore[arg-type]
    store.remember_timer("h", "t1", 60)
    now[0] = 60 + TIMER_MEMORY_GRACE_SECONDS - 1
    assert store.original_timer_seconds("h", "t1") == 60
    now[0] = 60 + TIMER_MEMORY_GRACE_SECONDS
    assert store.original_timer_seconds("h", "t1") is None

    for index in range(MAX_REMEMBERED_TIMERS + 10):
        store.remember_timer("h", f"x{index}", 3600)
    assert store.original_timer_seconds("h", "x0") is None
    assert store.original_timer_seconds("h", f"x{MAX_REMEMBERED_TIMERS + 9}") == 3600
