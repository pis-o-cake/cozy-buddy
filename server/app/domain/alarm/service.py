"""알람 사본 관리와 허브 동기화.

허브가 정본이다. 서버는 앱에서 온 변경을 허브에 전달하고, 허브의 확인(ack)과
스냅샷으로 사본을 맞춘다.
"""

import uuid
from typing import cast

from cozy.v1 import alarm_pb2
from cozy.v1.envelope_pb2 import Envelope
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.connections import ConnectionManager
from app.core.exceptions import BadRequestError, NotFoundError
from app.domain.alarm import crud
from app.domain.alarm.models import Alarm, AlarmKindName, SyncState
from app.domain.alarm.schemas import AlarmRead
from app.domain.hub.service import HubService


def apply_proto(row: Alarm, alarm: alarm_pb2.Alarm) -> None:
    """cozy.v1.Alarm 의 내용을 모델 컬럼으로 옮긴다. revision 은 건드리지 않는다."""
    row.kind = (
        AlarmKindName.TIMER if alarm.kind == alarm_pb2.ALARM_KIND_TIMER else AlarmKindName.ALARM
    )
    row.label = alarm.label
    row.enabled = alarm.enabled
    row.sunrise = alarm.sunrise
    row.fires_at_epoch_ms = None
    row.hour = None
    row.minute = None
    row.days = []
    schedule = alarm.WhichOneof("schedule")
    if schedule == "one_shot":
        row.fires_at_epoch_ms = alarm.one_shot.fires_at_epoch_ms
    elif schedule == "repeating":
        row.hour = alarm.repeating.hour
        row.minute = alarm.repeating.minute
        row.days = [int(day) for day in alarm.repeating.days]


def to_proto(row: Alarm) -> alarm_pb2.Alarm:
    """모델을 cozy.v1.Alarm 으로 되돌린다."""
    alarm = alarm_pb2.Alarm(
        alarm_id=row.alarm_id,
        kind=alarm_pb2.ALARM_KIND_TIMER
        if row.kind == AlarmKindName.TIMER
        else alarm_pb2.ALARM_KIND_ALARM,
        label=row.label,
        enabled=row.enabled,
        sunrise=row.sunrise,
        revision=row.revision,
        hub_id=row.hub_id,
    )
    if row.fires_at_epoch_ms is not None:
        alarm.one_shot.fires_at_epoch_ms = row.fires_at_epoch_ms
    elif row.hour is not None and row.minute is not None:
        alarm.repeating.hour = row.hour
        alarm.repeating.minute = row.minute
        alarm.repeating.days.extend(cast(list[alarm_pb2.DayOfWeek], row.days))
    return alarm


class AlarmService:
    """알람 사본의 저장과 허브·앱 사이 중계."""

    def __init__(
        self, session: AsyncSession, connections: ConnectionManager, hubs: HubService
    ) -> None:
        self._session = session
        self._connections = connections
        self._hubs = hubs

    async def list_alarms(self, hub_id: str | None = None) -> list[AlarmRead]:
        """알람 사본과 sync_state 를 돌려준다."""
        rows = await crud.list_alarms(self._session, hub_id)
        return [AlarmRead.model_validate(row) for row in rows]

    async def handle_snapshot(self, hub_id: str, snapshot: alarm_pb2.AlarmSnapshot) -> None:
        """허브의 전체 목록으로 synced 사본을 교체한다. pending 행은 유지한다."""
        pending_ids = {row.alarm_id for row in await crud.list_pending(self._session, hub_id)}
        await crud.delete_synced(self._session, hub_id)
        for alarm in snapshot.alarms:
            if not alarm.alarm_id or alarm.alarm_id in pending_ids:
                continue
            row = Alarm(
                hub_id=hub_id,
                alarm_id=alarm.alarm_id,
                revision=alarm.revision,
                sync_state=SyncState.SYNCED,
            )
            apply_proto(row, alarm)
            self._session.add(row)
        await self._session.commit()

        forwarded = alarm_pb2.AlarmSnapshot()
        forwarded.CopyFrom(snapshot)
        forwarded.hub_id = hub_id
        await self._connections.broadcast_to_mobiles(Envelope(alarm_snapshot=forwarded))

    async def handle_ack(self, hub_id: str, ack: alarm_pb2.AlarmAck) -> None:
        """허브의 처리 결과를 사본에 반영하고 앱에 전달한다."""
        row = await crud.get(self._session, hub_id, ack.alarm_id)
        if not ack.ok:
            logger.warning(
                "Hub rejected alarm change: hub={} alarm={} code={}", hub_id, ack.alarm_id, ack.code
            )
        elif row is not None and row.sync_state == SyncState.PENDING_DELETE:
            await crud.delete_row(self._session, row)
            await self._session.commit()
        elif row is not None:
            row.sync_state = SyncState.SYNCED
            row.revision = ack.revision
            await self._session.commit()
        await self._connections.broadcast_to_mobiles(Envelope(alarm_ack=ack))

    async def handle_mobile_upsert(self, upsert: alarm_pb2.AlarmUpsert) -> None:
        """앱의 생성·수정 요청을 저장하고, 허브가 온라인이면 즉시 전달한다.

        Raises:
            BadRequestError: 대상 허브가 지정되지 않았을 때.
            NotFoundError: 등록되지 않은 허브일 때.
        """
        hub_id = upsert.alarm.hub_id
        await self._require_hub(hub_id)
        alarm_id = upsert.alarm.alarm_id or uuid.uuid4().hex
        row = await crud.get(self._session, hub_id, alarm_id)
        if row is None:
            row = Alarm(hub_id=hub_id, alarm_id=alarm_id)
            self._session.add(row)
        apply_proto(row, upsert.alarm)
        row.sync_state = SyncState.PENDING_TO_HUB
        await self._session.commit()

        message = alarm_pb2.AlarmUpsert(request_id=upsert.request_id, alarm=to_proto(row))
        await self._connections.send_to_hub(hub_id, Envelope(alarm_upsert=message))

    async def handle_mobile_delete(self, delete: alarm_pb2.AlarmDelete) -> None:
        """앱의 삭제 요청을 pending_delete 로 표시하고, 허브가 온라인이면 즉시 전달한다.

        Raises:
            BadRequestError: 대상 허브가 지정되지 않았을 때.
            NotFoundError: 등록되지 않은 허브이거나, 오프라인 허브에 없는 알람일 때.
        """
        hub_id = delete.hub_id
        await self._require_hub(hub_id)
        row = await crud.get(self._session, hub_id, delete.alarm_id)
        online = self._connections.is_hub_online(hub_id)
        if row is None and not online:
            raise NotFoundError(f"Alarm not found: {delete.alarm_id}", "alarm.not_found")
        if row is not None:
            row.sync_state = SyncState.PENDING_DELETE
            await self._session.commit()
        await self._connections.send_to_hub(hub_id, Envelope(alarm_delete=delete))

    async def deliver_pending(self, hub_id: str) -> None:
        """허브 재연결 직후 대기 중인 변경을 보내고, 스냅샷을 요청한다."""
        for row in await crud.list_pending(self._session, hub_id):
            request_id = uuid.uuid4().hex
            if row.sync_state == SyncState.PENDING_DELETE:
                envelope = Envelope(
                    alarm_delete=alarm_pb2.AlarmDelete(
                        request_id=request_id, alarm_id=row.alarm_id, hub_id=hub_id
                    )
                )
            else:
                envelope = Envelope(
                    alarm_upsert=alarm_pb2.AlarmUpsert(request_id=request_id, alarm=to_proto(row))
                )
            if not await self._connections.send_to_hub(hub_id, envelope):
                return
        await self._connections.send_to_hub(
            hub_id, Envelope(alarm_sync_request=alarm_pb2.AlarmSyncRequest(hub_id=hub_id))
        )

    async def _require_hub(self, hub_id: str) -> None:
        if not hub_id:
            raise BadRequestError("Alarm hub_id is required", "alarm.hub_required")
        if not await self._hubs.exists(hub_id):
            raise NotFoundError(f"Hub not found: {hub_id}", "hub.not_found")
