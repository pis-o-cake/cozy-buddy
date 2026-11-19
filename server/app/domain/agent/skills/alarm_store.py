"""알람·타이머 저장소 접근. 스킬과 확인 질문 해석이 함께 쓴다."""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
KEY_HUB_UNREACHABLE = "agent.alarm.hub_unreachable"
KEY_HUB_REJECTED = "agent.alarm.hub_rejected"
KEY_HUB_PENDING = "agent.alarm.hub_pending"
# 타이머가 울린 뒤에도 길이를 남겨 두는 여유와, 기억할 타이머 수 상한.
TIMER_MEMORY_GRACE_SECONDS = 300.0
MAX_REMEMBERED_TIMERS = 256


@dataclass(frozen=True, slots=True)
class UpsertOutcome:
    """허브에 알람을 만든 결과.

    Attributes:
        error_key: 실패했을 때 사용자 메시지 키. 성공이면 빈 문자열.
        pending: 허브에 보냈지만 제한 시간 안에 ack 가 오지 않아 확정하지 못했는지.
    """

    error_key: str = ""
    pending: bool = False


PENDING_DELETE = "pending_delete"
REJECTED = "rejected"
TIMER_KIND = "timer"
REMINDER_KIND = "reminder"


class AlarmStore:
    """허브의 알람 사본을 읽고 쓴다. 알람 정본은 허브이므로 쓰기는 AlarmService 를 거친다."""

    def __init__(
        self,
        database: Callable[[], Database],
        connections: ConnectionManager,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._database = database
        self._connections = connections
        self._clock = clock
        # 원래 맞춘 타이머 길이와 기억을 버릴 시각. 스냅샷에는 만든 시각이 없어 서버가 기억한다.
        self._timer_seconds: dict[tuple[str, str], tuple[int, float]] = {}

    @asynccontextmanager
    async def _service(self) -> AsyncIterator[AlarmService]:
        async with self._database().session() as session:
            hubs = HubService(session, self._connections)
            yield AlarmService(session, self._connections, hubs)

    def remember_timer(self, hub_id: str, alarm_id: str, seconds: int) -> None:
        """타이머를 처음 맞춘 길이를 기억한다. 울린 뒤 여유 시간이 지나면 버린다."""
        self._prune_timers()
        expires = self._clock() + seconds + TIMER_MEMORY_GRACE_SECONDS
        self._timer_seconds[(hub_id, alarm_id)] = (seconds, expires)
        while len(self._timer_seconds) > MAX_REMEMBERED_TIMERS:
            del self._timer_seconds[next(iter(self._timer_seconds))]

    def original_timer_seconds(self, hub_id: str, alarm_id: str) -> int | None:
        """기억해 둔 원래 타이머 길이. 서버가 만들지 않았거나 재시작했거나 오래됐으면 None."""
        self._prune_timers()
        entry = self._timer_seconds.get((hub_id, alarm_id))
        return None if entry is None else entry[0]

    def _prune_timers(self) -> None:
        now = self._clock()
        for key in [k for k, (_, expires) in self._timer_seconds.items() if expires <= now]:
            del self._timer_seconds[key]

    def _forget_missing_timers(self, hub_id: str, alive: set[str]) -> None:
        for key in [k for k in self._timer_seconds if k[0] == hub_id and k[1] not in alive]:
            del self._timer_seconds[key]

    async def upsert(self, hub_id: str, alarm: alarm_pb2.Alarm) -> UpsertOutcome:
        """알람을 허브에 만들고 허브의 ack 를 짧게 기다린다.

        Returns:
            ack 로 확정했으면 빈 결과. 허브가 오프라인이거나 거절하거나 요청이 잘못됐으면
            error_key 를 채우고, 시간 안에 ack 가 없으면 pending 으로 표시한다.
        """
        if not hub_id:
            return UpsertOutcome(KEY_HUB_REQUIRED)
        alarm.hub_id = hub_id
        request_id = uuid.uuid4().hex
        waiter = self._connections.alarm_acks.expect(hub_id, request_id)
        try:
            async with self._service() as service:
                sent = await service.handle_mobile_upsert(
                    alarm_pb2.AlarmUpsert(request_id=request_id, alarm=alarm)
                )
            if not sent:
                logger.warning("Alarm not delivered, hub unreachable: hub={}", hub_id)
                return UpsertOutcome(KEY_HUB_UNREACHABLE)
            return await self._await_ack(hub_id, request_id, waiter)
        except CozyError as exc:
            logger.warning("Alarm creation rejected: {}", exc)
            return UpsertOutcome(exc.message_key or KEY_INTERNAL)
        finally:
            self._connections.alarm_acks.discard(request_id)

    async def _await_ack(
        self, hub_id: str, request_id: str, waiter: asyncio.Future[alarm_pb2.AlarmAck]
    ) -> UpsertOutcome:
        try:
            async with asyncio.timeout(self._connections.alarm_acks.timeout_seconds):
                ack = await waiter
        except TimeoutError:
            logger.warning("Alarm ack timed out: hub={} request={}", hub_id, request_id)
            return UpsertOutcome(pending=True)
        if not ack.ok:
            return UpsertOutcome(KEY_HUB_REJECTED)
        return UpsertOutcome()

    async def live_rows(self, hub_id: str) -> list[AlarmRead]:
        """삭제 대기 중이거나 허브가 거절한 것을 뺀 알람·타이머."""
        async with self._service() as service:
            rows = await service.list_alarms(hub_id)
        self._forget_missing_timers(hub_id, {r.alarm_id for r in rows})
        return [r for r in rows if r.sync_state not in (PENDING_DELETE, REJECTED)]

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
