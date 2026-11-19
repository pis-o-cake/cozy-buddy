from collections.abc import Callable

import pytest
from cozy.v1.alarm_pb2 import (
    ALARM_KIND_ALARM,
    DAY_OF_WEEK_MONDAY,
    DAY_OF_WEEK_SUNDAY,
    Alarm,
    AlarmAck,
    AlarmDelete,
    AlarmSnapshot,
    AlarmUpsert,
    OneShot,
    Repeating,
)
from cozy.v1.common_pb2 import ERROR_CODE_NOT_FOUND
from cozy.v1.envelope_pb2 import Envelope
from cozy.v1.session_pb2 import Ping
from fastapi.testclient import TestClient

from app.domain.alarm.service import MAX_ALARM_LABEL_CHARS
from tests.conftest import HUB_ID, connect_hub, connect_mobile, eventually, recv, send


def repeating_alarm(alarm_id: str = "a1", revision: int = 0) -> Alarm:
    return Alarm(
        alarm_id=alarm_id,
        kind=ALARM_KIND_ALARM,
        label="기상",
        repeating=Repeating(hour=7, minute=30, days=[DAY_OF_WEEK_MONDAY, DAY_OF_WEEK_SUNDAY]),
        enabled=True,
        sunrise=True,
        revision=revision,
        hub_id=HUB_ID,
    )


def alarms(client: TestClient) -> list[dict[str, object]]:
    result: list[dict[str, object]] = client.get("/api/alarm", params={"hub_id": HUB_ID}).json()
    return result


def register_hub(client: TestClient) -> None:
    with connect_hub(client):
        pass
    eventually(lambda: not client.get("/api/hub").json()[0]["online"])


def test_snapshot_is_stored_and_forwarded_to_mobile(client: TestClient) -> None:
    one_shot = Alarm(
        alarm_id="t1", label="타이머", one_shot=OneShot(fires_at_epoch_ms=99), revision=4
    )
    with connect_mobile(client) as mobile, connect_hub(client) as hub:
        send(
            hub,
            Envelope(
                alarm_snapshot=AlarmSnapshot(hub_id=HUB_ID, alarms=[repeating_alarm(), one_shot])
            ),
        )
        forwarded = recv(mobile)
        stored = eventually(lambda: alarms(client) if len(alarms(client)) == 2 else None)

    assert forwarded.alarm_snapshot.hub_id == HUB_ID
    assert len(forwarded.alarm_snapshot.alarms) == 2
    by_id = {a["alarm_id"]: a for a in stored}
    assert by_id["a1"]["hour"] == 7
    assert by_id["a1"]["days"] == [DAY_OF_WEEK_MONDAY, DAY_OF_WEEK_SUNDAY]
    assert by_id["a1"]["fires_at_epoch_ms"] is None
    assert by_id["a1"]["sync_state"] == "synced"
    assert by_id["t1"]["fires_at_epoch_ms"] == 99
    assert by_id["t1"]["revision"] == 4


def test_snapshot_replaces_synced_but_keeps_pending(client: TestClient) -> None:
    register_hub(client)
    with connect_mobile(client) as mobile:
        send(
            mobile, Envelope(alarm_upsert=AlarmUpsert(request_id="r", alarm=repeating_alarm("p1")))
        )
        eventually(lambda: alarms(client))
        with connect_hub(client, consume_sync_request=False) as hub:
            recv(hub)  # AlarmUpsert
            recv(hub)  # AlarmSyncRequest
            send(
                hub,
                Envelope(
                    alarm_snapshot=AlarmSnapshot(hub_id=HUB_ID, alarms=[repeating_alarm("s1")])
                ),
            )
            recv(mobile)
            stored = eventually(lambda: {a["alarm_id"] for a in alarms(client)} == {"p1", "s1"})
    assert stored


def test_mobile_upsert_forwarded_to_online_hub_and_ack_returns(client: TestClient) -> None:
    with connect_hub(client) as hub, connect_mobile(client) as mobile:
        send(
            mobile, Envelope(alarm_upsert=AlarmUpsert(request_id="req-1", alarm=repeating_alarm()))
        )
        forwarded = recv(hub)
        assert forwarded.alarm_upsert.request_id == "req-1"
        assert forwarded.alarm_upsert.alarm.alarm_id == "a1"
        assert forwarded.alarm_upsert.alarm.repeating.hour == 7
        assert alarms(client)[0]["sync_state"] == "pending_to_hub"

        ack = AlarmAck(request_id="req-1", alarm_id="a1", revision=5, ok=True)
        send(hub, Envelope(alarm_ack=ack))
        assert recv(mobile).alarm_ack.revision == 5
        row = eventually(lambda: [a for a in alarms(client) if a["sync_state"] == "synced"])
    assert row[0]["revision"] == 5


def test_rejected_ack_marks_rejected_forwards_hub_id_and_requests_snapshot(
    client: TestClient,
) -> None:
    with connect_hub(client) as hub, connect_mobile(client) as mobile:
        send(mobile, Envelope(alarm_upsert=AlarmUpsert(request_id="r", alarm=repeating_alarm())))
        recv(hub)
        send(hub, Envelope(alarm_ack=AlarmAck(request_id="r", alarm_id="a1", ok=False)))
        forwarded = recv(mobile).alarm_ack
        assert forwarded.ok is False and forwarded.hub_id == HUB_ID
        assert recv(hub).alarm_sync_request.hub_id == HUB_ID
    assert alarms(client)[0]["sync_state"] == "rejected"


def test_rejected_change_is_not_redelivered_and_snapshot_clears_it(client: TestClient) -> None:
    with connect_hub(client) as hub, connect_mobile(client) as mobile:
        send(mobile, Envelope(alarm_upsert=AlarmUpsert(request_id="r", alarm=repeating_alarm())))
        recv(hub)
        send(hub, Envelope(alarm_ack=AlarmAck(request_id="r", alarm_id="a1", ok=False)))
        recv(mobile)
        recv(hub)

    with connect_hub(client, consume_sync_request=False) as hub:
        assert recv(hub).WhichOneof("body") == "alarm_sync_request"
        send(hub, Envelope(alarm_snapshot=AlarmSnapshot(hub_id=HUB_ID, alarms=[])))
        eventually(lambda: alarms(client) == [])


def test_rejected_delete_reverts_to_synced(client: TestClient) -> None:
    with connect_hub(client) as hub, connect_mobile(client) as mobile:
        send(hub, Envelope(alarm_snapshot=AlarmSnapshot(hub_id=HUB_ID, alarms=[repeating_alarm()])))
        recv(mobile)
        eventually(lambda: alarms(client))
        delete = AlarmDelete(request_id="d1", alarm_id="a1", hub_id=HUB_ID)
        send(mobile, Envelope(alarm_delete=delete))
        recv(hub)
        send(hub, Envelope(alarm_ack=AlarmAck(request_id="d1", alarm_id="a1", ok=False)))
        recv(mobile)
        eventually(lambda: alarms(client)[0]["sync_state"] == "synced")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: setattr(a, "alarm_id", "x" * 65),
        lambda a: setattr(a, "alarm_id", "bad id!"),
        lambda a: setattr(a.repeating, "hour", 24),
        lambda a: setattr(a.repeating, "minute", 60),
        lambda a: a.repeating.days.append(0),
        lambda a: a.repeating.days.append(8),
        lambda a: a.repeating.days.append(DAY_OF_WEEK_MONDAY),
    ],
)
def test_mobile_upsert_with_invalid_fields_is_rejected(
    client: TestClient, mutate: Callable[[Alarm], None]
) -> None:
    register_hub(client)
    alarm = repeating_alarm()
    mutate(alarm)
    with connect_mobile(client) as mobile:
        send(mobile, Envelope(alarm_upsert=AlarmUpsert(request_id="r", alarm=alarm)))
        error = recv(mobile).error
    assert error.message_key == "alarm.invalid"
    assert alarms(client) == []


def test_snapshot_with_duplicate_alarm_ids_still_commits(client: TestClient) -> None:
    snapshot = AlarmSnapshot(
        hub_id=HUB_ID, alarms=[repeating_alarm("d1"), repeating_alarm("d1"), repeating_alarm("d2")]
    )
    with connect_mobile(client) as mobile, connect_hub(client) as hub:
        send(hub, Envelope(alarm_snapshot=snapshot))
        recv(mobile)
        stored = eventually(lambda: alarms(client))
    assert sorted(a["alarm_id"] for a in stored) == ["d1", "d2"]


def test_upsert_while_hub_offline_is_delivered_after_reconnect(client: TestClient) -> None:
    register_hub(client)
    with connect_mobile(client) as mobile:
        send(mobile, Envelope(alarm_upsert=AlarmUpsert(request_id="r", alarm=repeating_alarm())))
        eventually(lambda: alarms(client))
        assert alarms(client)[0]["sync_state"] == "pending_to_hub"

        with connect_hub(client, consume_sync_request=False) as hub:
            delivered = recv(hub)
            sync_request = recv(hub)
            assert delivered.alarm_upsert.alarm.alarm_id == "a1"
            assert delivered.alarm_upsert.request_id
            assert sync_request.alarm_sync_request.hub_id == HUB_ID

            send(
                hub,
                Envelope(alarm_ack=AlarmAck(request_id="x", alarm_id="a1", revision=2, ok=True)),
            )
            recv(mobile)
            eventually(lambda: alarms(client)[0]["sync_state"] == "synced")


def test_delete_flow(client: TestClient) -> None:
    with connect_hub(client) as hub, connect_mobile(client) as mobile:
        send(hub, Envelope(alarm_snapshot=AlarmSnapshot(hub_id=HUB_ID, alarms=[repeating_alarm()])))
        recv(mobile)
        eventually(lambda: alarms(client))

        send(
            mobile,
            Envelope(alarm_delete=AlarmDelete(request_id="d1", alarm_id="a1", hub_id=HUB_ID)),
        )
        assert recv(hub).alarm_delete.alarm_id == "a1"
        assert alarms(client)[0]["sync_state"] == "pending_delete"

        send(hub, Envelope(alarm_ack=AlarmAck(request_id="d1", alarm_id="a1", revision=6, ok=True)))
        recv(mobile)
        eventually(lambda: alarms(client) == [])


def test_upsert_for_unknown_hub_returns_not_found(client: TestClient) -> None:
    with connect_mobile(client) as mobile:
        send(mobile, Envelope(alarm_upsert=AlarmUpsert(request_id="r9", alarm=repeating_alarm())))
        reply = recv(mobile)
    assert reply.error.code == ERROR_CODE_NOT_FOUND
    assert reply.error.request_id == "r9"


def test_mobile_upsert_with_oversized_label_is_rejected(client: TestClient) -> None:
    register_hub(client)
    long_alarm = repeating_alarm()
    long_alarm.label = "가" * (MAX_ALARM_LABEL_CHARS + 1)
    with connect_mobile(client) as mobile:
        send(mobile, Envelope(alarm_upsert=AlarmUpsert(request_id="r", alarm=long_alarm)))
        error = recv(mobile).error
    assert error.message_key == "alarm.label_too_long"
    assert alarms(client) == []


def test_mobile_upsert_stops_at_max_alarms_per_hub(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.domain.alarm.service.MAX_ALARMS_PER_HUB", 2)
    register_hub(client)
    with connect_mobile(client) as mobile:
        for alarm_id in ("a1", "a2"):
            upsert = AlarmUpsert(request_id=alarm_id, alarm=repeating_alarm(alarm_id))
            send(mobile, Envelope(alarm_upsert=upsert))
        eventually(lambda: len(alarms(client)) == 2)
        upsert = AlarmUpsert(request_id="r3", alarm=repeating_alarm("a3"))
        send(mobile, Envelope(alarm_upsert=upsert))
        assert recv(mobile).error.message_key == "alarm.limit_reached"
        # 기존 알람의 수정은 한도와 무관하게 허용한다.
        upsert = AlarmUpsert(request_id="r4", alarm=repeating_alarm("a1"))
        send(mobile, Envelope(alarm_upsert=upsert))
        send(mobile, Envelope(ping=Ping(sent_epoch_ms=1)))
        assert recv(mobile).WhichOneof("body") == "pong"
    assert {a["alarm_id"] for a in alarms(client)} == {"a1", "a2"}


def test_snapshot_truncates_labels_and_caps_alarm_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.domain.alarm.service.MAX_ALARMS_PER_HUB", 2)
    long_alarm = repeating_alarm("s1")
    long_alarm.label = "나" * (MAX_ALARM_LABEL_CHARS + 50)
    snapshot = AlarmSnapshot(
        hub_id=HUB_ID, alarms=[long_alarm, repeating_alarm("s2"), repeating_alarm("s3")]
    )
    with connect_mobile(client) as mobile, connect_hub(client) as hub:
        send(hub, Envelope(alarm_snapshot=snapshot))
        recv(mobile)
        stored = eventually(lambda: alarms(client))
    assert {a["alarm_id"] for a in stored} == {"s1", "s2"}
    label = next(a["label"] for a in stored if a["alarm_id"] == "s1")
    assert isinstance(label, str) and len(label) == MAX_ALARM_LABEL_CHARS
