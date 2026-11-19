"""알람 사본 관리와 허브 동기화.

허브가 정본이다. 서버는 앱에서 온 변경을 허브에 전달하고, 허브의 확인(ack)과
스냅샷으로 사본을 맞춘다.
"""

import re
import uuid
from typing import Any, cast

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

MAX_ALARM_LABEL_CHARS = 100
MAX_ALARMS_PER_HUB = 200
MAX_ALARM_ID_CHARS = 64
MAX_HOUR = 23
MAX_MINUTE = 59
_ALARM_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_DAY_VALUES = frozenset(range(1, 8))
KEY_INVALID = "alarm.invalid"

_KIND_NAMES: dict[int, AlarmKindName] = {
    alarm_pb2.ALARM_KIND_TIMER: AlarmKindName.TIMER,
    alarm_pb2.ALARM_KIND_REMINDER: AlarmKindName.REMINDER,
}
_KIND_PROTOS: dict[str, Any] = {
    AlarmKindName.TIMER: alarm_pb2.ALARM_KIND_TIMER,
    AlarmKindName.REMINDER: alarm_pb2.ALARM_KIND_REMINDER,
}


def apply_proto(row: Alarm, alarm: alarm_pb2.Alarm) -> None:
    """cozy.v1.Alarm 의 내용을 모델 컬럼으로 옮긴다. revision 은 건드리지 않는다."""
    row.kind = _KIND_NAMES.get(alarm.kind, AlarmKindName.ALARM)
    # 허브가 정본이라 스냅샷 전체를 거절하지 않고 자른다. 앱 요청은 service 에서 먼저 거절한다.
    row.label = alarm.label[:MAX_ALARM_LABEL_CHARS]
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
        kind=_KIND_PROTOS.get(row.kind, alarm_pb2.ALARM_KIND_ALARM),
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


def _is_valid_alarm_id(alarm_id: str) -> bool:
    return len(alarm_id) <= MAX_ALARM_ID_CHARS and _ALARM_ID_PATTERN.fullmatch(alarm_id) is not None


def validate_alarm(alarm: alarm_pb2.Alarm) -> None:
    """앱이 보낸 알람 필드를 검사한다.

    Raises:
        BadRequestError: alarm_id 형식, 시각 범위, 요일 값이 올바르지 않을 때.
    """
    if alarm.alarm_id and not _is_valid_alarm_id(alarm.alarm_id):
        raise BadRequestError("Invalid alarm id", KEY_INVALID)
    if alarm.WhichOneof("schedule") != "repeating":
        return
    repeating = alarm.repeating
    days = list(repeating.days)
    if repeating.hour > MAX_HOUR or repeating.minute > MAX_MINUTE:
        raise BadRequestError("Alarm time out of range", KEY_INVALID)
    if not _DAY_VALUES.issuperset(days) or len(set(days)) != len(days):
        raise BadRequestError("Invalid alarm days", KEY_INVALID)


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
        if len(snapshot.alarms) > MAX_ALARMS_PER_HUB:
            logger.warning(
                "Alarm snapshot truncated: hub={} received={} kept={}",
                hub_id,
                len(snapshot.alarms),
                MAX_ALARMS_PER_HUB,
            )
        seen: set[str] = set()
        for alarm in snapshot.alarms[:MAX_ALARMS_PER_HUB]:
            if not _is_valid_alarm_id(alarm.alarm_id):
                logger.warning("Snapshot alarm with invalid id skipped: hub={}", hub_id)
                continue
            if alarm.alarm_id in seen:
                logger.warning(
                    "Duplicate alarm id in snapshot skipped: hub={} alarm={}",
                    hub_id,
                    alarm.alarm_id,
                )
                continue
            seen.add(alarm.alarm_id)
            if alarm.alarm_id in pending_ids:
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
        """허브의 처리 결과를 사본에 반영하고 앱에 전달한다.

        허브가 거절하면 변경 대기 상태를 풀고(삭제는 synced, 수정은 rejected) 스냅샷을 요청해
        사본을 허브의 값으로 되돌린다. 앱에는 hub_id 를 채운 ack 를 보내 실패를 보여 주게 한다.
        """
        row = await crud.get(self._session, hub_id, ack.alarm_id)
        if not ack.ok:
            logger.warning(
                "Hub rejected alarm change: hub={} alarm={} code={}", hub_id, ack.alarm_id, ack.code
            )
            await self._revert_rejected(hub_id, row)
        elif row is not None and row.sync_state == SyncState.PENDING_DELETE:
            await crud.delete_row(self._session, row)
            await self._session.commit()
        elif row is not None:
            row.sync_state = SyncState.SYNCED
            row.revision = ack.revision
            await self._session.commit()
        self._connections.alarm_acks.resolve(hub_id, ack)
        forwarded = alarm_pb2.AlarmAck()
        forwarded.CopyFrom(ack)
        forwarded.hub_id = hub_id
        await self._connections.broadcast_to_mobiles(Envelope(alarm_ack=forwarded))

    async def _revert_rejected(self, hub_id: str, row: Alarm | None) -> None:
        if row is None or row.sync_state == SyncState.SYNCED:
            return
        row.sync_state = (
            SyncState.SYNCED if row.sync_state == SyncState.PENDING_DELETE else SyncState.REJECTED
        )
        await self._session.commit()
        await self._connections.send_to_hub(
            hub_id, Envelope(alarm_sync_request=alarm_pb2.AlarmSyncRequest(hub_id=hub_id))
        )

    async def handle_mobile_upsert(self, upsert: alarm_pb2.AlarmUpsert) -> bool:
        """앱의 생성·수정 요청을 저장하고, 허브가 온라인이면 즉시 전달한다.

        Returns:
            허브에 바로 보냈으면 True. 오프라인이거나 전송에 실패하면 False(재연결 때 다시 보낸다).

        Raises:
            BadRequestError: 대상 허브가 지정되지 않았거나, 이름이 너무 길거나, 필드 값이
                올바르지 않거나, 허브의 알람이 `MAX_ALARMS_PER_HUB` 개에 도달했을 때.
            NotFoundError: 등록되지 않은 허브일 때.
        """
        hub_id = upsert.alarm.hub_id
        await self._require_hub(hub_id)
        if len(upsert.alarm.label) > MAX_ALARM_LABEL_CHARS:
            raise BadRequestError("Alarm label too long", "alarm.label_too_long")
        validate_alarm(upsert.alarm)
        alarm_id = upsert.alarm.alarm_id or uuid.uuid4().hex
        row = await crud.get(self._session, hub_id, alarm_id)
        if row is None:
            if await crud.count(self._session, hub_id) >= MAX_ALARMS_PER_HUB:
                logger.warning("Alarm limit reached: hub={}", hub_id)
                raise BadRequestError("Alarm limit reached", "alarm.limit_reached")
            row = Alarm(hub_id=hub_id, alarm_id=alarm_id)
            self._session.add(row)
        apply_proto(row, upsert.alarm)
        row.sync_state = SyncState.PENDING_TO_HUB
        await self._session.commit()

        message = alarm_pb2.AlarmUpsert(request_id=upsert.request_id, alarm=to_proto(row))
        return await self._connections.send_to_hub(hub_id, Envelope(alarm_upsert=message))

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
