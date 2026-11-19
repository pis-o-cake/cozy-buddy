"""알람·타이머 저장소 접근. 스킬과 확인 질문 해석이 함께 쓴다."""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from cozy.v1 import alarm_pb2
from loguru import logger

from app.core.connections import ConnectionManager
from app.core.database import Database
from app.core.exceptions import CozyError
from app.domain.agent.skills.base import KEY_INTERNAL
from app.domain.alarm.schemas import AlarmRead
from app.domain.alarm.service import AlarmService
from app.domain.hub.service import HubService

KEY_HUB_REQUIRED = "agent.hub_required"
PENDING_DELETE = "pending_delete"
TIMER_KIND = "timer"
REMINDER_KIND = "reminder"


class AlarmStore:
    """허브의 알람 사본을 읽고 쓴다. 알람 정본은 허브이므로 쓰기는 AlarmService 를 거친다."""

    def __init__(self, database: Callable[[], Database], connections: ConnectionManager) -> None:
        self._database = database
        self._connections = connections
        # 원래 맞춘 타이머 길이. 허브 스냅샷에는 만든 시각이 없어 서버가 기억한다.
        self._timer_seconds: dict[tuple[str, str], int] = {}

    @asynccontextmanager
    async def _service(self) -> AsyncIterator[AlarmService]:
        async with self._database().session() as session:
            hubs = HubService(session, self._connections)
            yield AlarmService(session, self._connections, hubs)

    def remember_timer(self, hub_id: str, alarm_id: str, seconds: int) -> None:
        """타이머를 처음 맞춘 길이를 기억한다."""
        self._timer_seconds[(hub_id, alarm_id)] = seconds

    def original_timer_seconds(self, hub_id: str, alarm_id: str) -> int | None:
        """기억해 둔 원래 타이머 길이. 서버가 만들지 않았거나 재시작했으면 None."""
        return self._timer_seconds.get((hub_id, alarm_id))

    async def upsert(self, hub_id: str, alarm: alarm_pb2.Alarm) -> str | None:
        """알람을 허브에 만든다. 실패하면 사용자 메시지 키를, 성공하면 None 을 돌려준다."""
        if not hub_id:
            return KEY_HUB_REQUIRED
        alarm.hub_id = hub_id
        try:
            async with self._service() as service:
                await service.handle_mobile_upsert(
                    alarm_pb2.AlarmUpsert(request_id=uuid.uuid4().hex, alarm=alarm)
                )
        except CozyError as exc:
            logger.warning("Alarm creation rejected: {}", exc)
            return exc.message_key or KEY_INTERNAL
        return None

    async def live_rows(self, hub_id: str) -> list[AlarmRead]:
        """삭제 대기 중이 아닌 알람·타이머."""
        async with self._service() as service:
            rows = await service.list_alarms(hub_id)
        return [r for r in rows if r.sync_state != PENDING_DELETE]

    async def delete_rows(self, hub_id: str, rows: list[AlarmRead]) -> None:
        """AlarmDelete 를 허브로 보낸다. 거부되면 CozyError 를 그대로 올린다."""
        async with self._service() as service:
            for row in rows:
                await service.handle_mobile_delete(
                    alarm_pb2.AlarmDelete(
                        request_id=uuid.uuid4().hex, alarm_id=row.alarm_id, hub_id=hub_id
                    )
                )
                self._timer_seconds.pop((hub_id, row.alarm_id), None)


def new_alarm(
    kind: alarm_pb2.AlarmKind,
    label: str | None,
    *,
    fires_at: datetime | None = None,
    hour: int = 0,
    minute: int = 0,
    days: tuple[int, ...] = (),
) -> alarm_pb2.Alarm:
    """일회성(fires_at) 또는 반복(hour/minute/days) 알람 메시지를 만든다."""
    alarm = alarm_pb2.Alarm(alarm_id=uuid.uuid4().hex, kind=kind, label=label or "", enabled=True)
    if fires_at is not None:
        alarm.one_shot.fires_at_epoch_ms = int(fires_at.timestamp() * 1000)
        return alarm
    alarm.repeating.hour = hour
    alarm.repeating.minute = minute
    alarm.repeating.days.extend(days)  # type: ignore[arg-type]
    return alarm
